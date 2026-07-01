"""Service de projection des transverses — mode OMBRE (le PLAN). Couche APPLICATIVE.

⚠️ HERMÉTIQUE : ce service ne touche AUCUN serveur Nextcloud (ni lecture ni écriture). Il
lit le core DB LOCAL, calcule l'état serveur DÉSIRÉ d'un transverse monté, et délègue à
l'adaptateur la construction du PLAN de commandes occ (qui ne sont PAS exécutées).

Séparation stricte :
- cœur : borne la matrice par le plafond du montage (borner_matrice, étape 4) — agnostique ;
- application (ici) : assemble depuis la DB (portée + octrois par groupe, déjà figés) ;
- adaptateur : traduit en commandes occ (planifier_projection_occ) — sans les exécuter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import (
    PlanProjection,
    planifier_projection_occ,
)
from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.ports.adaptateur_ressource import DroitGroupe
from swisspipe.core.services.droits_effectifs import borner_matrice
from swisspipe.persistence.models import Montage, Ressource
from swisspipe.persistence.models import Octroi as OctroiModel


class MontageIntrouvableError(LookupError):
    """Le montage ciblé n'existe pas."""


@dataclass(frozen=True)
class RessourceProjetee:
    """Une ressource exposée par le montage + ses droits effectifs (bornés) par groupe."""

    chemin: str
    nom: str
    droits: frozenset[DroitGroupe]


@dataclass(frozen=True)
class EtatProjete:
    """État serveur DÉSIRÉ d'un transverse monté (structure + permissions bornées)."""

    chemin_hote: str
    ressources: tuple[RessourceProjetee, ...]


def etat_projete_transverse(session: Session, montage_id: uuid.UUID) -> EtatProjete:
    """Calcule l'état désiré d'un transverse monté, borné par le plafond + limité à la
    portée. Lecture seule sur le core DB LOCAL (aucun contact serveur)."""
    montage = session.get(Montage, montage_id)
    if montage is None:
        raise MontageIntrouvableError(f"montage {montage_id} introuvable")
    plafond = Matrice.depuis_jsonb(montage.matrice_plafond)
    chemins_exposes = set(montage.portee["chemins"])

    ressources: list[RessourceProjetee] = []
    for ressource in session.scalars(
        select(Ressource)
        .where(Ressource.espace_id == montage.espace_transverse_id)
        .order_by(Ressource.chemin)
    ).all():
        if ressource.chemin not in chemins_exposes:
            continue  # hors portée -> absent de la projection
        droits: set[DroitGroupe] = set()
        for octroi in session.scalars(
            select(OctroiModel).where(OctroiModel.ressource_id == ressource.id)
        ).all():
            if octroi.matrice is None:  # HERITER/REFUSER : pas de matrice à projeter
                continue
            borne = borner_matrice(Matrice.depuis_jsonb(octroi.matrice), plafond)
            droits.add(DroitGroupe(str(octroi.groupe_id), borne))
        ressources.append(
            RessourceProjetee(
                chemin=ressource.chemin, nom=ressource.chemin.lstrip("/"), droits=frozenset(droits)
            )
        )
    return EtatProjete(chemin_hote=montage.chemin_hote, ressources=tuple(ressources))


def planifier_projection_transverse(session: Session, montage_id: uuid.UUID) -> PlanProjection:
    """PLAN de projection (mode ombre) d'un transverse monté. N'exécute AUCUNE commande."""
    etat = etat_projete_transverse(session, montage_id)
    return planifier_projection_occ(etat.chemin_hote, [(r.nom, r.droits) for r in etat.ressources])
