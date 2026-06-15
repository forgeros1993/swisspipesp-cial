# swisspipesp-cial

**SwissPipe — cœur de gouvernance des accès.**

Décide « qui peut quoi sur quelle ressource » de façon agnostique et auditable par
lecture. Architecture hexagonale : le cœur ne connaît aucun système externe ; des
adaptateurs traduisent ses décisions vers des exécutants concrets (Nextcloud, mail,
bâtiment).

Voir [`CLAUDE.md`](./CLAUDE.md) pour le principe, les 6 invariants, le glossaire figé,
les conventions et le découpage en lots.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · psycopg 3 · PostgreSQL
· pytest · ruff · mypy.

## Structure

```
swisspipe/
  core/         # cœur agnostique (domain / services / ports) — zéro lib externe métier
  adapters/     # inbound (FastAPI) + outbound (fake, puis nextcloud)
  persistence/  # SQLAlchemy + migrations Alembic
  tests/
docs/adr/       # décisions d'architecture
```

## Démarrage (dev)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # puis renseigner DATABASE_URL
ruff check . && mypy swisspipe && pytest
```

## Décisions d'architecture

- [ADR-0013 — Espaces transverses](docs/adr/ADR-0013-espaces-transverses.md)
- [ADR-0014 — Journal d'accès](docs/adr/ADR-0014-journal-acces.md)
