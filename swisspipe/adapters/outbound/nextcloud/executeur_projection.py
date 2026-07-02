"""Exécuteur occ RÉEL de la projection transverse — implémente ExecuteurProjection.

Fine couche I/O : la logique (delta, plan de commandes) vit dans le cœur
(delta_projection) et dans planifier_reconcile_occ (pur, testé hermétiquement). Ici on
ne fait que LIRE l'état réel (SQL, lecture seule) et EXÉCUTER les commandes du plan.

INV-5 : ne détruit JAMAIS de données — le plan ne contient que des poses/clears de
règles ACL et des ajouts/retraits d'accès base (groupfolders:group). Aucun
groupfolders:delete, aucun rm.
"""

from __future__ import annotations

import json
from typing import Any

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import (
    lire_etat_acl_transverse,
    planifier_reconcile_occ,
)
from swisspipe.adapters.outbound.nextcloud.occ_runner import executer_occ
from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.services.delta_projection import DeltaProjection


class ExecuteurProjectionOcc:
    """Exécute le reconcile ACL d'UN Group Folder (id occ) via SSH.

    L'appelant est responsable du CIBLAGE (garde zztest_ côté pilote de test) : cette
    classe n'écrit que sur le folder `gf_id` reçu.
    """

    def __init__(self, gf_id: str, *, alias: str | None = None, occ_dir: str | None = None) -> None:
        self._gf_id = str(gf_id)
        self._alias = alias
        self._occ_dir = occ_dir
        self.commandes_executees: list[tuple[str, ...]] = []

    # --- lectures (aucune mutation) ------------------------------------------

    def _folder(self) -> dict[str, Any]:
        folders = json.loads(
            executer_occ(
                ["groupfolders:list", "--output=json"], alias=self._alias, occ_dir=self._occ_dir
            )
        )
        items = list(folders.values()) if isinstance(folders, dict) else folders
        for f in items:
            if str(f.get("id")) == self._gf_id:
                return dict(f)
        raise KeyError(f"Group Folder introuvable : {self._gf_id!r}")

    def lire_etat(self) -> dict[str, dict[str, Matrice]]:
        """État ACL réel du GF, décodé (SQL group_folders_acl ⋈ filecache). Lecture seule."""
        storage_id = int(self._folder()["storageId"])
        return lire_etat_acl_transverse(storage_id, alias=self._alias, occ_dir=self._occ_dir)

    def lire_acces_base(self) -> frozenset[str]:
        """Groupes ayant l'accès base au GF (groups_list). Lecture seule."""
        return frozenset((self._folder().get("groups_list") or {}).keys())

    # --- exécution du delta ---------------------------------------------------

    def appliquer_delta(self, delta: DeltaProjection, groupes_desires: frozenset[str]) -> None:
        """Exécute UNIQUEMENT le delta : plan pur (planifier_reconcile_occ) puis occ.

        Accès base : ajouté pour les groupes désirés qui ne l'ont pas ; retiré UNIQUEMENT
        pour les groupes dont ce delta retire des règles et qui ne restent pas désirés.
        Un groupe TIERS (accès base posé hors de nous, sans règle gérée) n'est JAMAIS
        touché — le reconcile n'exécute que son delta.
        """
        base_actuels = self.lire_acces_base()
        retires_base = {g for (_nom, g) in delta.a_retirer} - groupes_desires
        plan = planifier_reconcile_occ(
            self._gf_id,
            delta,
            groupes_a_ajouter=sorted(groupes_desires - base_actuels),
            groupes_a_retirer=sorted(retires_base & base_actuels),
        )
        for commande in plan.commandes:
            executer_occ(list(commande), alias=self._alias, occ_dir=self._occ_dir)
            self.commandes_executees.append(commande)
