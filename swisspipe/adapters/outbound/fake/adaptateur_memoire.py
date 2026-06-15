"""Adaptateur fake en mémoire — implémentation de référence du port (tests).

Implémente le Protocol AdaptateurRessource en stockant tout dans des dicts Python.
Sert aux tests et de référence exécutable du contrat (§3.2). adapters/ n'est PAS
soumis au garde-fou de pureté du cœur, mais cet adaptateur n'a de toute façon besoin
que de la stdlib + le port.

Comportement :
- creer_ressource : génère une clé externe (uuid stdlib) et mémorise la ressource.
- appliquer_droits : REMPLACE intégralement l'état des droits (idempotent).
- lire_droits_effectifs : relit l'état stocké.
- archiver_ressource : marque archivé SANS supprimer (la clé reste, avec un flag).
- renommer_ressource : met à jour le nom.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from uuid import uuid4

from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource, DroitGroupe


@dataclass
class _RessourceMemoire:
    type: str
    chemin: str
    nom: str
    archivee: bool = False
    droits: frozenset[DroitGroupe] = field(default_factory=frozenset)


class AdaptateurMemoire:
    """Implémentation en mémoire d'AdaptateurRessource. État dans des dicts."""

    def __init__(self) -> None:
        self._ressources: dict[str, _RessourceMemoire] = {}

    def _exiger(self, cle_externe: str) -> _RessourceMemoire:
        if cle_externe not in self._ressources:
            raise KeyError(f"ressource inconnue : {cle_externe!r}")
        return self._ressources[cle_externe]

    # --- Contrat AdaptateurRessource -----------------------------------------

    def creer_ressource(self, descripteur: DescripteurRessource) -> str:
        cle_externe = uuid4().hex
        self._ressources[cle_externe] = _RessourceMemoire(
            type=descripteur.type, chemin=descripteur.chemin, nom=descripteur.nom
        )
        return cle_externe

    def archiver_ressource(self, cle_externe: str) -> None:
        # Archivage réversible : on marque, on ne supprime jamais (INV-5).
        self._exiger(cle_externe).archivee = True

    def renommer_ressource(self, cle_externe: str, nouveau_nom: str) -> None:
        self._exiger(cle_externe).nom = nouveau_nom

    def appliquer_droits(self, cle_externe: str, droits: Collection[DroitGroupe]) -> None:
        # Remplacement intégral -> idempotent (même état réappliqué = même résultat).
        self._exiger(cle_externe).droits = frozenset(droits)

    def lire_droits_effectifs(self, cle_externe: str) -> frozenset[DroitGroupe]:
        return self._exiger(cle_externe).droits

    # --- Helpers d'inspection (tests uniquement, hors Protocol) ---------------

    def est_archivee(self, cle_externe: str) -> bool:
        return self._exiger(cle_externe).archivee

    def nom_courant(self, cle_externe: str) -> str:
        return self._exiger(cle_externe).nom
