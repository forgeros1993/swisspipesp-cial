"""Calcul des droits effectifs — moteur de résolution (spec §9.2, §9.3).

100% stdlib (importe le domaine : Octroi, Matrice, Mode). Aucun import externe
(garde-fou de pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

PÉRIMÈTRE DE CETTE ÉTAPE (L1) — héritage en cascade avec les trois modes, pour UN
groupe, sur une arborescence de ressources d'UN même espace, à instant figé (INV-3 :
les octrois passés en entrée sont déjà figés, ce service ne recalcule rien en temps
réel — il lit l'état fourni). NE FAIT PAS encore (placeholders architecturaux) :
- le plafond de montage (§9.3 point 3) -> L2 ;
- la résolution des rôles (§9.3 point 4) -> L2 ;
- le multi-espaces -> L2.

TROU DOCUMENTÉ — COMBINAISON MULTI-GROUPES (décision en attente, Cédric) :
un compte porte plusieurs groupes (personnel + organisationnels) ; sur une ressource,
chacun peut donner un droit différent. La règle de combinaison (le plus permissif
gagne ? un REFUSER sur un groupe écrase-t-il un octroi positif d'un autre ?) n'est PAS
tranchée par la spec disponible. `droit_effectif_compte` expose la future signature
mais lève NotImplementedError tant que la règle n'est pas fixée.

Représentation d'entrée (la plus simple, en structures stdlib) :
- `parents` : Mapping[str, str | None] — ressource_id -> parent_id (None = racine).
- `octrois` : Mapping[tuple[str, str], Octroi] — (ressource_id, groupe_id) -> Octroi.
Les ressources et groupes sont désignés par leur id opaque (str) ; on n'a pas besoin
des entités complètes ici.

Interprétations documentées (cf. analyse de session) :
- HERITER jusqu'à la racine sans aucune matrice -> AUCUN droit (deny-by-default).
- REFUSER se propage vers le bas par la chaîne d'héritage : un descendant en HERITER
  sous un ancêtre REFUSER est bloqué. Un descendant avec son PROPRE octroi explicite
  est résolu par son octroi d'abord (l'atteignabilité à travers un ancêtre invisible
  est une préoccupation de visibilité distincte, traitée en amont, pas ici).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from swisspipe.core.domain.matrice import Matrice, Mode
from swisspipe.core.domain.octroi import Octroi


@dataclass(frozen=True)
class DroitEffectif:
    """Résultat immuable d'une résolution de droit pour un groupe sur une ressource.

    Trois états :
    - accordé  : `matrice` non None, `bloque` False -> droit effectif = la matrice.
    - aucun    : `matrice` None, `bloque` False -> rien d'accordé (deny-by-default).
    - bloqué   : `bloque` True -> REFUSER, ressource non visible (matrice toujours None).
    """

    matrice: Matrice | None = None
    bloque: bool = False

    def __post_init__(self) -> None:
        if self.bloque and self.matrice is not None:
            raise ValueError("un droit bloqué (REFUSER) ne porte pas de matrice")

    @classmethod
    def accorde(cls, matrice: Matrice) -> DroitEffectif:
        return cls(matrice=matrice, bloque=False)

    @classmethod
    def aucun(cls) -> DroitEffectif:
        return cls(matrice=None, bloque=False)

    @classmethod
    def refuse(cls) -> DroitEffectif:
        return cls(matrice=None, bloque=True)

    @property
    def accessible(self) -> bool:
        """True si un droit positif s'applique (ni bloqué, ni vide)."""
        return self.matrice is not None and not self.bloque


def droit_effectif_groupe(
    ressource_id: str,
    groupe_id: str,
    parents: Mapping[str, str | None],
    octrois: Mapping[tuple[str, str], Octroi],
) -> DroitEffectif:
    """Droit effectif d'UN groupe sur UNE ressource, par héritage en cascade (§9.2/§9.3).

    Résolution, en partant de la ressource cible puis en remontant les parents :
    - REFUSER sur le nœud courant -> bloqué (court-circuite tout le reste, §9.3).
    - MODIFIER sur le nœud courant -> sa matrice propre s'applique.
    - HERITER (ou aucun octroi) -> on remonte au parent et on ré-applique.
    Si on dépasse la racine sans jamais trouver de matrice -> aucun droit.

    Pure et déterministe. Détecte les cycles (arbo malformée) et lève ValueError.
    """
    courant: str | None = ressource_id
    visites: set[str] = set()

    while courant is not None:
        if courant in visites:
            raise ValueError(f"cycle détecté dans l'arborescence des ressources via {courant!r}")
        visites.add(courant)

        octroi = octrois.get((courant, groupe_id))
        if octroi is not None:
            if octroi.est_bloquant:  # REFUSER
                return DroitEffectif.refuse()
            if octroi.mode is Mode.MODIFIER:
                # Garanti non None par l'invariant d'Octroi (MODIFIER => matrice).
                assert octroi.matrice is not None
                return DroitEffectif.accorde(octroi.matrice)
            # HERITER : on continue vers le parent.

        courant = parents.get(courant)

    # Remontée jusqu'au-dessus de la racine sans matrice -> deny-by-default.
    return DroitEffectif.aucun()


def droit_effectif_compte(
    groupe_ids: Iterable[str],
    ressource_id: str,
    parents: Mapping[str, str | None],
    octrois: Mapping[tuple[str, str], Octroi],
) -> DroitEffectif:
    """Droit effectif d'un COMPTE (plusieurs groupes) — combinaison. NON IMPLÉMENTÉ.

    Signature prête pour L2/quand la règle sera fixée. Combinera les résultats
    `droit_effectif_groupe` de chaque groupe du compte.
    """
    raise NotImplementedError(
        # DÉCISION EN ATTENTE (Cédric) : règle de combinaison multi-groupes, notamment
        # priorité d'un REFUSER sur un octroi positif d'un autre groupe. Voir §9.x.
        "combinaison multi-groupes non tranchée par la spec — voir docstring du module"
    )
