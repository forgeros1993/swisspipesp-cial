"""Value object Instance — un projet réel = un espace transverse (spec §5.2/§5.3).

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Une Instance matérialise un Modèle (modele.py) : elle porte les métadonnées remplies
et le SQUELETTE imposé sous forme de ressources ABSTRAITES (pas de vrais dossiers
Nextcloud — l'agnosticité du cœur, §3.3). Le squelette est GELÉ (§5.3) : ni renommé
ni supprimé, MÊME par un droit de Suppression — c'est une règle STRUCTURELLE qui prime
sur tout droit. Créer une instance ≠ nommer une personne (INV-5) : aucune identité ici.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.modele import Modele
from swisspipe.core.domain.ressource import Ressource

# Nature d'un espace matérialisé par un modèle. Constante domaine (le cœur ne connaît
# pas l'enum de persistance ; token cohérent avec NatureEspace.TRANSVERSE côté DB).
NATURE_TRANSVERSE = "transverse"


class SqueletteGeleError(Exception):
    """Tentative de renommer/supprimer un dossier du squelette imposé (gelé, §5.3).

    Gel STRUCTUREL : il prime sur tout droit, y compris Suppression. Se corrige en
    changeant le Modèle, jamais en forçant l'Instance.
    """


@dataclass(frozen=True)
class Instance:
    """Projet réel matérialisant un Modèle. `nature` toujours 'transverse'.

    `metadonnees` : dict libre conforme au schéma du modèle (validé à l'instanciation).
    `ressources_squelette` : dossiers imposés, GELÉS. `ressources_libres` : dossiers
    ajoutés librement (si le modèle l'autorise), eux supprimables/renommables.
    `cle_reconciliation` : RÉSERVÉ au futur ID ERP (Odoo) — champ nu, aucune logique ici.
    """

    id: str
    modele_id: str
    nom: str
    metadonnees: Mapping[str, Any]
    cle_reconciliation: str | None = None
    ressources_squelette: tuple[Ressource, ...] = field(default_factory=tuple)
    ressources_libres: tuple[Ressource, ...] = field(default_factory=tuple)
    dossiers_libres_autorises: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadonnees", dict(self.metadonnees))
        object.__setattr__(self, "ressources_squelette", tuple(self.ressources_squelette))
        object.__setattr__(self, "ressources_libres", tuple(self.ressources_libres))

    @property
    def nature(self) -> str:
        """Une instance est TOUJOURS un espace transverse (spec §5.2)."""
        return NATURE_TRANSVERSE

    @property
    def ressources(self) -> tuple[Ressource, ...]:
        """Toutes les ressources : squelette imposé + dossiers libres."""
        return self.ressources_squelette + self.ressources_libres

    def est_squelette(self, ressource_id: str) -> bool:
        """Vrai si `ressource_id` appartient au squelette imposé (donc gelé)."""
        return any(r.id == ressource_id for r in self.ressources_squelette)

    def ajouter_ressource_libre(self, ressource: Ressource) -> Instance:
        """Ajoute un dossier libre — refus (ValueError) si le modèle l'interdit."""
        if not self.dossiers_libres_autorises:
            raise ValueError("ce modèle n'autorise pas les dossiers libres")
        return replace(self, ressources_libres=(*self.ressources_libres, ressource))

    def supprimer_ressource(
        self, ressource_id: str, *, matrice_droit: Matrice | None = None
    ) -> Instance:
        """Supprime une ressource — REFUS si elle appartient au squelette gelé (§5.3).

        `matrice_droit` est accepté pour PROUVER que le gel est structurel : même une
        Matrice de Suppression ne lève PAS le gel (le paramètre est ignoré pour le
        squelette). Un dossier libre, lui, est bien supprimé.
        """
        if self.est_squelette(ressource_id):
            raise SqueletteGeleError(
                f"ressource {ressource_id!r} du squelette imposé : gel structurel, "
                "suppression interdite même avec un droit de Suppression (§5.3)"
            )
        libres = tuple(r for r in self.ressources_libres if r.id != ressource_id)
        if len(libres) == len(self.ressources_libres):
            raise LookupError(f"ressource {ressource_id!r} inconnue de l'instance")
        return replace(self, ressources_libres=libres)

    def renommer_ressource(self, ressource_id: str, nouveau_chemin: str) -> Instance:
        """Renomme une ressource — REFUS si elle appartient au squelette gelé (§5.3)."""
        if self.est_squelette(ressource_id):
            raise SqueletteGeleError(
                f"ressource {ressource_id!r} du squelette imposé : gel structurel, "
                "renommage interdit (§5.3)"
            )
        trouve = False
        libres: list[Ressource] = []
        for r in self.ressources_libres:
            if r.id == ressource_id:
                trouve = True
                libres.append(replace(r, chemin=nouveau_chemin))
            else:
                libres.append(r)
        if not trouve:
            raise LookupError(f"ressource {ressource_id!r} inconnue de l'instance")
        return replace(self, ressources_libres=tuple(libres))


def instancier(
    modele: Modele,
    *,
    nom: str,
    metadonnees: Mapping[str, Any],
    instance_id: str,
    cle_reconciliation: str | None = None,
) -> Instance:
    """Matérialise un Modèle en Instance (spec §5.2). Pur et déterministe.

    Valide d'abord les métadonnées contre le schéma du modèle (lève ValueError si non
    conformes — AVANT toute matérialisation). Fabrique une ressource abstraite par
    dossier imposé, avec un id DÉTERMINISTE (`{instance_id}:{cle}`) — pas d'UUID ni
    d'aléa dans le cœur. Chemin = `/{libelle}`. Ne nomme personne (INV-5).
    """
    modele.valider_metadonnees(metadonnees)
    squelette = tuple(
        Ressource(
            id=f"{instance_id}:{d.cle}",
            type="folder",
            chemin=f"/{d.libelle}",
            espace_id=instance_id,
        )
        for d in modele.arborescence_imposee.dossiers
    )
    return Instance(
        id=instance_id,
        modele_id=modele.id,
        nom=nom,
        metadonnees=metadonnees,
        cle_reconciliation=cle_reconciliation,
        ressources_squelette=squelette,
        dossiers_libres_autorises=modele.arborescence_imposee.dossiers_libres_autorises,
    )
