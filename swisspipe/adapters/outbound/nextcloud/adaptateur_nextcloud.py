"""Adaptateur Nextcloud — Group Folders via `occ` en SSH.

Implémente le même Protocol AdaptateurRessource que le fake — c'est ce qui prouve
l'agnosticité du cœur. Hors cœur : peut importer ce qu'il veut. Canal tranché : `occ`
exécuté en SSH (cf. occ_runner) ; pas d'API HTTP externe. Le SQL brut n'est utilisé
qu'en dernier recours et serait documenté (pas nécessaire ici).

Principe §3.2 : l'adaptateur ne fait AUCUNE logique métier, il TRADUIT. La matrice
reçue/relue est convertie via traduction.py (sens aller et inverse).

État des tranches :
- Tranche A : `lire_droits_effectifs` (LECTURE).
- Tranche B (ici) : `creer_ressource`, `renommer_ressource`, `archiver_ressource`.
- Tranche C : `appliquer_droits` (mapping fin des permissions) -> NotImplementedError.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any

from swisspipe.adapters.outbound.nextcloud.occ_runner import executer_occ
from swisspipe.adapters.outbound.nextcloud.sql_runner import executer_select
from swisspipe.adapters.outbound.nextcloud.traduction import (
    matrice_vers_permissions_nextcloud,
    matrice_vers_verbes_acl,
    permissions_nextcloud_vers_matrice,
    regle_acl_vers_matrice,
)
from swisspipe.core.ports.adaptateur_ressource import DescripteurRessource, DroitGroupe

# Chemins racine du contenu d'un Group Folder dans filecache (selon versions NC).
_CHEMINS_RACINE = ("files", "")


# --- Projection en mode OMBRE (le PLAN) : calcule les commandes occ SANS les exécuter ---


@dataclass(frozen=True)
class PlanProjection:
    """Plan de projection = suite ORDONNÉE de commandes occ qui SERAIENT exécutées.

    Aucune n'est exécutée : ce type est pur (données). `rendu()` l'affiche.
    """

    commandes: tuple[tuple[str, ...], ...]

    def rendu(self) -> str:
        return "\n".join("php occ " + " ".join(cmd) for cmd in self.commandes)


def planifier_projection_occ(
    chemin_hote: str,
    ressources: Iterable[tuple[str, Collection[DroitGroupe]]],
) -> PlanProjection:
    """Construit le PLAN occ d'une projection (mode ombre). PUR : n'exécute RIEN.

    Pour chaque ressource (nom, droits déjà résolus + bornés par le cœur) : un Group
    Folder au point de montage (`{chemin_hote}/{nom}`), l'activation de l'ACL, puis une
    règle ACL par groupe. Les verbes proviennent de matrice_vers_verbes_acl (traduction
    L1 réutilisée, pas réécrite). L'id externe étant inconnu hors exécution, le point de
    montage sert de référence symbolique dans le plan.
    """
    base = chemin_hote.rstrip("/")
    commandes: list[tuple[str, ...]] = []
    for nom, droits in ressources:
        point = f"{base}/{nom}"
        commandes.append(("groupfolders:create", point))
        commandes.append(("groupfolders:permissions", point, "-e"))
        for dg in sorted(droits, key=lambda d: d.groupe_id):
            verbes = matrice_vers_verbes_acl(dg.matrice)
            commandes.append(
                ("groupfolders:permissions", point, "/", "-g", dg.groupe_id, "--", *verbes)
            )
    return PlanProjection(tuple(commandes))


class AdaptateurNextcloud:
    """Adaptateur Group Folders. Pilote `occ` en SSH (config via occ_runner).

    `base_url`/`utilisateur`/`mot_de_passe` (HTTP) sont conservés pour de futurs usages
    (OCS/WebDAV) mais ne servent PAS au canal occ. `ssh_alias`/`occ_dir` permettent de
    surcharger la cible SSH ; à défaut, les valeurs d'occ_runner (env ou constantes).
    """

    def __init__(
        self,
        base_url: str,
        utilisateur: str,
        mot_de_passe: str,
        *,
        ssh_alias: str | None = None,
        occ_dir: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._utilisateur = utilisateur
        self._mot_de_passe = mot_de_passe
        self._ssh_alias = ssh_alias
        self._occ_dir = occ_dir

    # --- Helpers occ ---------------------------------------------------------

    def _occ(self, args: list[str]) -> str:
        return executer_occ(args, alias=self._ssh_alias, occ_dir=self._occ_dir)

    def _lister_folders(self) -> list[dict[str, Any]]:
        folders: list[dict[str, Any]] = json.loads(
            self._occ(["groupfolders:list", "--output=json"])
        )
        return folders

    def _folder_par_id(self, cle_externe: str) -> dict[str, Any] | None:
        return next(
            (f for f in self._lister_folders() if str(f.get("id")) == str(cle_externe)),
            None,
        )

    # --- Traduction (RÉELLE, testable sans serveur) --------------------------

    def traduire_droits(self, droits: Collection[DroitGroupe]) -> dict[str, int]:
        """État de droits -> {groupe_id: masque_permissions_nextcloud}. Pur."""
        return {d.groupe_id: matrice_vers_permissions_nextcloud(d.matrice) for d in droits}

    # --- Contrat AdaptateurRessource -----------------------------------------

    def creer_ressource(self, descripteur: DescripteurRessource) -> str:
        """Crée un Group Folder (mount point = descripteur.nom). Retourne son id externe.

        `groupfolders:create <nom> --output=json` retourne l'id du folder (un entier).
        Cet id est l'identifiant externe que le cœur stockera dans ressource_mapping.
        """
        sortie = self._occ(["groupfolders:create", descripteur.nom, "--output=json"])
        return str(self._parser_id_create(sortie))

    @staticmethod
    def _parser_id_create(sortie: str) -> int:
        """Parse l'id retourné par create (sortie = `21`, `21\\n` ou JSON `21`)."""
        texte = sortie.strip()
        try:
            return int(json.loads(texte))
        except (ValueError, TypeError):
            return int(texte)

    def renommer_ressource(self, cle_externe: str, nouveau_nom: str) -> None:
        """Renomme le Group Folder (`groupfolders:rename <id> <nom>`)."""
        self._occ(["groupfolders:rename", str(cle_externe), nouveau_nom])

    def archiver_ressource(self, cle_externe: str) -> None:
        """Archivage RÉVERSIBLE (INV-5) : retire tous les groupes du folder.

        Le folder devient inaccessible mais EXISTE toujours (id + données conservés).
        Réversible en réajoutant les groupes (via appliquer_droits, Tranche C). On
        n'appelle JAMAIS `groupfolders:delete` (suppression dure interdite par INV-5).
        """
        folder = self._folder_par_id(cle_externe)
        if folder is None:
            raise KeyError(f"Group Folder introuvable : {cle_externe!r}")
        groups_list = folder.get("groups_list") or {}
        for groupe_id in list(groups_list):
            self._occ(["groupfolders:group", str(cle_externe), groupe_id, "-d"])

    def appliquer_droits(self, cle_externe: str, droits: Collection[DroitGroupe]) -> None:
        """Réalise l'état COMPLET désiré en ACL fine sur le chemin racine (idempotent).

        - Active l'ACL (`-e`, idempotent).
        - Pour chaque groupe désiré : `clear` puis pose des 5 verbes (gouvernés) via
          matrice_vers_verbes_acl -> round-trip symétrique avec lire (C1).
        - Réconciliation : tout groupe qui avait une règle racine et n'est PLUS dans
          l'état désiré est `clear` (retrait de l'accès fantôme).

        INV-4 : groupes uniquement (`-g`), jamais de user/circle direct. État complet,
        pas un diff. Tranche C2 : chemin racine (cohérent avec C1).
        """
        folder = self._folder_par_id(cle_externe)
        if folder is None:
            raise KeyError(f"Group Folder introuvable : {cle_externe!r}")

        desire: dict[str, DroitGroupe] = {}
        for d in droits:
            if not d.groupe_id:
                raise ValueError("DroitGroupe sans groupe_id (INV-4 : un groupe, jamais vide)")
            desire[d.groupe_id] = d

        self._occ(["groupfolders:permissions", str(cle_externe), "-e"])
        actuels = self._groupes_acl_racine(folder)

        for groupe_id, dg in desire.items():
            self._poser_regle(cle_externe, groupe_id, matrice_vers_verbes_acl(dg.matrice))
        for groupe_id in actuels - set(desire):
            self._poser_regle(cle_externe, groupe_id, ["clear"])

    def _poser_regle(self, cle_externe: str, groupe_id: str, verbes: list[str]) -> None:
        base = ["groupfolders:permissions", str(cle_externe), "/", "-g", groupe_id, "--"]
        # clear systématique avant pose -> état net, idempotent.
        self._occ([*base, "clear"])
        if verbes != ["clear"]:
            self._occ([*base, *verbes])

    def _groupes_acl_racine(self, folder: dict[str, Any]) -> set[str]:
        lignes = executer_select(
            "SELECT a.mapping_type, a.mapping_id, c.path "
            "FROM {p}group_folders_acl a "
            "JOIN {p}filecache c ON c.fileid = a.fileid "
            "WHERE c.storage = ?",
            [folder["storageId"]],
            alias=self._ssh_alias,
            occ_dir=self._occ_dir,
        )
        return {
            str(ligne["mapping_id"])
            for ligne in lignes
            if ligne.get("path") in _CHEMINS_RACINE and ligne["mapping_type"] == "group"
        }

    def lire_droits_effectifs(self, cle_externe: str) -> frozenset[DroitGroupe]:
        """Relit l'état réel d'un Group Folder (réconciliation / détection de dérive).

        `cle_externe` = id du Group Folder (str).
        - ACL désactivée -> droits niveau folder (`groups_list`, sens inverse du masque).
        - ACL activée -> règles fines par chemin lues en SQL (group_folders_acl), pour
          CETTE tranche limitées au chemin RACINE du folder (la gestion par sous-chemin
          viendra avec la notion de Ressource=chemin). Le JOIN sur filecache exclut
          nativement les règles orphelines (folders supprimés).
        Un groupe sans droit (deny read / masque sans read) est omis du frozenset.
        """
        cible = self._folder_par_id(cle_externe)
        if cible is None:
            raise KeyError(f"Group Folder introuvable : {cle_externe!r}")

        if not cible.get("acl"):
            return self._droits_niveau_folder(cible)
        return self._droits_acl_racine(cible)

    def _droits_niveau_folder(self, folder: dict[str, Any]) -> frozenset[DroitGroupe]:
        droits: set[DroitGroupe] = set()
        for groupe_id, masque in (folder.get("groups_list") or {}).items():
            matrice = permissions_nextcloud_vers_matrice(int(masque))
            if matrice is not None:
                droits.add(DroitGroupe(groupe_id, matrice))
        return frozenset(droits)

    def _droits_acl_racine(self, folder: dict[str, Any]) -> frozenset[DroitGroupe]:
        lignes = executer_select(
            "SELECT a.mapping_type, a.mapping_id, a.mask, a.permissions, c.path "
            "FROM {p}group_folders_acl a "
            "JOIN {p}filecache c ON c.fileid = a.fileid "
            "WHERE c.storage = ?",
            [folder["storageId"]],
            alias=self._ssh_alias,
            occ_dir=self._occ_dir,
        )
        droits: set[DroitGroupe] = set()
        for ligne in lignes:
            if ligne.get("path") not in _CHEMINS_RACINE:
                continue  # tranche C1 : règle racine uniquement
            matrice = regle_acl_vers_matrice(int(ligne["mask"]), int(ligne["permissions"]))
            if matrice is not None:
                droits.add(DroitGroupe(str(ligne["mapping_id"]), matrice))
        return frozenset(droits)
