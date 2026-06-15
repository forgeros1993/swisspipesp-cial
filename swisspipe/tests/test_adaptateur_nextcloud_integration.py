"""Test d'intégration de l'adaptateur Nextcloud — cycle de vie sur folder JETABLE.

Touche le vrai serveur (écritures). Skip si pas d'accès SSH. Ne manipule QUE le folder
qu'il crée (préfixe zztest_, id >= 21) — JAMAIS un folder de prod (ids 4-20). Nettoyage
garanti par `finally` (delete --force, OK ici car c'est du nettoyage de test, pas
l'archivage de prod).
"""

from __future__ import annotations

import subprocess

import pytest

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import AdaptateurNextcloud
from swisspipe.adapters.outbound.nextcloud.occ_runner import NEXTCLOUD_SSH_ALIAS, executer_occ
from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource

# Au-delà des ids de prod (4-20). Garde-fou : le test refuse de toucher un id de prod.
_ID_PROD_MAX = 20


def _serveur_accessible() -> bool:
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", NEXTCLOUD_SSH_ALIAS, "true"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def test_cycle_de_vie_creer_renommer_archiver() -> None:
    if not _serveur_accessible():
        pytest.skip(f"serveur SSH '{NEXTCLOUD_SSH_ALIAS}' injoignable — test d'intégration skippé")

    adaptateur = AdaptateurNextcloud("", "", "")
    cle: str | None = None
    try:
        # CREATE
        cle = adaptateur.creer_ressource(
            DescripteurRessource(type="folder", chemin="/", nom="zztest_swisspipe_cycle")
        )
        assert isinstance(cle, str) and cle
        assert int(cle) > _ID_PROD_MAX, "SÉCURITÉ : le test ne doit jamais viser un id de prod"
        folder = adaptateur._folder_par_id(cle)
        assert folder is not None
        assert folder["mountPoint"] == "zztest_swisspipe_cycle"

        # RENAME
        adaptateur.renommer_ressource(cle, "zztest_swisspipe_cycle2")
        assert adaptateur._folder_par_id(cle)["mountPoint"] == "zztest_swisspipe_cycle2"

        # ARCHIVE (réversible) : on ajoute un groupe, puis on archive -> groups vide.
        executer_occ(["groupfolders:group", cle, "admin", "read"])
        assert adaptateur._folder_par_id(cle)["groups_list"], "le groupe devrait être présent"

        adaptateur.archiver_ressource(cle)
        folder_archive = adaptateur._folder_par_id(cle)
        assert folder_archive is not None, "archivé = inaccessible mais EXISTE toujours"
        # JSON : dict vide sérialisé en [] ou {}.
        assert folder_archive["groups_list"] in ([], {})
    finally:
        if cle is not None:
            # Nettoyage du folder jetable (delete dur OK ICI : test, pas prod).
            executer_occ(["groupfolders:delete", str(cle), "--force"])
