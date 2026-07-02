"""Teardown de la DÉMO dimensionnelle Alpha/Beta/Gamma — À LANCER MANUELLEMENT.

Supprime UNIQUEMENT les objets de démo : Group Folders au mountPoint `demo_*`, groupes
Nextcloud `grp_demo_*`, et (optionnel, --drop-db) la base locale swisspipe_demo.
GARDES : jamais un GF d'id 4-20 (les 17 vraies sociétés + ZZ_TEST_LOCK_SYNC), jamais un
objet hors namespace demo_. custom_tags n'est pas concerné (aucune interaction).

Usage :
    .venv/bin/python scripts/teardown_demo.py            # dry-run (liste ce qui serait supprimé)
    .venv/bin/python scripts/teardown_demo.py --apply    # supprime GF demo_ + groupes grp_demo_
    .venv/bin/python scripts/teardown_demo.py --apply --drop-db   # + DROP DATABASE swisspipe_demo
                                                        (nécessite un rôle avec le droit DROP)
"""

from __future__ import annotations

import argparse
import json
import sys

from swisspipe.adapters.outbound.nextcloud.occ_runner import executer_occ as occ


def gflist() -> list[dict]:
    folders = json.loads(occ(["groupfolders:list", "--output=json"]))
    return list(folders.values()) if isinstance(folders, dict) else folders


def groupes_nc() -> list[str]:
    gj = json.loads(occ(["group:list", "--output=json"]))
    return list(gj.keys()) if isinstance(gj, dict) else gj


def main() -> int:
    p = argparse.ArgumentParser(description="Teardown démo (namespace demo_ uniquement)")
    p.add_argument("--apply", action="store_true", help="supprimer réellement (défaut: dry-run)")
    p.add_argument("--drop-db", action="store_true", help="DROP DATABASE swisspipe_demo aussi")
    args = p.parse_args()

    cibles_gf = [
        (int(f["id"]), f["mountPoint"])
        for f in gflist()
        if str(f.get("mountPoint", "")).startswith("demo_")
    ]
    cibles_grp = [g for g in groupes_nc() if g.startswith("grp_demo_")]

    print(f"GF demo_ ciblés   : {cibles_gf or 'aucun'}")
    print(f"groupes grp_demo_ : {cibles_grp or 'aucun'}")

    for gid, mount in cibles_gf:
        # GARDE ABSOLUE : jamais la plage prod, jamais hors namespace.
        if 4 <= gid <= 20 or not mount.startswith("demo_"):
            print(f"REFUS: GF {gid} ({mount!r}) hors namespace demo_ — STOP", file=sys.stderr)
            return 1

    if not args.apply:
        print("\nDRY-RUN — rien supprimé. Relancer avec --apply.")
        return 0

    for gid, mount in cibles_gf:
        occ(["groupfolders:delete", str(gid), "-f"])
        print(f"supprimé: GF {gid} ({mount})")
    for grp in cibles_grp:
        occ(["group:delete", grp])
        print(f"supprimé: groupe {grp}")

    if args.drop_db:
        print("DROP DATABASE swisspipe_demo : à lancer avec un rôle habilité, ex.")
        print("  sudo -u postgres dropdb swisspipe_demo")

    print("Teardown démo terminé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
