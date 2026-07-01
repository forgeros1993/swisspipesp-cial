"""Value object RoleAffectation — résolution rôle → titulaire (spec §5.4, INV-1/3/4).

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Désigner un titulaire est l'acte HUMAIN autorisé (source='humain', INV-1) : il vise un
GROUPE PERSONNEL (jamais un compte/personne en direct, INV-4). L'instant est DÉCLARÉ et
FIGÉ (effectif_depuis, INV-3) — jamais recalculé « live ». Re-désigner = NOUVELLE
affectation (value object immuable) ; l'ancienne n'est pas mutée.

Note INV-4 : ce type ne peut pas, seul, garantir que `groupe_perso_id` désigne bien un
groupe PERSONNEL (le type de groupe vit en persistance) — c'est le service qui le vérifie
à la désignation. Ici on encode la CIBLE (un groupe), jamais un humain nommé.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SourceAffectation(Enum):
    """Provenance d'une affectation. 'humain' = désignation manuelle (seule pour l'instant ;
    'api:odoo' piloté par l'ERP viendra en L4)."""

    HUMAIN = "humain"


@dataclass(frozen=True)
class RoleAffectation:
    """Affectation figée d'un rôle à un groupe personnel, à un instant déclaré.

    `espace_id` = l'instance ; `role_id` = le rôle du modèle ; `groupe_perso_id` = la
    cible (un groupe PERSONNEL, INV-4) ; `effectif_depuis` = instant figé (INV-3).
    """

    espace_id: str
    role_id: str
    groupe_perso_id: str
    source: SourceAffectation
    effectif_depuis: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceAffectation):
            raise TypeError(f"source doit être un SourceAffectation, reçu {type(self.source)!r}")
        if not isinstance(self.effectif_depuis, datetime):
            raise TypeError(
                f"effectif_depuis doit être un datetime, reçu {type(self.effectif_depuis)!r}"
            )
        if not self.groupe_perso_id:
            raise ValueError("une affectation vise un groupe personnel (groupe_perso_id requis)")


def affecter(
    *,
    espace_id: str,
    role_id: str,
    groupe_perso_id: str,
    effectif_depuis: datetime,
    source: SourceAffectation = SourceAffectation.HUMAIN,
) -> RoleAffectation:
    """Fabrique une affectation (désignation d'un titulaire). Pure ; instant figé injecté."""
    return RoleAffectation(
        espace_id=espace_id,
        role_id=role_id,
        groupe_perso_id=groupe_perso_id,
        source=source,
        effectif_depuis=effectif_depuis,
    )
