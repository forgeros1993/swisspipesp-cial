"""Décodage de l'état ACL réel d'un GF transverse (adaptateur, HERMÉTIQUE).

Source de vérité = lignes SQL group_folders_acl ⋈ filecache (étape 7). Le décodeur
transforme ces lignes en { sous_chemin → { groupe → Matrice } }, en RÉUTILISANT
regle_acl_vers_matrice. Aucun serveur ici : lignes simulées.
"""

from __future__ import annotations

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import decoder_etat_acl
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)

# Bits NC : read=1 update=2 create=4 delete=8 share=16 ; mask=31 = les 5 gouvernés
# (matrice_vers_verbes_acl gouverne tout -> round-trip symétrique).
_MASK_TOUS = 31


def _ligne(path: str, groupe: str, permissions: int, mtype: str = "group") -> dict:
    return {
        "path": path,
        "mapping_type": mtype,
        "mapping_id": groupe,
        "mask": _MASK_TOUS,
        "permissions": permissions,
    }


def test_decode_etat_simule() -> None:
    lignes = [
        _ligne("files/Plans", "zztest_grp_demo", 3),  # +read +write -> ÉCRITURE
        _ligne("files/Correspondance", "zztest_grp_demo", 1),  # +read seul -> LECTURE
    ]
    etat = decoder_etat_acl(lignes)
    assert etat == {
        "Plans": {"zztest_grp_demo": ECRITURE},
        "Correspondance": {"zztest_grp_demo": LECTURE},
    }


def test_decode_gf_vide() -> None:
    assert decoder_etat_acl([]) == {}


def test_decode_ignore_non_groupes() -> None:
    # INV-4 : seules les règles ciblant un GROUPE nous concernent (user/circle ignorés).
    lignes = [
        _ligne("files/Plans", "marie", 3, mtype="user"),
        _ligne("files/Plans", "grp", 1),
    ]
    assert decoder_etat_acl(lignes) == {"Plans": {"grp": LECTURE}}


def test_decode_ignore_racine_et_regles_sans_droit() -> None:
    lignes = [
        _ligne("files", "grp", 3),  # règle racine (pas un sous-dossier) -> ignorée ici
        _ligne("files/Plans", "grp2", 0),  # aucun bit accordé -> pas de droit -> omis
    ]
    assert decoder_etat_acl(lignes) == {}
