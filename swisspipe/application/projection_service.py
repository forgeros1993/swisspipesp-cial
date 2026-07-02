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
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import (
    PlanProjection,
    planifier_projection_occ,
)
from swisspipe.adapters.outbound.nextcloud.traduction import matrice_projetable
from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.montage import EtatMontage
from swisspipe.core.ports.adaptateur_ressource import DroitGroupe
from swisspipe.core.services.delta_projection import DeltaProjection, calculer_delta
from swisspipe.core.services.droits_effectifs import borner_matrice
from swisspipe.persistence.models import Groupe, Montage, Ressource
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
    portée. Lecture seule sur le core DB LOCAL (aucun contact serveur).

    Un montage ARCHIVÉ (fenêtre fermée, §3 étape 2) projette un état désiré VIDE :
    aucune ressource exposée — via le delta, ça donne un plan de retrait pur (INV-5).
    Les droits sont clés par `groupe.cle` (le nom de groupe NC réel, modèle L1), pas
    par l'UUID interne.
    """
    montage = session.get(Montage, montage_id)
    if montage is None:
        raise MontageIntrouvableError(f"montage {montage_id} introuvable")
    if montage.etat is EtatMontage.ARCHIVE:
        return EtatProjete(chemin_hote=montage.chemin_hote, ressources=())
    plafonds = montage.matrice_plafond  # plafond PAR RESSOURCE (spec §4.4)
    chemins_exposes = set(montage.portee["chemins"])

    ressources: list[RessourceProjetee] = []
    for ressource in session.scalars(
        select(Ressource)
        .where(Ressource.espace_id == montage.espace_transverse_id)
        .order_by(Ressource.chemin)
    ).all():
        if ressource.chemin not in chemins_exposes:
            continue  # hors portée -> absent de la projection
        plafond = Matrice.depuis_jsonb(plafonds[ressource.chemin])  # plafond de CETTE ressource
        droits: set[DroitGroupe] = set()
        # groupe.cle = le nom de groupe Nextcloud réel (modèle L1) — la projection cible
        # ce nom, jamais l'UUID interne (qui n'existe pas côté serveur).
        for octroi, groupe_cle in session.execute(
            select(OctroiModel, Groupe.cle)
            .join(Groupe, Groupe.id == OctroiModel.groupe_id)
            .where(OctroiModel.ressource_id == ressource.id)
        ).all():
            if octroi.matrice is None:  # HERITER/REFUSER : pas de matrice à projeter
                continue
            borne = borner_matrice(Matrice.depuis_jsonb(octroi.matrice), plafond)
            droits.add(DroitGroupe(groupe_cle, borne))
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


# --- Reconcile orchestré (moule du reconcile L1 : désiré complet → actuel → delta) ----


class ExecuteurProjection(Protocol):
    """Contrat applicatif (duck-typé) d'un exécutant de projection transverse.

    PAS un port du cœur (le seul port cœur reste AdaptateurRessource) : c'est une
    interface de la couche application, implémentée par un fake (tests) et par
    l'exécuteur occ réel (adaptateur Nextcloud).
    """

    def lire_etat(self) -> dict[str, dict[str, Matrice]]:
        """État ACL RÉEL : { sous_chemin → { groupe → Matrice } }. Lecture seule."""
        ...

    def appliquer_delta(self, delta: DeltaProjection, groupes_desires: frozenset[str]) -> None:
        """Exécute UNIQUEMENT le delta (pose/modif/retrait + accès base). Jamais de
        destruction de données (INV-5)."""
        ...


@dataclass(frozen=True)
class RapportProjection:
    """Issue d'un reconcile de projection : le delta constaté + s'il a été appliqué."""

    delta: DeltaProjection
    applique: bool


def reconcilier_projection(
    session: Session,
    montage_id: uuid.UUID,
    *,
    executeur: ExecuteurProjection,
    apply: bool = False,
) -> RapportProjection:
    """Reconcile la projection d'un transverse monté (moule du reconcile L1, spec §3.2).

    Assemble l'état DÉSIRÉ COMPLET (etat_projete_transverse : borné plafond par
    ressource + limité à la portée, octrois déjà figés — INV-3 ; VIDE si montage
    archivé) → lit l'état RÉEL (executeur) → calcule le delta (cœur pur) →
    SHADOW/dry-run par défaut (aucune écriture) ; `apply=True` exécute UNIQUEMENT le
    delta. No-op STRICT si conforme (delta vide -> zéro mutation). Idempotent.
    """
    etat = etat_projete_transverse(session, montage_id)
    # Comparer LE COMPARABLE : CLASSEMENT/TÉLÉCHARGEMENT n'ont pas de verbe ACL (perte
    # documentée dans traduction.py) — sans cette normalisation, la relecture divergerait
    # du désiré à chaque run (a_modifier perpétuel, idempotence cassée).
    desire: dict[str, dict[str, Matrice]] = {
        r.nom: {dg.groupe_id: matrice_projetable(dg.matrice) for dg in r.droits}
        for r in etat.ressources
    }
    actuel = executeur.lire_etat()
    delta = calculer_delta(desire, actuel)

    if not apply or delta.est_vide:
        return RapportProjection(delta=delta, applique=False)

    groupes_desires = frozenset(g for par_groupe in desire.values() for g in par_groupe)
    executeur.appliquer_delta(delta, groupes_desires)
    return RapportProjection(delta=delta, applique=True)
