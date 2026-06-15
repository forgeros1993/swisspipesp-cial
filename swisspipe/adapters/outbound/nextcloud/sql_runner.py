"""Runner SQL LECTURE SEULE — exécute un SELECT sur la base Nextcloud via SSH + PDO.

Recours documenté : `occ` est aveugle sur les règles ACL fines de Group Folders
(`group_folders_acl`), seul le SQL les expose. Tout le couplage « SQL via SSH » est
isolé ici (même philosophie qu'occ_runner). Réutilise la config SSH d'occ_runner.

GARDE-FOU : seules les requêtes SELECT sont acceptées (vérifié côté Python). Aucune
écriture via ce runner.

⚠️ Piège Nextcloud : `config/config.php` **définit** `$CONFIG` (il ne le *retourne*
pas). On utilise donc `require` + lecture de `$CONFIG`, JAMAIS `include` + valeur de
retour. Les identifiants de base restent côté serveur, jamais imprimés.

Le placeholder `{p}` dans la requête est remplacé côté serveur par le vrai préfixe de
tables (`$CONFIG['dbtableprefix']`). Les paramètres sont passés séparément à PDO
(prepare/execute), jamais concaténés.
"""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
from collections.abc import Sequence
from typing import Any

from swisspipe.adapters.outbound.nextcloud.occ_runner import (
    NEXTCLOUD_OCC_PATH,
    NEXTCLOUD_SSH_ALIAS,
)

_TIMEOUT_DEFAUT = 60

# Script PHP exécuté sur le serveur (lu sur stdin par `php`). Lit le payload (requête +
# params) depuis une variable d'environnement encodée base64, exécute en prepared
# statement, renvoie les lignes en JSON. config.php DÉFINIT $CONFIG (require, pas include).
_PHP_LECTEUR = r"""<?php
error_reporting(E_ERROR);
$CONFIG = [];
require 'config/config.php';
$payload = json_decode(base64_decode(getenv('SP_SQL_PAYLOAD')), true);
$query = (string)($payload['query'] ?? '');
$params = $payload['params'] ?? [];
$prefix = $CONFIG['dbtableprefix'] ?? 'oc_';
$query = str_replace('{p}', $prefix, $query);
$h = $CONFIG['dbhost']; $port = 3306;
if (strpos($h, ':') !== false) { list($h, $port) = explode(':', $h, 2); }
$pdo = new PDO("mysql:host=$h;port=$port;dbname={$CONFIG['dbname']}",
               $CONFIG['dbuser'], $CONFIG['dbpassword'],
               [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
$st = $pdo->prepare($query);
$st->execute(array_values($params));
echo json_encode($st->fetchAll(PDO::FETCH_ASSOC));
"""


class SqlError(RuntimeError):
    """Échec d'exécution SQL (SSH KO, requête non-SELECT, erreur PDO, timeout)."""


def executer_select(
    requete: str,
    params: Sequence[Any] = (),
    *,
    alias: str | None = None,
    occ_dir: str | None = None,
    timeout: int = _TIMEOUT_DEFAUT,
) -> list[dict[str, Any]]:
    """Exécute un SELECT paramétré et retourne les lignes (liste de dicts).

    `requete` doit commencer par SELECT (garde-fou lecture seule). `{p}` y est remplacé
    par le préfixe de tables côté serveur. Les `params` sont liés via PDO (pas de
    concaténation). Lève SqlError sinon.
    """
    if not requete.lstrip().upper().startswith("SELECT"):
        raise SqlError("sql_runner : seules les requêtes SELECT sont autorisées (lecture seule)")

    alias = alias or NEXTCLOUD_SSH_ALIAS
    occ_dir = occ_dir or NEXTCLOUD_OCC_PATH

    payload = base64.b64encode(
        json.dumps({"query": requete, "params": list(params)}).encode("utf-8")
    ).decode("ascii")

    commande_distante = f"cd {occ_dir} && SP_SQL_PAYLOAD={shlex.quote(payload)} php"
    argv = ["ssh", "-o", "BatchMode=yes", alias, commande_distante]

    try:
        proc = subprocess.run(
            argv,
            input=_PHP_LECTEUR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SqlError(f"SQL timeout ({timeout}s)") from exc
    except OSError as exc:
        raise SqlError(f"ssh introuvable / non exécutable : {exc}") from exc

    if proc.returncode != 0:
        raise SqlError(f"SQL a échoué (exit {proc.returncode}) : {proc.stderr.strip()}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SqlError(f"sortie SQL non-JSON : {proc.stdout[:200]!r}") from exc
