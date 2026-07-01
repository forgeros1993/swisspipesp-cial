"""Service de droits effectifs montage-aware — couche APPLICATIVE (câble cœur + persistance).

Règle de dépendance : `application → core, persistence`. Le CALCUL vit dans le cœur
(core/services/droits_effectifs) ; ce service ne fait qu'ASSEMBLER l'état figé depuis la
base (groupes du compte, octrois posés, plafond + portée du montage) et déléguer au cœur.

On CALCULE seulement (§9.3) — aucune projection sur le vrai Nextcloud. INV-3 : on lit
l'état courant (octrois déjà figés, dont ceux posés par rôle à l'étape 3), aucune
évaluation de rôle « live ».
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.services.droits_effectifs import DroitEffectif, droit_effectif_via_montage
from swisspipe.persistence.models import GroupeMembre, Montage, Ressource
from swisspipe.persistence.models import Octroi as OctroiModel


class MontageIntrouvableError(LookupError):
    """Le montage ciblé n'existe pas."""


def droit_effectif_montre(
    session: Session,
    *,
    compte_id: str,
    ressource_id: uuid.UUID,
    montage_id: uuid.UUID | None = None,
) -> DroitEffectif:
    """Droit effectif d'un compte sur une ressource, éventuellement VUE via un montage.

    `montage_id=None` -> calcul L1 pur (espace dimensionnel non monté). Sinon on applique
    le plafond (montage.matrice_plafond) et la portée (montage.portee) — anti-escalade §9.3.
    """
    res_str = str(ressource_id)

    # Groupes du compte (perso + orga) — réutilise l'appartenance L1 (groupe_membre).
    groupe_ids = [
        str(gid)
        for gid in session.scalars(
            select(GroupeMembre.groupe_id).where(GroupeMembre.compte_id == compte_id)
        ).all()
    ]
    groupes_connus = set(groupe_ids)

    # Octrois figés de la ressource pour ces groupes (état courant — inclut les octrois
    # posés par rôle à l'étape 3). Arbo transverse plate : la ressource est sa propre racine.
    octrois: dict[tuple[str, str], Octroi] = {}
    for o in session.scalars(
        select(OctroiModel).where(OctroiModel.ressource_id == ressource_id)
    ).all():
        gid = str(o.groupe_id)
        if gid in groupes_connus:
            octrois[(res_str, gid)] = Octroi.depuis_jsonb(
                {"mode": o.mode.value, "matrice": o.matrice}
            )
    parents: dict[str, str | None] = {res_str: None}

    plafond: Matrice | None = None
    portee: frozenset[str] | None = None
    if montage_id is not None:
        montage = session.get(Montage, montage_id)
        if montage is None:
            raise MontageIntrouvableError(f"montage {montage_id} introuvable")
        # Plafond PAR RESSOURCE (spec §4.4) : on borne par le plafond DE CETTE ressource.
        chemin_cible = session.scalar(select(Ressource.chemin).where(Ressource.id == ressource_id))
        plafonds = montage.matrice_plafond
        if chemin_cible is not None and chemin_cible in plafonds:
            plafond = Matrice.depuis_jsonb(plafonds[chemin_cible])
        chemins_exposes = set(montage.portee["chemins"])
        portee = frozenset(
            str(rid)
            for rid, chemin in session.execute(
                select(Ressource.id, Ressource.chemin).where(
                    Ressource.espace_id == montage.espace_transverse_id
                )
            ).all()
            if chemin in chemins_exposes
        )

    return droit_effectif_via_montage(
        groupe_ids, res_str, parents, octrois, plafond=plafond, portee=portee
    )
