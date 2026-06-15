"""Service de renversement — projection de navigation PURE (spec §6).

100% stdlib (core/services autorise pydantic mais c'est inutile ici : pure logique
de regroupement). Aucun import externe (garde-fou de pureté :
swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Le renversement prend un ensemble PLAT d'EspaceDimensionnel et le présente groupé
selon un ordre de dimensions choisi (§6.2). Mêmes espaces, regroupement différent,
RIEN ne bouge physiquement : aucun droit modifié, aucun adaptateur touché, aucun
arbre matérialisé en amont (§6.3 : « on ne matérialise aucun arbre »).

Périmètre (§6.3 point 1) : ce service NE filtre PAS par droits. Il reçoit déjà les
espaces VISIBLES (le filtrage par droits est fait ailleurs, en amont) et se contente
de les projeter.

Choix documentés :
- Tri déterministe à chaque niveau : par `libelle`, puis par `valeur_cle` (départage
  stable). Les feuilles multiples d'un même chemin sont triées par `espace_id`.
- Dimension absente d'un espace : l'espace est groupé sous un nœud « (non défini) »
  pour cette dimension (jamais exclu — un espace visible ne doit pas disparaître de
  la navigation).
- Ordre vide : arbre vide (aucun regroupement possible).
- `espace_id` d'une feuille = la `signature` de l'EspaceDimensionnel (le value object
  du domaine n'a pas d'UUID, son identité est sa signature ; la persistance mappera
  vers l'UUID réel).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from swisspipe.core.domain.topologie import EspaceDimensionnel

# Libellé/clé du nœud regroupant les espaces sans valeur sur une dimension de l'ordre.
NON_DEFINI = "(non défini)"


@dataclass(frozen=True)
class NoeudNavigation:
    """Nœud de l'arbre de navigation (immuable).

    `espace_id` est renseigné UNIQUEMENT sur les feuilles (espaces terminaux) ; il
    vaut None sur les nœuds intermédiaires.
    """

    dimension_cle: str
    valeur_cle: str
    libelle: str
    enfants: tuple[NoeudNavigation, ...] = ()
    espace_id: str | None = None


@dataclass(frozen=True)
class ArbreNavigation:
    """Racine de l'arbre : les nœuds de premier niveau + l'ordre de dimensions utilisé."""

    noeuds: tuple[NoeudNavigation, ...] = ()
    ordre: tuple[str, ...] = field(default_factory=tuple)


def _libelle(
    dimension_cle: str, valeur_cle: str, libelles: Mapping[tuple[str, str], str]
) -> str:
    """Libellé d'affichage (§6.5) : map fournie, sinon fallback sur la valeur_cle."""
    if valeur_cle == NON_DEFINI:
        return NON_DEFINI
    return libelles.get((dimension_cle, valeur_cle), valeur_cle)


def _tri_noeud(noeud: NoeudNavigation) -> tuple[str, str]:
    """Clé de tri déterministe : libellé puis valeur_cle."""
    return (noeud.libelle, noeud.valeur_cle)


def _construire(
    espaces: list[EspaceDimensionnel],
    ordre: tuple[str, ...],
    profondeur: int,
    libelles: Mapping[tuple[str, str], str],
) -> tuple[NoeudNavigation, ...]:
    dimension_cle = ordre[profondeur]
    dernier_niveau = profondeur == len(ordre) - 1

    # Groupe les espaces par valeur sur la dimension courante (sentinel si absente).
    groupes: dict[str, list[EspaceDimensionnel]] = {}
    for espace in espaces:
        valeur = espace.valeur_sur(dimension_cle)
        valeur_cle = valeur if valeur is not None else NON_DEFINI
        groupes.setdefault(valeur_cle, []).append(espace)

    noeuds: list[NoeudNavigation] = []
    for valeur_cle, sous_espaces in groupes.items():
        libelle = _libelle(dimension_cle, valeur_cle, libelles)

        if not dernier_niveau:
            enfants = _construire(sous_espaces, ordre, profondeur + 1, libelles)
            noeuds.append(NoeudNavigation(dimension_cle, valeur_cle, libelle, enfants, None))
        elif len(sous_espaces) == 1:
            # Chemin -> 1 espace : le nœud terminal EST la feuille.
            noeuds.append(
                NoeudNavigation(
                    dimension_cle, valeur_cle, libelle, (), sous_espaces[0].signature
                )
            )
        else:
            # Plusieurs espaces sous le même chemin (différents sur une dimension
            # hors-ordre) : feuilles distinctes sous un nœud intermédiaire (§6.5).
            feuilles = tuple(
                sorted(
                    (
                        NoeudNavigation(dimension_cle, valeur_cle, libelle, (), e.signature)
                        for e in sous_espaces
                    ),
                    key=lambda n: n.espace_id or "",
                )
            )
            noeuds.append(NoeudNavigation(dimension_cle, valeur_cle, libelle, feuilles, None))

    return tuple(sorted(noeuds, key=_tri_noeud))


def renverser(
    espaces: Iterable[EspaceDimensionnel],
    ordre_dimensions: Sequence[str],
    libelles: Mapping[tuple[str, str], str] | None = None,
) -> ArbreNavigation:
    """Projette un nuage plat d'espaces en arbre de navigation, selon `ordre_dimensions`.

    Projection PURE et déterministe : ne modifie rien, ne lit aucun droit (les espaces
    reçus sont déjà ceux visibles). Pivot partiel supporté : `ordre_dimensions` peut
    couvrir une partie seulement des dimensions (commune=1, PME=2, holding=3).
    """
    ordre = tuple(ordre_dimensions)
    if not ordre:
        return ArbreNavigation(noeuds=(), ordre=ordre)
    table = dict(libelles) if libelles is not None else {}
    noeuds = _construire(list(espaces), ordre, 0, table)
    return ArbreNavigation(noeuds=noeuds, ordre=ordre)
