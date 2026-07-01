"""Service de rôles — couche APPLICATIVE (câble cœur + persistance). Curseur « imposée » (§5.4).

Règle de dépendance : `application → core, persistence`. La LOGIQUE (traduction matrice
par rôle → Octrois L1) vit dans le cœur (core/domain/role.octrois_pour_role) ; ce service
ORCHESTRE la persistance.

Désigner un titulaire (spec §5.4) :
- vérifie que la cible est un groupe PERSONNEL (INV-4) ;
- crée une `role_affectation` figée (effectif_depuis, INV-3), source='humain' (INV-1) ;
- POSE des Octrois L1 CONCRETS (réutilise l'Octroi L1) selon la matrice par rôle du modèle ;
- trace chaque pose dans `journal_acces` (le journal des DROITS), action='octroi',
  groupe_id = le groupe personnel — PAS dans journal_evenements.
Retirer : révoque ces octrois (action='revocation') + marque retire_at (réversible).

INV-3 : les octrois sont FIGÉS ici, jamais ré-évalués à la lecture (aucune évaluation
de rôle « live » n'est ajoutée au calcul des droits).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swisspipe.core.domain.acteurs import TypeGroupe
from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.domain.role import octrois_pour_role
from swisspipe.core.domain.role_affectation import SourceAffectation
from swisspipe.persistence.models import (
    ActionJournal,
    Espace,
    Groupe,
    JournalAcces,
    Modele,
    Ressource,
    Role,
    RoleAffectation,
)
from swisspipe.persistence.models import Octroi as OctroiModel

ACTEUR_DEFAUT = "system:roles"


class GroupeNonPersonnelError(ValueError):
    """La cible d'une affectation n'est pas un groupe PERSONNEL (INV-4)."""


class RoleIntrouvableError(LookupError):
    """Le rôle / l'affectation ciblé n'existe pas."""


@dataclass(frozen=True)
class AffectationCree:
    """Résultat d'une désignation/retrait : identifiants persistés."""

    affectation_id: uuid.UUID
    octroi_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


def enregistrer_role(
    session: Session, *, modele_id: uuid.UUID, cle: str, libelle: str
) -> uuid.UUID:
    """Persiste un rôle défini par un modèle et renvoie son id."""
    row = Role(modele_id=modele_id, cle=cle, libelle=libelle)
    session.add(row)
    session.flush()
    return row.id


def _octrois_du_role(
    session: Session, espace_id: uuid.UUID, role_id: uuid.UUID
) -> dict[uuid.UUID, tuple[str, Octroi]]:
    """{ressource_id → (role_cle, Octroi)} imposés par le rôle sur l'instance. Pur côté cœur.

    Traduit la matrice par rôle du modèle en Octrois L1 concrets, en résolvant chaque
    dossier imposé vers la ressource réelle de l'instance (via son chemin `/{libelle}`).
    """
    role = session.get(Role, role_id)
    if role is None:
        raise RoleIntrouvableError(f"rôle {role_id} introuvable")
    espace = session.get(Espace, espace_id)
    if espace is None or espace.modele_id is None:
        raise RoleIntrouvableError(f"instance {espace_id} introuvable ou sans modèle")
    modele = session.get(Modele, espace.modele_id)
    if modele is None:
        raise RoleIntrouvableError(f"modèle {espace.modele_id} introuvable")

    # Matrice par rôle (jsonb) -> objets Matrice du cœur.
    brut = modele.matrice_par_role or {}
    matrice_par_role = {
        r: {dossier: Matrice.depuis_jsonb(m) for dossier, m in par_dossier.items()}
        for r, par_dossier in brut.items()
    }

    # dossier_cle -> libelle (depuis l'arborescence du modèle) -> chemin '/{libelle}'.
    libelle_par_dossier = {d["cle"]: d["libelle"] for d in modele.arborescence["dossiers"]}
    # Le cœur travaille avec des ids de ressource en str (agnostique) ; ici on convertit
    # les UUID de persistance en str à l'aller, puis on reconvertit au retour.
    ressource_par_chemin: dict[str, uuid.UUID] = {}
    for rid, chemin in session.execute(
        select(Ressource.id, Ressource.chemin).where(Ressource.espace_id == espace_id)
    ).all():
        ressource_par_chemin[chemin] = rid
    ressource_par_dossier: dict[str, str] = {
        dossier: str(ressource_par_chemin[f"/{libelle_par_dossier[dossier]}"])
        for dossier in matrice_par_role.get(role.cle, {})
        if dossier in libelle_par_dossier
    }

    octrois = octrois_pour_role(matrice_par_role, role.cle, ressource_par_dossier)
    return {uuid.UUID(rid): (role.cle, octroi) for rid, octroi in octrois.items()}


def designer_titulaire_role(
    session: Session,
    *,
    instance_espace_id: uuid.UUID,
    role_id: uuid.UUID,
    groupe_perso_id: uuid.UUID,
    acteur: str,
    effectif_depuis: datetime,
    source: SourceAffectation = SourceAffectation.HUMAIN,
) -> AffectationCree:
    """Désigne un titulaire : affectation figée + pose des Octrois L1 + journal_acces."""
    groupe = session.get(Groupe, groupe_perso_id)
    if groupe is None or groupe.type is not TypeGroupe.PERSONNEL:
        raise GroupeNonPersonnelError(
            f"la cible d'une affectation doit être un groupe personnel (INV-4) : {groupe_perso_id}"
        )

    octrois = _octrois_du_role(session, instance_espace_id, role_id)

    affectation = RoleAffectation(
        espace_id=instance_espace_id,
        role_id=role_id,
        groupe_perso_id=groupe_perso_id,
        source=source,
        effectif_depuis=effectif_depuis,
    )
    session.add(affectation)
    session.flush()

    octroi_ids: list[uuid.UUID] = []
    for ressource_id, (role_cle, octroi) in octrois.items():
        matrice_jsonb = octroi.matrice.vers_jsonb() if octroi.matrice is not None else None
        row = OctroiModel(
            ressource_id=ressource_id,
            groupe_id=groupe_perso_id,
            mode=octroi.mode,
            matrice=matrice_jsonb,
        )
        session.add(row)
        session.add(
            JournalAcces(
                ressource_id=ressource_id,
                groupe_id=groupe_perso_id,
                action=ActionJournal.OCTROI,
                matrice_avant=None,
                matrice_apres=matrice_jsonb,
                cause={
                    "type": "role",
                    "role_id": str(role_id),
                    "role_cle": role_cle,
                    "espace_id": str(instance_espace_id),
                    "source": source.value,
                },
                acteur=acteur,
            )
        )
        session.flush()
        octroi_ids.append(row.id)

    return AffectationCree(affectation_id=affectation.id, octroi_ids=tuple(octroi_ids))


def retirer_titulaire(
    session: Session, affectation_id: uuid.UUID, *, acteur: str
) -> AffectationCree:
    """Retire un titulaire : révoque les Octrois posés + marque retire_at (réversible).

    Trace chaque révocation dans journal_acces (action='revocation'). L'affectation est
    conservée (retire_at renseigné), jamais supprimée en dur.
    """
    affectation = session.get(RoleAffectation, affectation_id)
    if affectation is None:
        raise RoleIntrouvableError(f"affectation {affectation_id} introuvable")

    octrois = _octrois_du_role(session, affectation.espace_id, affectation.role_id)
    for ressource_id, (role_cle, _octroi) in octrois.items():
        existant = session.scalar(
            select(OctroiModel).where(
                OctroiModel.ressource_id == ressource_id,
                OctroiModel.groupe_id == affectation.groupe_perso_id,
            )
        )
        matrice_avant = existant.matrice if existant is not None else None
        if existant is not None:
            session.delete(existant)
        session.add(
            JournalAcces(
                ressource_id=ressource_id,
                groupe_id=affectation.groupe_perso_id,
                action=ActionJournal.REVOCATION,
                matrice_avant=matrice_avant,
                matrice_apres=None,
                cause={
                    "type": "role",
                    "role_id": str(affectation.role_id),
                    "role_cle": role_cle,
                    "espace_id": str(affectation.espace_id),
                    "source": affectation.source.value,
                },
                acteur=acteur,
            )
        )

    affectation.retire_at = func.now()
    session.flush()
    return AffectationCree(affectation_id=affectation_id)
