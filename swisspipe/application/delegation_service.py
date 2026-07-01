"""Service de délégation — curseur « déléguée » (§5.4). Couche APPLICATIVE.

Règle de dépendance : `application → core, persistence`. Sur une instance dont la
politique est DELEGUEE (ou LIBRE, cas dégénéré traité pareil), un admin attribue lui-même
un Octroi L1 à un GROUPE (personnel OU organisationnel, INV-4), BORNÉ par un plafond
(l'enveloppe consentie). Réutilise `Matrice.couvre` (garde-fou §9.3) : un droit demandé
AU-DELÀ du plafond est REJETÉ, rien n'est posé.

L'octroi va dans journal_acces (journal des DROITS), action='octroi',
cause={politique:'deleguee', …} — PAS dans journal_evenements.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from swisspipe.core.domain.matrice import Matrice, Mode
from swisspipe.core.domain.modele import PolitiqueDroits
from swisspipe.persistence.models import (
    ActionJournal,
    Espace,
    Groupe,
    JournalAcces,
    Modele,
    Ressource,
)
from swisspipe.persistence.models import Octroi as OctroiModel


class PolitiqueNonDelegueeError(ValueError):
    """L'instance n'autorise pas l'attribution à la main (politique 'imposee')."""


class DepassementPlafondError(ValueError):
    """Le droit demandé dépasse le plafond de délégation (§9.3) — rien posé."""


class CibleInvalideError(LookupError):
    """Groupe ou ressource cible introuvable / hors de l'instance."""


@dataclass(frozen=True)
class OctroiDelegue:
    """Résultat d'une attribution déléguée."""

    octroi_id: uuid.UUID


def attribuer_droit_delegue(
    session: Session,
    *,
    instance_espace_id: uuid.UUID,
    groupe_id: uuid.UUID,
    ressource_id: uuid.UUID,
    matrice: Matrice,
    plafond: Matrice,
    acteur: str,
) -> OctroiDelegue:
    """Attribue un Octroi L1 délégué, borné par `plafond`. Rejette si > plafond ou si la
    politique de l'instance n'est pas déléguée. Aucun octroi posé en cas de rejet."""
    espace = session.get(Espace, instance_espace_id)
    if espace is None or espace.modele_id is None:
        raise CibleInvalideError(f"instance {instance_espace_id} introuvable ou sans modèle")
    modele = session.get(Modele, espace.modele_id)
    if modele is None:
        raise CibleInvalideError(f"modèle {espace.modele_id} introuvable")
    if modele.politique_droits is PolitiqueDroits.IMPOSEE:
        raise PolitiqueNonDelegueeError(
            "attribution déléguée interdite : la politique de l'instance est 'imposee' "
            "(les droits viennent des rôles)"
        )

    # Garde-fou §9.3 : le plafond doit COUVRIR le droit demandé, sinon rejet (rien posé).
    if not plafond.couvre(matrice):
        raise DepassementPlafondError(
            f"droit {matrice.vers_jsonb()} au-delà du plafond {plafond.vers_jsonb()} — rejeté"
        )

    groupe = session.get(Groupe, groupe_id)
    if groupe is None:  # personnel OU organisationnel : les deux sont valides ici (INV-4)
        raise CibleInvalideError(f"groupe {groupe_id} introuvable")
    ressource = session.get(Ressource, ressource_id)
    if ressource is None or ressource.espace_id != instance_espace_id:
        raise CibleInvalideError(
            f"ressource {ressource_id} hors de l'instance {instance_espace_id}"
        )

    matrice_jsonb = matrice.vers_jsonb()
    octroi = OctroiModel(
        ressource_id=ressource_id,
        groupe_id=groupe_id,
        mode=Mode.MODIFIER,
        matrice=matrice_jsonb,
    )
    session.add(octroi)
    session.add(
        JournalAcces(
            ressource_id=ressource_id,
            groupe_id=groupe_id,
            action=ActionJournal.OCTROI,
            matrice_avant=None,
            matrice_apres=matrice_jsonb,
            cause={
                "type": "delegation",
                "politique": modele.politique_droits.value,
                "espace_id": str(instance_espace_id),
                "plafond": plafond.vers_jsonb(),
            },
            acteur=acteur,
        )
    )
    session.flush()
    return OctroiDelegue(octroi_id=octroi.id)
