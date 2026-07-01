"""Plafond PAR RESSOURCE bout-en-bout (§4.4) — le différentiel dans UN SEUL montage.

Même montage, plafond {Plans: ÉCRITURE, Correspondance: LECTURE} : Plans garde ÉCRITURE,
Correspondance est plafonnée ÉCRITURE→LECTURE. Vérifié au calcul effectif (étape 4) ET
dans le plan occ (étape 6A). Postgres local, aucun serveur.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.nextcloud.traduction import matrice_vers_verbes_acl
from swisspipe.application.droits_effectifs_service import droit_effectif_montre
from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import monter_instance
from swisspipe.application.projection_service import planifier_projection_transverse
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele, PolitiqueDroits
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    GroupeMembre,
    NatureEspace,
    Ressource,
    TypeGroupe,
    signature_combinaison,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _rid(session: Session, espace_id, chemin: str):
    return session.scalar(
        select(Ressource.id).where(Ressource.espace_id == espace_id, Ressource.chemin == chemin)
    )


def _setup(session: Session):
    modele = Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
            ),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        matrice_par_role={"responsable": {"plans": ECRITURE, "correspondance": ECRITURE}},
        politique_droits=PolitiqueDroits.IMPOSEE,
    )
    mid = enregistrer_modele(session, modele)
    inst = instancier_modele(session, modele, modele_id=mid, nom="XY", metadonnees={}, acteur="rh")
    role_id = enregistrer_role(session, modele_id=mid, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle="perso:marie")
    session.add(perso)
    session.flush()
    session.add(GroupeMembre(groupe_id=perso.id, compte_id="marie"))
    session.flush()
    designer_titulaire_role(
        session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=perso.id,
        acteur="admin",
        effectif_depuis=T0,
    )
    host = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("rh", "rh")]),
    )
    session.add(host)
    session.flush()
    # PLAFOND PAR RESSOURCE : Plans ÉCRITURE (gardé), Correspondance LECTURE (plafonnée).
    montage = monter_instance(
        session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=host.id,
        chemin_hote="/RH",
        portee_chemins={"/Plans", "/Correspondance"},
        matrice_plafond={"/Plans": ECRITURE, "/Correspondance": LECTURE},
        consenti_par="admin",
        acteur="admin",
    )
    return inst, montage.montage_id


# ---------------------------------------------------------------------------
# §2 — calcul effectif : différentiel dans le MÊME montage
# ---------------------------------------------------------------------------


def test_calcul_plans_ecriture_correspondance_lecture(db_session: Session) -> None:
    inst, montage_id = _setup(db_session)
    plans = _rid(db_session, inst.espace_id, "/Plans")
    corr = _rid(db_session, inst.espace_id, "/Correspondance")

    r_plans = droit_effectif_montre(
        db_session, compte_id="marie", ressource_id=plans, montage_id=montage_id
    )
    r_corr = droit_effectif_montre(
        db_session, compte_id="marie", ressource_id=corr, montage_id=montage_id
    )

    assert r_plans.matrice == ECRITURE  # plafond ÉCRITURE -> gardé
    assert r_corr.matrice == LECTURE  # plafond LECTURE -> plafonné (octroi ÉCRITURE capé)


# ---------------------------------------------------------------------------
# §3 — plan occ : le différentiel visible dans les verbes
# ---------------------------------------------------------------------------


def test_plan_reflete_le_differentiel(db_session: Session) -> None:
    _inst, montage_id = _setup(db_session)
    plan = planifier_projection_transverse(db_session, montage_id)

    def verbes(point: str) -> list[str] | None:
        for c in plan.commandes:
            if c[:2] == ("groupfolders:permissions", point) and "--" in c:
                return list(c[c.index("--") + 1 :])
        return None

    assert verbes("/RH/Plans") == matrice_vers_verbes_acl(ECRITURE)  # +write
    assert verbes("/RH/Correspondance") == matrice_vers_verbes_acl(LECTURE)  # -write
    assert "+write" in verbes("/RH/Plans")
    assert "+write" not in verbes("/RH/Correspondance")
