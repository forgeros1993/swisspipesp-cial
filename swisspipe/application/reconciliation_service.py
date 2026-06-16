"""Orchestrateur de réconciliation — couche APPLICATIVE (câble cœur + ports + persistance).

Règle de dépendance : `application → core, ports, persistence, adapters`. JAMAIS l'inverse
(le cœur ne connaît pas `application`). Ce module PEUT importer persistence/adapters —
c'est son rôle d'orchestration. Il n'est PAS dans `core/` (donc hors garde-fou de pureté).

Réconciliation = protection anti-upgrade Nextcloud (CLAUDE.md §9) : compare l'état de
droits DÉSIRÉ (cœur, depuis les octrois) à l'état RÉEL (exécutant), et si dérive →
réapplique le désiré + trace au journal (INV-6).

Décisions actées :
- cause jsonb : {"type": "reconciliation", "divergence": "<manquant|en_trop|matrice>",
  "declencheur": "<manuel|auto>"}.
- action/divergence : manquant→octroi (avant=null, apres=désirée) ;
  en_trop→revocation (avant=réelle, apres=null) ;
  divergente→modification (avant=réelle, apres=désirée).
- acteur : "system:reconciliation".
- No-op STRICT : si conforme, aucun appliquer_droits, aucune ligne de journal.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.adapters.outbound.nextcloud.etat_nextcloud import EtatNextcloud
from swisspipe.core.domain.matrice import Matrice
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.ports.adaptateur_ressource import AdaptateurRessource
from swisspipe.core.services.reconciliation import Divergence, comparer_droits, etat_desire
from swisspipe.persistence.models import (
    ActionJournal,
    EtatSysteme,
    Groupe,
    JournalAcces,
    RessourceMapping,
)
from swisspipe.persistence.models import Octroi as OctroiModel

ADAPTATEUR = "nextcloud"
ACTEUR = "system:reconciliation"

# Namespace déterministe pour fabriquer un groupe_id traçable quand un groupe existe côté
# Nextcloud mais PAS au cœur (groupe externe). journal_acces.groupe_id est NOT NULL et
# SANS FK -> un uuid5 stable (même nom NC -> même uuid) satisfait la contrainte tout en
# restant joignable ; l'identité lisible va dans cause.groupe_nc. Aucune action sans trace.
_NS_GROUPE_NC = uuid.uuid5(uuid.NAMESPACE_DNS, "groupe-nc.swisspipe")


class RessourceNonMappeeError(LookupError):
    """La ressource n'a pas de mapping vers l'adaptateur cible — réconciliation impossible."""


def reconcilier_ressource(
    session: Session,
    adaptateur: AdaptateurRessource,
    ressource_id: uuid.UUID,
    *,
    declencheur: str = "manuel",
) -> Divergence:
    """Réconcilie UNE ressource : état désiré (cœur) vs réel (adaptateur). Niveau folder.

    Retourne TOUJOURS le `Divergence` (le diagnostic), que l'appelant sache ce qui a été
    fait. No-op strict si conforme. Lève `RessourceNonMappeeError` si pas de mapping Nextcloud.
    """
    mapping = session.get(RessourceMapping, (ressource_id, ADAPTATEUR))
    if mapping is None:
        raise RessourceNonMappeeError(f"ressource {ressource_id} non mappée à '{ADAPTATEUR}'")
    cle_externe = mapping.cle_externe
    res_str = str(ressource_id)

    # Octrois de la ressource + nom NC du groupe (groupe.cle) + son uuid (pour le journal).
    lignes = session.execute(
        select(OctroiModel, Groupe.cle, Groupe.id)
        .join(Groupe, Groupe.id == OctroiModel.groupe_id)
        .where(OctroiModel.ressource_id == ressource_id)
    ).all()

    octrois: dict[tuple[str, str], Octroi] = {}
    groupe_ids: set[str] = set()
    id_par_cle: dict[str, uuid.UUID] = {}
    for octroi_db, cle, gid in lignes:
        octrois[(res_str, cle)] = Octroi.depuis_jsonb(
            {"mode": octroi_db.mode.value, "matrice": octroi_db.matrice}
        )
        groupe_ids.add(cle)
        id_par_cle[cle] = gid

    parents: dict[str, str | None] = {res_str: None}  # niveau folder, pas d'héritage
    desire = etat_desire(res_str, groupe_ids, parents, octrois)
    reel = adaptateur.lire_droits_effectifs(cle_externe)
    div = comparer_droits(desire, reel)

    if div.est_conforme:
        return div  # no-op strict : on ne touche Nextcloud que sur divergence réelle

    adaptateur.appliquer_droits(cle_externe, desire)

    # Résoudre les cle -> groupe_id manquantes (groupes en trop : hors octrois).
    cles_a_resoudre = (
        {dg.groupe_id for dg in div.groupes_manquants}
        | {dg.groupe_id for dg in div.groupes_en_trop}
        | {md.groupe_id for md in div.matrices_divergentes}
    ) - id_par_cle.keys()
    if cles_a_resoudre:
        for cle, gid in session.execute(
            select(Groupe.cle, Groupe.id).where(Groupe.cle.in_(cles_a_resoudre))
        ).all():
            id_par_cle[cle] = gid

    for dg in div.groupes_manquants:
        _tracer(
            session,
            ressource_id,
            dg.groupe_id,
            id_par_cle.get(dg.groupe_id),
            ActionJournal.OCTROI,
            None,
            dg.matrice,
            "manquant",
            declencheur,
        )
    for dg in div.groupes_en_trop:
        _tracer(
            session,
            ressource_id,
            dg.groupe_id,
            id_par_cle.get(dg.groupe_id),
            ActionJournal.REVOCATION,
            dg.matrice,
            None,
            "en_trop",
            declencheur,
        )
    for md in div.matrices_divergentes:
        _tracer(
            session,
            ressource_id,
            md.groupe_id,
            id_par_cle.get(md.groupe_id),
            ActionJournal.MODIFICATION,
            md.reelle,
            md.attendue,
            "matrice",
            declencheur,
        )

    return div


def _tracer(
    session: Session,
    ressource_id: uuid.UUID,
    cle_nc: str,
    groupe_id_coeur: uuid.UUID | None,
    action: ActionJournal,
    avant: Matrice | None,
    apres: Matrice | None,
    divergence: str,
    declencheur: str,
) -> None:
    """Insère TOUJOURS une ligne de journal (append-only) — aucune action sans trace (INV-6).

    Si le groupe existe au cœur -> son uuid. Sinon (groupe externe, présent côté Nextcloud
    mais inconnu du cœur — typiquement après un upgrade/manip externe) -> uuid5 déterministe
    du nom NC + identité lisible dans `cause.groupe_nc`. groupe_id reste NOT NULL et sans
    FK, donc l'uuid synthétique est valide et stable (réconciliations répétées -> même uuid).
    """
    cause: dict[str, str] = {
        "type": "reconciliation",
        "divergence": divergence,
        "declencheur": declencheur,
    }
    if groupe_id_coeur is not None:
        groupe_id = groupe_id_coeur
    else:
        groupe_id = uuid.uuid5(_NS_GROUPE_NC, cle_nc)
        cause["groupe_nc"] = cle_nc  # groupe externe non mappé : identité préservée

    session.add(
        JournalAcces(
            ressource_id=ressource_id,
            groupe_id=groupe_id,
            action=action,
            matrice_avant=avant.vers_jsonb() if avant is not None else None,
            matrice_apres=apres.vers_jsonb() if apres is not None else None,
            cause=cause,
            acteur=ACTEUR,
        )
    )


@dataclass(frozen=True)
class ResultatRessource:
    """Issue de la réconciliation d'UNE ressource lors d'un balayage."""

    ressource_id: uuid.UUID
    statut: str  # "conforme" | "reparee" | "erreur"
    divergence: Divergence | None = None
    erreur: str | None = None  # "TypeErreur: message" si statut == "erreur"


@dataclass(frozen=True)
class RapportReconciliation:
    """Agrégat du balayage. Une ressource en erreur n'arrête jamais le balayage."""

    resultats: tuple[ResultatRessource, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.resultats)

    @property
    def nb_conformes(self) -> int:
        return sum(1 for r in self.resultats if r.statut == "conforme")

    @property
    def nb_reparees(self) -> int:
        return sum(1 for r in self.resultats if r.statut == "reparee")

    @property
    def nb_erreurs(self) -> int:
        return sum(1 for r in self.resultats if r.statut == "erreur")

    @property
    def ressources_en_erreur(self) -> tuple[ResultatRessource, ...]:
        return tuple(r for r in self.resultats if r.statut == "erreur")


def reconcilier_tout(
    session: Session,
    adaptateur: AdaptateurRessource,
    *,
    declencheur: str = "auto",
) -> RapportReconciliation:
    """Balaie TOUTES les ressources mappées Nextcloud et réconcilie chacune. RÉSILIENT.

    Commit PAR ressource (les réparations déjà faites survivent à une erreur ultérieure) ;
    sur exception -> rollback du partiel de CETTE ressource puis on continue. On capture
    `Exception` (générique) : l'orchestrateur en masse pilote un AdaptateurRessource et ne
    connaît PAS les exceptions concrètes d'un adaptateur (agnosticité préservée). Ordre
    déterministe (par ressource_id) -> balayage reproductible.
    """
    ids = session.scalars(
        select(RessourceMapping.ressource_id)
        .where(RessourceMapping.adaptateur == ADAPTATEUR)
        .order_by(RessourceMapping.ressource_id)
    ).all()

    resultats: list[ResultatRessource] = []
    for rid in ids:
        try:
            div = reconcilier_ressource(session, adaptateur, rid, declencheur=declencheur)
            session.commit()
            statut = "conforme" if div.est_conforme else "reparee"
            resultats.append(ResultatRessource(rid, statut, divergence=div))
        except Exception as e:
            session.rollback()
            resultats.append(ResultatRessource(rid, "erreur", erreur=f"{type(e).__name__}: {e}"))
    return RapportReconciliation(tuple(resultats))


def _doit_reconcilier(
    etat_actuel: EtatNextcloud, marqueur: dict[str, object] | None
) -> tuple[bool, str]:
    """Décide s'il faut réconcilier + la raison (traçabilité). Pur. Règle = OU logique.

    Déclencher « pour rien » est sûr (no-op strict si rien n'a cassé) -> on est généreux.
    """
    if marqueur is None:
        return True, "premiere_execution"
    precedent = EtatNextcloud.depuis_dict(marqueur)
    if etat_actuel.gf_active and not precedent.gf_active:
        return True, "gf_reactive"
    if etat_actuel.nc_version != precedent.nc_version:
        return True, "nc_version_change"
    if etat_actuel.gf_version != precedent.gf_version:
        return True, "gf_version_change"
    return False, "inchange"


def verifier_et_reconcilier(
    session: Session,
    adaptateur: AdaptateurRessource,
    lecteur_etat: Callable[[], EtatNextcloud],
    *,
    cle_marqueur: str = "nextcloud",
) -> RapportReconciliation | None:
    """Détecte un changement d'état Nextcloud (upgrade / réactivation GF) -> réconcilie.

    `lecteur_etat` est INJECTÉ (callable -> EtatNextcloud) pour tester avec un mock. Compare
    l'état courant au marqueur stocké (`etat_systeme[cle_marqueur]`) ; si la règle OU
    déclenche (première fois / GF réactivé / version NC ou GF changée) -> `reconcilier_tout`
    + met à jour le marqueur + commit, et retourne le rapport. Sinon -> None (no-op).
    """
    etat_actuel = lecteur_etat()
    ligne = session.get(EtatSysteme, cle_marqueur)
    marqueur = ligne.valeur if ligne is not None else None

    doit, _raison = _doit_reconcilier(etat_actuel, marqueur)
    if not doit:
        return None

    rapport = reconcilier_tout(session, adaptateur, declencheur="auto")

    if ligne is not None:
        ligne.valeur = etat_actuel.vers_dict()
    else:
        session.add(EtatSysteme(cle=cle_marqueur, valeur=etat_actuel.vers_dict()))
    session.commit()

    return rapport
