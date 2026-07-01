"""Tests de RoleAffectation (core/domain/role_affectation.py) — résolution rôle→titulaire.

Désigner un titulaire = créer une affectation vers un GROUPE PERSONNEL (jamais un compte
en direct, INV-4), à un instant DÉCLARÉ et FIGÉ (effectif_depuis, INV-3). Re-désigner =
NOUVELLE affectation ; l'ancienne n'est jamais « recalculée ». 100% domaine pur.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from swisspipe.core.domain.role_affectation import (
    RoleAffectation,
    SourceAffectation,
    affecter,
)

T0 = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def test_affecter_cree_affectation_vers_groupe_perso() -> None:
    a = affecter(
        espace_id="inst-xy",
        role_id="role-resp",
        groupe_perso_id="perso:marie",
        effectif_depuis=T0,
    )
    assert a.espace_id == "inst-xy"
    assert a.role_id == "role-resp"
    assert a.groupe_perso_id == "perso:marie"
    assert a.source is SourceAffectation.HUMAIN  # désignation = acte humain (INV-1)
    assert a.effectif_depuis == T0


def test_source_valeurs() -> None:
    # source='humain' seule pour l'instant (api:odoo -> L4).
    assert SourceAffectation.HUMAIN.value == "humain"


def test_source_hors_enum_rejetee() -> None:
    with pytest.raises(TypeError, match="SourceAffectation"):
        RoleAffectation(
            espace_id="i",
            role_id="r",
            groupe_perso_id="perso:x",
            source="humain",  # type: ignore[arg-type]
            effectif_depuis=T0,
        )


def test_effectif_depuis_fige_et_immutable() -> None:
    # INV-3 : l'instant est celui déclaré à la désignation, figé (frozen).
    a = affecter(espace_id="i", role_id="r", groupe_perso_id="perso:x", effectif_depuis=T0)
    assert a.effectif_depuis == T0
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.effectif_depuis = T1  # type: ignore[misc]


def test_re_designation_est_une_nouvelle_affectation() -> None:
    # INV-3 : re-désigner ne « recalcule » pas l'ancienne ; c'est une NOUVELLE affectation.
    ancienne = affecter(
        espace_id="i", role_id="r", groupe_perso_id="perso:marie", effectif_depuis=T0
    )
    nouvelle = affecter(
        espace_id="i", role_id="r", groupe_perso_id="perso:jean", effectif_depuis=T1
    )
    assert ancienne != nouvelle
    assert ancienne.groupe_perso_id == "perso:marie"  # l'ancienne est intacte
    assert ancienne.effectif_depuis == T0
    assert nouvelle.groupe_perso_id == "perso:jean"
    assert nouvelle.effectif_depuis == T1
