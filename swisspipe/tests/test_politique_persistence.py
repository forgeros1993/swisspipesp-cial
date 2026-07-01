"""Persistance du curseur (migration 0006) — colonne modele.politique_droits + enum.

Additif : AUCUN changement à journal_acces ni à son enum action.
"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


def test_migration_ajoute_politique_droits(migrated_engine: Engine) -> None:
    insp = inspect(migrated_engine)
    cols = {c["name"] for c in insp.get_columns("modele")}
    assert "politique_droits" in cols


def test_enum_politique_droits_present(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        labels = set(
            conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'politique_droits'"
                )
            ).scalars()
        )
    assert labels == {"imposee", "deleguee", "libre"}


def test_enum_action_journal_toujours_inchange(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        labels = set(
            conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'action_journal'"
                )
            ).scalars()
        )
    assert labels == {"octroi", "revocation", "gel", "degel", "modification"}
