"""Value object Rôle + pose des droits d'un rôle (spec §5.4 « curseur imposée »).

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Un Rôle est DÉFINI par un modèle (modele_id + cle + libelle). La matrice IMPOSÉE par
rôle vit sur le Modèle (modele.matrice_par_role) et RÉUTILISE la Matrice L1. `octrois_pour_role`
traduit cette matrice en Octrois L1 CONCRETS sur des ressources réelles — c'est la pose
figée (INV-3) : elle est calculée UNE fois à la désignation, jamais ré-évaluée à la lecture.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.octroi import Octroi


@dataclass(frozen=True)
class Role:
    """Rôle défini par un modèle. Identité = (modele_id, cle) ; `libelle` = nom affiché."""

    modele_id: str
    cle: str
    libelle: str

    def __post_init__(self) -> None:
        if not self.cle:
            raise ValueError("un rôle exige une clé non vide")


def octrois_pour_role(
    matrice_par_role: Mapping[str, Mapping[str, Matrice]],
    role_cle: str,
    ressource_par_dossier: Mapping[str, str],
) -> dict[str, Octroi]:
    """Traduit la matrice imposée d'un rôle en Octrois L1 concrets, par ressource.

    Pur et déterministe : pour chaque (dossier → Matrice) du rôle, pose un Octroi
    MODIFIER de cette matrice sur la ressource concrète correspondante
    (`ressource_par_dossier[dossier]`). Rôle inconnu -> aucun octroi (dict vide).
    RÉUTILISE l'Octroi L1 : c'est le SEUL type d'octroi (INV-4 : cible = un groupe).
    """
    octrois: dict[str, Octroi] = {}
    for dossier_cle, matrice in matrice_par_role.get(role_cle, {}).items():
        ressource_id = ressource_par_dossier[dossier_cle]
        octrois[ressource_id] = Octroi.modifier(matrice)
    return octrois
