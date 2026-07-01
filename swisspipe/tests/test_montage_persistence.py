"""Persistance des montages (migration 0004) sur Postgres de test (voir conftest.py).

Additif au socle : table `montage`, enum `etat_montage`, extension de `type_evenement`
('montage'/'demontage'). journal_acces (droits, INV-6) reste INTOUCHÉ.
"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def test_migration_cree_table_montage(migrated_engine: Engine) -> None:
    insp = inspect(migrated_engine)
    assert "montage" in set(insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("montage")}
    assert {
        "id",
        "espace_transverse_id",
        "espace_hote_id",
        "chemin_hote",
        "portee",
        "matrice_plafond",
        "consenti_par",
        "consenti_at",
        "etat",
    } <= cols


def test_enum_type_evenement_etendu(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        labels = set(
            conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'type_evenement'"
                )
            ).scalars()
        )
    assert {"instanciation", "montage", "demontage"} <= labels


def test_enum_etat_montage_present(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        labels = set(
            conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'etat_montage'"
                )
            ).scalars()
        )
    assert labels == {"actif", "archive"}


def test_journal_acces_toujours_intouche(migrated_engine: Engine) -> None:
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
