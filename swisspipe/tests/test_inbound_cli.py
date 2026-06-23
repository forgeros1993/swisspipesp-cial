"""Tests de l'inbound CLI/cron contre Postgres + adaptateur fake (hermétique côté serveur).

Prouve : dry-run/shadow n'écrit RIEN ; --apply corrige + journalise (estampillé) ; advisory
lock cron sérialise les runs concurrents. Gaté par DATABASE_URL_TEST (skip sinon, via db_session).
Adaptateur = fake mémoire (aucun appel serveur Nextcloud).
"""

from __future__ import annotations

import argparse
import uuid

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from swisspipe.adapters.inbound.cli import (
    _CRON_LOCK_KEY,
    EXIT_DIVERGENCE,
    EXIT_OK,
    _cmd_cron,
    _cmd_reconcilier,
    _cmd_verifier,
)
from swisspipe.adapters.outbound.fake.adaptateur_memoire import AdaptateurMemoire
from swisspipe.core.domain.acteurs import TypeGroupe
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
    signature_combinaison,
)
from swisspipe.persistence.models import Octroi as OctroiModel

ECRITURE = Matrice(NiveauPrincipal.ECRITURE)


def _seed(session: Session, fake: AdaptateurMemoire) -> tuple[uuid.UUID, str, str]:
    """Crée un cœur minimal (espace/ressource/groupe/octroi/mapping) + la ressource fake."""
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("t", uuid.uuid4().hex)]),
    )
    session.add(espace)
    session.flush()
    ressource = Ressource(type="folder", espace_id=espace.id, chemin="/")
    session.add(ressource)
    session.flush()
    grp = f"grp_{uuid.uuid4().hex[:8]}"
    session.add(Groupe(type=TypeGroupe.ORGANISATIONNEL, cle=grp))
    cle = fake.creer_ressource(DescripteurRessource(type="folder", chemin="/", nom="zz"))
    session.add(
        RessourceMapping(ressource_id=ressource.id, adaptateur="nextcloud", cle_externe=cle)
    )
    session.add(
        OctroiModel(
            ressource_id=ressource.id,
            groupe_id=session.scalar(select(Groupe.id).where(Groupe.cle == grp)),
            mode=Octroi.modifier(ECRITURE).mode,
            matrice=ECRITURE.vers_jsonb(),
        )
    )
    session.flush()
    return ressource.id, grp, cle


def _args(**kw: object) -> argparse.Namespace:
    base: dict[str, object] = {"ressource": None, "apply": False, "acteur": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_verifier_dry_run_detecte_sans_ecrire(db_session: Session) -> None:
    """verifier (DRY-RUN) : divergence détectée (octroi sans application fake) ; rien écrit."""
    fake = AdaptateurMemoire()
    _rid, _grp, cle = _seed(db_session, fake)  # fake n'a AUCUN droit appliqué -> divergence

    code = _cmd_verifier(db_session, fake, _args())

    assert code == EXIT_DIVERGENCE
    assert fake.lire_droits_effectifs(cle) == frozenset()  # DRY-RUN : rien appliqué


def test_verifier_conforme(db_session: Session) -> None:
    """verifier : pas de divergence quand le fake reflète déjà l'état désiré -> EXIT_OK."""
    fake = AdaptateurMemoire()
    _rid, grp, cle = _seed(db_session, fake)
    fake.appliquer_droits(cle, {DroitGroupe(grp, ECRITURE)})  # réel == désiré

    assert _cmd_verifier(db_session, fake, _args()) == EXIT_OK


def test_reconcilier_sans_apply_ne_touche_rien(db_session: Session) -> None:
    """reconcilier sans --apply = DRY-RUN : divergence montrée, fake inchangé."""
    fake = AdaptateurMemoire()
    _rid, _grp, cle = _seed(db_session, fake)

    code = _cmd_reconcilier(db_session, fake, _args(apply=False))

    assert code == EXIT_DIVERGENCE
    assert fake.lire_droits_effectifs(cle) == frozenset()  # rien écrit


def test_reconcilier_apply_corrige_et_journalise(db_session: Session) -> None:
    """reconcilier --apply : applique l'état désiré + trace au journal (estampille cli)."""
    fake = AdaptateurMemoire()
    rid, grp, cle = _seed(db_session, fake)

    code = _cmd_reconcilier(db_session, fake, _args(apply=True, acteur="jof"))

    assert code == EXIT_OK
    assert DroitGroupe(grp, ECRITURE) in fake.lire_droits_effectifs(cle)  # appliqué
    entrees = db_session.scalars(
        select(JournalAcces).where(JournalAcces.ressource_id == rid)
    ).all()
    assert entrees and entrees[0].cause is not None  # mutation tracée
    assert entrees[0].cause["declencheur"] == "cli:jof"  # estampillage origine


def test_cron_shadow_ne_touche_rien(db_session: Session) -> None:
    """cron-run par défaut = SHADOW : détecte, n'écrit rien (la divergence persiste)."""
    fake = AdaptateurMemoire()
    _rid, _grp, cle = _seed(db_session, fake)

    code = _cmd_cron(db_session, fake, _args(apply=False))

    assert code == EXIT_DIVERGENCE
    assert fake.lire_droits_effectifs(cle) == frozenset()  # shadow : aucune écriture


def test_cron_apply_corrige(db_session: Session) -> None:
    """cron-run --apply : répare et journalise (declencheur=cron)."""
    fake = AdaptateurMemoire()
    rid, grp, cle = _seed(db_session, fake)

    assert _cmd_cron(db_session, fake, _args(apply=True)) == EXIT_OK
    assert DroitGroupe(grp, ECRITURE) in fake.lire_droits_effectifs(cle)
    entrees = db_session.scalars(
        select(JournalAcces).where(JournalAcces.ressource_id == rid)
    ).all()
    assert entrees and entrees[0].cause is not None
    assert entrees[0].cause["declencheur"] == "cron"


def test_cron_advisory_lock_skip_concurrent(db_session: Session, migrated_engine: Engine) -> None:
    """advisory lock : si un autre run tient le lock, cron-run s'abstient (skip), n'écrit rien."""
    fake = AdaptateurMemoire()
    _rid, _grp, cle = _seed(db_session, fake)

    autre = migrated_engine.connect()
    try:
        # Un "autre run" prend le lock sur une connexion distincte.
        pris = autre.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _CRON_LOCK_KEY}
        ).scalar()
        assert pris

        code = _cmd_cron(db_session, fake, _args(apply=True))  # devrait s'abstenir

        assert code == EXIT_OK  # skip bénin
        assert fake.lire_droits_effectifs(cle) == frozenset()  # n'a PAS tourné -> rien écrit
    finally:
        autre.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _CRON_LOCK_KEY})
        autre.close()
