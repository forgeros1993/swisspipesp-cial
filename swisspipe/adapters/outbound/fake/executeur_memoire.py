"""Exécuteur de projection transverse EN MÉMOIRE — pendant fake de ExecuteurProjectionOcc.

Implémente le Protocol applicatif ExecuteurProjection (application/projection_service).
Sert au wiring CLI en mode SWISSPIPE_ADAPTER=fake (hermétique) ; les tests utilisent leur
propre fake instrumenté. État volatile (un process) — suffisant pour un dry-run/smoke.
"""

from __future__ import annotations

from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.services.delta_projection import DeltaProjection


class ExecuteurProjectionMemoire:
    """État ACL simulé { sous_chemin → { groupe → Matrice } } + accès base."""

    def __init__(self) -> None:
        self.etat: dict[str, dict[str, Matrice]] = {}
        self.acces_base: set[str] = set()

    def lire_etat(self) -> dict[str, dict[str, Matrice]]:
        return {nom: dict(par_g) for nom, par_g in self.etat.items()}

    def appliquer_delta(self, delta: DeltaProjection, groupes_desires: frozenset[str]) -> None:
        self.acces_base |= groupes_desires
        for (nom, grp), m in {**delta.a_creer, **delta.a_modifier}.items():
            self.etat.setdefault(nom, {})[grp] = m
        for nom, grp in delta.a_retirer:
            self.etat.get(nom, {}).pop(grp, None)
            if nom in self.etat and not self.etat[nom]:
                del self.etat[nom]
        retires = {g for (_nom, g) in delta.a_retirer} - groupes_desires
        self.acces_base -= retires
