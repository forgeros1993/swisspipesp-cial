"""Value objects de topologie — Dimension, ValeurDimension, EspaceDimensionnel.

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Point conceptuel CRITIQUE (spec §4.2, §6.3) : un espace dimensionnel n'est PAS un
nœud d'arbre, c'est le CROISEMENT d'une valeur par dimension — un tuple. L'arbre de
navigation n'existe jamais en dur ; il sera calculé à la lecture par le renversement
(service, étape suivante). Le domaine ne modélise donc qu'un ensemble PLAT de tuples,
sans aucune hiérarchie matérialisée.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Dimension:
    """Axe de classification (spec §1, §4.1).

    Identité métier = `cle` seule : deux dimensions de même `cle` sont la même
    (libelle/rang exclus de l'égalité et du hash).
    """

    cle: str
    libelle: str = field(compare=False)
    rang: int = field(default=0, compare=False)  # 0 = dimension mère


@dataclass(frozen=True)
class ValeurDimension:
    """Valeur prise sur un axe (spec §4.1).

    Identité = couple (dimension_cle, cle). `libelle` sert de nom en navigation
    (§6.5) mais n'entre pas dans l'identité.
    """

    dimension_cle: str
    cle: str
    libelle: str = field(compare=False)


@dataclass(frozen=True)
class Coordonnee:
    """Choix d'une valeur sur une dimension, au sein d'un espace.

    Paire (dimension → valeur choisie). Identité = les deux champs.
    """

    dimension_cle: str
    valeur_cle: str


@dataclass(frozen=True)
class EspaceDimensionnel:
    """Croisement de coordonnées (spec §4.2) — un tuple plat, pas un nœud d'arbre.

    Règle : au plus une valeur par dimension. Pas de nom propre (§6.5 : en v1 un
    espace est nommé par ses valeurs de dimension en navigation).
    """

    coordonnees: frozenset[Coordonnee] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordonnees", frozenset(self.coordonnees))
        for coord in self.coordonnees:
            if not isinstance(coord, Coordonnee):
                raise TypeError(f"coordonnée invalide : {type(coord)!r}")
        # Unicité : une dimension ne peut porter qu'une valeur dans un espace.
        compte = Counter(c.dimension_cle for c in self.coordonnees)
        doublons = sorted(dim for dim, n in compte.items() if n > 1)
        if doublons:
            raise ValueError(
                "une dimension ne peut avoir qu'une valeur dans un espace "
                f"(dimensions en double : {', '.join(doublons)})"
            )

    @property
    def signature(self) -> str:
        """Signature canonique, déterministe et insensible à l'ordre.

        Construite à partir des coordonnées triées par dimension_cle. Garantit
        l'unicité de combinaison (§4.2 : pas deux "Finance" pour "Alpha"). Même jeu
        de coordonnées -> même signature ; jeu différent -> signature différente.
        """
        return ";".join(
            f"{c.dimension_cle}={c.valeur_cle}"
            for c in sorted(self.coordonnees, key=lambda c: c.dimension_cle)
        )

    def valeur_sur(self, dimension_cle: str) -> str | None:
        """Valeur choisie sur `dimension_cle`, ou None si la dimension est absente."""
        for coord in self.coordonnees:
            if coord.dimension_cle == dimension_cle:
                return coord.valeur_cle
        return None
