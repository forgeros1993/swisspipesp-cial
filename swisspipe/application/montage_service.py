"""Service de montage — couche APPLICATIVE (câble cœur + persistance).

Règle de dépendance : `application → core, persistence`. Le cœur (core/) ne connaît
JAMAIS ce module. La LOGIQUE (validation portée ⊆ instance, deux clés) vit dans le cœur
(core/domain/montage.monter) ; ce service ORCHESTRE la persistance.

Ce qu'un montage persiste (spec §4.4/§5.5) :
- une ligne `montage` : OÙ (hôte + chemin) + PLAFOND (Matrice L1 en jsonb) + portée + état ;
- une ligne `journal_evenements` (type='montage', append-only) — RIEN dans `journal_acces`.
Archiver (§3) : etat='archive' (réversible, pas de suppression dure) + événement 'demontage'.

AUCUN nouveau port : le seul port du cœur reste AdaptateurRessource. INV-1 : le montage
décide OÙ + PLAFOND, jamais QUI (aucune personne nommée ; consenti_par = auteur du
consentement de l'hôte, pas un bénéficiaire).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.montage import EtatMontage, Portee, monter, plafond_vers_jsonb
from swisspipe.persistence.models import JournalEvenement, Montage, Ressource, TypeEvenement


class MontageIntrouvableError(LookupError):
    """Le montage ciblé n'existe pas — archivage impossible."""


@dataclass(frozen=True)
class MontageCree:
    """Résultat d'un montage : identifiant persisté (pour l'appelant/CLI)."""

    montage_id: uuid.UUID


def _tracer(
    session: Session,
    type_evenement: TypeEvenement,
    espace_id: uuid.UUID,
    montage_id: uuid.UUID,
    espace_transverse_id: uuid.UUID,
    espace_hote_id: uuid.UUID,
    acteur: str,
) -> None:
    session.add(
        JournalEvenement(
            espace_id=espace_id,
            type_evenement=type_evenement,
            cause={
                "montage_id": str(montage_id),
                "espace_transverse_id": str(espace_transverse_id),
                "espace_hote_id": str(espace_hote_id),
            },
            acteur=acteur,
        )
    )


def monter_instance(
    session: Session,
    *,
    espace_transverse_id: uuid.UUID,
    espace_hote_id: uuid.UUID,
    chemin_hote: str,
    portee_chemins: Iterable[str],
    matrice_plafond: Matrice | Mapping[str, Matrice],
    consenti_par: str,
    acteur: str,
    consenti_at: str | None = None,
) -> MontageCree:
    """Monte une instance sur un hôte (spec §4.4). Le cœur valide (portée ⊆ ressources
    réelles de l'instance + deux clés + couverture du plafond) AVANT toute écriture.

    `matrice_plafond` est un plafond PAR RESSOURCE {ressource → Matrice}. Une Matrice nue
    est acceptée par commodité et étendue en plafond uniforme sur toute la portée.
    """
    portee = frozenset(portee_chemins)
    if isinstance(matrice_plafond, Matrice):
        plafond: Mapping[str, Matrice] = {chemin: matrice_plafond for chemin in portee}
    else:
        plafond = dict(matrice_plafond)

    # Unicité du point de montage parmi les montages ACTIFS : deux montages actifs au
    # même chemin_hote résoudraient le MÊME Group Folder (mountPoint = la clé externe)
    # et le reconcile de l'un retirerait les règles de l'autre (flapping destructif).
    # Un montage ARCHIVÉ libère son chemin (réversibilité préservée).
    doublon = session.scalar(
        select(Montage.id).where(
            Montage.chemin_hote == chemin_hote, Montage.etat == EtatMontage.ACTIF
        )
    )
    if doublon is not None:
        raise ValueError(
            f"chemin_hote {chemin_hote!r} déjà occupé par le montage actif {doublon} — "
            "démonter (archiver) d'abord, ou choisir un autre point de montage"
        )

    chemins_instance = set(
        session.scalars(
            select(Ressource.chemin).where(Ressource.espace_id == espace_transverse_id)
        ).all()
    )
    montage_id = uuid.uuid4()

    # Le cœur décide : lève ValueError si portée hors instance / consentement / plafond incomplet.
    dom = monter(
        montage_id=str(montage_id),
        espace_transverse_id=str(espace_transverse_id),
        chemins_instance=chemins_instance,
        espace_hote_id=str(espace_hote_id),
        chemin_hote=chemin_hote,
        portee=Portee(chemins=portee),
        matrice_plafond=plafond,
        consenti_par=consenti_par,
        consenti_at=consenti_at,
    )

    session.add(
        Montage(
            id=montage_id,
            espace_transverse_id=espace_transverse_id,
            espace_hote_id=espace_hote_id,
            chemin_hote=dom.chemin_hote,
            portee={"chemins": sorted(dom.portee.chemins)},
            matrice_plafond=plafond_vers_jsonb(dom.matrice_plafond),
            consenti_par=dom.consenti_par,
            etat=EtatMontage.ACTIF,
        )
    )
    _tracer(
        session,
        TypeEvenement.MONTAGE,
        espace_transverse_id,
        montage_id,
        espace_transverse_id,
        espace_hote_id,
        acteur,
    )
    session.flush()
    return MontageCree(montage_id=montage_id)


def archiver_montage(session: Session, montage_id: uuid.UUID, *, acteur: str) -> MontageCree:
    """Archive un montage (etat='archive', réversible) + trace un événement 'demontage'.

    N'écrit RIEN dans journal_acces (un démontage n'est pas une révocation de droit ici).
    """
    montage = session.get(Montage, montage_id)
    if montage is None:
        raise MontageIntrouvableError(f"montage {montage_id} introuvable")
    montage.etat = EtatMontage.ARCHIVE
    _tracer(
        session,
        TypeEvenement.DEMONTAGE,
        montage.espace_transverse_id,
        montage_id,
        montage.espace_transverse_id,
        montage.espace_hote_id,
        acteur,
    )
    session.flush()
    return MontageCree(montage_id=montage_id)
