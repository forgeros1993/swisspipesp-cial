"""Adaptateur Nextcloud — Group Folders via `occ` en SSH.

Implémente le même Protocol AdaptateurRessource que le fake — c'est ce qui prouve
l'agnosticité du cœur. Hors cœur : peut importer ce qu'il veut. Canal tranché : `occ`
exécuté en SSH (cf. occ_runner) ; pas d'API HTTP externe. Le SQL brut n'est utilisé
qu'en dernier recours et serait documenté (pas nécessaire ici).

Principe §3.2 : l'adaptateur ne fait AUCUNE logique métier, il TRADUIT. La matrice
reçue/relue est convertie via traduction.py (sens aller et inverse).

État des tranches :
- Tranche A (ici) : câblage + `lire_droits_effectifs` (LECTURE seule).
- Tranche B/C : `creer/archiver/renommer/appliquer` (écritures) -> NotImplementedError.
"""

from __future__ import annotations

import json
from collections.abc import Collection

from swisspipe.adapters.outbound.nextcloud.occ_runner import executer_occ
from swisspipe.adapters.outbound.nextcloud.traduction import (
    matrice_vers_permissions_nextcloud,
    permissions_nextcloud_vers_matrice,
)
from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource, DroitGroupe


class AdaptateurNextcloud:
    """Adaptateur Group Folders. Pilote `occ` en SSH (config via occ_runner).

    `base_url`/`utilisateur`/`mot_de_passe` (HTTP) sont conservés pour de futurs usages
    (OCS/WebDAV) mais ne servent PAS au canal occ. `ssh_alias`/`occ_dir` permettent de
    surcharger la cible SSH ; à défaut, les valeurs d'occ_runner (env ou constantes).
    """

    def __init__(
        self,
        base_url: str,
        utilisateur: str,
        mot_de_passe: str,
        *,
        ssh_alias: str | None = None,
        occ_dir: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._utilisateur = utilisateur
        self._mot_de_passe = mot_de_passe
        self._ssh_alias = ssh_alias
        self._occ_dir = occ_dir

    # --- Traduction (RÉELLE, testable sans serveur) --------------------------

    def traduire_droits(self, droits: Collection[DroitGroupe]) -> dict[str, int]:
        """État de droits -> {groupe_id: masque_permissions_nextcloud}. Pur."""
        return {d.groupe_id: matrice_vers_permissions_nextcloud(d.matrice) for d in droits}

    # --- Contrat AdaptateurRessource -----------------------------------------

    def creer_ressource(self, descripteur: DescripteurRessource) -> str:
        # TRANCHE B/C : occ groupfolders:create.
        raise NotImplementedError("écriture Nextcloud — Tranche B/C")

    def archiver_ressource(self, cle_externe: str) -> None:
        # TRANCHE B/C : archivage réversible (jamais de suppression dure, INV-5).
        raise NotImplementedError("écriture Nextcloud — Tranche B/C")

    def renommer_ressource(self, cle_externe: str, nouveau_nom: str) -> None:
        # TRANCHE B/C : occ groupfolders:rename.
        raise NotImplementedError("écriture Nextcloud — Tranche B/C")

    def appliquer_droits(self, cle_externe: str, droits: Collection[DroitGroupe]) -> None:
        # TRANCHE B/C : occ groupfolders:group / groupfolders:permissions.
        raise NotImplementedError("écriture Nextcloud — Tranche B/C")

    def lire_droits_effectifs(self, cle_externe: str) -> frozenset[DroitGroupe]:
        """Relit l'état réel d'un Group Folder (réconciliation / détection de dérive).

        `cle_externe` = id du Group Folder (str). Lit `occ groupfolders:list
        --output=json`, trouve le folder, traduit chaque (groupe -> masque) via le sens
        inverse. Un masque sans bit read -> aucun droit, le groupe est omis du résultat.
        """
        sortie = executer_occ(
            ["groupfolders:list", "--output=json"],
            alias=self._ssh_alias,
            occ_dir=self._occ_dir,
        )
        folders = json.loads(sortie)

        cible = next((f for f in folders if str(f.get("id")) == str(cle_externe)), None)
        if cible is None:
            raise KeyError(f"Group Folder introuvable : {cle_externe!r}")

        droits: set[DroitGroupe] = set()
        for groupe_id, masque in cible.get("groups_list", {}).items():
            matrice = permissions_nextcloud_vers_matrice(int(masque))
            if matrice is not None:
                droits.add(DroitGroupe(groupe_id, matrice))
        return frozenset(droits)
