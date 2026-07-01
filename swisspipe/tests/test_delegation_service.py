"""Tests du service de délégation (application/delegation_service.py) — curseur §5.4.

Sur une instance 'deleguee', un admin attribue un Octroi L1 à un groupe (perso OU orga,
INV-4), BORNÉ par un plafond : au-delà du plafond -> REJETÉ (rien posé). Tracé dans
journal_acces (le journal des DROITS), pas journal_evenements. Postgres local.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swisspipe.application.delegation_service import (
    DepassementPlafondError,
    PolitiqueNonDelegueeError,
    attribuer_droit_delegue,
)
from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    DossierImpose,
    Modele,
    PolitiqueDroits,
)
from swisspipe.persistence.models import (
    Groupe,
    JournalAcces,
    JournalEvenement,
    Ressource,
    TypeGroupe,
)
from swisspipe.persistence.models import Octroi as OctroiModel

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)


def _modele(politique: PolitiqueDroits) -> Modele:
    return Modele(
        id="collab",
        nom="Espace collaboratif",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(DossierImpose(cle="plans", libelle="Plans"),),
            dossiers_libres_autorises=False,
        ),
        roles=(),
        politique_droits=politique,
    )


def _setup(session: Session, politique: PolitiqueDroits = PolitiqueDroits.DELEGUEE):
    modele = _modele(politique)
    modele_id = enregistrer_modele(session, modele)
    inst = instancier_modele(
        session, modele, modele_id=modele_id, nom="XY", metadonnees={}, acteur="admin"
    )
    plans = session.scalar(
        select(Ressource.id).where(
            Ressource.espace_id == inst.espace_id, Ressource.chemin == "/Plans"
        )
    )
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle="perso:marie")
    orga = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle="orga:equipe")
    session.add_all([perso, orga])
    session.flush()
    return inst, plans, perso.id, orga.id


def _matrice_octroi(session: Session, ressource_id, groupe_id):
    return session.scalar(
        select(OctroiModel.matrice).where(
            OctroiModel.ressource_id == ressource_id, OctroiModel.groupe_id == groupe_id
        )
    )


# ---------------------------------------------------------------------------
# §1.2 — borné par le plafond
# ---------------------------------------------------------------------------


def test_droit_sous_le_plafond_pose(db_session: Session) -> None:
    inst, plans, perso, _orga = _setup(db_session)
    attribuer_droit_delegue(
        db_session,
        instance_espace_id=inst.espace_id,
        groupe_id=perso,
        ressource_id=plans,
        matrice=LECTURE,
        plafond=ECRITURE,  # LECTURE <= ÉCRITURE -> OK
        acteur="admin",
    )
    assert _matrice_octroi(db_session, plans, perso) == LECTURE.vers_jsonb()


def test_droit_au_dela_du_plafond_rejete(db_session: Session) -> None:
    inst, plans, perso, _orga = _setup(db_session)
    with pytest.raises(DepassementPlafondError):
        attribuer_droit_delegue(
            db_session,
            instance_espace_id=inst.espace_id,
            groupe_id=perso,
            ressource_id=plans,
            matrice=ECRITURE,
            plafond=LECTURE,  # ÉCRITURE > LECTURE -> REJET
            acteur="admin",
        )
    assert db_session.scalar(select(func.count()).select_from(OctroiModel)) == 0


def test_cible_organisationnelle_acceptee(db_session: Session) -> None:
    # La déléguée peut viser une ÉQUIPE (contrairement au rôle qui vise un perso).
    inst, plans, _perso, orga = _setup(db_session)
    attribuer_droit_delegue(
        db_session,
        instance_espace_id=inst.espace_id,
        groupe_id=orga,
        ressource_id=plans,
        matrice=LECTURE,
        plafond=ECRITURE,
        acteur="admin",
    )
    assert _matrice_octroi(db_session, plans, orga) == LECTURE.vers_jsonb()


def test_instance_imposee_refuse(db_session: Session) -> None:
    # Sur 'imposee', les droits viennent des rôles, pas de la main.
    inst, plans, perso, _orga = _setup(db_session, PolitiqueDroits.IMPOSEE)
    with pytest.raises(PolitiqueNonDelegueeError):
        attribuer_droit_delegue(
            db_session,
            instance_espace_id=inst.espace_id,
            groupe_id=perso,
            ressource_id=plans,
            matrice=LECTURE,
            plafond=ECRITURE,
            acteur="admin",
        )
    assert db_session.scalar(select(func.count()).select_from(OctroiModel)) == 0


# ---------------------------------------------------------------------------
# §1.3 — journal_acces (pas journal_evenements)
# ---------------------------------------------------------------------------


def test_trace_journal_acces_pas_evenements(db_session: Session) -> None:
    inst, plans, perso, _orga = _setup(db_session)
    ev_avant = db_session.scalar(select(func.count()).select_from(JournalEvenement))

    attribuer_droit_delegue(
        db_session,
        instance_espace_id=inst.espace_id,
        groupe_id=perso,
        ressource_id=plans,
        matrice=LECTURE,
        plafond=ECRITURE,
        acteur="admin",
    )

    lignes = db_session.scalars(select(JournalAcces).where(JournalAcces.groupe_id == perso)).all()
    assert len(lignes) == 1
    assert lignes[0].action.value == "octroi"
    assert lignes[0].cause["politique"] == "deleguee"
    assert lignes[0].matrice_apres == LECTURE.vers_jsonb()
    # journal_evenements inchangé (une attribution de droit n'est pas un événement de cycle de vie).
    assert db_session.scalar(select(func.count()).select_from(JournalEvenement)) == ev_avant
