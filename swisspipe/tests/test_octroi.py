"""Tests du value object Octroi (core/domain/octroi.py)."""

from __future__ import annotations

import dataclasses

import pytest

from swisspipe.core.domain.matrice import (
    DroitAdditionnel,
    Matrice,
    Mode,
    NiveauPrincipal,
)
from swisspipe.core.domain.octroi import Octroi

MATRICE_DEMO = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})


# ---------------------------------------------------------------------------
# Constructeurs de confort -> octrois valides
# ---------------------------------------------------------------------------


def test_heriter_construit_octroi_sans_matrice() -> None:
    o = Octroi.heriter()
    assert o.mode is Mode.HERITER
    assert o.matrice is None


def test_modifier_construit_octroi_avec_matrice() -> None:
    o = Octroi.modifier(MATRICE_DEMO)
    assert o.mode is Mode.MODIFIER
    assert o.matrice == MATRICE_DEMO


def test_refuser_construit_octroi_sans_matrice() -> None:
    o = Octroi.refuser()
    assert o.mode is Mode.REFUSER
    assert o.matrice is None


# ---------------------------------------------------------------------------
# Règles de cohérence -> ValueError
# ---------------------------------------------------------------------------


def test_modifier_sans_matrice_rejete() -> None:
    with pytest.raises(ValueError, match="MODIFIER exige une matrice"):
        Octroi(Mode.MODIFIER, None)


def test_heriter_avec_matrice_rejete() -> None:
    with pytest.raises(ValueError, match="HERITER ne porte pas de matrice"):
        Octroi(Mode.HERITER, MATRICE_DEMO)


def test_refuser_avec_matrice_rejete() -> None:
    with pytest.raises(ValueError, match="REFUSER est un blocage"):
        Octroi(Mode.REFUSER, MATRICE_DEMO)


# ---------------------------------------------------------------------------
# Immuabilité
# ---------------------------------------------------------------------------


def test_octroi_est_frozen() -> None:
    o = Octroi.heriter()
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.mode = Mode.REFUSER  # type: ignore[misc]


def test_octroi_matrice_non_mutable() -> None:
    o = Octroi.modifier(MATRICE_DEMO)
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.matrice = None  # type: ignore[misc]


def test_octroi_hachable() -> None:
    a = Octroi.modifier(MATRICE_DEMO)
    b = Octroi.modifier(MATRICE_DEMO)
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


# ---------------------------------------------------------------------------
# est_bloquant
# ---------------------------------------------------------------------------


def test_est_bloquant_seulement_pour_refuser() -> None:
    assert Octroi.refuser().est_bloquant is True
    assert Octroi.heriter().est_bloquant is False
    assert Octroi.modifier(MATRICE_DEMO).est_bloquant is False


# ---------------------------------------------------------------------------
# Round-trip jsonb pour les 3 modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "octroi",
    [Octroi.heriter(), Octroi.modifier(MATRICE_DEMO), Octroi.refuser()],
)
def test_round_trip_jsonb(octroi: Octroi) -> None:
    assert Octroi.depuis_jsonb(octroi.vers_jsonb()) == octroi


def test_vers_jsonb_modifier_format() -> None:
    assert Octroi.modifier(MATRICE_DEMO).vers_jsonb() == {
        "mode": "modifier",
        "matrice": {"niveau": "ecriture", "additionnels": ["classement"]},
    }


def test_vers_jsonb_heriter_matrice_null() -> None:
    assert Octroi.heriter().vers_jsonb() == {"mode": "heriter", "matrice": None}


def test_vers_jsonb_refuser_matrice_null() -> None:
    assert Octroi.refuser().vers_jsonb() == {"mode": "refuser", "matrice": None}


def test_depuis_jsonb_revalide_coherence() -> None:
    # Un jsonb incohérent (modifier sans matrice) est rejeté à la lecture.
    with pytest.raises(ValueError, match="MODIFIER exige une matrice"):
        Octroi.depuis_jsonb({"mode": "modifier", "matrice": None})


def test_mode_invalide_leve_typeerror() -> None:
    with pytest.raises(TypeError):
        Octroi("heriter")  # type: ignore[arg-type]
