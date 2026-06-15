"""Runner SSH pour `occ` — exécute une commande occ Nextcloud sur le serveur.

Isolé et testable. L'adaptateur Nextcloud pilote Group Folders via la CLI `occ`
lancée en SSH (canal tranché : pas d'API HTTP externe). Tout le couplage « comment on
joint le serveur » vit ICI, nulle part ailleurs.

Config (surchargée par l'environnement, sinon constantes documentées) :
- NEXTCLOUD_SSH_ALIAS : alias SSH (dans ~/.ssh/config). Défaut : "vellis-nx".
- NEXTCLOUD_OCC_PATH  : répertoire contenant occ sur le serveur. Défaut :
  "~/sites/nx.vellis.ch" (le ~ est expansé par le shell distant).
"""

from __future__ import annotations

import os
import shlex
import subprocess

NEXTCLOUD_SSH_ALIAS = os.environ.get("NEXTCLOUD_SSH_ALIAS", "vellis-nx")
NEXTCLOUD_OCC_PATH = os.environ.get("NEXTCLOUD_OCC_PATH", "~/sites/nx.vellis.ch")

# Timeout par défaut (s) : occ peut être lent côté hébergeur.
_TIMEOUT_DEFAUT = 60


class OccError(RuntimeError):
    """Échec d'exécution d'une commande occ (SSH KO, occ non-zéro, timeout)."""


def executer_occ(
    args: list[str],
    *,
    alias: str | None = None,
    occ_dir: str | None = None,
    timeout: int = _TIMEOUT_DEFAUT,
) -> str:
    """Exécute `php occ <args>` sur le serveur via SSH et retourne stdout.

    `args` est une liste d'arguments (échappés individuellement). Lève OccError si la
    connexion échoue, si occ retourne un code non nul, ou en cas de timeout.
    """
    alias = alias or NEXTCLOUD_SSH_ALIAS
    occ_dir = occ_dir or NEXTCLOUD_OCC_PATH

    # `occ_dir` n'est pas quoté pour laisser le shell distant expanser le ~ ; c'est une
    # constante de config, pas une entrée utilisateur. Les args, eux, sont quotés.
    commande_distante = f"cd {occ_dir} && php occ " + " ".join(shlex.quote(a) for a in args)
    argv = ["ssh", "-o", "BatchMode=yes", alias, commande_distante]

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OccError(f"occ timeout ({timeout}s) : {' '.join(args)}") from exc
    except OSError as exc:
        raise OccError(f"ssh introuvable / non exécutable : {exc}") from exc

    if proc.returncode != 0:
        raise OccError(
            f"occ a échoué (exit {proc.returncode}) pour `{' '.join(args)}` : "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    return proc.stdout
