"""Projection des transverses — LE PLAN (mode ombre, HERMÉTIQUE). Aucun serveur touché.

On calcule, depuis le core DB LOCAL, le PLAN de projection d'un transverse monté : la
structure (Group Folder par ressource au point de montage) + les permissions (matrice
EFFECTIVE par groupe, bornée par le plafond + limitée à la portée — étape 4). On AFFICHE
les commandes occ qui SERAIENT exécutées ; AUCUNE ne l'est. Réutilise la traduction L1.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.nextcloud import occ_runner, sql_runner
from swisspipe.adapters.outbound.nextcloud.traduction import matrice_vers_verbes_acl
from swisspipe.application.delegation_service import attribuer_droit_delegue
from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import monter_instance
from swisspipe.application.projection_service import planifier_projection_transverse
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    DossierImpose,
    Modele,
    PolitiqueDroits,
)
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    JournalAcces,
    NatureEspace,
    Ressource,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.persistence.models import Octroi as OctroiModel

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _modele(politique: PolitiqueDroits, matrice_par_role: dict | None = None) -> Modele:
    return Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
                DossierImpose(cle="divers", libelle="Divers"),
            ),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        matrice_par_role=matrice_par_role or {},
        politique_droits=politique,
    )


def _host(session: Session, sig: str):
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL, combinaison_signature=signature_combinaison([(sig, sig)])
    )
    session.add(espace)
    session.flush()
    return espace.id


def _ressource_id(session: Session, espace_id, chemin: str):
    return session.scalar(
        select(Ressource.id).where(Ressource.espace_id == espace_id, Ressource.chemin == chemin)
    )


def _perms(plan, point: str, groupe_id: str):
    """Verbes ACL posés pour `groupe_id` au `point`, ou None."""
    for c in plan.commandes:
        if c[:2] == ("groupfolders:permissions", point) and "-g" in c:
            if c[c.index("-g") + 1] == groupe_id and "--" in c:
                return list(c[c.index("--") + 1 :])
    return None


def _points_crees(plan) -> set[str]:
    return {c[1] for c in plan.commandes if c[0] == "groupfolders:create"}


def _setup_imposee(session: Session):
    """Instance imposée : rôle Responsable pose ÉCRITURE sur /Plans (groupe perso marie).
    Montage plafond LECTURE, portée {/Plans, /Correspondance} (PAS /Divers)."""
    modele = _modele(PolitiqueDroits.IMPOSEE, {"responsable": {"plans": ECRITURE}})
    modele_id = enregistrer_modele(session, modele)
    inst = instancier_modele(
        session, modele, modele_id=modele_id, nom="XY", metadonnees={}, acteur="rh"
    )
    role_id = enregistrer_role(session, modele_id=modele_id, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle="perso:marie")
    session.add(perso)
    session.flush()
    designer_titulaire_role(
        session,
        instance_espace_id=inst.espace_id,
        role_id=role_id,
        groupe_perso_id=perso.id,
        acteur="admin",
        effectif_depuis=T0,
    )
    hote = _host(session, "rh")
    montage = monter_instance(
        session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Plans", "/Correspondance"},
        matrice_plafond=LECTURE,
        consenti_par="admin",
        acteur="admin",
    )
    return inst, montage.montage_id, str(perso.id)


# ---------------------------------------------------------------------------
# §1 — plan d'un transverse imposé
# ---------------------------------------------------------------------------


def test_plan_imposee_structure(db_session: Session) -> None:
    _inst, montage_id, _perso = _setup_imposee(db_session)
    plan = planifier_projection_transverse(db_session, montage_id)
    # Structure : un GF par ressource DANS la portée, au point de montage /RH.
    assert _points_crees(plan) == {"/RH/Plans", "/RH/Correspondance"}


def test_plafond_respecte_dans_le_plan(db_session: Session) -> None:
    # Octroi sous-jacent ÉCRITURE, plafond LECTURE -> le plan pose LECTURE, PAS ÉCRITURE.
    _inst, montage_id, perso = _setup_imposee(db_session)
    plan = planifier_projection_transverse(db_session, montage_id)
    verbes = _perms(plan, "/RH/Plans", perso)
    assert verbes == matrice_vers_verbes_acl(LECTURE)
    assert "+write" not in verbes  # anti-escalade visible dans le plan


def test_portee_respectee_divers_absent(db_session: Session) -> None:
    _inst, montage_id, _perso = _setup_imposee(db_session)
    plan = planifier_projection_transverse(db_session, montage_id)
    # /Divers est hors portée -> ABSENT du plan.
    assert "/RH/Divers" not in _points_crees(plan)
    assert all("Divers" not in arg for cmd in plan.commandes for arg in cmd)


# ---------------------------------------------------------------------------
# §1 — plan d'un transverse délégué
# ---------------------------------------------------------------------------


def test_plan_deleguee(db_session: Session) -> None:
    modele = _modele(PolitiqueDroits.DELEGUEE)
    modele_id = enregistrer_modele(db_session, modele)
    inst = instancier_modele(
        db_session, modele, modele_id=modele_id, nom="XY", metadonnees={}, acteur="admin"
    )
    plans = _ressource_id(db_session, inst.espace_id, "/Plans")
    orga = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle="orga:equipe")
    db_session.add(orga)
    db_session.flush()
    attribuer_droit_delegue(
        db_session,
        instance_espace_id=inst.espace_id,
        groupe_id=orga.id,
        ressource_id=plans,
        matrice=LECTURE,
        plafond=ECRITURE,
        acteur="admin",
    )
    hote = _host(db_session, "eq")
    montage = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/Equipe",
        portee_chemins={"/Plans"},
        matrice_plafond=ECRITURE,
        consenti_par="admin",
        acteur="admin",
    )
    plan = planifier_projection_transverse(db_session, montage.montage_id)
    assert _perms(plan, "/Equipe/Plans", str(orga.id)) == matrice_vers_verbes_acl(LECTURE)


# ---------------------------------------------------------------------------
# §2 — commande PLAN = ZÉRO contact serveur, ZÉRO mutation
# ---------------------------------------------------------------------------


def test_plan_zero_contact_serveur_zero_mutation(db_session: Session, monkeypatch) -> None:
    _inst, montage_id, _perso = _setup_imposee(db_session)
    octrois_avant = db_session.scalar(select(func.count()).select_from(OctroiModel))
    journal_avant = db_session.scalar(select(func.count()).select_from(JournalAcces))

    # Garde-fou : toute exécution SSH/occ lève -> prouve qu'AUCUNE n'a lieu.
    def _boom(*a, **k):
        raise AssertionError("CONTACT SERVEUR INTERDIT (mode ombre)")

    monkeypatch.setattr(occ_runner.subprocess, "run", _boom)
    monkeypatch.setattr(sql_runner.subprocess, "run", _boom)

    plan = planifier_projection_transverse(db_session, montage_id)  # ne doit PAS lever

    assert len(plan.commandes) > 0
    # ZÉRO mutation du core DB.
    assert db_session.scalar(select(func.count()).select_from(OctroiModel)) == octrois_avant
    assert db_session.scalar(select(func.count()).select_from(JournalAcces)) == journal_avant


# ---------------------------------------------------------------------------
# §3 — non-régression : le plan RÉUTILISE la traduction L1 (pas de réécriture)
# ---------------------------------------------------------------------------


def test_plan_reutilise_traduction_l1(db_session: Session) -> None:
    _inst, montage_id, perso = _setup_imposee(db_session)
    plan = planifier_projection_transverse(db_session, montage_id)
    # Les verbes du plan proviennent EXACTEMENT de matrice_vers_verbes_acl (L1), pas d'un
    # mapping réécrit dans la couche projection.
    assert _perms(plan, "/RH/Plans", perso) == matrice_vers_verbes_acl(LECTURE)
