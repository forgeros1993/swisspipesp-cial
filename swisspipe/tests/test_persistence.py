"""Tests de persistance sur Postgres (swisspipe_test). Voir conftest.py."""

from __future__ import annotations

import pytest
from sqlalchemy import Connection, Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, Mode, NiveauPrincipal
from swisspipe.persistence.models import (
    Dimension,
    Espace,
    EspaceCoordonnee,
    Groupe,
    NatureEspace,
    Octroi,
    Ressource,
    RessourceMapping,
    ValeurDimension,
    signature_combinaison,
)
from swisspipe.core.domain.acteurs import TypeGroupe

TABLES_ATTENDUES = {
    "dimension",
    "valeur_dimension",
    "espace",
    "espace_coordonnee",
    "groupe",
    "groupe_membre",
    "ressource",
    "ressource_mapping",
    "octroi",
    "journal_acces",
}


def test_migration_cree_les_tables(migrated_engine: Engine) -> None:
    tables = set(inspect(migrated_engine).get_table_names())
    assert TABLES_ATTENDUES <= tables


def test_inserer_lire_espace_avec_coordonnees(db_session: Session) -> None:
    societe = Dimension(cle="societe", libelle="Société", rang=0)
    departement = Dimension(cle="departement", libelle="Département", rang=1)
    db_session.add_all([societe, departement])
    db_session.flush()

    alpha = ValeurDimension(dimension_id=societe.id, cle="alpha", libelle="Alpha")
    finance = ValeurDimension(dimension_id=departement.id, cle="finance", libelle="Finance")
    db_session.add_all([alpha, finance])
    db_session.flush()

    sig = signature_combinaison([("societe", "alpha"), ("departement", "finance")])
    espace = Espace(nature=NatureEspace.DIMENSIONNEL, combinaison_signature=sig)
    db_session.add(espace)
    db_session.flush()
    db_session.add_all(
        [
            EspaceCoordonnee(espace_id=espace.id, dimension_id=societe.id, valeur_id=alpha.id),
            EspaceCoordonnee(
                espace_id=espace.id, dimension_id=departement.id, valeur_id=finance.id
            ),
        ]
    )
    db_session.flush()

    coords = db_session.scalars(
        select(EspaceCoordonnee).where(EspaceCoordonnee.espace_id == espace.id)
    ).all()
    assert len(coords) == 2
    relu = db_session.get(Espace, espace.id)
    assert relu is not None
    assert relu.combinaison_signature == "departement=finance;societe=alpha"


def test_unicite_combinaison_rejette_doublon(db_session: Session) -> None:
    # Deux espaces au même jeu de coordonnées -> même signature -> rejet d'intégrité.
    sig = signature_combinaison([("societe", "alpha"), ("departement", "finance")])
    db_session.add(Espace(nature=NatureEspace.DIMENSIONNEL, combinaison_signature=sig))
    db_session.flush()
    db_session.add(Espace(nature=NatureEspace.DIMENSIONNEL, combinaison_signature=sig))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_octroi_matrice_jsonb_roundtrip(db_session: Session) -> None:
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("societe", "alpha")]),
    )
    groupe = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle="orga:finance")
    db_session.add_all([espace, groupe])
    db_session.flush()
    ressource = Ressource(type="folder", espace_id=espace.id, chemin="/Plans")
    db_session.add(ressource)
    db_session.flush()

    matrice = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    octroi = Octroi(
        ressource_id=ressource.id,
        groupe_id=groupe.id,
        mode=Mode.MODIFIER,
        matrice=matrice.vers_jsonb(),
    )
    db_session.add(octroi)
    db_session.flush()
    db_session.expire(octroi)

    relu = db_session.get(Octroi, octroi.id)
    assert relu is not None
    assert relu.mode is Mode.MODIFIER
    assert relu.matrice == {"niveau": "ecriture", "additionnels": ["classement"]}
    # Round-trip cohérent avec le format du domaine.
    assert Matrice.depuis_jsonb(relu.matrice) == matrice


def test_mapping_externe_vit_dans_ressource_mapping_pas_ressource(db_session: Session) -> None:
    # La table ressource ne porte AUCUN identifiant externe.
    colonnes_ressource = set(Ressource.__table__.columns.keys())
    assert colonnes_ressource == {"id", "type", "espace_id", "chemin", "created_at", "updated_at"}
    assert "cle_externe" in RessourceMapping.__table__.columns

    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("societe", "beta")]),
    )
    db_session.add(espace)
    db_session.flush()
    ressource = Ressource(type="folder", espace_id=espace.id, chemin="/Compta")
    db_session.add(ressource)
    db_session.flush()
    db_session.add(
        RessourceMapping(
            ressource_id=ressource.id,
            adaptateur="nextcloud",
            cle_externe="123:/remote.php/dav/Compta",
        )
    )
    db_session.flush()

    mapping = db_session.get(RessourceMapping, (ressource.id, "nextcloud"))
    assert mapping is not None
    assert mapping.cle_externe == "123:/remote.php/dav/Compta"


# ---------------------------------------------------------------------------
# LE garde-fou : journal_acces append-only (INV-6)
# ---------------------------------------------------------------------------


def test_journal_append_only(connection: Connection) -> None:
    # INSERT autorisé.
    jid = connection.execute(
        text(
            "INSERT INTO journal_acces (ressource_id, groupe_id, action) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), 'octroi') RETURNING id"
        )
    ).scalar_one()

    # UPDATE rejeté par le trigger (savepoint pour récupérer la transaction).
    with pytest.raises(DBAPIError, match="append-only"):
        with connection.begin_nested():
            connection.execute(
                text("UPDATE journal_acces SET acteur = 'pirate' WHERE id = :id"), {"id": jid}
            )

    # DELETE rejeté.
    with pytest.raises(DBAPIError, match="append-only"):
        with connection.begin_nested():
            connection.execute(text("DELETE FROM journal_acces WHERE id = :id"), {"id": jid})

    # La ligne d'origine est intacte.
    assert connection.execute(
        text("SELECT acteur FROM journal_acces WHERE id = :id"), {"id": jid}
    ).scalar_one() is None

    # Correction = ligne compensatoire (nouvel INSERT), jamais modification (§10.2).
    connection.execute(
        text(
            "INSERT INTO journal_acces (ressource_id, groupe_id, action) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), 'modification')"
        )
    )
    assert connection.execute(text("SELECT count(*) FROM journal_acces")).scalar_one() == 2


def test_journal_truncate_rejete(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO journal_acces (ressource_id, groupe_id, action) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), 'octroi')"
        )
    )
    with pytest.raises(DBAPIError, match="append-only"):
        with connection.begin_nested():
            connection.execute(text("TRUNCATE journal_acces"))
