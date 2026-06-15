"""Réconciliation (logique PURE) — état désiré du cœur vs état réel de l'exécutant.

100% logique : stdlib + domaine + ports (DroitGroupe est du CONTRAT cœur, importable).
AUCUN réseau, AUCUNE persistance ici (garde-fou de pureté : test_core_purity, CLAUDE.md
§1). L'orchestrateur applicatif (qui câble Postgres + adaptateur) viendra en partie 2.

Rôle : produire l'état de droits DÉSIRÉ par ressource (`etat_desire`) et le comparer à
l'état RÉEL relu côté exécutant (`comparer_droits`) pour détecter une dérive — la
fondation de la protection contre les upgrades Nextcloud (CLAUDE.md §9). La réparation =
réappliquer le désiré via `appliquer_droits` (côté orchestrateur).

Décisions actées :
- `groupe.cle` = nom du groupe Nextcloud. La clé du groupe dans le cœur EST son
  identifiant côté exécutant ; `groupe_ids` reçus ici sont donc des noms NC.
- Niveau folder d'abord : ressource racine, `parents = {ressource_id: None}` (pas
  d'héritage). L'héritage par sous-chemin = L2.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.ports.adaptateur_ressource import DroitGroupe
from swisspipe.core.services.droits_effectifs import droit_effectif_groupe


def etat_desire(
    ressource_id: str,
    groupe_ids: Iterable[str],
    parents: Mapping[str, str | None],
    octrois: Mapping[tuple[str, str], Octroi],
) -> frozenset[DroitGroupe]:
    """État de droits désiré d'UNE ressource, sous la forme attendue par l'adaptateur.

    Pour chaque groupe, on calcule `droit_effectif_groupe` : si le résultat est
    `accessible` (matrice non None, pas bloqué) -> un `DroitGroupe(groupe_id, matrice)`,
    sinon le groupe est OMIS (pas d'accès = absent de l'état désiré).

    C'est le pendant « PAR GROUPE » de la décision du cœur — on veut une ACL par groupe
    NC. On n'utilise donc PAS `droit_effectif_compte` (qui combine les groupes d'UNE
    personne en une seule matrice : c'est une vue account-centric pour l'audit/visibilité,
    pas l'état à poser groupe par groupe sur l'exécutant).

    `groupe_ids` = noms NC (puisque `groupe.cle` = nom NC).
    """
    droits: set[DroitGroupe] = set()
    for groupe_id in groupe_ids:
        effectif = droit_effectif_groupe(ressource_id, groupe_id, parents, octrois)
        if effectif.accessible:
            assert effectif.matrice is not None  # garanti par .accessible
            droits.add(DroitGroupe(groupe_id, effectif.matrice))
    return frozenset(droits)


@dataclass(frozen=True)
class MatriceDivergente:
    """Un groupe présent des deux côtés mais avec une matrice différente."""

    groupe_id: str
    attendue: Matrice  # ce que le cœur veut
    reelle: Matrice  # ce qui est réellement appliqué côté exécutant


@dataclass(frozen=True)
class Divergence:
    """Diagnostic précis de l'écart entre l'état désiré et l'état réel.

    - `groupes_manquants` : désirés mais ABSENTS du réel (droits perdus — ex. après un
      upgrade qui a cassé Group Folders). À réappliquer.
    - `groupes_en_trop` : présents dans le réel mais PAS désirés (droits fantômes). À retirer.
    - `matrices_divergentes` : groupes des deux côtés, matrice différente (ex. réel =
      LECTURE, désiré = ÉCRITURE). À corriger.
    """

    groupes_manquants: frozenset[DroitGroupe] = frozenset()
    groupes_en_trop: frozenset[DroitGroupe] = frozenset()
    matrices_divergentes: frozenset[MatriceDivergente] = frozenset()

    @property
    def est_conforme(self) -> bool:
        """True si rien à réparer (les trois ensembles sont vides)."""
        return not (self.groupes_manquants or self.groupes_en_trop or self.matrices_divergentes)


def comparer_droits(
    desire: frozenset[DroitGroupe],
    reel: frozenset[DroitGroupe],
) -> Divergence:
    """Compare l'état désiré (cœur) à l'état réel (exécutant) et diagnostique la dérive.

    Pur et déterministe. Indexe par `groupe_id` (un groupe = au plus une matrice de
    chaque côté). Réparer = réappliquer le désiré (côté orchestrateur, partie 2).
    """
    par_groupe_desire = {dg.groupe_id: dg.matrice for dg in desire}
    par_groupe_reel = {dg.groupe_id: dg.matrice for dg in reel}

    manquants = frozenset(
        DroitGroupe(gid, matrice)
        for gid, matrice in par_groupe_desire.items()
        if gid not in par_groupe_reel
    )
    en_trop = frozenset(
        DroitGroupe(gid, matrice)
        for gid, matrice in par_groupe_reel.items()
        if gid not in par_groupe_desire
    )
    divergentes = frozenset(
        MatriceDivergente(gid, par_groupe_desire[gid], par_groupe_reel[gid])
        for gid in par_groupe_desire.keys() & par_groupe_reel.keys()
        if par_groupe_desire[gid] != par_groupe_reel[gid]
    )
    return Divergence(manquants, en_trop, divergentes)
