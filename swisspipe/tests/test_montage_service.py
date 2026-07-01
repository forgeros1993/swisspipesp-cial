"""Tests du service de montage (application/montage_service.py) sur Postgres de test.

Persiste un montage (fenêtre d'une instance sur un hôte) + trace journal_evenements
(montage/demontage). Le journal des DROITS (journal_acces) n'est JAMAIS écrit ici.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from swisspipe.application.instanciation_service import (
    InstanceCree,
    enregistrer_modele,
    instancier_modele,
)
from swisspipe.application.montage_service import archiver_montage, monter_instance
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    ChampMeta,
    DossierImpose,
    Modele,
    SystemeReference,
)
from swisspipe.persistence.models import (
    Espace,
    JournalAcces,
    JournalEvenement,
    Montage,
    NatureEspace,
    TypeEvenement,
    signature_combinaison,
)

ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
LECTURE = Matrice(NiveauPrincipal.LECTURE)


def _modele_employe() -> Modele:
    return Modele(
        id="employe",
        nom="Employé",
        arborescence_imposee=ArborescenceImposee(
            dossiers=[
                DossierImpose(cle="documents", libelle="Documents"),
                DossierImpose(cle="salaire", libelle="Salaire"),
                DossierImpose(cle="evaluations", libelle="Evaluations"),
            ],
            dossiers_libres_autorises=False,
        ),
        schema_metadonnees=[
            ChampMeta(
                cle="nom", libelle="Nom", type="texte", systeme_reference=SystemeReference.HUMAIN
            ),
        ],
        roles=["rh"],
    )


def _instance_employe(session: Session) -> InstanceCree:
    """Crée une instance « Employé Alice » (espace transverse + 3 ressources)."""
    modele = _modele_employe()
    modele_id = enregistrer_modele(session, modele)
    return instancier_modele(
        session,
        modele,
        modele_id=modele_id,
        nom="Alice",
        metadonnees={"nom": "Alice"},
        acteur="rh",
    )


def _host(session: Session, sig: str) -> uuid.UUID:
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([(sig, sig)]),
    )
    session.add(espace)
    session.flush()
    return espace.id


# ---------------------------------------------------------------------------
# §5 — Montage persisté + journal_evenements 'montage'
# ---------------------------------------------------------------------------


def test_monter_persiste_montage_et_trace(db_session: Session) -> None:
    inst = _instance_employe(db_session)
    hote = _host(db_session, "rh")

    res = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Documents", "/Salaire"},
        matrice_plafond=ECRITURE,
        consenti_par="admin_rh",
        acteur="admin_rh",
    )

    montage = db_session.get(Montage, res.montage_id)
    assert montage is not None
    assert montage.espace_transverse_id == inst.espace_id
    assert montage.espace_hote_id == hote
    assert montage.etat.value == "actif"
    assert set(montage.portee["chemins"]) == {"/Documents", "/Salaire"}
    assert montage.matrice_plafond == ECRITURE.vers_jsonb()

    evs = db_session.scalars(
        select(JournalEvenement).where(JournalEvenement.espace_id == inst.espace_id)
    ).all()
    montages = [e for e in evs if e.type_evenement is TypeEvenement.MONTAGE]
    assert len(montages) == 1
    ev = montages[0]
    assert ev.cause["montage_id"] == str(res.montage_id)
    assert ev.cause["espace_transverse_id"] == str(inst.espace_id)
    assert ev.cause["espace_hote_id"] == str(hote)
    assert ev.acteur == "admin_rh"


def test_archiver_trace_demontage(db_session: Session) -> None:
    inst = _instance_employe(db_session)
    hote = _host(db_session, "rh")
    res = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Documents"},
        matrice_plafond=ECRITURE,
        consenti_par="admin_rh",
        acteur="admin_rh",
    )

    archiver_montage(db_session, res.montage_id, acteur="admin_rh")

    montage = db_session.get(Montage, res.montage_id)
    assert montage.etat.value == "archive"
    demontages = db_session.scalars(
        select(JournalEvenement).where(
            JournalEvenement.espace_id == inst.espace_id,
            JournalEvenement.type_evenement == TypeEvenement.DEMONTAGE,
        )
    ).all()
    assert len(demontages) == 1
    assert demontages[0].cause["montage_id"] == str(res.montage_id)


def test_montage_n_ecrit_rien_dans_journal_acces(db_session: Session) -> None:
    inst = _instance_employe(db_session)
    hote = _host(db_session, "rh")
    res = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Documents"},
        matrice_plafond=ECRITURE,
        consenti_par="admin_rh",
        acteur="admin_rh",
    )
    archiver_montage(db_session, res.montage_id, acteur="admin_rh")
    assert db_session.scalar(select(func.count()).select_from(JournalAcces)) == 0


def test_lignes_evenements_montage_append_only(db_session: Session) -> None:
    inst = _instance_employe(db_session)
    hote = _host(db_session, "rh")
    res = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=hote,
        chemin_hote="/RH",
        portee_chemins={"/Documents"},
        matrice_plafond=ECRITURE,
        consenti_par="admin_rh",
        acteur="admin_rh",
    )
    ev_id = db_session.scalar(
        select(JournalEvenement.id).where(JournalEvenement.type_evenement == TypeEvenement.MONTAGE)
    )
    with pytest.raises(DBAPIError, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE journal_evenements SET acteur = 'pirate' WHERE id = :id"),
                {"id": ev_id},
            )
    assert res.montage_id is not None


# ---------------------------------------------------------------------------
# §4 — Self-service structurel : hôte = espace personnel, portée restreinte
# ---------------------------------------------------------------------------


def test_monter_dans_espace_personnel_portee_restreinte(db_session: Session) -> None:
    inst = _instance_employe(db_session)
    perso = _host(db_session, "perso-alice")  # espace personnel (structure seulement)

    res = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=perso,
        chemin_hote="/MonEspace",
        portee_chemins={"/Documents", "/Salaire"},  # restreint (pas /Evaluations)
        matrice_plafond=LECTURE,
        consenti_par="alice",
        acteur="alice",
    )
    montage = db_session.get(Montage, res.montage_id)
    assert montage.espace_hote_id == perso
    assert set(montage.portee["chemins"]) == {"/Documents", "/Salaire"}
    assert montage.etat.value == "actif"


# ---------------------------------------------------------------------------
# Portée invalide : refus + rien persisté
# ---------------------------------------------------------------------------


def test_portee_absente_refusee_rien_persiste(db_session: Session) -> None:
    inst = _instance_employe(db_session)
    hote = _host(db_session, "rh")

    with pytest.raises(ValueError, match="absente"):
        monter_instance(
            db_session,
            espace_transverse_id=inst.espace_id,
            espace_hote_id=hote,
            chemin_hote="/RH",
            portee_chemins={"/Documents", "/Inexistant"},
            matrice_plafond=ECRITURE,
            consenti_par="admin_rh",
            acteur="admin_rh",
        )
    assert db_session.scalar(select(func.count()).select_from(Montage)) == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(JournalEvenement)
            .where(JournalEvenement.type_evenement == TypeEvenement.MONTAGE)
        )
        == 0
    )
