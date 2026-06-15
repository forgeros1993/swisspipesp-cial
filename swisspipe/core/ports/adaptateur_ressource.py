"""Port AdaptateurRessource — contrat que tout exécutant doit implémenter (spec §3.2).

Vit dans core/ports/, donc soumis au garde-fou de pureté : n'importe que la stdlib +
le domaine (Matrice). Aucune dépendance d'infra (test_core_purity.py, CLAUDE.md §1).

Ce port est la frontière hexagonale : le cœur décide « qui peut quoi », l'adaptateur
TRADUIT vers un exécutant concret (Nextcloud, mail, bâtiment) sans aucune logique
métier. La matrice reçue est DÉJÀ résolue par le cœur ; l'adaptateur ne fait que
l'appliquer.

Placement des DTO : `DescripteurRessource` et `DroitGroupe` décrivent le CONTRAT
(données échangées à la frontière), pas des concepts du domaine — ils vivent donc ici,
avec le Protocol. Seule `Matrice` (concept du domaine) est importée.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from swisspipe.core.domain.matrice import Matrice


@dataclass(frozen=True)
class DescripteurRessource:
    """Description d'une ressource à créer côté exécutant.

    PAS d'identifiant externe en entrée : c'est l'adaptateur qui le génère et le
    retourne (le cœur ne connaît jamais l'id externe — agnosticité §3.3).
    """

    type: str  # folder, mailbox, door, … (chaîne libre, extensible)
    chemin: str
    nom: str


@dataclass(frozen=True)
class DroitGroupe:
    """Un couple (groupe, matrice) de l'état de droits désiré, résolu par le cœur.

    Frozen/hachable -> un état de droits = un frozenset[DroitGroupe], comparable et
    indépendant de l'ordre (utile à l'idempotence et à la détection de dérive).
    """

    groupe_id: str
    matrice: Matrice


@runtime_checkable
class AdaptateurRessource(Protocol):
    """Contrat minimal d'un adaptateur de ressource (spec §3.2).

    runtime_checkable : permet un isinstance structurel dans les tests.
    """

    def creer_ressource(self, descripteur: DescripteurRessource) -> str:
        """Crée la ressource côté exécutant et retourne son identifiant EXTERNE."""
        ...

    def archiver_ressource(self, cle_externe: str) -> None:
        """Archive (réversible). JAMAIS de suppression dure (INV-5 : l'API n'efface pas)."""
        ...

    def renommer_ressource(self, cle_externe: str, nouveau_nom: str) -> None:
        """Renomme la ressource côté exécutant."""
        ...

    def appliquer_droits(self, cle_externe: str, droits: Collection[DroitGroupe]) -> None:
        """Applique l'état COMPLET désiré (pas un diff), idempotent.

        Reçoit l'état cible déjà résolu par le cœur. L'adaptateur ne fait AUCUNE
        logique métier : il traduit l'état tel quel vers l'exécutant. Ré-appliquer le
        même état doit produire le même résultat.
        """
        ...

    def lire_droits_effectifs(self, cle_externe: str) -> frozenset[DroitGroupe]:
        """Relit l'état RÉEL côté exécutant (réconciliation / détection de dérive).

        Retourne la même forme que celle attendue par `appliquer_droits`.
        """
        ...
