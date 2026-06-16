"""Tests du déclenchement automatique de la réconciliation (détection d'upgrade).

- `_doit_reconcilier` : pur (sans réseau).
- `lire_etat_nextcloud` : parsing via mock de `executer_occ` (sans réseau).
- `verifier_et_reconcilier` : via mock du lecteur + fake adaptateur + db_session.
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.fake.adaptateur_memoire import AdaptateurMemoire
from swisspipe.adapters.outbound.nextcloud.etat_nextcloud import (
    EtatNextcloud,
    lire_etat_nextcloud,
)
from swisspipe.application.reconciliation_service import (
    _doit_reconcilier,
    verifier_et_reconcilier,
)
from swisspipe.persistence.models import EtatSysteme

ETAT = EtatNextcloud(nc_version="33.0.4", gf_version="21.0.8", gf_active=True)


# ---------------------------------------------------------------------------
# _doit_reconcilier — pur
# ---------------------------------------------------------------------------


def test_premiere_execution() -> None:
    assert _doit_reconcilier(ETAT, None) == (True, "premiere_execution")


def test_gf_reactive() -> None:
    precedent = EtatNextcloud("33.0.4", "21.0.8", gf_active=False).vers_dict()
    assert _doit_reconcilier(ETAT, precedent) == (True, "gf_reactive")


def test_nc_version_change() -> None:
    precedent = EtatNextcloud("33.0.3", "21.0.8", gf_active=True).vers_dict()
    assert _doit_reconcilier(ETAT, precedent) == (True, "nc_version_change")


def test_gf_version_change() -> None:
    precedent = EtatNextcloud("33.0.4", "21.0.7", gf_active=True).vers_dict()
    assert _doit_reconcilier(ETAT, precedent) == (True, "gf_version_change")


def test_inchange_no_reconciliation() -> None:
    # CAS CLÉ : état identique au marqueur -> on ne réconcilie PAS.
    assert _doit_reconcilier(ETAT, ETAT.vers_dict()) == (False, "inchange")


# ---------------------------------------------------------------------------
# lire_etat_nextcloud — parsing via mock executer_occ
# ---------------------------------------------------------------------------

_STATUT = '{"installed":true,"version":"33.0.4.1","versionstring":"33.0.4","maintenance":false}'


def _mock_occ(enabled: dict[str, str], disabled: dict[str, str]):
    import json

    apps = json.dumps({"enabled": enabled, "disabled": disabled})

    def faux(args, **kwargs):  # type: ignore[no-untyped-def]
        if args[0] == "status":
            return _STATUT
        return apps

    return faux


def test_lire_etat_gf_enabled() -> None:
    with patch(
        "swisspipe.adapters.outbound.nextcloud.etat_nextcloud.executer_occ",
        side_effect=_mock_occ({"groupfolders": "21.0.8", "files": "1.0"}, {}),
    ):
        etat = lire_etat_nextcloud()
    assert etat == EtatNextcloud("33.0.4", "21.0.8", gf_active=True)


def test_lire_etat_gf_disabled() -> None:
    # GF désactivé (cas bug #3246) : inactif mais version encore lisible.
    with patch(
        "swisspipe.adapters.outbound.nextcloud.etat_nextcloud.executer_occ",
        side_effect=_mock_occ({"files": "1.0"}, {"groupfolders": "21.0.8"}),
    ):
        etat = lire_etat_nextcloud()
    assert etat == EtatNextcloud("33.0.4", "21.0.8", gf_active=False)


def test_lire_etat_gf_absent() -> None:
    with patch(
        "swisspipe.adapters.outbound.nextcloud.etat_nextcloud.executer_occ",
        side_effect=_mock_occ({"files": "1.0"}, {}),
    ):
        etat = lire_etat_nextcloud()
    assert etat == EtatNextcloud("33.0.4", gf_version=None, gf_active=False)


# ---------------------------------------------------------------------------
# verifier_et_reconcilier — via mock lecteur + db_session (aucune ressource mappée)
# ---------------------------------------------------------------------------


def _marqueur(session: Session) -> EtatSysteme | None:
    return session.get(EtatSysteme, "nextcloud")


def test_premiere_execution_reconcilie_et_cree_marqueur(db_session: Session) -> None:
    fake = AdaptateurMemoire()
    rapport = verifier_et_reconcilier(db_session, fake, lambda: ETAT)
    assert rapport is not None  # a réconcilié (baseline ; aucune ressource -> total 0)
    assert rapport.total == 0
    marqueur = _marqueur(db_session)
    assert marqueur is not None
    assert marqueur.valeur == ETAT.vers_dict()


def test_etat_identique_noop(db_session: Session) -> None:
    db_session.add(EtatSysteme(cle="nextcloud", valeur=ETAT.vers_dict()))
    db_session.flush()

    rapport = verifier_et_reconcilier(db_session, AdaptateurMemoire(), lambda: ETAT)

    assert rapport is None  # no-op : état == marqueur
    assert _marqueur(db_session).valeur == ETAT.vers_dict()  # inchangé


def test_gf_version_change_reconcilie_et_met_a_jour(db_session: Session) -> None:
    ancien = EtatNextcloud("33.0.4", "21.0.7", gf_active=True)
    db_session.add(EtatSysteme(cle="nextcloud", valeur=ancien.vers_dict()))
    db_session.flush()

    rapport = verifier_et_reconcilier(db_session, AdaptateurMemoire(), lambda: ETAT)

    assert rapport is not None
    assert _marqueur(db_session).valeur == ETAT.vers_dict()  # marqueur mis à jour


def test_idempotence_deux_appels(db_session: Session) -> None:
    fake = AdaptateurMemoire()
    r1 = verifier_et_reconcilier(db_session, fake, lambda: ETAT)
    r2 = verifier_et_reconcilier(db_session, fake, lambda: ETAT)
    assert r1 is not None  # 1re fois : premiere_execution
    assert r2 is None  # 2e fois : état == marqueur -> no-op


# ---------------------------------------------------------------------------
# Intégration : lecteur contre le VRAI serveur (skip sans SSH, lecture seule)
# ---------------------------------------------------------------------------


def _serveur_accessible() -> bool:
    import subprocess

    from swisspipe.adapters.outbound.nextcloud.occ_runner import NEXTCLOUD_SSH_ALIAS

    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", NEXTCLOUD_SSH_ALIAS, "true"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def test_lire_etat_nextcloud_serveur_reel() -> None:
    import pytest

    if not _serveur_accessible():
        pytest.skip("serveur SSH injoignable — test d'intégration lecteur skippé")

    etat = lire_etat_nextcloud()
    # Preuve que le parsing fonctionne contre le vrai occ (lecture seule).
    assert isinstance(etat.nc_version, str) and etat.nc_version.startswith("33.")
    assert etat.gf_active is True
    assert etat.gf_version is not None and etat.gf_version != ""
    print(f"\nÉTAT RÉEL LU : {etat.vers_dict()}")
