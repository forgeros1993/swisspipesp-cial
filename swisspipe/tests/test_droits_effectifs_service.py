"""Droits effectifs montage-aware, bout-en-bout sur Postgres (application/droits_effectifs_service).

Chaîne RÉELLE : un octroi ÉCRITURE posé par rôle (étape 3) sur un groupe personnel, vu à
travers un montage dont le plafond est LECTURE (étape 2), donne un droit effectif LECTURE
(anti-escalade §9.3). Sans montage, l'espace dimensionnel non monté reste ÉCRITURE (non-régr).
On CALCULE seulement — aucune projection Nextcloud.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.application.droits_effectifs_service import droit_effectif_montre
from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import monter_instance
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele
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


def _modele() -> Modele:
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
        roles=("responsable",),
        matrice_par_role={"responsable": {"plans": ECRITURE}},
    )


def _ressource_id(session: Session, espace_id, chemin: str):
    return session.scalar(
        select(Ressource.id).where(Ressource.espace_id == espace_id, Ressource.chemin == chemin)
    )


def _host(session: Session, sig: str):
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL, combinaison_signature=signature_combinaison([(sig, sig)])
    )
    session.add(espace)
    session.flush()
    return espace.id


def _setup(session: Session):
    """Instance + rôle Responsable posé (ÉCRITURE sur /Plans) pour le compte « marie »."""
    modele = _modele()
    modele_id = enregistrer_modele(session, modele)
    inst = instancier_modele(
        session, modele, modele_id=modele_id, nom="XY", metadonnees={}, acteur="rh"
    )
    role_id = enregistrer_role(session, modele_id=modele_id, cle="responsable", libelle="Resp")
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
    return inst


# ---------------------------------------------------------------------------
# §3 — Anti-escalade bout-en-bout
# ---------------------------------------------------------------------------


def test_ecriture_posee_par_role_plafonnee_a_lecture_via_montage(db_session: Session) -> None:
    inst = _setup(db_session)
    plans = _ressource_id(db_session, inst.espace_id, "/Plans")
    hote = _host(db_session, "rh")
    montage = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Plans"},
        matrice_plafond=LECTURE,  # plafond LECTURE < octroi ÉCRITURE
        consenti_par="admin_rh",
        acteur="admin_rh",
    )

    res = droit_effectif_montre(
        db_session, compte_id="marie", ressource_id=plans, montage_id=montage.montage_id
    )
    assert res.matrice == LECTURE  # plafonné, PAS ÉCRITURE


# ---------------------------------------------------------------------------
# §4 — Non-régression : sans montage, l'espace non monté reste ÉCRITURE
# ---------------------------------------------------------------------------


def test_sans_montage_reste_ecriture(db_session: Session) -> None:
    inst = _setup(db_session)
    plans = _ressource_id(db_session, inst.espace_id, "/Plans")
    res = droit_effectif_montre(db_session, compte_id="marie", ressource_id=plans, montage_id=None)
    assert res.matrice == ECRITURE  # identique au calcul L1 (T3 préservé)


# ---------------------------------------------------------------------------
# §2 — Portée : ressource hors portée invisible via le montage
# ---------------------------------------------------------------------------


def test_hors_portee_invisible(db_session: Session) -> None:
    inst = _setup(db_session)
    plans = _ressource_id(db_session, inst.espace_id, "/Plans")
    hote = _host(db_session, "rh")
    # Montage n'exposant QUE /Correspondance -> /Plans invisible via ce montage.
    montage = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Correspondance"},
        matrice_plafond=ECRITURE,
        consenti_par="admin_rh",
        acteur="admin_rh",
    )
    res = droit_effectif_montre(
        db_session, compte_id="marie", ressource_id=plans, montage_id=montage.montage_id
    )
    assert res.matrice is None
    assert not res.accessible


def test_dans_portee_visible(db_session: Session) -> None:
    inst = _setup(db_session)
    plans = _ressource_id(db_session, inst.espace_id, "/Plans")
    hote = _host(db_session, "rh")
    montage = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Plans"},
        matrice_plafond=ECRITURE,
        consenti_par="admin_rh",
        acteur="admin_rh",
    )
    res = droit_effectif_montre(
        db_session, compte_id="marie", ressource_id=plans, montage_id=montage.montage_id
    )
    assert res.accessible
    assert res.matrice == ECRITURE  # plafond ÉCRITURE >= octroi ÉCRITURE -> inchangé
