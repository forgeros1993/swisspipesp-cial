"""Tests du calcul des droits effectifs (core/services/droits_effectifs.py)."""

from __future__ import annotations

import pytest

from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.services.droits_effectifs import (
    DroitEffectif,
    droit_effectif_compte,
    droit_effectif_groupe,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
G = "g1"

# Arborescence : racine -> enfant -> petit
PARENTS: dict[str, str | None] = {"racine": None, "enfant": "racine", "petit": "enfant"}


def _reso(ressource_id: str, octrois: dict[tuple[str, str], Octroi]) -> DroitEffectif:
    return droit_effectif_groupe(ressource_id, G, PARENTS, octrois)


# ---------------------------------------------------------------------------
# Héritage
# ---------------------------------------------------------------------------


def test_heritage_simple() -> None:
    octrois = {
        ("racine", G): Octroi.modifier(ECRITURE),
        ("enfant", G): Octroi.heriter(),
    }
    res = _reso("enfant", octrois)
    assert res.matrice == ECRITURE
    assert res.accessible is True


def test_heritage_en_cascade() -> None:
    octrois = {
        ("racine", G): Octroi.modifier(ECRITURE),
        ("enfant", G): Octroi.heriter(),
        ("petit", G): Octroi.heriter(),
    }
    assert _reso("petit", octrois).matrice == ECRITURE


def test_heritage_sans_octroi_intermediaire() -> None:
    # Aucun octroi sur enfant/petit -> remonte quand même jusqu'à racine.
    octrois = {("racine", G): Octroi.modifier(ECRITURE)}
    assert _reso("petit", octrois).matrice == ECRITURE


def test_surcharge_intermediaire_la_plus_proche_gagne() -> None:
    octrois = {
        ("racine", G): Octroi.modifier(LECTURE),
        ("enfant", G): Octroi.modifier(ECRITURE),
        ("petit", G): Octroi.heriter(),
    }
    # petit hérite du MODIFIER le plus proche (enfant=Écriture), pas de la racine.
    assert _reso("petit", octrois).matrice == ECRITURE
    # racine garde sa propre matrice.
    assert _reso("racine", octrois).matrice == LECTURE


# ---------------------------------------------------------------------------
# REFUSER
# ---------------------------------------------------------------------------


def test_refuser_direct_bloque() -> None:
    octrois = {
        ("racine", G): Octroi.modifier(ECRITURE),
        ("enfant", G): Octroi.refuser(),
    }
    res = _reso("enfant", octrois)
    assert res.bloque is True
    assert res.matrice is None
    assert res.accessible is False


def test_refuser_herite_bloque_les_descendants() -> None:
    # enfant REFUSER, petit en HERITER -> petit bloqué (propagation vers le bas).
    octrois = {
        ("racine", G): Octroi.modifier(ECRITURE),
        ("enfant", G): Octroi.refuser(),
        ("petit", G): Octroi.heriter(),
    }
    assert _reso("petit", octrois).bloque is True


def test_refuser_ancetre_mais_descendant_modifier_propre() -> None:
    # Frontière documentée : un octroi MODIFIER propre est résolu en premier.
    octrois = {
        ("racine", G): Octroi.refuser(),
        ("enfant", G): Octroi.modifier(ECRITURE),
    }
    res = _reso("enfant", octrois)
    assert res.bloque is False
    assert res.matrice == ECRITURE


# ---------------------------------------------------------------------------
# Deny-by-default
# ---------------------------------------------------------------------------


def test_arbo_entierement_heriter_aucun_droit() -> None:
    octrois = {
        ("racine", G): Octroi.heriter(),
        ("enfant", G): Octroi.heriter(),
        ("petit", G): Octroi.heriter(),
    }
    res = _reso("petit", octrois)
    assert res.matrice is None
    assert res.bloque is False
    assert res.accessible is False


def test_aucun_octroi_du_tout_aucun_droit() -> None:
    assert _reso("petit", {}).accessible is False


# ---------------------------------------------------------------------------
# Combinaison multi-groupes (D6 : modèle additif)
# ---------------------------------------------------------------------------


def test_compte_un_seul_groupe_egal_groupe() -> None:
    octrois = {("racine", G): Octroi.modifier(ECRITURE), ("enfant", G): Octroi.heriter()}
    compte = droit_effectif_compte([G], "enfant", PARENTS, octrois)
    assert compte == droit_effectif_groupe("enfant", G, PARENTS, octrois)


def test_cas_cedric_refuser_un_groupe_positif_autre() -> None:
    # « Salaires direction » : REFUSER pour Finance, positif pour Direction.
    parents: dict[str, str | None] = {"salaires": None}
    octrois = {
        ("salaires", "finance"): Octroi.refuser(),
        ("salaires", "direction"): Octroi.modifier(ECRITURE),
    }
    # Marie ∈ {Finance, Direction} -> voit le dossier via Direction.
    marie = droit_effectif_compte(["finance", "direction"], "salaires", parents, octrois)
    assert marie.accessible is True
    assert marie.matrice == ECRITURE
    # Quelqu'un uniquement dans Finance -> bloqué via son groupe -> aucun accès.
    paul = droit_effectif_compte(["finance"], "salaires", parents, octrois)
    assert paul.accessible is False
    assert paul.bloque is False  # côté compte : aucun, pas « bloqué »


def test_compte_deux_positifs_union() -> None:
    parents: dict[str, str | None] = {"r": None}
    octrois = {
        ("r", "a"): Octroi.modifier(Matrice(NiveauPrincipal.LECTURE, {DroitAdditionnel.CREATION})),
        ("r", "b"): Octroi.modifier(Matrice(NiveauPrincipal.ECRITURE)),
    }
    res = droit_effectif_compte(["a", "b"], "r", parents, octrois)
    assert res.matrice == Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CREATION})


def test_compte_tous_rien_aucun_acces() -> None:
    parents: dict[str, str | None] = {"r": None}
    octrois = {("r", "a"): Octroi.refuser(), ("r", "b"): Octroi.heriter()}
    res = droit_effectif_compte(["a", "b"], "r", parents, octrois)
    assert res.accessible is False
    assert res.matrice is None and res.bloque is False


def test_compte_sans_groupe_aucun_acces() -> None:
    assert droit_effectif_compte([], "petit", PARENTS, {}).accessible is False


def test_compte_determinisme_ordre_groupes() -> None:
    parents: dict[str, str | None] = {"r": None}
    octrois = {
        ("r", "a"): Octroi.modifier(Matrice(NiveauPrincipal.LECTURE, {DroitAdditionnel.CREATION})),
        ("r", "b"): Octroi.modifier(Matrice(NiveauPrincipal.ECRITURE)),
    }
    assert droit_effectif_compte(["a", "b"], "r", parents, octrois) == droit_effectif_compte(
        ["b", "a"], "r", parents, octrois
    )


# ---------------------------------------------------------------------------
# Invariants du résultat + déterminisme
# ---------------------------------------------------------------------------


def test_resultat_immuable() -> None:
    import dataclasses

    res = DroitEffectif.accorde(ECRITURE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.matrice = LECTURE  # type: ignore[misc]


def test_resultat_bloque_avec_matrice_rejete() -> None:
    with pytest.raises(ValueError, match="bloqué"):
        DroitEffectif(matrice=ECRITURE, bloque=True)


def test_determinisme() -> None:
    octrois = {
        ("racine", G): Octroi.modifier(LECTURE),
        ("enfant", G): Octroi.modifier(ECRITURE),
        ("petit", G): Octroi.heriter(),
    }
    assert _reso("petit", octrois) == _reso("petit", octrois)


def test_cycle_detecte() -> None:
    parents_cycliques: dict[str, str | None] = {"a": "b", "b": "a"}
    with pytest.raises(ValueError, match="cycle"):
        droit_effectif_groupe("a", G, parents_cycliques, {})
