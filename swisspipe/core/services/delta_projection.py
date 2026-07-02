"""Delta de projection — réconciliation d'état d'un transverse projeté (spec §3.2).

100% stdlib (importe le domaine : Matrice). Aucun import externe (garde-fou de pureté :
swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Même principe que le reconcile L1 (core/services/reconciliation.comparer_droits) : on
compare un état DÉSIRÉ COMPLET à l'état RÉEL, jamais un diff incrémental. Forme d'état :
{ ressource → { groupe → Matrice } } — agnostique (les ressources/groupes sont des ids
opaques ; l'adaptateur traduit vers occ). RÈGLE D'OR : idempotence (desire == actuel →
delta VIDE → l'orchestrateur ne touche à rien, no-op strict).

Deny-by-default : `a_creer`/`a_modifier` sont TOUJOURS des sous-ensembles du désiré (la
valeur cible est celle du désiré, jamais inventée) ; tout ce qui est présent côté réel
mais absent du désiré part en `a_retirer` (fenêtre fermée / hors portée / fantôme).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from swisspipe.core.domain.matrice import Matrice

# Une règle est identifiée par (ressource, groupe).
Regle = tuple[str, str]


@dataclass(frozen=True)
class DeltaProjection:
    """Écart désiré vs réel, en trois familles. Immuable.

    - a_creer    : règles absentes du réel -> à poser (valeur = désiré).
    - a_modifier : règles divergentes -> à ramener AU DÉSIRÉ (valeur = désiré).
    - a_retirer  : règles présentes au réel mais hors du désiré -> à retirer.
    """

    a_creer: Mapping[Regle, Matrice] = field(default_factory=dict)
    a_modifier: Mapping[Regle, Matrice] = field(default_factory=dict)
    a_retirer: frozenset[Regle] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "a_creer", dict(self.a_creer))
        object.__setattr__(self, "a_modifier", dict(self.a_modifier))
        object.__setattr__(self, "a_retirer", frozenset(self.a_retirer))

    @property
    def est_vide(self) -> bool:
        """True si désiré == réel : le reconcile est un no-op strict."""
        return not self.a_creer and not self.a_modifier and not self.a_retirer


def calculer_delta(
    desire: Mapping[str, Mapping[str, Matrice]],
    actuel: Mapping[str, Mapping[str, Matrice]],
) -> DeltaProjection:
    """Calcule l'écart désiré vs réel. Pur et déterministe.

    Idempotence garantie : calculer_delta(x, x).est_vide est True pour tout x.
    """
    a_creer: dict[Regle, Matrice] = {}
    a_modifier: dict[Regle, Matrice] = {}
    a_retirer: set[Regle] = set()

    for ressource, par_groupe in desire.items():
        reel = actuel.get(ressource, {})
        for groupe, matrice in par_groupe.items():
            courant = reel.get(groupe)
            if courant is None:
                a_creer[(ressource, groupe)] = matrice
            elif courant != matrice:
                a_modifier[(ressource, groupe)] = matrice  # cible = le DÉSIRÉ

    for ressource, par_groupe in actuel.items():
        voulu = desire.get(ressource, {})
        for groupe in par_groupe:
            if groupe not in voulu:
                a_retirer.add((ressource, groupe))

    return DeltaProjection(a_creer=a_creer, a_modifier=a_modifier, a_retirer=frozenset(a_retirer))
