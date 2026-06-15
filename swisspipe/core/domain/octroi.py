"""Value object Octroi — association cohérente d'un Mode et d'une Matrice.

100% stdlib + value objects du domaine (matrice.py). Aucun import externe
(garde-fou de pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Règle de cohérence (spec §9.2, modèle §4.6) : les trois modes ne se comportent
pas pareil vis-à-vis de la matrice.

- HERITER (défaut) : reprend la matrice du parent -> matrice = None.
- MODIFIER : surcharge éditable -> matrice OBLIGATOIRE (une Matrice concrète).
- REFUSER : liste noire, blocage sec, ressource non visible -> matrice = None
  (un refus n'est pas un "niveau de droit", c'est un blocage).

Trois seules constructions valides : Octroi.heriter(), Octroi.modifier(m),
Octroi.refuser(). Sérialisation jsonb round-trippable.

    {"mode": "modifier", "matrice": {"niveau": "ecriture", "additionnels": ["classement"]}}
    {"mode": "heriter", "matrice": null}
    {"mode": "refuser", "matrice": null}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from swisspipe.core.domain.matrice import Matrice, Mode


@dataclass(frozen=True)
class Octroi:
    """Octroi immuable : un mode + (selon le mode) une matrice propre.

    La cohérence mode/matrice est garantie à la construction (__post_init__).
    Préférer les constructeurs de confort aux appels directs.
    """

    mode: Mode
    matrice: Matrice | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, Mode):
            raise TypeError(f"mode doit être un Mode, reçu {type(self.mode)!r}")
        if self.matrice is not None and not isinstance(self.matrice, Matrice):
            raise TypeError(f"matrice doit être une Matrice ou None, reçu {type(self.matrice)!r}")

        if self.mode is Mode.MODIFIER and self.matrice is None:
            raise ValueError("une surcharge MODIFIER exige une matrice")
        if self.mode is Mode.HERITER and self.matrice is not None:
            raise ValueError("HERITER ne porte pas de matrice propre, elle est héritée du parent")
        if self.mode is Mode.REFUSER and self.matrice is not None:
            raise ValueError("REFUSER est un blocage, pas une matrice")

    # --- Constructeurs de confort : les 3 seules façons valides ---------------

    @classmethod
    def heriter(cls) -> Octroi:
        """Octroi par défaut : la matrice est héritée du parent."""
        return cls(mode=Mode.HERITER, matrice=None)

    @classmethod
    def modifier(cls, matrice: Matrice) -> Octroi:
        """Surcharge éditable : pose une matrice propre."""
        return cls(mode=Mode.MODIFIER, matrice=matrice)

    @classmethod
    def refuser(cls) -> Octroi:
        """Blocage sec : ressource non visible, aucun droit."""
        return cls(mode=Mode.REFUSER, matrice=None)

    # --- Aides ----------------------------------------------------------------

    @property
    def est_bloquant(self) -> bool:
        """True si REFUSER (utile au futur calcul des droits effectifs)."""
        return self.mode is Mode.REFUSER

    # --- Sérialisation jsonb (modèle §4.6) ------------------------------------

    def vers_jsonb(self) -> dict[str, Any]:
        """Sérialise mode + matrice (null si pas de matrice propre)."""
        return {
            "mode": self.mode.value,
            "matrice": self.matrice.vers_jsonb() if self.matrice is not None else None,
        }

    @classmethod
    def depuis_jsonb(cls, data: dict[str, Any]) -> Octroi:
        """Désérialise. La cohérence mode/matrice est re-validée par __post_init__."""
        mode = Mode(data["mode"])
        matrice_data = data.get("matrice")
        matrice = Matrice.depuis_jsonb(matrice_data) if matrice_data is not None else None
        return cls(mode=mode, matrice=matrice)
