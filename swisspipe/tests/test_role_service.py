"""Tests du service de rôles (application/role_service.py) sur Postgres de test.

Désigner un titulaire POSE des Octrois L1 concrets sur un groupe PERSONNEL (curseur
imposée, §5.4), tracés dans journal_acces (le journal des DROITS). Retirer révoque.
Les droits sont FIGÉS (INV-3), la cible est toujours un groupe personnel (INV-4).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.role_service import (
    designer_titulaire_role,
    enregistrer_role,
    retirer_titulaire,
)
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    DossierImpose,
    Modele,
)
from swisspipe.persistence.models import (
    Groupe,
    JournalAcces,
    JournalEvenement,
    Ressource,
    RoleAffectation,
    TypeGroupe,
)
from swisspipe.persistence.models import Octroi as OctroiModel

ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
LECTURE = Matrice(NiveauPrincipal.LECTURE)
T0 = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def _modele_immobilier() -> Modele:
    return Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
            ),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable", "employe"),
        matrice_par_role={"responsable": {"plans": ECRITURE, "correspondance": LECTURE}},
    )


def _setup(
    session: Session, *, groupe_type: TypeGroupe = TypeGroupe.PERSONNEL, cle: str = "perso:marie"
):
    modele = _modele_immobilier()
    modele_id = enregistrer_modele(session, modele)
    inst = instancier_modele(
        session, modele, modele_id=modele_id, nom="Projet XY", metadonnees={}, acteur="rh"
    )
    role_id = enregistrer_role(
        session, modele_id=modele_id, cle="responsable", libelle="Responsable"
    )
    groupe = Groupe(type=groupe_type, cle=cle)
    session.add(groupe)
    session.flush()
    return inst, role_id, groupe.id


def _octrois(session: Session, groupe_id) -> dict[str, dict]:
    """{chemin ressource → matrice jsonb} des octrois posés sur `groupe_id`."""
    rows = session.execute(
        select(Ressource.chemin, OctroiModel.matrice)
        .join(OctroiModel, OctroiModel.ressource_id == Ressource.id)
        .where(OctroiModel.groupe_id == groupe_id)
    ).all()
    return {chemin: matrice for chemin, matrice in rows}


# ---------------------------------------------------------------------------
# §3 — Pose des droits (curseur imposée)
# ---------------------------------------------------------------------------


def test_designer_pose_octrois_concrets(db_session: Session) -> None:
    inst, role_id, groupe_id = _setup(db_session)

    designer_titulaire_role(
        db_session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=groupe_id,
        acteur="admin",
        effectif_depuis=T0,
    )

    octrois = _octrois(db_session, groupe_id)
    assert octrois == {
        "/Plans": ECRITURE.vers_jsonb(),
        "/Correspondance": LECTURE.vers_jsonb(),
    }


def test_inv4_cible_doit_etre_groupe_personnel(db_session: Session) -> None:
    # INV-4 : jamais un compte/personne en direct — et pas un groupe organisationnel ici.
    inst, role_id, groupe_orga = _setup(
        db_session, groupe_type=TypeGroupe.ORGANISATIONNEL, cle="orga:equipe"
    )
    with pytest.raises(ValueError, match="personnel"):
        designer_titulaire_role(
            db_session,
            instance_espace_id=inst.espace_id,
            role_id=role_id,
            groupe_perso_id=groupe_orga,
            acteur="admin",
            effectif_depuis=T0,
        )
    # Rien posé.
    assert db_session.scalar(select(func.count()).select_from(OctroiModel)) == 0
    assert db_session.scalar(select(func.count()).select_from(RoleAffectation)) == 0


# ---------------------------------------------------------------------------
# §4 — Journal des DROITS (journal_acces), pas journal_evenements
# ---------------------------------------------------------------------------


def test_designer_trace_journal_acces_pas_evenements(db_session: Session) -> None:
    inst, role_id, groupe_id = _setup(db_session)
    ev_avant = db_session.scalar(select(func.count()).select_from(JournalEvenement))

    designer_titulaire_role(
        db_session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=groupe_id,
        acteur="admin",
        effectif_depuis=T0,
    )

    lignes = db_session.scalars(
        select(JournalAcces).where(JournalAcces.groupe_id == groupe_id)
    ).all()
    assert len(lignes) == 2  # /Plans + /Correspondance
    assert {ligne.action.value for ligne in lignes} == {"octroi"}
    assert all(ligne.cause["role_id"] == str(role_id) for ligne in lignes)
    assert all(ligne.cause["espace_id"] == str(inst.espace_id) for ligne in lignes)
    assert all(ligne.cause["source"] == "humain" for ligne in lignes)
    # RIEN dans journal_evenements (une pose de droit n'est pas un événement de cycle de vie).
    ev_apres = db_session.scalar(select(func.count()).select_from(JournalEvenement))
    assert ev_apres == ev_avant


def test_retirer_revoque_octrois_et_trace_revocation(db_session: Session) -> None:
    inst, role_id, groupe_id = _setup(db_session)
    aff = designer_titulaire_role(
        db_session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=groupe_id,
        acteur="admin",
        effectif_depuis=T0,
    )
    assert _octrois(db_session, groupe_id)  # posés

    retirer_titulaire(db_session, aff.affectation_id, acteur="admin")

    assert _octrois(db_session, groupe_id) == {}  # révoqués
    revocations = db_session.scalars(
        select(JournalAcces).where(
            JournalAcces.groupe_id == groupe_id, JournalAcces.action == "revocation"
        )
    ).all()
    assert len(revocations) == 2
    aff_row = db_session.get(RoleAffectation, aff.affectation_id)
    assert aff_row.retire_at is not None  # retrait tracé, affectation conservée


# ---------------------------------------------------------------------------
# §2/§3 — INV-3 : figé à l'instant déclaré + re-désignation = nouvelle affectation
# ---------------------------------------------------------------------------


def test_inv3_effectif_depuis_fige_et_octroi_non_reevalue(db_session: Session) -> None:
    inst, role_id, groupe_id = _setup(db_session)
    aff = designer_titulaire_role(
        db_session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=groupe_id,
        acteur="admin",
        effectif_depuis=T0,
    )
    aff_row = db_session.get(RoleAffectation, aff.affectation_id)
    assert aff_row.effectif_depuis == T0  # figé à l'instant DÉCLARÉ (INV-3)
    # L'octroi posé est celui figé, relu identique (aucune ré-évaluation « live »).
    assert _octrois(db_session, groupe_id)["/Plans"] == ECRITURE.vers_jsonb()


def test_re_designation_cree_nouvelle_affectation(db_session: Session) -> None:
    inst, role_id, groupe_marie = _setup(db_session)
    designer_titulaire_role(
        db_session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=groupe_marie,
        acteur="admin",
        effectif_depuis=T0,
    )
    autre = Groupe(type=TypeGroupe.PERSONNEL, cle="perso:jean")
    db_session.add(autre)
    db_session.flush()
    designer_titulaire_role(
        db_session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=autre.id,
        acteur="admin",
        effectif_depuis=datetime(2026, 8, 1, tzinfo=UTC),
    )
    # 2 affectations distinctes (l'ancienne n'est pas « recalculée »).
    assert db_session.scalar(select(func.count()).select_from(RoleAffectation)) == 2
