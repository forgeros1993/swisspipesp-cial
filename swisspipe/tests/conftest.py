"""Fixtures de persistance : Postgres de test via DATABASE_URL_TEST.

La base de test est construite par la MIGRATION Alembic (pas create_all) afin
d'inclure le trigger append-only du journal. DATABASE_URL_TEST est lue depuis
l'environnement ; les tests sont skippés proprement si elle est absente.

Lancer :
    export DATABASE_URL_TEST=postgresql+psycopg://swisspipe:swisspipe@localhost:5432/swisspipe_test
    .venv/bin/python -m pytest swisspipe/tests/test_persistence.py
(Rappel WSL : sudo service postgresql start dans un nouveau terminal.)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST non défini — Postgres de test requis")
    return url


@pytest.fixture(scope="session")
def migrated_engine(database_url: str) -> Engine:
    """Schéma reconstruit à neuf via la migration Alembic (trigger inclus)."""
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    os.environ["DATABASE_URL"] = database_url  # lu par migrations/env.py
    command.upgrade(cfg, "head")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def connection(migrated_engine: Engine) -> Connection:
    """Connexion enveloppée d'une transaction annulée en fin de test (isolation)."""
    conn = migrated_engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
def db_session(connection: Connection) -> Session:
    """Session ORM en savepoint, sans toucher l'annulation de la transaction externe."""
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        yield session
