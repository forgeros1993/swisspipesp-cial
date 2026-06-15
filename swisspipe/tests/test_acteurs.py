"""Tests de l'entité Groupe (core/domain/acteurs.py)."""

from __future__ import annotations

import dataclasses

import pytest

from swisspipe.core.domain.acteurs import Groupe, TypeGroupe


def test_construction_groupe() -> None:
    g = Groupe("g-1", TypeGroupe.PERSONNEL, "perso:marie")
    assert g.id == "g-1"
    assert g.type is TypeGroupe.PERSONNEL
    assert g.cle == "perso:marie"


def test_identite_par_id() -> None:
    # Même id, type et cle différents -> même groupe (identité = id).
    a = Groupe("g-1", TypeGroupe.PERSONNEL, "perso:marie")
    b = Groupe("g-1", TypeGroupe.ORGANISATIONNEL, "orga:autre")
    assert a == b
    assert hash(a) == hash(b)


def test_ids_distincts_sont_distincts() -> None:
    a = Groupe("g-1", TypeGroupe.PERSONNEL, "perso:marie")
    b = Groupe("g-2", TypeGroupe.PERSONNEL, "perso:marie")
    assert a != b


def test_est_personnel() -> None:
    g = Groupe("g-1", TypeGroupe.PERSONNEL, "perso:marie")
    assert g.est_personnel is True
    assert g.est_organisationnel is False


def test_est_organisationnel() -> None:
    g = Groupe("g-2", TypeGroupe.ORGANISATIONNEL, "orga:technique-alpha")
    assert g.est_organisationnel is True
    assert g.est_personnel is False


def test_immutabilite() -> None:
    g = Groupe("g-1", TypeGroupe.PERSONNEL, "perso:marie")
    with pytest.raises(dataclasses.FrozenInstanceError):
        g.id = "g-2"  # type: ignore[misc]


def test_hachabilite() -> None:
    a = Groupe("g-1", TypeGroupe.PERSONNEL, "perso:marie")
    b = Groupe("g-1", TypeGroupe.PERSONNEL, "perso:marie")
    assert len({a, b}) == 1


def test_types_groupe() -> None:
    assert {t.value for t in TypeGroupe} == {"personnel", "organisationnel"}
