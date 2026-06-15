"""Entité Groupe — le SEUL porteur de droits (spec §4.5, INV-4).

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

INV-4 : tout droit est porté par un GROUPE, jamais par une personne en direct.
Cette entité est la raison d'être de cette indirection — aucune ressource, aucun
octroi ne référence un humain ; tout passe par un Groupe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TypeGroupe(Enum):
    """Nature d'un groupe.

    - PERSONNEL : représente UN SEUL humain (cible des rôles, INV-3/INV-4).
    - ORGANISATIONNEL : équipe collective, typiquement dérivée d'une valeur de
      dimension (ex. "technique-alpha").
    """

    PERSONNEL = "personnel"
    ORGANISATIONNEL = "organisationnel"


@dataclass(frozen=True)
class Groupe:
    """Porteur de droits (spec §4.5, INV-4). Identité = `id`.

    Un groupe PERSONNEL = exactement un humain ; un groupe ORGANISATIONNEL = une
    équipe dérivée d'une valeur de dimension. Le domaine ne stocke PAS les membres
    ici (ils vivront dans `groupe_membre` en persistance) ; il n'encode que la
    sémantique de type, utile au futur calcul des droits effectifs.

    Rappel INV-4 : aucun droit n'est attaché à une personne en direct — tout droit
    transite par un Groupe. C'est exactement pourquoi cette entité existe.

    `id` est une chaîne opaque (UUID en pratique) ; le domaine ne génère pas
    d'identifiant lui-même. `cle` est lisible (ex. "perso:marie",
    "orga:technique-alpha") mais n'entre PAS dans l'identité.
    """

    id: str
    type: TypeGroupe = field(compare=False)
    cle: str = field(compare=False)

    @property
    def est_personnel(self) -> bool:
        """True si le groupe représente un seul humain (PERSONNEL)."""
        return self.type is TypeGroupe.PERSONNEL

    @property
    def est_organisationnel(self) -> bool:
        """True si le groupe est une équipe collective (ORGANISATIONNEL)."""
        return self.type is TypeGroupe.ORGANISATIONNEL
