"""Tests du service d'instanciation (application/instanciation_service.py).

Persiste un espace transverse + son squelette + une ligne journal_evenements, sur
Postgres de test (fixture db_session). Le journal des DROITS (journal_acces) n'est
JAMAIS écrit par une instanciation (un événement de cycle de vie n'est pas un droit).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from swisspipe.application.instanciation_service import (
    enregistrer_modele,
    instancier_modele,
)
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
    NatureEspace,
    Ressource,
    TypeEvenement,
)


def _modele_immobilier() -> Modele:
    return Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=[
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
                DossierImpose(cle="divers", libelle="Divers"),
            ],
            dossiers_libres_autorises=True,
        ),
        schema_metadonnees=[
            ChampMeta(
                cle="adresse",
                libelle="Adresse",
                type="texte",
                systeme_reference=SystemeReference.HUMAIN,
            ),
        ],
        roles=["chef_de_projet"],
    )


def test_instancier_cree_espace_transverse_et_squelette(db_session: Session) -> None:
    modele = _modele_immobilier()
    modele_id = enregistrer_modele(db_session, modele)

    res = instancier_modele(
        db_session,
        modele,
        modele_id=modele_id,
        nom="Chemin des Roses 12",
        metadonnees={"adresse": "Chemin des Roses 12"},
        acteur="cedric",
    )

    espace = db_session.get(Espace, res.espace_id)
    assert espace is not None
    assert espace.nature is NatureEspace.TRANSVERSE
    assert espace.modele_id == modele_id
    assert espace.metadonnees == {"adresse": "Chemin des Roses 12"}

    ressources = db_session.scalars(
        select(Ressource).where(Ressource.espace_id == res.espace_id)
    ).all()
    assert {r.chemin for r in ressources} == {"/Plans", "/Correspondance", "/Divers"}
    assert all(r.type == "folder" for r in ressources)


def test_instanciation_ecrit_journal_evenements(db_session: Session) -> None:
    modele = _modele_immobilier()
    modele_id = enregistrer_modele(db_session, modele)

    res = instancier_modele(
        db_session,
        modele,
        modele_id=modele_id,
        nom="Chemin des Roses 12",
        metadonnees={"adresse": "Chemin des Roses 12"},
        acteur="cedric",
    )

    evenements = db_session.scalars(
        select(JournalEvenement).where(JournalEvenement.espace_id == res.espace_id)
    ).all()
    assert len(evenements) == 1
    ev = evenements[0]
    assert ev.type_evenement is TypeEvenement.INSTANCIATION
    assert ev.cause["modele_id"] == str(modele_id)
    assert ev.cause["instance_id"] == str(res.espace_id)
    assert ev.acteur == "cedric"


def test_instanciation_n_ecrit_rien_dans_journal_acces(db_session: Session) -> None:
    modele = _modele_immobilier()
    modele_id = enregistrer_modele(db_session, modele)
    instancier_modele(
        db_session,
        modele,
        modele_id=modele_id,
        nom="X",
        metadonnees={"adresse": "A"},
        acteur="cedric",
    )
    # Une instanciation n'est PAS un droit : journal_acces reste vide.
    assert db_session.scalar(select(func.count()).select_from(JournalAcces)) == 0


def test_metadonnees_non_conformes_rejetees_rien_persiste(db_session: Session) -> None:
    modele = _modele_immobilier()
    modele_id = enregistrer_modele(db_session, modele)

    with pytest.raises(ValueError, match="non conformes"):
        instancier_modele(
            db_session,
            modele,
            modele_id=modele_id,
            nom="X",
            metadonnees={},  # 'adresse' manquante
            acteur="cedric",
        )
    # Aucun espace transverse ni événement créé.
    assert (
        db_session.scalar(
            select(func.count()).select_from(Espace).where(Espace.nature == NatureEspace.TRANSVERSE)
        )
        == 0
    )
    assert db_session.scalar(select(func.count()).select_from(JournalEvenement)) == 0


def test_ligne_evenement_est_append_only(db_session: Session) -> None:
    modele = _modele_immobilier()
    modele_id = enregistrer_modele(db_session, modele)
    res = instancier_modele(
        db_session,
        modele,
        modele_id=modele_id,
        nom="X",
        metadonnees={"adresse": "A"},
        acteur="cedric",
    )
    ev_id = db_session.scalar(
        select(JournalEvenement.id).where(JournalEvenement.espace_id == res.espace_id)
    )
    # Le trigger interdit toute modification de la ligne d'instanciation.
    from sqlalchemy import text

    with pytest.raises(DBAPIError, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE journal_evenements SET acteur = 'pirate' WHERE id = :id"),
                {"id": ev_id},
            )
