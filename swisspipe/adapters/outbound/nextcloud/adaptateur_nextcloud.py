"""Adaptateur Nextcloud — squelette (tranche 1/N : traduction, SANS serveur).

Implémentera le même Protocol AdaptateurRessource que le fake — c'est ce qui prouve
l'agnosticité du cœur. Hors cœur : autorisé à importer des libs externes (réseau) aux
tranches ultérieures ; cette tranche n'en a aucune.

CETTE TRANCHE : la traduction matrice -> permissions Nextcloud est RÉELLE et testable
(`traduire_droits`). Tout ce qui nécessite le réseau (OCS / WebDAV / Group Folders)
lève NotImplementedError et sera codé plus tard.
"""

from __future__ import annotations

from collections.abc import Collection

from swisspipe.adapters.outbound.nextcloud.traduction import matrice_vers_permissions_nextcloud
from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource, DroitGroupe


class AdaptateurNextcloud:
    """Adaptateur Group Folders. Config de connexion stockée mais inutilisée ici.

    Les paramètres (base_url, utilisateur, mot_de_passe) viendront d'un .env local à la
    tranche réseau ; on ne s'en sert pas encore.
    """

    def __init__(self, base_url: str, utilisateur: str, mot_de_passe: str) -> None:
        self._base_url = base_url
        self._utilisateur = utilisateur
        self._mot_de_passe = mot_de_passe

    # --- Traduction (RÉELLE, testable sans serveur) --------------------------

    def traduire_droits(self, droits: Collection[DroitGroupe]) -> dict[str, int]:
        """État de droits -> {groupe_id: masque_permissions_nextcloud}. Pur.

        C'est le rôle propre de l'adaptateur : traduire une décision déjà résolue par
        le cœur vers la représentation de l'exécutant.
        """
        return {d.groupe_id: matrice_vers_permissions_nextcloud(d.matrice) for d in droits}

    # --- Contrat AdaptateurRessource -----------------------------------------

    def creer_ressource(self, descripteur: DescripteurRessource) -> str:
        # TRANCHE ULTÉRIEURE : appel réel Nextcloud (OCS Group Folders : créer le dossier).
        raise NotImplementedError("appel réseau Nextcloud — tranche ultérieure")

    def archiver_ressource(self, cle_externe: str) -> None:
        # TRANCHE ULTÉRIEURE : appel réel Nextcloud (OCS/WebDAV : archivage réversible).
        raise NotImplementedError("appel réseau Nextcloud — tranche ultérieure")

    def renommer_ressource(self, cle_externe: str, nouveau_nom: str) -> None:
        # TRANCHE ULTÉRIEURE : appel réel Nextcloud (WebDAV MOVE / OCS rename).
        raise NotImplementedError("appel réseau Nextcloud — tranche ultérieure")

    def appliquer_droits(self, cle_externe: str, droits: Collection[DroitGroupe]) -> None:
        # (a) traduction : RÉELLE et testable.
        permissions = self.traduire_droits(droits)
        # (b) envoi : stubbé.
        self._envoyer_permissions(cle_externe, permissions)

    def lire_droits_effectifs(self, cle_externe: str) -> frozenset[DroitGroupe]:
        # TRANCHE ULTÉRIEURE : relire l'état réel côté Nextcloud (réconciliation/dérive).
        raise NotImplementedError("appel réseau Nextcloud — tranche ultérieure")

    # --- Envoi réseau (stub) -------------------------------------------------

    def _envoyer_permissions(self, cle_externe: str, permissions: dict[str, int]) -> None:
        # TRANCHE ULTÉRIEURE : pousser les permissions vers Nextcloud (OCS Group Folders).
        raise NotImplementedError("appel réseau Nextcloud — tranche ultérieure")
