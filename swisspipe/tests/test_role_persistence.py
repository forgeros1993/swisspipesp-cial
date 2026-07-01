"""Persistance des rôles (migration 0005) sur Postgres de test (voir conftest.py).

Additif : tables role, role_affectation, correspondance_compte + colonne
modele.matrice_par_role + enum source_affectation. AUCUN changement à journal_acces ni
à son enum action (INV-6 : le journal des droits reste tel quel).
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from swisspipe.persistence.models import CorrespondanceCompte


def test_migration_cree_tables_roles(migrated_engine: Engine) -> None:
    insp = inspect(migrated_engine)
    tables = set(insp.get_table_names())
    assert {"role", "role_affectation", "correspondance_compte"} <= tables
    aff_cols = {c["name"] for c in insp.get_columns("role_affectation")}
    assert {"espace_id", "role_id", "groupe_perso_id", "source", "effectif_depuis"} <= aff_cols
    modele_cols = {c["name"] for c in insp.get_columns("modele")}
    assert "matrice_par_role" in modele_cols


def test_enum_source_affectation_present(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        labels = set(
            conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'source_affectation'"
                )
            ).scalars()
        )
    assert labels == {"humain"}


def test_enum_action_journal_inchange(migrated_engine: Engine) -> None:
    # INV-6 : le journal des droits n'a gagné AUCUNE valeur d'action.
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


def test_journal_acces_colonnes_inchangees(migrated_engine: Engine) -> None:
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


# ---------------------------------------------------------------------------
# §5 — correspondance_compte : round-trip + unicité (systeme, cle_externe)
# ---------------------------------------------------------------------------


def test_correspondance_compte_round_trip(db_session: Session) -> None:
    db_session.add(CorrespondanceCompte(compte_id="marie", systeme="odoo", cle_externe="emp-42"))
    db_session.flush()
    relu = db_session.get(CorrespondanceCompte, ("odoo", "emp-42"))
    assert relu is not None
    assert relu.compte_id == "marie"


def test_correspondance_compte_unicite(db_session: Session) -> None:
    db_session.add(CorrespondanceCompte(compte_id="marie", systeme="odoo", cle_externe="emp-42"))
    db_session.flush()
    db_session.add(CorrespondanceCompte(compte_id="autre", systeme="odoo", cle_externe="emp-42"))
    with pytest.raises(IntegrityError):
        db_session.flush()
