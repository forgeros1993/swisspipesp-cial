"""Tests de la traduction Nextcloud + squelette adaptateur (sans serveur)."""

from __future__ import annotations

import inspect
import subprocess

import pytest

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import AdaptateurNextcloud
from swisspipe.adapters.outbound.nextcloud.occ_runner import NEXTCLOUD_SSH_ALIAS
from swisspipe.adapters.outbound.nextcloud.traduction import (
    matrice_vers_permissions_nextcloud,
    matrice_vers_verbes_acl,
    permissions_nextcloud_vers_matrice,
    regle_acl_vers_matrice,
)
from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.ports.adaptateur_ressource import (
    AdaptateurRessource,
    DroitGroupe,
)


def _m(niveau: NiveauPrincipal, *additionnels: DroitAdditionnel) -> Matrice:
    return Matrice(niveau, frozenset(additionnels))


# ---------------------------------------------------------------------------
# Niveaux principaux
# ---------------------------------------------------------------------------


def test_lecture_vaut_read() -> None:
    assert matrice_vers_permissions_nextcloud(_m(NiveauPrincipal.LECTURE)) == 1


def test_ecriture_vaut_read_update() -> None:
    assert matrice_vers_permissions_nextcloud(_m(NiveauPrincipal.ECRITURE)) == 3


def test_suppression_vaut_read_update_delete() -> None:
    assert matrice_vers_permissions_nextcloud(_m(NiveauPrincipal.SUPPRESSION)) == 11


# ---------------------------------------------------------------------------
# Additionnel CREATION (+4)
# ---------------------------------------------------------------------------


def test_lecture_creation() -> None:
    assert (
        matrice_vers_permissions_nextcloud(_m(NiveauPrincipal.LECTURE, DroitAdditionnel.CREATION))
        == 5
    )


def test_ecriture_creation() -> None:
    assert (
        matrice_vers_permissions_nextcloud(_m(NiveauPrincipal.ECRITURE, DroitAdditionnel.CREATION))
        == 7
    )


def test_suppression_creation() -> None:
    assert (
        matrice_vers_permissions_nextcloud(
            _m(NiveauPrincipal.SUPPRESSION, DroitAdditionnel.CREATION)
        )
        == 15
    )


# ---------------------------------------------------------------------------
# CLASSEMENT -> create|delete (décision documentée, À CONFIRMER)
# ---------------------------------------------------------------------------


def test_classement_ajoute_create_et_delete() -> None:
    # Lecture(1) + create(4) + delete(8) = 13.
    assert (
        matrice_vers_permissions_nextcloud(_m(NiveauPrincipal.LECTURE, DroitAdditionnel.CLASSEMENT))
        == 13
    )


def test_classement_sur_ecriture() -> None:
    # Écriture(3) | create(4) | delete(8) = 15.
    assert (
        matrice_vers_permissions_nextcloud(
            _m(NiveauPrincipal.ECRITURE, DroitAdditionnel.CLASSEMENT)
        )
        == 15
    )


# ---------------------------------------------------------------------------
# TELECHARGEMENT -> aucun bit (question ouverte, non mappable)
# ---------------------------------------------------------------------------


def test_telechargement_n_ajoute_aucun_bit() -> None:
    assert (
        matrice_vers_permissions_nextcloud(
            _m(NiveauPrincipal.LECTURE, DroitAdditionnel.TELECHARGEMENT)
        )
        == 1
    )
    assert (
        matrice_vers_permissions_nextcloud(
            _m(NiveauPrincipal.ECRITURE, DroitAdditionnel.TELECHARGEMENT)
        )
        == 3
    )


def test_telechargement_seul_avec_creation() -> None:
    # Téléchargement ignoré, seul create compte : Lecture(1)+create(4)=5.
    m = _m(NiveauPrincipal.LECTURE, DroitAdditionnel.TELECHARGEMENT, DroitAdditionnel.CREATION)
    assert matrice_vers_permissions_nextcloud(m) == 5


def test_tous_additionnels_combines() -> None:
    # Suppression(11) | create(4) | delete(8) ; téléchargement ignoré = 15.
    m = _m(
        NiveauPrincipal.SUPPRESSION,
        DroitAdditionnel.CREATION,
        DroitAdditionnel.CLASSEMENT,
        DroitAdditionnel.TELECHARGEMENT,
    )
    assert matrice_vers_permissions_nextcloud(m) == 15


def test_jamais_de_bit_share() -> None:
    for niveau in NiveauPrincipal:
        for add in ([], list(DroitAdditionnel)):
            bits = matrice_vers_permissions_nextcloud(_m(niveau, *add))
            assert bits & 16 == 0  # share jamais octroyé


def test_determinisme() -> None:
    m = _m(NiveauPrincipal.ECRITURE, DroitAdditionnel.CREATION)
    assert matrice_vers_permissions_nextcloud(m) == matrice_vers_permissions_nextcloud(m)


# ---------------------------------------------------------------------------
# Squelette adaptateur
# ---------------------------------------------------------------------------

CONFIG = ("https://nc.example", "user", "secret")


def test_adaptateur_satisfait_structure_du_protocol() -> None:
    a = AdaptateurNextcloud(*CONFIG)
    assert isinstance(a, AdaptateurRessource)  # runtime_checkable : 5 méthodes présentes


def test_signatures_des_cinq_methodes() -> None:
    for nom in (
        "creer_ressource",
        "archiver_ressource",
        "renommer_ressource",
        "appliquer_droits",
        "lire_droits_effectifs",
    ):
        assert callable(getattr(AdaptateurNextcloud, nom))
    # Signatures attendues (paramètres hors self).
    params = list(inspect.signature(AdaptateurNextcloud.appliquer_droits).parameters)
    assert params == ["self", "cle_externe", "droits"]


def test_traduire_droits_est_reel() -> None:
    a = AdaptateurNextcloud(*CONFIG)
    droits = [
        DroitGroupe("g1", _m(NiveauPrincipal.ECRITURE)),
        DroitGroupe("g2", _m(NiveauPrincipal.LECTURE, DroitAdditionnel.CREATION)),
    ]
    assert a.traduire_droits(droits) == {"g1": 3, "g2": 5}


# Les 5 méthodes du port sont désormais implémentées (A: lire ; B: creer/renommer/
# archiver ; C2: appliquer_droits). Le comportement réel est couvert par les tests
# d'intégration skippables (round-trip appliquer->lire, idempotence, réconciliation).


# ---------------------------------------------------------------------------
# Traduction INVERSE (masque Nextcloud -> Matrice) — pur, toujours vert
# ---------------------------------------------------------------------------


def test_inverse_31_suppression_creation() -> None:
    # 31 = read|update|create|delete|share -> SUPPRESSION + CRÉATION (share ignoré).
    m = permissions_nextcloud_vers_matrice(31)
    assert m == Matrice(NiveauPrincipal.SUPPRESSION, {DroitAdditionnel.CREATION})


def test_inverse_15_egal_31_share_ignore() -> None:
    # 15 = read|update|create|delete (sans share) -> même Matrice que 31.
    assert permissions_nextcloud_vers_matrice(15) == permissions_nextcloud_vers_matrice(31)


def test_inverse_1_lecture() -> None:
    assert permissions_nextcloud_vers_matrice(1) == Matrice(NiveauPrincipal.LECTURE)


def test_inverse_3_ecriture() -> None:
    assert permissions_nextcloud_vers_matrice(3) == Matrice(NiveauPrincipal.ECRITURE)


def test_inverse_7_ecriture_creation() -> None:
    assert permissions_nextcloud_vers_matrice(7) == Matrice(
        NiveauPrincipal.ECRITURE, {DroitAdditionnel.CREATION}
    )


def test_inverse_0_aucun_droit() -> None:
    assert permissions_nextcloud_vers_matrice(0) is None


def test_inverse_share_seul_sans_read_aucun_droit() -> None:
    # 16 = share seul, pas de read -> aucun droit.
    assert permissions_nextcloud_vers_matrice(16) is None


# ---------------------------------------------------------------------------
# Règle ACL (mask + permissions) -> Matrice — pur, toujours vert
# ---------------------------------------------------------------------------


def test_acl_read_write_governe_write_deny_donne_lecture() -> None:
    # +read -write : mask=3 (read+write gouvernés), permissions=1 (read allow, write deny).
    assert regle_acl_vers_matrice(3, 1) == Matrice(NiveauPrincipal.LECTURE)


def test_acl_deny_read_donne_refuser_none() -> None:
    # -read : mask=3, permissions=0 -> aucun read autorisé -> REFUSER -> None.
    assert regle_acl_vers_matrice(3, 0) is None


def test_acl_tout_gouverne_read_seul_lecture() -> None:
    # mask=31 (tous verbes gouvernés), permissions=1 (read seul) -> LECTURE.
    assert regle_acl_vers_matrice(31, 1) == Matrice(NiveauPrincipal.LECTURE)


def test_acl_suppression() -> None:
    # mask=31, permissions=11 (read+write+delete) -> SUPPRESSION.
    assert regle_acl_vers_matrice(31, 11) == Matrice(NiveauPrincipal.SUPPRESSION)


def test_acl_ecriture_creation() -> None:
    # mask=31, permissions=7 (read+write+create) -> ÉCRITURE + CRÉATION.
    assert regle_acl_vers_matrice(31, 7) == Matrice(
        NiveauPrincipal.ECRITURE, {DroitAdditionnel.CREATION}
    )


def test_acl_mask_nul_donne_none() -> None:
    # Rien de gouverné -> mask&permissions=0 -> None.
    assert regle_acl_vers_matrice(0, 31) is None


# ---------------------------------------------------------------------------
# Matrice -> verbes ACL (gouverne les 5 verbes) — pur, toujours vert
# ---------------------------------------------------------------------------


def test_verbes_lecture() -> None:
    assert matrice_vers_verbes_acl(_m(NiveauPrincipal.LECTURE)) == [
        "+read", "-write", "-create", "-delete", "-share",
    ]


def test_verbes_ecriture() -> None:
    assert matrice_vers_verbes_acl(_m(NiveauPrincipal.ECRITURE)) == [
        "+read", "+write", "-create", "-delete", "-share",
    ]


def test_verbes_suppression() -> None:
    assert matrice_vers_verbes_acl(_m(NiveauPrincipal.SUPPRESSION)) == [
        "+read", "+write", "-create", "+delete", "-share",
    ]


def test_verbes_ecriture_creation() -> None:
    assert matrice_vers_verbes_acl(
        _m(NiveauPrincipal.ECRITURE, DroitAdditionnel.CREATION)
    ) == ["+read", "+write", "+create", "-delete", "-share"]


def test_verbes_round_trip_via_inverse() -> None:
    # Les verbes posés se relisent en la même matrice (symétrie appliquer->lire).
    for matrice in (
        _m(NiveauPrincipal.LECTURE),
        _m(NiveauPrincipal.ECRITURE),
        _m(NiveauPrincipal.SUPPRESSION),
        _m(NiveauPrincipal.ECRITURE, DroitAdditionnel.CREATION),
    ):
        verbes = matrice_vers_verbes_acl(matrice)
        mask = 0
        permissions = 0
        bits = {"read": 1, "write": 2, "create": 4, "delete": 8, "share": 16}
        for v in verbes:
            signe, nom = v[0], v[1:]
            mask |= bits[nom]
            if signe == "+":
                permissions |= bits[nom]
        assert regle_acl_vers_matrice(mask, permissions) == matrice


# ---------------------------------------------------------------------------
# Test RÉSEAU : lire_droits_effectifs contre le vrai serveur (skip si pas d'accès)
# ---------------------------------------------------------------------------


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


def test_lire_droits_effectifs_serveur_reel() -> None:
    if not _serveur_accessible():
        pytest.skip(f"serveur SSH '{NEXTCLOUD_SSH_ALIAS}' injoignable — test réseau skippé")

    a = AdaptateurNextcloud("", "", "")
    # Folder id 5 = "Alpha Conseil SAS" (observé). On vérifie un état cohérent.
    droits = a.lire_droits_effectifs("5")
    assert isinstance(droits, frozenset)
    assert len(droits) >= 1
    for dg in droits:
        assert isinstance(dg, DroitGroupe)
        assert dg.matrice is not None  # au moins le niveau read
        assert dg.groupe_id != ""
