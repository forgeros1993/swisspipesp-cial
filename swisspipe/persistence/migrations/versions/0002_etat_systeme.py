"""etat_systeme — marqueur clé/valeur mutable (détection de changement Nextcloud).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-16

Table MUTABLE (pas un journal, pas de trigger append-only) : on y met à jour le dernier
état vu (ex. version NC / version+état Group Folders) pour détecter un upgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etat_systeme",
        sa.Column("cle", sa.Text(), nullable=False),
        sa.Column("valeur", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("cle", name="pk_etat_systeme"),
    )


def downgrade() -> None:
    op.drop_table("etat_systeme")
