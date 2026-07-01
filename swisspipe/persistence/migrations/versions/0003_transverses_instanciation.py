"""Transverses (L2 étape 1) — table modele, colonnes transverses sur espace,
journal_evenements append-only.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-01

ADDITIF au socle L1 : ne touche NI journal_acces (journal des droits, INV-6) NI ses
enums. On ajoute :
- `modele` : gabarit d'un espace transverse (arborescence imposée + schéma + rôles).
- `espace.modele_id / metadonnees / cle_reconciliation` : une instance est un espace
  nature='transverse' lié à son modèle (colonnes nullables -> les espaces dimensionnels
  L1 restent valides tels quels).
- `journal_evenements` : journal append-only SÉPARÉ pour les événements de cycle de vie
  (instanciation ; enum extensible pour montage/archivage/changement de modèle plus tard),
  avec son PROPRE trigger anti UPDATE/DELETE/TRUNCATE.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enum extensible : une seule valeur pour l'instant (spec §5.2). create_type=False :
# créé explicitement dans upgrade().
type_evenement = postgresql.ENUM("instanciation", name="type_evenement", create_type=False)

_UUID = postgresql.UUID(as_uuid=True)
_PK = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def _id() -> sa.Column[Any]:
    return sa.Column("id", _UUID, primary_key=True, server_default=_PK)


def _created() -> sa.Column[Any]:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False)


def _updated() -> sa.Column[Any]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=_NOW,
        onupdate=_NOW,
        nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    type_evenement.create(bind, checkfirst=True)

    # Gabarit d'espace transverse.
    op.create_table(
        "modele",
        _id(),
        sa.Column("nom", sa.Text(), nullable=False),
        sa.Column("arborescence", postgresql.JSONB(), nullable=False),
        sa.Column("schema_metadonnees", postgresql.JSONB(), nullable=False),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        _created(),
        _updated(),
    )

    # Une instance = un espace transverse lié à son modèle (colonnes nullables).
    op.add_column(
        "espace",
        sa.Column("modele_id", _UUID, sa.ForeignKey("modele.id"), nullable=True),
    )
    op.add_column("espace", sa.Column("metadonnees", postgresql.JSONB(), nullable=True))
    op.add_column("espace", sa.Column("cle_reconciliation", sa.Text(), nullable=True))

    # Journal des ÉVÉNEMENTS de cycle de vie (séparé du journal des droits).
    op.create_table(
        "journal_evenements",  # append-only (pas de FK : historique)
        _id(),
        sa.Column("espace_id", _UUID, nullable=False),
        sa.Column("type_evenement", type_evenement, nullable=False),
        sa.Column("cause", postgresql.JSONB(), nullable=True),
        sa.Column("acteur", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )

    # --- GARDE-FOU : journal_evenements append-only (même patron que journal_acces) ----
    op.execute(
        """
        CREATE OR REPLACE FUNCTION journal_evenements_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'journal_evenements est append-only : % interdit. '
                'Corriger par une ligne compensatoire (INSERT).', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_evenements_no_update_delete
        BEFORE UPDATE OR DELETE ON journal_evenements
        FOR EACH ROW EXECUTE FUNCTION journal_evenements_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_evenements_no_truncate
        BEFORE TRUNCATE ON journal_evenements
        FOR EACH STATEMENT EXECUTE FUNCTION journal_evenements_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS journal_evenements_no_truncate ON journal_evenements")
    op.execute("DROP TRIGGER IF EXISTS journal_evenements_no_update_delete ON journal_evenements")
    op.execute("DROP FUNCTION IF EXISTS journal_evenements_append_only()")
    op.drop_table("journal_evenements")

    op.drop_column("espace", "cle_reconciliation")
    op.drop_column("espace", "metadonnees")
    op.drop_column("espace", "modele_id")

    op.drop_table("modele")

    bind = op.get_bind()
    type_evenement.drop(bind, checkfirst=True)
