"""Tests du delta de projection (core/services/delta_projection.py) — cœur PUR.

calculer_delta(desire, actuel) où chaque état = { ressource → { groupe → Matrice } }.
RÈGLE D'OR : idempotence (desire == actuel → delta VIDE). Le delta ne fabrique JAMAIS
un droit hors du désiré (deny-by-default). 100% data-pure, zéro occ.
"""

from __future__ import annotations

import pytest

from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.services.delta_projection import calculer_delta

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
SUPPRESSION = Matrice(NiveauPrincipal.SUPPRESSION)

DESIRE = {
    "Plans": {"grp": ECRITURE},
    "Correspondance": {"grp": LECTURE},
}


# ---------------------------------------------------------------------------
# Idempotence (la règle d'or)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("etat", [{}, DESIRE, {"X": {"a": LECTURE, "b": SUPPRESSION}}])
def test_desire_egale_actuel_delta_vide(etat: dict) -> None:
    delta = calculer_delta(etat, etat)
    assert delta.est_vide
    assert delta.a_creer == {}
    assert delta.a_modifier == {}
    assert delta.a_retirer == frozenset()


# ---------------------------------------------------------------------------
# Les trois familles
# ---------------------------------------------------------------------------


def test_regle_absente_cote_serveur_a_creer() -> None:
    delta = calculer_delta(DESIRE, {"Plans": {"grp": ECRITURE}})
    assert delta.a_creer == {("Correspondance", "grp"): LECTURE}
    assert delta.a_modifier == {}
    assert delta.a_retirer == frozenset()


@pytest.mark.parametrize(
    ("serveur", "desire"),
    [(ECRITURE, LECTURE), (LECTURE, ECRITURE), (SUPPRESSION, LECTURE)],
)
def test_regle_divergente_a_modifier_cible_le_desire(serveur: Matrice, desire: Matrice) -> None:
    delta = calculer_delta({"Plans": {"grp": desire}}, {"Plans": {"grp": serveur}})
    assert delta.a_modifier == {("Plans", "grp"): desire}  # la valeur cible = le DÉSIRÉ
    assert delta.a_creer == {} and delta.a_retirer == frozenset()


def test_regle_en_trop_a_retirer() -> None:
    # Hors portée / fenêtre fermée : présent serveur, absent du désiré.
    delta = calculer_delta({}, DESIRE)
    assert delta.a_retirer == frozenset({("Plans", "grp"), ("Correspondance", "grp")})
    assert delta.a_creer == {} and delta.a_modifier == {}
    assert not delta.est_vide


def test_groupe_en_trop_sur_ressource_partagee() -> None:
    # Même ressource, un groupe désiré + un groupe fantôme -> retirer SEULEMENT le fantôme.
    delta = calculer_delta(
        {"Plans": {"grp": ECRITURE}},
        {"Plans": {"grp": ECRITURE, "fantome": LECTURE}},
    )
    assert delta.a_retirer == frozenset({("Plans", "fantome")})
    assert delta.a_creer == {} and delta.a_modifier == {}


# ---------------------------------------------------------------------------
# Deny-by-default : le delta ne fabrique JAMAIS un droit hors du désiré
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actuel",
    [{}, DESIRE, {"Plans": {"autre": SUPPRESSION}}, {"Zombie": {"grp": ECRITURE}}],
)
def test_creer_modifier_toujours_sous_ensemble_du_desire(actuel: dict) -> None:
    delta = calculer_delta(DESIRE, actuel)
    cles_desire = {(r, g) for r, par_g in DESIRE.items() for g in par_g}
    assert set(delta.a_creer) <= cles_desire
    assert set(delta.a_modifier) <= cles_desire
    for (r, g), m in {**delta.a_creer, **delta.a_modifier}.items():
        assert m == DESIRE[r][g]  # jamais une valeur inventée
