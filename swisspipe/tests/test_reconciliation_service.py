"""Tests de l'orchestrateur de réconciliation (application/reconciliation_service.py).

Via AdaptateurMemoire (fake, SANS réseau) + session Postgres (fixture db_session).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.fake.adaptateur_memoire import AdaptateurMemoire
from swisspipe.application.reconciliation_service import (
    _NS_GROUPE_NC,
    RessourceNonMappeeError,
    reconcilier_ressource,
    reconcilier_tout,
)
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource, DroitGroupe
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    JournalAcces,
    NatureEspace,
    Ressource,
    RessourceMapping,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.persistence.models import Octroi as OctroiModel

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)


class _SpyMemoire(AdaptateurMemoire):
    """Fake instrumenté : compte les appels à appliquer_droits (preuve du no-op strict)."""

    def __init__(self) -> None:
        super().__init__()
        self.nb_appliquer = 0

    def appliquer_droits(self, cle_externe, droits):  # type: ignore[no-untyped-def]
        self.nb_appliquer += 1
        super().appliquer_droits(cle_externe, droits)


def _creer_ressource(
    session: Session,
    *,
    octrois: dict[str, Octroi],
    groupes_sans_octroi: tuple[str, ...] = (),
    avec_mapping: bool = True,
) -> tuple[Ressource, _SpyMemoire, str | None]:
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("t", uuid.uuid4().hex)]),
    )
    session.add(espace)
    session.flush()
    ressource = Ressource(type="folder", espace_id=espace.id, chemin="/")
    session.add(ressource)
    session.flush()

    groupes: dict[str, Groupe] = {}
    for cle in set(octrois) | set(groupes_sans_octroi):
        g = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle=cle)
        session.add(g)
        groupes[cle] = g
    session.flush()

    for cle, octroi_dom in octrois.items():
        session.add(
            OctroiModel(
                ressource_id=ressource.id,
                groupe_id=groupes[cle].id,
                mode=octroi_dom.mode,
                matrice=octroi_dom.matrice.vers_jsonb() if octroi_dom.matrice is not None else None,
            )
        )
    session.flush()

    fake = _SpyMemoire()
    cle_ext: str | None = None
    if avec_mapping:
        cle_ext = fake.creer_ressource(DescripteurRessource(type="folder", chemin="/", nom="zz"))
        session.add(
            RessourceMapping(ressource_id=ressource.id, adaptateur="nextcloud", cle_externe=cle_ext)
        )
        session.flush()
    return ressource, fake, cle_ext


def _journal(session: Session, ressource_id: uuid.UUID) -> list[JournalAcces]:
    return list(
        session.execute(
            select(JournalAcces).where(JournalAcces.ressource_id == ressource_id)
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Cas conforme : no-op strict
# ---------------------------------------------------------------------------


def test_conforme_noop_strict(db_session: Session) -> None:
    ressource, fake, cle = _creer_ressource(db_session, octrois={"gA": Octroi.modifier(ECRITURE)})
    fake.appliquer_droits(cle, {DroitGroupe("gA", ECRITURE)})  # réel == désiré
    fake.nb_appliquer = 0  # reset après le seed

    div = reconcilier_ressource(db_session, fake, ressource.id)

    assert div.est_conforme
    assert fake.nb_appliquer == 0  # AUCUN appliquer_droits
    assert _journal(db_session, ressource.id) == []  # AUCUNE ligne de journal


# ---------------------------------------------------------------------------
# Dérive : groupe manquant
# ---------------------------------------------------------------------------


def test_derive_groupe_manquant(db_session: Session) -> None:
    ressource, fake, cle = _creer_ressource(db_session, octrois={"gA": Octroi.modifier(ECRITURE)})
    # réel vide (fake fraîchement créé) -> gA manquant

    div = reconcilier_ressource(db_session, fake, ressource.id)

    assert not div.est_conforme
    assert div.groupes_manquants == frozenset({DroitGroupe("gA", ECRITURE)})
    # le fake reflète maintenant le désiré
    assert fake.lire_droits_effectifs(cle) == frozenset({DroitGroupe("gA", ECRITURE)})
    lignes = _journal(db_session, ressource.id)
    assert len(lignes) == 1
    j = lignes[0]
    assert j.action.value == "octroi"
    assert j.matrice_avant is None
    assert j.matrice_apres == ECRITURE.vers_jsonb()
    assert j.cause["divergence"] == "manquant"
    assert j.acteur == "system:reconciliation"


# ---------------------------------------------------------------------------
# Dérive : groupe en trop
# ---------------------------------------------------------------------------


def test_derive_groupe_en_trop(db_session: Session) -> None:
    # Aucun octroi désiré, mais un groupe fantôme présent côté fake.
    ressource, fake, cle = _creer_ressource(db_session, octrois={}, groupes_sans_octroi=("gP",))
    fake.appliquer_droits(cle, {DroitGroupe("gP", LECTURE)})

    div = reconcilier_ressource(db_session, fake, ressource.id)

    assert div.groupes_en_trop == frozenset({DroitGroupe("gP", LECTURE)})
    assert fake.lire_droits_effectifs(cle) == frozenset()  # retiré
    lignes = _journal(db_session, ressource.id)
    assert len(lignes) == 1
    assert lignes[0].action.value == "revocation"
    assert lignes[0].matrice_avant == LECTURE.vers_jsonb()
    assert lignes[0].matrice_apres is None
    assert lignes[0].cause["divergence"] == "en_trop"


# ---------------------------------------------------------------------------
# Dérive : matrice divergente
# ---------------------------------------------------------------------------


def test_derive_matrice_divergente(db_session: Session) -> None:
    ressource, fake, cle = _creer_ressource(db_session, octrois={"gA": Octroi.modifier(ECRITURE)})
    fake.appliquer_droits(cle, {DroitGroupe("gA", LECTURE)})  # réel = LECTURE, désiré = ÉCRITURE

    div = reconcilier_ressource(db_session, fake, ressource.id)

    assert len(div.matrices_divergentes) == 1
    assert fake.lire_droits_effectifs(cle) == frozenset({DroitGroupe("gA", ECRITURE)})
    lignes = _journal(db_session, ressource.id)
    assert len(lignes) == 1
    assert lignes[0].action.value == "modification"
    assert lignes[0].matrice_avant == LECTURE.vers_jsonb()
    assert lignes[0].matrice_apres == ECRITURE.vers_jsonb()
    assert lignes[0].cause["divergence"] == "matrice"


# ---------------------------------------------------------------------------
# Ressource non mappée
# ---------------------------------------------------------------------------


def test_ressource_non_mappee_leve_exception(db_session: Session) -> None:
    ressource, fake, _ = _creer_ressource(
        db_session, octrois={"gA": Octroi.modifier(ECRITURE)}, avec_mapping=False
    )
    with pytest.raises(RessourceNonMappeeError):
        reconcilier_ressource(db_session, fake, ressource.id)


# ---------------------------------------------------------------------------
# Idempotence + déclencheur
# ---------------------------------------------------------------------------


def test_idempotence(db_session: Session) -> None:
    ressource, fake, _ = _creer_ressource(db_session, octrois={"gA": Octroi.modifier(ECRITURE)})

    div1 = reconcilier_ressource(db_session, fake, ressource.id)
    assert not div1.est_conforme
    assert len(_journal(db_session, ressource.id)) == 1

    div2 = reconcilier_ressource(db_session, fake, ressource.id)
    assert div2.est_conforme  # 2e fois : no-op
    assert len(_journal(db_session, ressource.id)) == 1  # aucune nouvelle ligne


def test_declencheur_auto_dans_cause(db_session: Session) -> None:
    ressource, fake, _ = _creer_ressource(db_session, octrois={"gA": Octroi.modifier(ECRITURE)})

    reconcilier_ressource(db_session, fake, ressource.id, declencheur="auto")

    lignes = _journal(db_session, ressource.id)
    assert lignes[0].cause["declencheur"] == "auto"


def test_derive_groupe_externe_inconnu_du_coeur_est_trace(db_session: Session) -> None:
    # Groupe présent côté Nextcloud mais SANS Groupe correspondant au cœur (cas suspect :
    # upgrade / manip externe). La révocation doit être TRACÉE, pas tue (INV-6).
    ressource, fake, cle = _creer_ressource(db_session, octrois={})
    fake.appliquer_droits(cle, {DroitGroupe("g_externe", LECTURE)})

    div = reconcilier_ressource(db_session, fake, ressource.id, declencheur="auto")

    assert div.groupes_en_trop == frozenset({DroitGroupe("g_externe", LECTURE)})
    assert fake.lire_droits_effectifs(cle) == frozenset()  # bien retiré côté NC

    lignes = _journal(db_session, ressource.id)
    assert len(lignes) == 1  # action tracée, pas de silence
    j = lignes[0]
    assert j.action.value == "revocation"
    assert j.matrice_avant == LECTURE.vers_jsonb()
    assert j.matrice_apres is None
    assert j.cause["divergence"] == "en_trop"
    assert j.cause["declencheur"] == "auto"
    # identité du groupe externe préservée + uuid déterministe (joignable, stable).
    assert j.cause["groupe_nc"] == "g_externe"
    assert j.groupe_id == uuid.uuid5(_NS_GROUPE_NC, "g_externe")


# ===========================================================================
# Balayage en masse — reconcilier_tout
# ===========================================================================


class _FakeResilient(AdaptateurMemoire):
    """Fake partagé par plusieurs ressources ; lève sur les cle_externe marquées cassées."""

    def __init__(self) -> None:
        super().__init__()
        self.cles_cassees: set[str] = set()

    def lire_droits_effectifs(self, cle_externe):  # type: ignore[no-untyped-def]
        if cle_externe in self.cles_cassees:
            raise RuntimeError("boom lecture NC")
        return super().lire_droits_effectifs(cle_externe)


def _creer_dans(
    session: Session, fake: AdaptateurMemoire, groupe_cle: str
) -> tuple[uuid.UUID, str]:
    """Crée une ressource (octroi {groupe_cle: ÉCRITURE}) mappée au `fake`. Pas de seed."""
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("t", uuid.uuid4().hex)]),
    )
    session.add(espace)
    session.flush()
    ressource = Ressource(type="folder", espace_id=espace.id, chemin="/")
    session.add(ressource)
    session.flush()
    groupe = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle=groupe_cle)
    session.add(groupe)
    session.flush()
    session.add(
        OctroiModel(
            ressource_id=ressource.id,
            groupe_id=groupe.id,
            mode=Octroi.modifier(ECRITURE).mode,
            matrice=ECRITURE.vers_jsonb(),
        )
    )
    session.flush()
    cle = fake.creer_ressource(DescripteurRessource(type="folder", chemin="/", nom="zz"))
    session.add(
        RessourceMapping(ressource_id=ressource.id, adaptateur="nextcloud", cle_externe=cle)
    )
    session.flush()
    return ressource.id, cle


def test_balayage_resilient(db_session: Session) -> None:
    fake = _FakeResilient()
    # 3 ressources partageant le même fake (octroi gX -> ÉCRITURE chacune).
    r0 = _creer_dans(db_session, fake, "g0")
    r1 = _creer_dans(db_session, fake, "g1")
    r2 = _creer_dans(db_session, fake, "g2")
    par_id = {rid: (rid, cle) for rid, cle in (r0, r1, r2)}

    # Ordre du balayage = tri par ressource_id. On place la CASSÉE en premier.
    ordonnes = sorted(par_id)  # ressource_ids triés
    cassee_id, cassee_cle = par_id[ordonnes[0]]
    reparable_id, reparable_cle = par_id[ordonnes[1]]
    conforme_id, conforme_cle = par_id[ordonnes[2]]

    fake.cles_cassees.add(cassee_cle)  # la 1re traitée lève -> erreur
    # conforme : on seed le réel == désiré (le groupe de cette ressource = g{index}).
    # On retrouve le groupe via l'octroi : chaque ressource a un seul groupe ÉCRITURE.
    conforme_groupe = db_session.execute(
        select(Groupe.cle)
        .join(OctroiModel, OctroiModel.groupe_id == Groupe.id)
        .where(OctroiModel.ressource_id == conforme_id)
    ).scalar_one()
    fake.appliquer_droits(conforme_cle, {DroitGroupe(conforme_groupe, ECRITURE)})
    # reparable : réel laissé vide -> son groupe est manquant -> reparee.

    db_session.commit()  # en prod les octrois préexistent ; on fige le setup avant le balayage

    rapport = reconcilier_tout(db_session, fake)

    statuts = {r.ressource_id: r.statut for r in rapport.resultats}
    assert statuts[cassee_id] == "erreur"
    assert statuts[reparable_id] == "reparee"
    assert statuts[conforme_id] == "conforme"
    # Agrégats justes.
    assert rapport.total == 3
    assert rapport.nb_conformes == 1
    assert rapport.nb_reparees == 1
    assert rapport.nb_erreurs == 1
    # Le message d'erreur est classifié.
    err = next(r for r in rapport.resultats if r.statut == "erreur")
    assert err.erreur is not None and "RuntimeError" in err.erreur
    # CŒUR DU TEST : la réparable est RÉELLEMENT réparée MALGRÉ l'erreur précoce.
    reparable_groupe = db_session.execute(
        select(Groupe.cle)
        .join(OctroiModel, OctroiModel.groupe_id == Groupe.id)
        .where(OctroiModel.ressource_id == reparable_id)
    ).scalar_one()
    assert fake.lire_droits_effectifs(reparable_cle) == frozenset(
        {DroitGroupe(reparable_groupe, ECRITURE)}
    )


def test_balayage_tout_conforme(db_session: Session) -> None:
    fake = AdaptateurMemoire()
    for i in range(3):
        rid, cle = _creer_dans(db_session, fake, f"gc{i}")
        groupe = db_session.execute(
            select(Groupe.cle)
            .join(OctroiModel, OctroiModel.groupe_id == Groupe.id)
            .where(OctroiModel.ressource_id == rid)
        ).scalar_one()
        fake.appliquer_droits(cle, {DroitGroupe(groupe, ECRITURE)})  # réel == désiré

    rapport = reconcilier_tout(db_session, fake)

    assert rapport.total == 3
    assert rapport.nb_conformes == 3
    assert rapport.nb_reparees == 0
    assert rapport.nb_erreurs == 0


def test_balayage_vide(db_session: Session) -> None:
    rapport = reconcilier_tout(db_session, AdaptateurMemoire())
    assert rapport.total == 0
    assert rapport.resultats == ()
    assert rapport.nb_erreurs == 0


def test_commit_par_ressource(db_session: Session) -> None:
    # 2 ressources OK (conformes) + 1 cassée -> 2 commits, 1 rollback.
    fake = _FakeResilient()
    ok = []
    for i in range(2):
        rid, cle = _creer_dans(db_session, fake, f"gok{i}")
        groupe = db_session.execute(
            select(Groupe.cle)
            .join(OctroiModel, OctroiModel.groupe_id == Groupe.id)
            .where(OctroiModel.ressource_id == rid)
        ).scalar_one()
        fake.appliquer_droits(cle, {DroitGroupe(groupe, ECRITURE)})
        ok.append(rid)
    _, cle_cassee = _creer_dans(db_session, fake, "gko")
    fake.cles_cassees.add(cle_cassee)
    db_session.commit()  # fige le setup avant de compter les commits du balayage

    with (
        patch.object(db_session, "commit", wraps=db_session.commit) as spy_commit,
        patch.object(db_session, "rollback", wraps=db_session.rollback) as spy_rollback,
    ):
        rapport = reconcilier_tout(db_session, fake)

    assert spy_commit.call_count == 2  # 1 par ressource non-erreur
    assert spy_rollback.call_count == 1  # la cassée
    assert rapport.nb_conformes == 2
    assert rapport.nb_erreurs == 1
