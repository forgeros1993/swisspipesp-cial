"""Service de réassignation départ/remplacement (§10.3). Couche APPLICATIVE.

Règle de dépendance : `application → core, persistence`. Transmet SÉLECTIVEMENT des
Octrois L1 d'un groupe personnel A (partant) vers un groupe personnel B (remplaçant) :
UNIQUEMENT la sélection donnée — jamais tout par défaut (garde-fou « remplaçant ≠ clone »).

Règles (§10.3) :
- A et B doivent être PERSONNELS (sinon refus) ;
- ne transmet QUE les ressources sélectionnées (sélection vide -> rien) ;
- ne touche JAMAIS les appartenances organisationnelles (aucune écriture sur groupe_membre).

Chaque octroi transmis va dans journal_acces (action='octroi',
cause={type:'reassignation', source_groupe:A}). La révocation des droits de A (qui part)
réutilise la révocation existante et n'est PAS faite ici.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.core.domain.acteurs import TypeGroupe
from swisspipe.persistence.models import ActionJournal, Groupe, JournalAcces
from swisspipe.persistence.models import Octroi as OctroiModel


class GroupePersonnelRequisError(ValueError):
    """La source ET la cible d'une réassignation doivent être des groupes personnels (§10.3)."""


class GroupeIntrouvableError(LookupError):
    """Groupe source ou cible introuvable."""


@dataclass(frozen=True)
class Reassignation:
    """Résultat : les octrois transmis (ids côté cible)."""

    octroi_ids: tuple[uuid.UUID, ...]


def _exiger_personnel(session: Session, groupe_id: uuid.UUID, role: str) -> None:
    groupe = session.get(Groupe, groupe_id)
    if groupe is None:
        raise GroupeIntrouvableError(f"groupe {role} {groupe_id} introuvable")
    if groupe.type is not TypeGroupe.PERSONNEL:
        raise GroupePersonnelRequisError(
            f"le groupe {role} doit être personnel (§10.3), reçu {groupe.type.value}"
        )


def transmettre_octrois(
    session: Session,
    *,
    groupe_source_id: uuid.UUID,
    groupe_cible_id: uuid.UUID,
    ressource_ids: Iterable[uuid.UUID],
    acteur: str,
) -> Reassignation:
    """Transmet les octrois SÉLECTIONNÉS de A vers B (groupes personnels). Rien d'autre."""
    _exiger_personnel(session, groupe_source_id, "source")
    _exiger_personnel(session, groupe_cible_id, "cible")

    selection = list(ressource_ids)
    transmis: list[uuid.UUID] = []
    for ressource_id in selection:
        source = session.scalar(
            select(OctroiModel).where(
                OctroiModel.ressource_id == ressource_id,
                OctroiModel.groupe_id == groupe_source_id,
            )
        )
        if source is None:
            continue  # A ne détient rien sur cette ressource -> rien à transmettre
        octroi = OctroiModel(
            ressource_id=ressource_id,
            groupe_id=groupe_cible_id,
            mode=source.mode,
            matrice=source.matrice,
        )
        session.add(octroi)
        session.add(
            JournalAcces(
                ressource_id=ressource_id,
                groupe_id=groupe_cible_id,
                action=ActionJournal.OCTROI,
                matrice_avant=None,
                matrice_apres=source.matrice,
                cause={
                    "type": "reassignation",
                    "source_groupe": str(groupe_source_id),
                },
                acteur=acteur,
            )
        )
        session.flush()
        transmis.append(octroi.id)

    return Reassignation(octroi_ids=tuple(transmis))
