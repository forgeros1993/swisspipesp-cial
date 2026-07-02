"""Démontage = ÉTAT DÉSIRÉ VIDE (§3) + plan de reconcile occ + correction groupe.cle.

Un montage archivé (archiver_montage, étape 2) projette un état désiré VIDE : via le
delta, ça donne un plan de RETRAIT PUR (clear ACL + retrait accès base) — JAMAIS de
destruction (INV-5 : pas de groupfolders:delete, pas de rm/mkdir). Réversible.
Correction au passage : les droits projetés sont clés par groupe.cle (le nom NC réel,
modèle L1), pas par l'UUID interne.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import planifier_reconcile_occ
from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import archiver_montage, monter_instance
from swisspipe.application.projection_service import etat_projete_transverse
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele, PolitiqueDroits
from swisspipe.core.domain.montage import EtatMontage
from swisspipe.core.services.delta_projection import calculer_delta
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    Montage,
    NatureEspace,
    TypeGroupe,
    signature_combinaison,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
T0 = datetime(2026, 7, 1, tzinfo=UTC)


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
    inst = instancier_modele(session, modele, modele_id=mid, nom="demo", metadonnees={}, acteur="a")
    rid = enregistrer_role(session, modele_id=mid, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle="zztest_grp_demo")
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
        combinaison_signature=signature_combinaison([("h", "h")]),
    )
    session.add(host)
    session.flush()
    montage = monter_instance(
        session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=host.id,
        chemin_hote="zztest_transverse_demo",
        portee_chemins={"/Plans", "/Correspondance"},
        matrice_plafond={"/Plans": ECRITURE, "/Correspondance": LECTURE},
        consenti_par="a",
        acteur="a",
    )
    return montage.montage_id


def _desire(session: Session, montage_id) -> dict[str, dict[str, Matrice]]:
    etat = etat_projete_transverse(session, montage_id)
    return {r.nom: {dg.groupe_id: dg.matrice for dg in r.droits} for r in etat.ressources}


# ---------------------------------------------------------------------------
# Correction : les droits projetés sont clés par groupe.cle (nom NC), pas l'UUID
# ---------------------------------------------------------------------------


def test_droits_projetes_cles_par_groupe_cle(db_session: Session) -> None:
    montage_id = _setup(db_session)
    desire = _desire(db_session, montage_id)
    assert desire["Plans"] == {"zztest_grp_demo": ECRITURE}  # cle NC, PAS un UUID
    assert desire["Correspondance"] == {"zztest_grp_demo": LECTURE}  # plafonné


# ---------------------------------------------------------------------------
# §3 — montage archivé → état désiré VIDE → delta = tout a_retirer
# ---------------------------------------------------------------------------


def test_montage_archive_projette_etat_vide(db_session: Session) -> None:
    montage_id = _setup(db_session)
    actuel = _desire(db_session, montage_id)  # comme si tout était déjà posé

    archiver_montage(db_session, montage_id, acteur="a")

    desire_apres = _desire(db_session, montage_id)
    assert desire_apres == {}  # fenêtre fermée -> plus rien d'exposé
    delta = calculer_delta(desire_apres, actuel)
    assert delta.a_retirer == frozenset(
        {("Plans", "zztest_grp_demo"), ("Correspondance", "zztest_grp_demo")}
    )
    assert delta.a_creer == {} and delta.a_modifier == {}


def test_plan_de_retrait_sans_destruction(db_session: Session) -> None:
    # INV-5 : le plan de retrait = clear ACL + retrait accès base, RIEN de destructeur.
    montage_id = _setup(db_session)
    actuel = _desire(db_session, montage_id)
    archiver_montage(db_session, montage_id, acteur="a")
    delta = calculer_delta({}, actuel)

    plan = planifier_reconcile_occ(
        "zztest_transverse_demo",
        delta,
        groupes_a_ajouter=(),
        groupes_a_retirer=("zztest_grp_demo",),
    )
    rendu = plan.rendu()
    # Uniquement retrait de règle + retrait d'accès base.
    assert "clear" in rendu
    assert (
        "groupfolders:group",
        "zztest_transverse_demo",
        "zztest_grp_demo",
        "-d",
    ) in plan.commandes
    # AUCUNE commande destructrice de données.
    for interdit in ("groupfolders:delete", "mkdir", "rm ", "rmdir", "unlink"):
        assert interdit not in rendu


def test_reversible_reactiver_reexpose(db_session: Session) -> None:
    montage_id = _setup(db_session)
    archiver_montage(db_session, montage_id, acteur="a")
    assert _desire(db_session, montage_id) == {}

    # Réactivation (réversibilité §3 étape 2) : la fenêtre se rouvre.
    row = db_session.get(Montage, montage_id)
    row.etat = EtatMontage.ACTIF
    db_session.flush()

    desire = _desire(db_session, montage_id)
    delta = calculer_delta(desire, {})  # serveur nettoyé après démontage
    assert set(delta.a_creer) == {
        ("Plans", "zztest_grp_demo"),
        ("Correspondance", "zztest_grp_demo"),
    }  # symétrie : re-exposer = re-créer


# ---------------------------------------------------------------------------
# Plan de reconcile : pose/modif/retrait via delta (forme occ)
# ---------------------------------------------------------------------------


def test_plan_reconcile_pose_et_modifie(db_session: Session) -> None:
    delta = calculer_delta(
        {"Plans": {"g": ECRITURE}, "Correspondance": {"g": LECTURE}},
        {"Correspondance": {"g": ECRITURE}},  # dérive : à ramener à LECTURE
    )
    plan = planifier_reconcile_occ(
        "zztest_transverse_demo", delta, groupes_a_ajouter=("g",), groupes_a_retirer=()
    )
    rendu = plan.rendu()
    assert ("groupfolders:group", "zztest_transverse_demo", "g", "read", "write") in plan.commandes
    assert "Plans -g g -- +read +write" in rendu  # a_creer
    assert "Correspondance -g g -- +read -write" in rendu  # a_modifier -> désiré (LECTURE)
    assert "groupfolders:create" not in rendu  # reconcile ACL : ne crée pas de GF
