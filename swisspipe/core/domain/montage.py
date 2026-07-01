"""Value object Montage — une FENÊTRE d'une instance transverse sur un hôte (spec §4.4/§5.5).

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Un montage DÉCIDE (INV-1) : OÙ (espace hôte + chemin) et le PLAFOND de droits par
ressource (RÉUTILISE la Matrice L1 — pas de nouveau type). Il ne nomme JAMAIS un
bénéficiaire (aucun groupe/personne ici : QUI viendra via les octrois, hors de ce type).

La `portee` (§5.5) est une FENÊTRE : elle déclare quelle portion de l'arbo de l'instance
CE montage expose — jamais une copie. Une même instance montée deux fois avec deux portées
= deux fenêtres sur UN SEUL stock. Un montage s'ARCHIVE (réversible), il ne se supprime
pas en dur (§3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from swisspipe.core.domain.matrice import Matrice


def plafond_vers_jsonb(matrice_plafond: Mapping[str, Matrice]) -> dict[str, Any]:
    """Sérialise le plafond par ressource : {ressource → matrice jsonb} (spec §4.4)."""
    return {ressource: m.vers_jsonb() for ressource, m in matrice_plafond.items()}


def plafond_depuis_jsonb(data: Mapping[str, Any]) -> dict[str, Matrice]:
    """Désérialise le plafond par ressource. Inverse de `plafond_vers_jsonb`."""
    return {ressource: Matrice.depuis_jsonb(v) for ressource, v in data.items()}


class EtatMontage(Enum):
    """État d'un montage. ARCHIVE = fenêtre fermée (réversible), pas une suppression dure."""

    ACTIF = "actif"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class Portee:
    """Portion de l'arbo de l'instance exposée par un montage (§5.5) — un ensemble de
    chemins de ressources. Non vide (une fenêtre expose quelque chose)."""

    chemins: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chemins", frozenset(self.chemins))
        if not self.chemins:
            raise ValueError("une portée doit exposer au moins une ressource")


@dataclass(frozen=True)
class Montage:
    """Fenêtre d'une instance sur un hôte. Décide OÙ + PLAFOND, jamais QUI (INV-1).

    `consenti_par` = admin de l'hôte qui a consenti (règle des DEUX CLÉS) — c'est un
    auteur de consentement, PAS un bénéficiaire de droits. `matrice_plafond` (spec §4.4)
    est un PLAFOND PAR RESSOURCE : {ressource → Matrice L1}. Règle de sûreté : il doit
    couvrir CHAQUE ressource de la portée. `consenti_at` = horodatage (métadonnée, injecté).
    """

    id: str
    espace_transverse_id: str
    espace_hote_id: str
    chemin_hote: str
    portee: Portee
    matrice_plafond: Mapping[str, Matrice]
    consenti_par: str
    consenti_at: str | None = None
    etat: EtatMontage = EtatMontage.ACTIF

    def __post_init__(self) -> None:
        if not isinstance(self.portee, Portee):
            raise TypeError(f"portee doit être une Portee, reçu {type(self.portee)!r}")
        object.__setattr__(self, "matrice_plafond", dict(self.matrice_plafond))
        for ressource, m in self.matrice_plafond.items():
            if not isinstance(m, Matrice):
                raise TypeError(f"plafond de {ressource!r} doit être une Matrice, reçu {type(m)!r}")
        if not isinstance(self.etat, EtatMontage):
            raise TypeError(f"etat doit être un EtatMontage, reçu {type(self.etat)!r}")
        # Règle des deux clés (INV-1) : pas de montage sans consentement de l'hôte.
        if not self.consenti_par:
            raise ValueError("consentement de l'hôte requis (règle des deux clés)")
        if not self.chemin_hote:
            raise ValueError("chemin_hote requis")
        # Sûreté §4.4 : le plafond doit couvrir CHAQUE ressource de la portée.
        non_couvertes = sorted(self.portee.chemins - set(self.matrice_plafond))
        if non_couvertes:
            raise ValueError(
                "plafond incomplet — ressource(s) de portée sans plafond : "
                + ", ".join(non_couvertes)
            )

    @property
    def exposees(self) -> frozenset[str]:
        """Ressources réellement exposées : la portée si actif, rien si archivé."""
        return self.portee.chemins if self.etat is EtatMontage.ACTIF else frozenset()

    def archiver(self) -> Montage:
        """Ferme la fenêtre (etat='archive') — la portée est conservée (réversible, §3)."""
        return replace(self, etat=EtatMontage.ARCHIVE)

    def reactiver(self) -> Montage:
        """Rouvre un montage archivé (réversibilité, §3)."""
        return replace(self, etat=EtatMontage.ACTIF)


def monter(
    *,
    montage_id: str,
    espace_transverse_id: str,
    chemins_instance: Iterable[str],
    espace_hote_id: str,
    chemin_hote: str,
    portee: Portee,
    matrice_plafond: Mapping[str, Matrice],
    consenti_par: str,
    consenti_at: str | None = None,
) -> Montage:
    """Fabrique un Montage valide (spec §4.4). Pur.

    Vérifie que la portée ne référence QUE des ressources existant réellement dans
    l'instance (`chemins_instance`) — exposer un dossier absent est refusé (ValueError).
    Le consentement (deux clés), la couverture du plafond par ressource et les types sont
    validés par le Montage lui-même.
    """
    disponibles = set(chemins_instance)
    absentes = sorted(portee.chemins - disponibles)
    if absentes:
        raise ValueError(
            "portée invalide — ressource(s) absente(s) de l'instance : " + ", ".join(absentes)
        )
    return Montage(
        id=montage_id,
        espace_transverse_id=espace_transverse_id,
        espace_hote_id=espace_hote_id,
        chemin_hote=chemin_hote,
        portee=portee,
        matrice_plafond=matrice_plafond,
        consenti_par=consenti_par,
        consenti_at=consenti_at,
    )
