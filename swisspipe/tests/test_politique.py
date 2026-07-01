"""Tests de la politique de droits du Modèle (core/domain/modele.py) — curseur §5.4.

Additif : politique_droits ∈ {imposee, deleguee, libre}, défaut 'imposee' (rétrocompat
étapes 1-4). 'libre' n'a pas de logique dédiée (cas dégénéré traité comme 'deleguee').
100% domaine pur.
"""

from __future__ import annotations

import pytest

from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    DossierImpose,
    Modele,
    PolitiqueDroits,
)


def _modele(**kwargs: object) -> Modele:
    return Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(DossierImpose(cle="plans", libelle="Plans"),),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        **kwargs,  # type: ignore[arg-type]
    )


def test_valeurs_politique() -> None:
    assert {p.value for p in PolitiqueDroits} == {"imposee", "deleguee", "libre"}


def test_modele_porte_sa_politique() -> None:
    m = _modele(politique_droits=PolitiqueDroits.DELEGUEE)
    assert m.politique_droits is PolitiqueDroits.DELEGUEE


def test_politique_defaut_imposee() -> None:
    # Anciens modèles (étapes 1-4) sans politique -> 'imposee'.
    assert _modele().politique_droits is PolitiqueDroits.IMPOSEE


def test_politique_hors_enum_rejetee() -> None:
    with pytest.raises(TypeError, match="PolitiqueDroits"):
        _modele(politique_droits="deleguee")


def test_round_trip_jsonb_avec_politique() -> None:
    m = _modele(politique_droits=PolitiqueDroits.DELEGUEE)
    assert Modele.depuis_jsonb(m.vers_jsonb()) == m


def test_depuis_jsonb_sans_politique_est_imposee() -> None:
    # jsonb d'un ancien modèle (sans la clé) -> défaut imposee.
    data = _modele().vers_jsonb()
    del data["politique_droits"]
    assert Modele.depuis_jsonb(data).politique_droits is PolitiqueDroits.IMPOSEE
