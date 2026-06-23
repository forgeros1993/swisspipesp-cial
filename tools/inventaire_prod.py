"""Inventaire LECTURE SEULE de l'état d'accès réel Nextcloud (T3, phase 3.a).

AUCUNE écriture serveur : uniquement `groupfolders:list` (occ) + SELECT (sql_runner, garde
lecture seule). Produit une PHOTO structurée (dict) = source de vérité pour le seed (Option A :
l'ACL/binding RÉEL, jamais le modèle fantôme custom_tags_permissions).

Usage : python -m tools.inventaire_prod   (imprime un résumé + un JSON sur stdout)
"""

from __future__ import annotations

import json
from typing import Any

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import AdaptateurNextcloud
from swisspipe.adapters.outbound.nextcloud.sql_runner import executer_select
from swisspipe.adapters.outbound.nextcloud.traduction import permissions_nextcloud_vers_matrice


def photographier() -> dict[str, Any]:
    a = AdaptateurNextcloud("", "", "")
    folders = a._lister_folders()  # occ groupfolders:list (LECTURE)

    societes: list[dict[str, Any]] = []
    for f in folders:
        groups_list: dict[str, int] = f.get("groups_list") or {}
        groupes = []
        for nom, masque in groups_list.items():
            mat = permissions_nextcloud_vers_matrice(int(masque))
            groupes.append(
                {
                    "groupe": nom,
                    "masque": int(masque),
                    "matrice": mat.vers_jsonb() if mat is not None else None,
                }
            )
        societes.append(
            {
                "folder_id": str(f["id"]),
                "mount_point": f.get("mountPoint"),
                "acl_fine_active": bool(f.get("acl")),
                "groupes": groupes,
            }
        )

    # ACL fine par type (INV-4 : y a-t-il des droits 'user' directs ?). Lecture seule.
    acl_par_type = executer_select(
        "SELECT mapping_type, COUNT(*) AS n FROM {p}group_folders_acl GROUP BY mapping_type"
    )
    # Hiérarchie custom_tags (Société/Département) — structure organisationnelle.
    hierarchie = executer_select(
        "SELECT category_type, COUNT(*) AS n FROM {p}custom_tags_hierarchy GROUP BY category_type"
    )

    return {
        "societes": societes,
        "acl_fine_par_type": acl_par_type,  # [] = aucune ACL fine -> 0 user-direct
        "hierarchie_custom_tags": hierarchie,
    }


def _resume(photo: dict[str, Any]) -> str:
    soc = photo["societes"]
    n_groupes_total = sum(len(s["groupes"]) for s in soc)
    n_acl_fine = sum(1 for s in soc if s["acl_fine_active"])
    user_direct = sum(r["n"] for r in photo["acl_fine_par_type"] if r["mapping_type"] == "user")
    return (
        f"societes(folders)={len(soc)} | octrois_reels(groupe x folder)={n_groupes_total} | "
        f"folders_acl_fine_active={n_acl_fine} | droits_user_direct(INV-4)={user_direct} | "
        f"hierarchie={photo['hierarchie_custom_tags']}"
    )


if __name__ == "__main__":
    p = photographier()
    print("RESUME:", _resume(p))
    print(json.dumps(p, indent=2, ensure_ascii=False))
