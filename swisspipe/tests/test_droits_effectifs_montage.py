"""Tests du calcul de droits effectifs MONTAGE-AWARE (core/services/droits_effectifs.py).

§9.3 : à travers un montage, le droit effectif est PLAFONNÉ par montage.matrice_plafond
(garde-fou anti-escalade) et limité à la PORTÉE. Étend le résolveur L1 sans le réécrire.
100% pur (aucun NC, aucune DB). INV-3 : on lit l'état figé, aucune évaluation « live ».
"""

from __future__ import annotations

import pytest

from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.services.droits_effectifs import (
    borner_matrice,
    droit_effectif_compte,
    droit_effectif_via_montage,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
SUPPRESSION = Matrice(NiveauPrincipal.SUPPRESSION)
ECRITURE_CLAS = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})


def _octrois(matrice: Matrice, ressource: str = "r", groupe: str = "g") -> dict:
    return {(ressource, groupe): Octroi.modifier(matrice)}


# ---------------------------------------------------------------------------
# borner_matrice : min niveau + intersection des additionnels
# ---------------------------------------------------------------------------


def test_borner_abaisse_le_niveau() -> None:
    assert borner_matrice(ECRITURE, LECTURE) == LECTURE


def test_borner_laisse_intact_si_sous_le_plafond() -> None:
    assert borner_matrice(LECTURE, ECRITURE) == LECTURE


def test_borner_intersecte_les_additionnels() -> None:
    # base Écriture+classement, plafond Lecture (sans additionnel) -> Lecture sans additionnel.
    assert borner_matrice(ECRITURE_CLAS, LECTURE) == LECTURE
    # plafond qui n'accorde pas classement retire l'additionnel.
    assert borner_matrice(ECRITURE_CLAS, ECRITURE) == ECRITURE


# ---------------------------------------------------------------------------
# §3 — ANTI-ESCALADE (le test qui compte le plus)
# ---------------------------------------------------------------------------


def test_octroi_ecriture_vu_via_plafond_lecture_devient_lecture() -> None:
    # Un octroi sous-jacent ÉCRITURE (posé par rôle) à travers un plafond LECTURE = LECTURE.
    res = droit_effectif_via_montage(
        ["g"], "r", {"r": None}, _octrois(ECRITURE), plafond=LECTURE, portee=frozenset({"r"})
    )
    assert res.matrice == LECTURE


def test_octroi_sous_le_plafond_reste_inchange() -> None:
    res = droit_effectif_via_montage(
        ["g"], "r", {"r": None}, _octrois(LECTURE), plafond=ECRITURE, portee=frozenset({"r"})
    )
    assert res.matrice == LECTURE


@pytest.mark.parametrize("base", [LECTURE, ECRITURE, SUPPRESSION, ECRITURE_CLAS])
@pytest.mark.parametrize("plafond", [LECTURE, ECRITURE, SUPPRESSION])
def test_aucun_chemin_ne_depasse_le_plafond(base: Matrice, plafond: Matrice) -> None:
    res = droit_effectif_via_montage(
        ["g"], "r", {"r": None}, _octrois(base), plafond=plafond, portee=frozenset({"r"})
    )
    assert res.matrice is not None
    # Niveau borné ET additionnels bornés : le plafond COUVRE toujours le résultat.
    assert plafond.couvre(res.matrice)


def test_plafond_ne_cree_jamais_de_droit() -> None:
    # Aucun octroi sous-jacent -> même un plafond ÉCRITURE n'accorde rien (deny-by-default).
    res = droit_effectif_via_montage(
        ["g"], "r", {"r": None}, {}, plafond=ECRITURE, portee=frozenset({"r"})
    )
    assert res.matrice is None
    assert not res.accessible


# ---------------------------------------------------------------------------
# §2 — PORTÉE : hors portée = invisible
# ---------------------------------------------------------------------------


def test_ressource_hors_portee_invisible() -> None:
    # r existe et a un octroi, mais la portée n'expose que r2 -> r invisible via ce montage.
    res = droit_effectif_via_montage(
        ["g"], "r", {"r": None}, _octrois(ECRITURE), plafond=ECRITURE, portee=frozenset({"r2"})
    )
    assert res.matrice is None
    assert not res.accessible


def test_ressource_dans_portee_visible() -> None:
    res = droit_effectif_via_montage(
        ["g"], "r", {"r": None}, _octrois(ECRITURE), plafond=ECRITURE, portee=frozenset({"r"})
    )
    assert res.accessible


# ---------------------------------------------------------------------------
# §4 — Non-régression : sans montage (plafond/portee None) == résolveur L1
# ---------------------------------------------------------------------------


def test_sans_montage_identique_au_resolveur_l1() -> None:
    octrois = _octrois(ECRITURE)
    base = droit_effectif_compte(["g"], "r", {"r": None}, octrois)
    via = droit_effectif_via_montage(["g"], "r", {"r": None}, octrois, plafond=None, portee=None)
    assert via == base
