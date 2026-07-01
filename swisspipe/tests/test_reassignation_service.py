"""Tests du service de réassignation (application/reassignation_service.py) — §10.3.

Transmet SÉLECTIVEMENT des octrois d'un groupe personnel A vers un groupe personnel B :
uniquement la sélection donnée (garde-fou « remplaçant ≠ clone »). Jamais les appartenances
organisationnelles. Tracé dans journal_acces (cause={reassignation, source_groupe:A}).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swisspipe.application.reassignation_service import (
    GroupePersonnelRequisError,
    transmettre_octrois,
)
from swisspipe.core.domain.matrice import Matrice, Mode, NiveauPrincipal
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    GroupeMembre,
    JournalAcces,
    NatureEspace,
    Ressource,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.persistence.models import Octroi as OctroiModel

ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
LECTURE = Matrice(NiveauPrincipal.LECTURE)


def _setup(session: Session):
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL, combinaison_signature=signature_combinaison([("t", "t")])
    )
    session.add(espace)
    session.flush()
    r1 = Ressource(type="folder", espace_id=espace.id, chemin="/R1")
    r2 = Ressource(type="folder", espace_id=espace.id, chemin="/R2")
    session.add_all([r1, r2])
    session.flush()
    a = Groupe(type=TypeGroupe.PERSONNEL, cle="perso:alice")
    b = Groupe(type=TypeGroupe.PERSONNEL, cle="perso:bob")
    orga = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle="orga:team")
    session.add_all([a, b, orga])
    session.flush()
    # A détient 2 octrois (r1: ÉCRITURE, r2: LECTURE).
    session.add_all(
        [
            OctroiModel(
                ressource_id=r1.id,
                groupe_id=a.id,
                mode=Mode.MODIFIER,
                matrice=ECRITURE.vers_jsonb(),
            ),
            OctroiModel(
                ressource_id=r2.id, groupe_id=a.id, mode=Mode.MODIFIER, matrice=LECTURE.vers_jsonb()
            ),
        ]
    )
    # Appartenance orga d'alice (ne doit PAS bouger).
    session.add(GroupeMembre(groupe_id=orga.id, compte_id="alice"))
    session.flush()
    return r1.id, r2.id, a.id, b.id, orga.id


def _octroi_b(session: Session, ressource_id, b_id):
    return session.scalar(
        select(OctroiModel.matrice).where(
            OctroiModel.ressource_id == ressource_id, OctroiModel.groupe_id == b_id
        )
    )


# ---------------------------------------------------------------------------
# §2.1 — Transmission SÉLECTIVE (anti role-creep)
# ---------------------------------------------------------------------------


def test_transmet_uniquement_la_selection(db_session: Session) -> None:
    r1, r2, a, b, _orga = _setup(db_session)
    transmettre_octrois(
        db_session, groupe_source_id=a, groupe_cible_id=b, ressource_ids=[r1], acteur="admin"
    )
    # r1 transmis à B...
    assert _octroi_b(db_session, r1, b) == ECRITURE.vers_jsonb()
    # ...r2 NON sélectionné -> NON transmis (garde-fou remplaçant ≠ clone).
    assert _octroi_b(db_session, r2, b) is None


def test_selection_vide_ne_transmet_rien(db_session: Session) -> None:
    r1, r2, a, b, _orga = _setup(db_session)
    transmettre_octrois(
        db_session, groupe_source_id=a, groupe_cible_id=b, ressource_ids=[], acteur="admin"
    )
    assert _octroi_b(db_session, r1, b) is None
    assert _octroi_b(db_session, r2, b) is None


def test_source_non_personnelle_refuse(db_session: Session) -> None:
    r1, _r2, _a, b, orga = _setup(db_session)
    with pytest.raises(GroupePersonnelRequisError):
        transmettre_octrois(
            db_session, groupe_source_id=orga, groupe_cible_id=b, ressource_ids=[r1], acteur="admin"
        )


def test_cible_non_personnelle_refuse(db_session: Session) -> None:
    r1, _r2, a, _b, orga = _setup(db_session)
    with pytest.raises(GroupePersonnelRequisError):
        transmettre_octrois(
            db_session, groupe_source_id=a, groupe_cible_id=orga, ressource_ids=[r1], acteur="admin"
        )


def test_appartenances_orga_inchangees(db_session: Session) -> None:
    r1, _r2, a, b, _orga = _setup(db_session)
    membres_avant = db_session.scalar(select(func.count()).select_from(GroupeMembre))
    transmettre_octrois(
        db_session, groupe_source_id=a, groupe_cible_id=b, ressource_ids=[r1], acteur="admin"
    )
    # La transmission ne touche QUE les octrois — aucune appartenance modifiée.
    assert db_session.scalar(select(func.count()).select_from(GroupeMembre)) == membres_avant


# ---------------------------------------------------------------------------
# §2.2 — Journal des DROITS
# ---------------------------------------------------------------------------


def test_journal_acces_reference_source(db_session: Session) -> None:
    r1, _r2, a, b, _orga = _setup(db_session)
    transmettre_octrois(
        db_session, groupe_source_id=a, groupe_cible_id=b, ressource_ids=[r1], acteur="admin"
    )
    lignes = db_session.scalars(select(JournalAcces).where(JournalAcces.groupe_id == b)).all()
    assert len(lignes) == 1
    assert lignes[0].action.value == "octroi"
    assert lignes[0].cause["type"] == "reassignation"
    assert lignes[0].cause["source_groupe"] == str(a)
    assert lignes[0].matrice_apres == ECRITURE.vers_jsonb()
