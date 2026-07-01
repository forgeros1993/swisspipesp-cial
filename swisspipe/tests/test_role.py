"""Tests du Rôle (core/domain/role.py) + matrice par rôle (additif au Modèle, étape 1).

Le Modèle définit des rôles ("responsable", "employe") et une matrice IMPOSÉE par rôle
{rôle → {ressource → Matrice L1}} — « Plans en Écriture pour Responsable ». RÉUTILISE la
Matrice L1 (aucun nouveau type). 100% domaine pur.
"""

from __future__ import annotations

import pytest

from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    DossierImpose,
    Modele,
)
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.domain.role import Role, octrois_pour_role

ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
LECTURE = Matrice(NiveauPrincipal.LECTURE)


def _modele(*, matrice_par_role: object = None) -> Modele:
    kwargs: dict[str, object] = {}
    if matrice_par_role is not None:
        kwargs["matrice_par_role"] = matrice_par_role
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
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# §1 — Rôle référence un modèle
# ---------------------------------------------------------------------------


def test_role_reference_un_modele() -> None:
    r = Role(modele_id="immobilier", cle="responsable", libelle="Responsable")
    assert r.modele_id == "immobilier"
    assert r.cle == "responsable"
    assert r.libelle == "Responsable"


# ---------------------------------------------------------------------------
# §1 — Matrice par rôle : ne cite que des rôles/ressources du modèle
# ---------------------------------------------------------------------------


def test_matrice_par_role_valide_acceptee() -> None:
    m = _modele(matrice_par_role={"responsable": {"plans": ECRITURE, "correspondance": LECTURE}})
    assert m.matrice_par_role["responsable"]["plans"] == ECRITURE


def test_matrice_par_role_role_inconnu_rejete() -> None:
    with pytest.raises(ValueError, match="rôle"):
        _modele(matrice_par_role={"inconnu": {"plans": ECRITURE}})


def test_matrice_par_role_ressource_inconnue_rejetee() -> None:
    with pytest.raises(ValueError, match="ressource"):
        _modele(matrice_par_role={"responsable": {"inexistant": ECRITURE}})


def test_modele_sans_matrice_par_role_est_valide() -> None:
    # Rétrocompatible étape 1 : le champ est optionnel (défaut vide).
    m = _modele()
    assert m.matrice_par_role == {}


# ---------------------------------------------------------------------------
# §1 — Round-trip jsonb (avec matrice par rôle)
# ---------------------------------------------------------------------------


def test_round_trip_jsonb_avec_matrice_par_role() -> None:
    m = _modele(matrice_par_role={"responsable": {"plans": ECRITURE}})
    assert Modele.depuis_jsonb(m.vers_jsonb()) == m


# ---------------------------------------------------------------------------
# §3 (logique pure) — octrois posés pour un rôle, sur des ressources concrètes
# ---------------------------------------------------------------------------


def test_octrois_pour_role_produit_octrois_concrets() -> None:
    mpr = {"responsable": {"plans": ECRITURE, "correspondance": LECTURE}}
    ressource_par_dossier = {"plans": "res-plans", "correspondance": "res-corr"}
    octrois = octrois_pour_role(mpr, "responsable", ressource_par_dossier)
    assert octrois == {
        "res-plans": Octroi.modifier(ECRITURE),
        "res-corr": Octroi.modifier(LECTURE),
    }


def test_octrois_pour_role_inconnu_vide() -> None:
    assert octrois_pour_role({"responsable": {"plans": ECRITURE}}, "employe", {"plans": "r"}) == {}
