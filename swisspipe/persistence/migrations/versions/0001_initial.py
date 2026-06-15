"""Initial — tables L1 + trigger journal append-only.

Revision ID: 0001
Revises:
Create Date: 2026-06-15

Crée le schéma du périmètre L1 (spec §4) et installe le garde-fou critique INV-6 :
un trigger Postgres qui rejette tout UPDATE et tout DELETE sur journal_acces. Seul
l'INSERT est permis ; une correction se fait par une ligne compensatoire (§10.2).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enums Postgres natifs (tokens = valeurs du domaine). create_type=False : on les crée
# explicitement dans upgrade() (sinon create_table tente un second CREATE TYPE).
nature_espace = postgresql.ENUM(
    "dimensionnel", "transverse", name="nature_espace", create_type=False
)
type_groupe = postgresql.ENUM(
    "personnel", "organisationnel", name="type_groupe", create_type=False
)
mode_octroi = postgresql.ENUM(
    "heriter", "modifier", "refuser", name="mode_octroi", create_type=False
)
action_journal = postgresql.ENUM(
    "octroi", "revocation", "gel", "degel", "modification",
    name="action_journal", create_type=False,
)

_UUID = postgresql.UUID(as_uuid=True)
_PK = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def _id() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True, server_default=_PK)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False)


def _updated() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=_NOW,
        onupdate=_NOW,
        nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for e in (nature_espace, type_groupe, mode_octroi, action_journal):
        e.create(bind, checkfirst=True)

    op.create_table(
        "dimension",
        _id(),
        sa.Column("cle", sa.String(), nullable=False),
        sa.Column("libelle", sa.String(), nullable=False),
        sa.Column("rang", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("parent_id", _UUID, sa.ForeignKey("dimension.id"), nullable=True),
        _created(),
        _updated(),
        sa.UniqueConstraint("cle", name="uq_dimension_cle"),
    )

    op.create_table(
        "valeur_dimension",
        _id(),
        sa.Column("dimension_id", _UUID, sa.ForeignKey("dimension.id"), nullable=False),
        sa.Column("cle", sa.String(), nullable=False),
        sa.Column("libelle", sa.String(), nullable=False),
        _created(),
        _updated(),
        sa.UniqueConstraint("dimension_id", "cle", name="uq_valeur_dimension_dim_cle"),
    )

    op.create_table(
        "espace",
        _id(),
        sa.Column("nature", nature_espace, nullable=False),
        sa.Column("archive", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_via", sa.Text(), nullable=True),
        sa.Column("combinaison_signature", sa.Text(), nullable=False),
        _created(),
        _updated(),
        # Unicité de combinaison (§4.2) : pas deux espaces au même jeu de coordonnées.
        sa.UniqueConstraint("combinaison_signature", name="uq_espace_combinaison_signature"),
    )

    op.create_table(
        "espace_coordonnee",
        sa.Column("espace_id", _UUID, sa.ForeignKey("espace.id"), nullable=False),
        sa.Column("dimension_id", _UUID, sa.ForeignKey("dimension.id"), nullable=False),
        sa.Column("valeur_id", _UUID, sa.ForeignKey("valeur_dimension.id"), nullable=False),
        _created(),
        # PK (espace_id, dimension_id) : au plus une valeur par dimension dans un espace.
        sa.PrimaryKeyConstraint("espace_id", "dimension_id", name="pk_espace_coordonnee"),
    )

    op.create_table(
        "groupe",
        _id(),
        sa.Column("type", type_groupe, nullable=False),
        sa.Column("cle", sa.String(), nullable=False),
        _created(),
        _updated(),
        sa.UniqueConstraint("cle", name="uq_groupe_cle"),
    )

    op.create_table(
        "groupe_membre",
        sa.Column("groupe_id", _UUID, sa.ForeignKey("groupe.id"), nullable=False),
        sa.Column("compte_id", sa.String(), nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("groupe_id", "compte_id", name="pk_groupe_membre"),
    )

    op.create_table(
        "ressource",
        _id(),
        # AUCUN id externe ici (agnosticité §3.3) -> ressource_mapping.
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("espace_id", _UUID, sa.ForeignKey("espace.id"), nullable=False),
        sa.Column("chemin", sa.Text(), nullable=False),
        _created(),
        _updated(),
    )

    op.create_table(
        "ressource_mapping",
        sa.Column("ressource_id", _UUID, sa.ForeignKey("ressource.id"), nullable=False),
        sa.Column("adaptateur", sa.Text(), nullable=False),
        sa.Column("cle_externe", sa.Text(), nullable=False),
        _created(),
        _updated(),
        sa.PrimaryKeyConstraint("ressource_id", "adaptateur", name="pk_ressource_mapping"),
    )

    op.create_table(
        "octroi",  # ÉTAT COURANT
        _id(),
        sa.Column("ressource_id", _UUID, sa.ForeignKey("ressource.id"), nullable=False),
        sa.Column("groupe_id", _UUID, sa.ForeignKey("groupe.id"), nullable=False),
        sa.Column("mode", mode_octroi, nullable=False),
        sa.Column("matrice", postgresql.JSONB(), nullable=True),
        _created(),
        _updated(),
        sa.UniqueConstraint("ressource_id", "groupe_id", name="uq_octroi_ressource_groupe"),
    )

    op.create_table(
        "journal_acces",  # JOURNAL append-only (pas de FK : historique)
        _id(),
        sa.Column("ressource_id", _UUID, nullable=False),
        sa.Column("groupe_id", _UUID, nullable=False),
        sa.Column("action", action_journal, nullable=False),
        sa.Column("matrice_avant", postgresql.JSONB(), nullable=True),
        sa.Column("matrice_apres", postgresql.JSONB(), nullable=True),
        sa.Column("cause", postgresql.JSONB(), nullable=True),
        sa.Column("acteur", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )

    # --- GARDE-FOU INV-6 : journal_acces append-only au niveau Postgres --------
    # Rejette tout UPDATE / DELETE / TRUNCATE. Seul l'INSERT est permis. Même un
    # accès direct à la base ne peut ni modifier ni supprimer une ligne de journal.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION journal_acces_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'journal_acces est append-only (INV-6) : % interdit. '
                'Corriger par une ligne compensatoire (INSERT).', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_acces_no_update_delete
        BEFORE UPDATE OR DELETE ON journal_acces
        FOR EACH ROW EXECUTE FUNCTION journal_acces_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_acces_no_truncate
        BEFORE TRUNCATE ON journal_acces
        FOR EACH STATEMENT EXECUTE FUNCTION journal_acces_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS journal_acces_no_truncate ON journal_acces")
    op.execute("DROP TRIGGER IF EXISTS journal_acces_no_update_delete ON journal_acces")
    op.execute("DROP FUNCTION IF EXISTS journal_acces_append_only()")

    for table in (
        "journal_acces",
        "octroi",
        "ressource_mapping",
        "ressource",
        "groupe_membre",
        "groupe",
        "espace_coordonnee",
        "espace",
        "valeur_dimension",
        "dimension",
    ):
        op.drop_table(table)

    bind = op.get_bind()
    for e in (action_journal, mode_octroi, type_groupe, nature_espace):
        e.drop(bind, checkfirst=True)
