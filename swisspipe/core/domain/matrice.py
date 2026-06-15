"""Value objects de la matrice de droits — cœur du domaine.

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : voir swisspipe/tests/test_core_purity.py et CLAUDE.md §1/§5).

Modèle (glossaire CLAUDE.md §4) :
- Niveau principal INCLUSIF : Lecture ⊂ Écriture ⊂ Suppression.
- Droits additionnels INDÉPENDANTS : Création, Classement, Téléchargement.
- Matrice = un niveau + un ensemble (éventuellement vide) d'additionnels.
- Modes : Hériter / Modifier / Refuser.

Sérialisation jsonb (format spec) :
    {"niveau": "ecriture", "additionnels": ["classement"]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NiveauPrincipal(Enum):
    """Niveau principal de droits, à sémantique INCLUSIVE.

    Suppression implique Écriture implique Lecture. La valeur de chaque membre est
    le token de sérialisation jsonb.
    """

    LECTURE = "lecture"
    ECRITURE = "ecriture"
    SUPPRESSION = "suppression"

    @property
    def rang(self) -> int:
        """Rang dans la chaîne d'inclusion (Lecture=1 < Écriture=2 < Suppression=3)."""
        return _RANGS_NIVEAU[self]

    def inclut(self, autre: NiveauPrincipal) -> bool:
        """Vrai si ce niveau couvre `autre` au sens inclusif.

        Suppression.inclut(Lecture) == True ; Lecture.inclut(Écriture) == False ;
        un niveau s'inclut lui-même.
        """
        return self.rang >= autre.rang


# Ordre d'inclusion. Défini hors de la classe pour rester un simple mapping stdlib.
_RANGS_NIVEAU: dict[NiveauPrincipal, int] = {
    NiveauPrincipal.LECTURE: 1,
    NiveauPrincipal.ECRITURE: 2,
    NiveauPrincipal.SUPPRESSION: 3,
}


class DroitAdditionnel(Enum):
    """Droits additionnels, INDÉPENDANTS entre eux (aucune relation d'inclusion)."""

    CREATION = "creation"
    CLASSEMENT = "classement"
    TELECHARGEMENT = "telechargement"


class Mode(Enum):
    """Mode d'application d'une matrice à un point de la topologie."""

    HERITER = "heriter"
    MODIFIER = "modifier"
    REFUSER = "refuser"


@dataclass(frozen=True)
class Matrice:
    """Matrice de droits immuable : un niveau principal + des additionnels.

    Immuable (frozen) et hachable (`additionnels` est un frozenset). Construire une
    variante = créer une nouvelle Matrice, jamais muter (cohérent avec INV-3 : les
    droits sont figés).
    """

    niveau: NiveauPrincipal
    additionnels: frozenset[DroitAdditionnel] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Coercition douce (set/list/tuple -> frozenset) sans casser l'immutabilité,
        # puis validation stricte des types pour garder le domaine sain.
        object.__setattr__(self, "additionnels", frozenset(self.additionnels))
        if not isinstance(self.niveau, NiveauPrincipal):
            raise TypeError(f"niveau doit être un NiveauPrincipal, reçu {type(self.niveau)!r}")
        for droit in self.additionnels:
            if not isinstance(droit, DroitAdditionnel):
                raise TypeError(
                    f"additionnel doit être un DroitAdditionnel, reçu {type(droit)!r}"
                )

    def couvre(self, autre: Matrice) -> bool:
        """Vrai si cette matrice est un PLAFOND ≥ `autre`.

        Couvre ssi le niveau inclut celui de `autre` ET tous les additionnels de
        `autre` sont présents ici. Base du futur calcul de plafond (L1 étape 2).
        """
        return self.niveau.inclut(autre.niveau) and autre.additionnels <= self.additionnels

    def vers_jsonb(self) -> dict[str, Any]:
        """Sérialise vers le format jsonb spec. Additionnels triés -> sortie stable."""
        return {
            "niveau": self.niveau.value,
            "additionnels": sorted(d.value for d in self.additionnels),
        }

    @classmethod
    def depuis_jsonb(cls, data: dict[str, Any]) -> Matrice:
        """Désérialise depuis le format jsonb spec. Inverse de `vers_jsonb`."""
        niveau = NiveauPrincipal(data["niveau"])
        additionnels = frozenset(DroitAdditionnel(token) for token in data.get("additionnels", ()))
        return cls(niveau=niveau, additionnels=additionnels)
