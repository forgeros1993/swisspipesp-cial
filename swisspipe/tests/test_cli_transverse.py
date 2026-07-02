"""CLI inbound TRANSVERSE (étape 9 §A) — moule T2 : shadow par défaut, --apply opt-in.

Tests hermétiques : Postgres local + FAKE exécuteur injecté (fabrique), ZÉRO serveur.
Prouve : shadow = 0 mutation · --apply exécute · "tous" exclut les archivés · idempotence
à travers le CLI (2e --apply = no-op).
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from swisspipe.adapters.inbound.cli import (
    EXIT_DIVERGENCE,
    EXIT_OK,
    _cmd_cron_transverse,
    _cmd_reconcilier_transverse,
)
from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import archiver_montage, monter_instance
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele, PolitiqueDroits
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    NatureEspace,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.tests.test_reconcile_projection_service import FakeExecuteur

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _montage(session: Session, suffixe: str) -> uuid.UUID:
    modele = Modele(
        id=f"m-{suffixe}",
        nom=f"Modèle {suffixe}",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(DossierImpose(cle="plans", libelle="Plans"),),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        matrice_par_role={"responsable": {"plans": ECRITURE}},
        politique_droits=PolitiqueDroits.IMPOSEE,
    )
    mid = enregistrer_modele(session, modele)
    inst = instancier_modele(
        session, modele, modele_id=mid, nom=suffixe, metadonnees={}, acteur="a"
    )
    rid = enregistrer_role(session, modele_id=mid, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle=f"zztest_grp_{suffixe}")
    session.add(perso)
    session.flush()
    designer_titulaire_role(
        session,
        instance_espace_id=inst.espace_id,
        role_id=rid,
        groupe_perso_id=perso.id,
        acteur="a",
        effectif_depuis=T0,
    )
    host = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("h", suffixe)]),
    )
    session.add(host)
    session.flush()
    montage = monter_instance(
        session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=host.id,
        chemin_hote=f"zztest_transverse_{suffixe}",
        portee_chemins={"/Plans"},
        matrice_plafond={"/Plans": ECRITURE},
        consenti_par="a",
        acteur="a",
    )
    return montage.montage_id


class FabriqueFake:
    """Fabrique injectable : un FakeExecuteur par montage, mémorisé (état persistant)."""

    def __init__(self) -> None:
        self.par_montage: dict[uuid.UUID, FakeExecuteur] = {}
        self.appels: list[uuid.UUID] = []

    def __call__(self, montage_id: uuid.UUID) -> FakeExecuteur:
        self.appels.append(montage_id)
        return self.par_montage.setdefault(montage_id, FakeExecuteur())


def _args(**kw: object) -> argparse.Namespace:
    base: dict[str, object] = {"montage": None, "apply": False}
    base.update(kw)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# SHADOW par défaut : delta constaté, ZÉRO mutation
# ---------------------------------------------------------------------------


def test_defaut_shadow_zero_mutation(db_session: Session, capsys) -> None:
    montage_id = _montage(db_session, "a")
    fabrique = FabriqueFake()

    code = _cmd_reconcilier_transverse(db_session, _args(), fabrique)

    assert code == EXIT_DIVERGENCE  # delta non vide, rien écrit -> signal monitoring
    fake = fabrique.par_montage[montage_id]
    assert fake.nb_mutations == 0
    assert fake.etat == {}
    sortie = capsys.readouterr().out
    assert "SHADOW" in sortie or "DRY-RUN" in sortie.upper()


def test_apply_execute_le_delta(db_session: Session) -> None:
    montage_id = _montage(db_session, "b")
    fabrique = FabriqueFake()

    code = _cmd_reconcilier_transverse(db_session, _args(apply=True), fabrique)

    assert code == EXIT_OK
    fake = fabrique.par_montage[montage_id]
    assert fake.etat == {"Plans": {"zztest_grp_b": ECRITURE}}
    assert fake.acces_base == {"zztest_grp_b"}


def test_cible_un_montage_par_id(db_session: Session) -> None:
    m1 = _montage(db_session, "c1")
    m2 = _montage(db_session, "c2")
    fabrique = FabriqueFake()

    _cmd_reconcilier_transverse(db_session, _args(montage=str(m1), apply=True), fabrique)

    assert fabrique.appels == [m1]  # m2 pas touché
    assert m2 not in fabrique.par_montage


def test_tous_exclut_les_archives(db_session: Session) -> None:
    actif = _montage(db_session, "d1")
    archive = _montage(db_session, "d2")
    archiver_montage(db_session, archive, acteur="a")
    fabrique = FabriqueFake()

    _cmd_reconcilier_transverse(db_session, _args(apply=True), fabrique)

    assert actif in fabrique.appels
    assert archive not in fabrique.appels  # les archivés sont EXCLUS du balayage


def test_second_apply_noop_via_cli(db_session: Session) -> None:
    montage_id = _montage(db_session, "e")
    fabrique = FabriqueFake()
    _cmd_reconcilier_transverse(db_session, _args(apply=True), fabrique)
    fake = fabrique.par_montage[montage_id]
    fake.nb_mutations = 0

    code = _cmd_reconcilier_transverse(db_session, _args(apply=True), fabrique)

    assert code == EXIT_OK
    assert fake.nb_mutations == 0  # idempotence à travers le CLI


# ---------------------------------------------------------------------------
# cron-transverse : SHADOW (surveillance), signale la divergence
# ---------------------------------------------------------------------------


def test_cron_transverse_shadow(db_session: Session, capsys) -> None:
    montage_id = _montage(db_session, "f")
    fabrique = FabriqueFake()

    code = _cmd_cron_transverse(db_session, _args(), fabrique)

    assert code == EXIT_DIVERGENCE
    assert fabrique.par_montage[montage_id].nb_mutations == 0
    assert "SHADOW" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Revue étape 9 : findings reproduits (blocker + majors + minor)
# ---------------------------------------------------------------------------


def test_cron_transverse_via_le_vrai_parser(db_session: Session, capsys) -> None:
    """BLOCKER revue : le sous-parser cron-transverse ne définit pas --montage ->
    AttributeError à CHAQUE run réel (masqué par les Namespace des tests)."""
    from swisspipe.adapters.inbound.cli import construire_parser

    args = construire_parser().parse_args(["cron-transverse"])  # args RÉELS
    code = _cmd_cron_transverse(db_session, args, FabriqueFake())
    assert code == EXIT_OK  # 0 montage actif -> conforme, pas de crash


def test_montage_uuid_invalide_erreur_propre(db_session: Session, capsys) -> None:
    """Minor revue : --montage <non-uuid> doit donner une erreur propre, pas un traceback."""
    from swisspipe.adapters.inbound.cli import EXIT_ERREUR

    code = _cmd_reconcilier_transverse(db_session, _args(montage="pas-un-uuid"), FabriqueFake())
    assert code == EXIT_ERREUR
    assert "uuid" in capsys.readouterr().out.lower()


def test_fabrique_nextcloud_collision_mountpoint_fail_closed(
    db_session: Session, monkeypatch
) -> None:
    """Major revue : deux GF au même mountPoint -> REFUS (jamais de 1er-match silencieux)."""
    import json as _json

    import pytest as _pytest

    import swisspipe.adapters.inbound.composition as compo

    m1 = _montage(db_session, "g")
    monkeypatch.setattr(
        compo,
        "executer_occ",
        lambda *a, **k: _json.dumps(
            [
                {"id": 30, "mountPoint": "zztest_transverse_g"},
                {"id": 31, "mountPoint": "zztest_transverse_g"},
            ]
        ),
    )
    fabrique = compo.fabrique_executeur_projection(db_session, "nextcloud")
    with _pytest.raises(compo.ConfigurationError, match="ambigu"):
        fabrique(m1)


def test_fabrique_nextcloud_match_unique_ok(db_session: Session, monkeypatch) -> None:
    import json as _json

    import swisspipe.adapters.inbound.composition as compo

    m1 = _montage(db_session, "h")
    monkeypatch.setattr(
        compo,
        "executer_occ",
        lambda *a, **k: _json.dumps([{"id": 42, "mountPoint": "zztest_transverse_h"}]),
    )
    ex = compo.fabrique_executeur_projection(db_session, "nextcloud")(m1)
    assert ex._gf_id == "42"
