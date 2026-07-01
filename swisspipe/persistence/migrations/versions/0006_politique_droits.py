"""Curseur de gouvernance (L2 étape 5) — colonne modele.politique_droits + enum.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-01

ADDITIF : colonne modele.politique_droits (enum imposee/deleguee/libre, défaut 'imposee'
-> rétrocompat étapes 1-4). AUCUN changement à journal_acces ni à son enum action.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

politique_droits = postgresql.ENUM(
    "imposee", "deleguee", "libre", name="politique_droits", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    politique_droits.create(bind, checkfirst=True)
    op.add_column(
        "modele",
        sa.Column(
            "politique_droits",
            politique_droits,
            server_default=sa.text("'imposee'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("modele", "politique_droits")
    bind = op.get_bind()
    politique_droits.drop(bind, checkfirst=True)
