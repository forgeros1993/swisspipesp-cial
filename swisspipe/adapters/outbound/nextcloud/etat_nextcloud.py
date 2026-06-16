"""Lecteur d'état Nextcloud — version NC + version/état de l'app Group Folders.

Hors du port `AdaptateurRessource` (qui ne concerne que les ressources) : lire l'état de
l'exécutant (version, app activée/désactivée) est une autre préoccupation. Infra occ via
le runner existant. Sert à la détection de changement (upgrade / réactivation de GF) qui
déclenche la réconciliation en masse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from swisspipe.adapters.outbound.nextcloud.occ_runner import executer_occ


@dataclass(frozen=True)
class EtatNextcloud:
    """Instantané de l'état Nextcloud pertinent pour la détection de changement."""

    nc_version: str
    gf_version: str | None  # None si Group Folders n'est ni activé ni désactivé (absent)
    gf_active: bool

    def vers_dict(self) -> dict[str, Any]:
        return {
            "nc_version": self.nc_version,
            "gf_version": self.gf_version,
            "gf_active": self.gf_active,
        }

    @classmethod
    def depuis_dict(cls, data: dict[str, Any]) -> EtatNextcloud:
        return cls(
            nc_version=str(data["nc_version"]),
            gf_version=data.get("gf_version"),
            gf_active=bool(data["gf_active"]),
        )


def lire_etat_nextcloud(
    *, alias: str | None = None, occ_dir: str | None = None
) -> EtatNextcloud:
    """Lit l'état réel via `occ status` + `occ app:list` (JSON). Lecture seule.

    - nc_version : `status.versionstring`.
    - gf_active : "groupfolders" présent dans la section `enabled` d'app:list.
    - gf_version : version dans `enabled` sinon dans `disabled` (None si absente des deux).
    """
    statut = json.loads(executer_occ(["status", "--output=json"], alias=alias, occ_dir=occ_dir))
    apps = json.loads(executer_occ(["app:list", "--output=json"], alias=alias, occ_dir=occ_dir))

    enabled = apps.get("enabled", {})
    disabled = apps.get("disabled", {})
    gf_active = "groupfolders" in enabled
    gf_version = enabled.get("groupfolders")
    if gf_version is None:
        gf_version = disabled.get("groupfolders")

    return EtatNextcloud(
        nc_version=str(statut["versionstring"]),
        gf_version=gf_version,
        gf_active=gf_active,
    )
