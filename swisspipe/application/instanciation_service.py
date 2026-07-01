"""Service d'instanciation — couche APPLICATIVE (câble cœur + persistance).

Règle de dépendance : `application → core, persistence`. Le cœur (core/) ne connaît
JAMAIS ce module (garde-fou de pureté). La LOGIQUE d'instanciation vit dans le cœur
(core/domain/instance.instancier : validation métadonnées + fabrication du squelette) ;
ce service ne fait qu'ORCHESTRER la persistance de ce que le cœur a décidé.

Ce qu'une instanciation persiste (spec §5.2/§5.3) :
- un `espace` nature='transverse' lié à son modèle (modele_id), portant les métadonnées ;
- une `ressource` abstraite par dossier imposé (le squelette) ;
- une ligne `journal_evenements` (type='instanciation', append-only) — et RIEN dans
  `journal_acces` (un événement de cycle de vie n'est pas un droit, INV-6).

INV-1/INV-5 : instancier décide OÙ (espace + squelette), jamais QUI ; aucune personne
n'est nommée ici.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from swisspipe.core.domain.instance import instancier
from swisspipe.core.domain.modele import Modele
from swisspipe.persistence.models import (
    Espace,
    JournalEvenement,
    NatureEspace,
    Ressource,
    TypeEvenement,
)
from swisspipe.persistence.models import Modele as ModeleRow


@dataclass(frozen=True)
class InstanceCree:
    """Résultat d'une instanciation : identifiants persistés (pour l'appelant/CLI)."""

    espace_id: uuid.UUID
    ressource_ids: tuple[uuid.UUID, ...]
    evenement_id: uuid.UUID


def enregistrer_modele(session: Session, modele: Modele) -> uuid.UUID:
    """Persiste un gabarit (modele.py) et renvoie son id. Sérialise au format domaine."""
    jsonb = modele.vers_jsonb()
    row = ModeleRow(
        nom=jsonb["nom"],
        arborescence=jsonb["arborescence_imposee"],
        schema_metadonnees=jsonb["schema_metadonnees"],
        roles=jsonb["roles"],
        matrice_par_role=jsonb["matrice_par_role"],
        politique_droits=modele.politique_droits,
    )
    session.add(row)
    session.flush()
    return row.id


def instancier_modele(
    session: Session,
    modele: Modele,
    *,
    modele_id: uuid.UUID,
    nom: str,
    metadonnees: Mapping[str, Any],
    acteur: str,
    cle_reconciliation: str | None = None,
) -> InstanceCree:
    """Instancie un Modèle : espace transverse + squelette + événement d'instanciation.

    La validation (métadonnées conformes au schéma) et la fabrication du squelette sont
    faites par le CŒUR (`instancier`). Si les métadonnées ne sont pas conformes, le cœur
    lève ValueError AVANT toute écriture -> rien n'est persisté. `modele_id` est l'id du
    gabarit déjà persisté (cf. enregistrer_modele), utilisé comme FK de l'espace.
    """
    espace_id = uuid.uuid4()

    # Le cœur décide (valide + fabrique le squelette). Lève avant toute persistance.
    instance = instancier(
        modele,
        nom=nom,
        metadonnees=metadonnees,
        instance_id=str(espace_id),
        cle_reconciliation=cle_reconciliation,
    )

    # Un espace transverse n'a pas de coordonnées : signature synthétique unique dérivée
    # de l'id -> contrainte d'unicité (§4.2) préservée sans collision entre instances.
    espace = Espace(
        id=espace_id,
        nature=NatureEspace.TRANSVERSE,
        combinaison_signature=f"transverse:{espace_id}",
        modele_id=modele_id,
        metadonnees=dict(instance.metadonnees),
        cle_reconciliation=instance.cle_reconciliation,
        created_via="instanciation",
    )
    session.add(espace)

    ressources = [
        Ressource(espace_id=espace_id, type=r.type, chemin=r.chemin)
        for r in instance.ressources_squelette
    ]
    session.add_all(ressources)

    evenement = JournalEvenement(
        espace_id=espace_id,
        type_evenement=TypeEvenement.INSTANCIATION,
        cause={
            "modele_id": str(modele_id),
            "instance_id": str(espace_id),
            "nom": nom,
        },
        acteur=acteur,
    )
    session.add(evenement)
    session.flush()

    return InstanceCree(
        espace_id=espace_id,
        ressource_ids=tuple(r.id for r in ressources),
        evenement_id=evenement.id,
    )
