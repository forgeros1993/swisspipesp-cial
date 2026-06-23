"""Seed du cœur depuis la photo d'inventaire (T3, phase 3.b — Option A).

Traduit l'état d'accès RÉEL (groupfolders coarse) dans le modèle possédé. Écrit UNIQUEMENT
dans le Postgres pointé par DATABASE_URL (jetable en T3). Aucune écriture Nextcloud.

Stratégie zéro-divergence (Option A) : pour chaque (folder, groupe), l'octroi porte la matrice
= permissions_nextcloud_vers_matrice(masque réel) — la MÊME traduction que lire_droits_effectifs.
Donc le désiré du cœur == le réel relu -> shadow conforme par construction.
"""

from __future__ import annotations

import re
from typing import Any

from swisspipe.adapters.inbound.composition import construire_sessionmaker
from swisspipe.core.domain.matrice import Matrice, Mode
from swisspipe.persistence.models import (
    Dimension,
    Espace,
    EspaceCoordonnee,
    Groupe,
    NatureEspace,
    Octroi,
    Ressource,
    RessourceMapping,
    TypeGroupe,
    ValeurDimension,
    signature_combinaison,
)
from tools.inventaire_prod import photographier


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "x"


def seed(photo: dict[str, Any], database_url: str | None = None) -> dict[str, int]:
    fabrique = construire_sessionmaker(database_url)
    cpt = {"dimensions": 0, "valeurs": 0, "espaces": 0, "groupes": 0, "ressources": 0, "octrois": 0}
    with fabrique() as s:
        # 2 dimensions (chaîne linéaire Société -> Département) — DOC 3.
        dim_soc = Dimension(cle="societe", libelle="Société", rang=0)
        s.add(dim_soc)
        s.flush()
        s.add(Dimension(cle="departement", libelle="Département", rang=1, parent_id=dim_soc.id))
        cpt["dimensions"] = 2

        groupes: dict[str, Groupe] = {}

        def groupe(nom: str) -> Groupe:
            if nom not in groupes:
                g = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle=nom)
                s.add(g)
                s.flush()
                groupes[nom] = g
                cpt["groupes"] += 1
            return groupes[nom]

        for soc in photo["societes"]:
            cle_val = _slug(soc["mount_point"] or soc["folder_id"])
            valeur = ValeurDimension(
                dimension_id=dim_soc.id, cle=cle_val, libelle=soc["mount_point"] or soc["folder_id"]
            )
            s.add(valeur)
            s.flush()
            cpt["valeurs"] += 1

            espace = Espace(
                nature=NatureEspace.DIMENSIONNEL,
                combinaison_signature=signature_combinaison([("societe", cle_val)]),
            )
            s.add(espace)
            s.flush()
            s.add(
                EspaceCoordonnee(espace_id=espace.id, dimension_id=dim_soc.id, valeur_id=valeur.id)
            )
            cpt["espaces"] += 1

            ressource = Ressource(type="folder", espace_id=espace.id, chemin="/")
            s.add(ressource)
            s.flush()
            cpt["ressources"] += 1
            s.add(
                RessourceMapping(
                    ressource_id=ressource.id, adaptateur="nextcloud", cle_externe=soc["folder_id"]
                )
            )

            for g in soc["groupes"]:
                if g["matrice"] is None:
                    continue  # masque sans read -> aucun droit positif (omis, cohérent lecture)
                s.add(
                    Octroi(
                        ressource_id=ressource.id,
                        groupe_id=groupe(g["groupe"]).id,
                        mode=Mode.MODIFIER,
                        matrice=Matrice.depuis_jsonb(g["matrice"]).vers_jsonb(),
                    )
                )
                cpt["octrois"] += 1
        s.commit()
    return cpt


if __name__ == "__main__":
    resultat = seed(photographier())
    print("SEED:", resultat)
