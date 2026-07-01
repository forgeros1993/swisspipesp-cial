"""Persistance L2 (transverses) : table modele, colonnes transverses sur espace,
journal_evenements append-only. Sur Postgres de test (voir conftest.py).

Le journal des DROITS (journal_acces, INV-6) reste un journal séparé et INTOUCHÉ :
les événements de cycle de vie (instanciation, plus tard montage/archivage) vivent
dans journal_evenements, avec son PROPRE trigger append-only.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import DBAPIError


def test_migration_cree_tables_et_colonnes_transverses(migrated_engine: Engine) -> None:
    insp = inspect(migrated_engine)
    tables = set(insp.get_table_names())
    assert {"modele", "journal_evenements"} <= tables
    espace_cols = {c["name"] for c in insp.get_columns("espace")}
    assert {"modele_id", "metadonnees", "cle_reconciliation"} <= espace_cols


def test_journal_evenements_append_only(connection: Connection) -> None:
    # INSERT autorisé.
    eid = connection.execute(
        text(
            "INSERT INTO journal_evenements (espace_id, type_evenement, acteur) "
            "VALUES (gen_random_uuid(), 'instanciation', 'cedric') RETURNING id"
        )
    ).scalar_one()

    # UPDATE rejeté par le trigger.
    with pytest.raises(DBAPIError, match="append-only"):
        with connection.begin_nested():
            connection.execute(
                text("UPDATE journal_evenements SET acteur = 'pirate' WHERE id = :id"),
                {"id": eid},
            )

    # DELETE rejeté.
    with pytest.raises(DBAPIError, match="append-only"):
        with connection.begin_nested():
            connection.execute(text("DELETE FROM journal_evenements WHERE id = :id"), {"id": eid})

    # TRUNCATE rejeté.
    with pytest.raises(DBAPIError, match="append-only"):
        with connection.begin_nested():
            connection.execute(text("TRUNCATE journal_evenements"))

    # La ligne d'origine est intacte.
    assert (
        connection.execute(
            text("SELECT acteur FROM journal_evenements WHERE id = :id"), {"id": eid}
        ).scalar_one()
        == "cedric"
    )


def test_journal_acces_intouche_par_la_migration(migrated_engine: Engine) -> None:
    # Le journal des DROITS n'a gagné aucune valeur d'action ni colonne : intact.
    insp = inspect(migrated_engine)
    cols = {c["name"] for c in insp.get_columns("journal_acces")}
    assert cols == {
        "id",
        "ressource_id",
        "groupe_id",
        "action",
        "matrice_avant",
        "matrice_apres",
        "cause",
        "acteur",
        "at",
    }
