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
from swisspipe.adapters.outbound.nextcloud.occ_runner import (
    NEXTCLOUD_OCC_PATH,
    NEXTCLOUD_SSH_ALIAS,
    executer_occ,
)
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource, DroitGroupe

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


def _nettoyer_acl_orphelins() -> None:
    """Supprime les règles ACL orphelines (fileid mort) — nettoyage de test uniquement.

    Write DB ciblé : ne touche QUE les lignes dont le fileid n'existe plus en filecache
    (folders supprimés). Jamais une règle vivante.
    """
    php = (
        "<?php error_reporting(E_ERROR); $CONFIG=[]; require 'config/config.php';"
        "$p=$CONFIG['dbtableprefix']??'oc_'; $h=$CONFIG['dbhost']; $port=3306;"
        "if(strpos($h,':')!==false){list($h,$port)=explode(':',$h,2);}"
        "$pdo=new PDO(\"mysql:host=$h;port=$port;dbname={$CONFIG['dbname']}\","
        "$CONFIG['dbuser'],$CONFIG['dbpassword']);"
        "$pdo->exec(\"DELETE FROM {$p}group_folders_acl WHERE fileid NOT IN "
        "(SELECT fileid FROM {$p}filecache)\");"
    )
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", NEXTCLOUD_SSH_ALIAS, f"cd {NEXTCLOUD_OCC_PATH} && php"],
        input=php,
        capture_output=True,
        text=True,
        timeout=30,
    )


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


def test_lire_droits_effectifs_acl_fine() -> None:
    if not _serveur_accessible():
        pytest.skip(f"serveur SSH '{NEXTCLOUD_SSH_ALIAS}' injoignable — test ACL skippé")

    adaptateur = AdaptateurNextcloud("", "", "")
    cle: str | None = None
    try:
        cle = adaptateur.creer_ressource(
            DescripteurRessource(type="folder", chemin="/", nom="zztest_swisspipe_aclread")
        )
        assert int(cle) > _ID_PROD_MAX, "SÉCURITÉ : jamais un id de prod"

        # Donne accès au groupe admin au niveau folder, active l'ACL, puis pose une règle
        # racine connue : +read, tout le reste deny -> LECTURE seule (mask 31, perms 1).
        executer_occ(["groupfolders:group", cle, "admin", "read", "write"])
        executer_occ(["groupfolders:permissions", cle, "-e"])
        executer_occ(
            ["groupfolders:permissions", cle, "/", "-g", "admin", "--",
             "+read", "-write", "-create", "-delete", "-share"]
        )

        droits = adaptateur.lire_droits_effectifs(cle)
        assert droits == frozenset({DroitGroupe("admin", Matrice(NiveauPrincipal.LECTURE))})
    finally:
        if cle is not None:
            executer_occ(["groupfolders:delete", str(cle), "--force"])
            _nettoyer_acl_orphelins()


def _creer_folder_test(adaptateur: AdaptateurNextcloud, nom: str) -> str:
    cle = adaptateur.creer_ressource(DescripteurRessource(type="folder", chemin="/", nom=nom))
    assert int(cle) > _ID_PROD_MAX, "SÉCURITÉ : jamais un id de prod"
    return cle


def test_appliquer_droits_round_trip() -> None:
    if not _serveur_accessible():
        pytest.skip(f"serveur SSH '{NEXTCLOUD_SSH_ALIAS}' injoignable — test C2 skippé")

    adaptateur = AdaptateurNextcloud("", "", "")
    cle: str | None = None
    etat = frozenset({DroitGroupe("admin", Matrice(NiveauPrincipal.ECRITURE))})
    try:
        cle = _creer_folder_test(adaptateur, "zztest_swisspipe_apply_rt")
        adaptateur.appliquer_droits(cle, etat)
        # appliquer -> lire = identité (réconciliation grandeur nature).
        assert adaptateur.lire_droits_effectifs(cle) == etat
    finally:
        if cle is not None:
            executer_occ(["groupfolders:delete", str(cle), "--force"])
            _nettoyer_acl_orphelins()


def test_appliquer_droits_idempotent() -> None:
    if not _serveur_accessible():
        pytest.skip(f"serveur SSH '{NEXTCLOUD_SSH_ALIAS}' injoignable — test C2 skippé")

    adaptateur = AdaptateurNextcloud("", "", "")
    cle: str | None = None
    etat = frozenset({DroitGroupe("admin", Matrice(NiveauPrincipal.SUPPRESSION))})
    try:
        cle = _creer_folder_test(adaptateur, "zztest_swisspipe_apply_idem")
        adaptateur.appliquer_droits(cle, etat)
        premier = adaptateur.lire_droits_effectifs(cle)
        adaptateur.appliquer_droits(cle, etat)  # ré-application du même état
        second = adaptateur.lire_droits_effectifs(cle)
        assert premier == second == etat
        assert len(second) == 1  # pas de doublon
    finally:
        if cle is not None:
            executer_occ(["groupfolders:delete", str(cle), "--force"])
            _nettoyer_acl_orphelins()


def test_appliquer_droits_reconciliation_retrait() -> None:
    if not _serveur_accessible():
        pytest.skip(f"serveur SSH '{NEXTCLOUD_SSH_ALIAS}' injoignable — test C2 skippé")

    adaptateur = AdaptateurNextcloud("", "", "")
    cle: str | None = None
    try:
        cle = _creer_folder_test(adaptateur, "zztest_swisspipe_apply_reco")
        adaptateur.appliquer_droits(cle, {DroitGroupe("admin", Matrice(NiveauPrincipal.ECRITURE))})
        assert adaptateur.lire_droits_effectifs(cle) != frozenset()
        # État désiré vide -> le groupe fantôme est retiré.
        adaptateur.appliquer_droits(cle, frozenset())
        assert adaptateur.lire_droits_effectifs(cle) == frozenset()
    finally:
        if cle is not None:
            executer_occ(["groupfolders:delete", str(cle), "--force"])
            _nettoyer_acl_orphelins()
