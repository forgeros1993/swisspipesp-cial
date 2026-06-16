"""Test GRANDEUR NATURE de la réconciliation — Postgres + vrai serveur Nextcloud.

Prouve la boucle complète cœur→adaptateur→Nextcloud→réparation contre le VRAI serveur :
état posé, dérive ACL simulée hors cœur (les 2 symptômes réels d'un upgrade), réconcilie,
vérifie la réparation + la trace au journal. Skip proprement sans SSH ou sans Postgres.

Garde-fous prod (NON négociables) : folder jetable `zztest_` (id > 20, jamais la prod
4-20), groupe NC jetable `zztest_grp_*`, cleanup garanti en `finally`. Le Postgres est
isolé par la fixture `db_session` (rollback) ; le côté Nextcloud (non transactionnel) est
nettoyé à la main.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import AdaptateurNextcloud
from swisspipe.adapters.outbound.nextcloud.occ_runner import (
    NEXTCLOUD_OCC_PATH,
    NEXTCLOUD_SSH_ALIAS,
    OccError,
    executer_occ,
)
from swisspipe.application.reconciliation_service import reconcilier_ressource
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource, DroitGroupe
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    JournalAcces,
    NatureEspace,
    Ressource,
    RessourceMapping,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.persistence.models import Octroi as OctroiModel

_ID_PROD_MAX = 20
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
LECTURE = Matrice(NiveauPrincipal.LECTURE)


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
    php = (
        "<?php error_reporting(E_ERROR); $CONFIG=[]; require 'config/config.php';"
        "$p=$CONFIG['dbtableprefix']??'oc_'; $h=$CONFIG['dbhost']; $port=3306;"
        "if(strpos($h,':')!==false){list($h,$port)=explode(':',$h,2);}"
        "$pdo=new PDO(\"mysql:host=$h;port=$port;dbname={$CONFIG['dbname']}\","
        "$CONFIG['dbuser'],$CONFIG['dbpassword']);"
        '$pdo->exec("DELETE FROM {$p}group_folders_acl WHERE fileid NOT IN '
        '(SELECT fileid FROM {$p}filecache)");'
    )
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", NEXTCLOUD_SSH_ALIAS, f"cd {NEXTCLOUD_OCC_PATH} && php"],
        input=php,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _journal(session: Session, ressource_id: uuid.UUID, action: str) -> list[JournalAcces]:
    lignes = session.execute(
        select(JournalAcces).where(JournalAcces.ressource_id == ressource_id)
    ).scalars()
    return [j for j in lignes if j.action.value == action]


def test_reconciliation_grandeur_nature(db_session: Session) -> None:
    if not _serveur_accessible():
        pytest.skip(f"serveur SSH '{NEXTCLOUD_SSH_ALIAS}' injoignable — grandeur nature skippé")

    adaptateur = AdaptateurNextcloud("", "", "")
    grp = f"zztest_grp_{uuid.uuid4().hex[:8]}"
    cle: str | None = None
    try:
        # --- Setup réel Nextcloud ---
        executer_occ(["group:add", grp])
        cle = adaptateur.creer_ressource(
            DescripteurRessource(type="folder", chemin="/", nom="zztest_swisspipe_reco")
        )
        assert int(cle) > _ID_PROD_MAX, "SÉCURITÉ : jamais un folder de prod"

        # --- Setup cœur (même db_session, flush, PAS de commit) ---
        espace = Espace(
            nature=NatureEspace.DIMENSIONNEL,
            combinaison_signature=signature_combinaison([("t", uuid.uuid4().hex)]),
        )
        db_session.add(espace)
        db_session.flush()
        ressource = Ressource(type="folder", espace_id=espace.id, chemin="/")
        db_session.add(ressource)
        db_session.flush()
        groupe = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle=grp)
        db_session.add(groupe)
        db_session.flush()
        db_session.add(
            RessourceMapping(ressource_id=ressource.id, adaptateur="nextcloud", cle_externe=cle)
        )
        db_session.add(
            OctroiModel(
                ressource_id=ressource.id,
                groupe_id=groupe.id,
                mode=Octroi.modifier(ECRITURE).mode,
                matrice=ECRITURE.vers_jsonb(),
            )
        )
        db_session.flush()
        rid = ressource.id

        # --- Conformité : poser le désiré -> reconcilier = no-op ---
        adaptateur.appliquer_droits(cle, {DroitGroupe(grp, ECRITURE)})
        div = reconcilier_ressource(db_session, adaptateur, rid)
        assert div.est_conforme
        assert _journal(db_session, rid, "modification") == []
        assert _journal(db_session, rid, "octroi") == []

        # --- Dérive A : matrice divergente (ÉCRITURE -> LECTURE hors cœur) ---
        executer_occ(
            [
                "groupfolders:permissions",
                cle,
                "/",
                "-g",
                grp,
                "--",
                "+read",
                "-write",
                "-create",
                "-delete",
                "-share",
            ]
        )
        assert adaptateur.lire_droits_effectifs(cle) == frozenset({DroitGroupe(grp, LECTURE)})

        div_a = reconcilier_ressource(db_session, adaptateur, rid)
        assert not div_a.est_conforme
        assert div_a.matrices_divergentes  # dérive matrice détectée
        assert adaptateur.lire_droits_effectifs(cle) == frozenset({DroitGroupe(grp, ECRITURE)})
        mods = _journal(db_session, rid, "modification")
        assert len(mods) == 1
        assert mods[0].matrice_avant == LECTURE.vers_jsonb()
        assert mods[0].matrice_apres == ECRITURE.vers_jsonb()
        assert mods[0].cause["divergence"] == "matrice"

        # --- Dérive B : groupe manquant (règle effacée, cas bug #3246) ---
        executer_occ(["groupfolders:permissions", cle, "/", "-g", grp, "--", "clear"])
        assert DroitGroupe(grp, ECRITURE) not in adaptateur.lire_droits_effectifs(cle)

        div_b = reconcilier_ressource(db_session, adaptateur, rid)
        assert not div_b.est_conforme
        assert div_b.groupes_manquants == frozenset({DroitGroupe(grp, ECRITURE)})
        assert adaptateur.lire_droits_effectifs(cle) == frozenset({DroitGroupe(grp, ECRITURE)})
        octrois = _journal(db_session, rid, "octroi")
        assert len(octrois) == 1
        assert octrois[0].matrice_avant is None
        assert octrois[0].matrice_apres == ECRITURE.vers_jsonb()
        assert octrois[0].cause["divergence"] == "manquant"
    finally:
        if cle is not None:
            executer_occ(["groupfolders:delete", str(cle), "--force"])
        try:
            executer_occ(["group:delete", grp])
        except OccError:
            pass  # best-effort
        _nettoyer_acl_orphelins()
