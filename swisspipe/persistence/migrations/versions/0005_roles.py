"""Rôles + résolution (L2 étape 3) — role, role_affectation, correspondance_compte
+ colonne modele.matrice_par_role + enum source_affectation.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-01

ADDITIF au socle : AUCUN changement à journal_acces ni à son enum action (INV-6 : le
journal des droits reste tel quel — on RÉUTILISE action='octroi'/'revocation'). On ajoute :
- `role` : rôle défini par un modèle (modele_id, cle, libelle).
- `role_affectation` : résolution rôle→groupe PERSONNEL (INV-4), figée (effectif_depuis,
  INV-3), source='humain' (INV-1). retire_at nullable = titulaire retiré (réversible).
- `correspondance_compte` : compte ↔ (systeme, cle_externe) pour l'ERP (L4 ; source
  'humain' suffit maintenant).
- `modele.matrice_par_role` (jsonb, additif) : la matrice imposée par rôle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_affectation = postgresql.ENUM("humain", name="source_affectation", create_type=False)

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
    source_affectation.create(bind, checkfirst=True)

    op.add_column("modele", sa.Column("matrice_par_role", postgresql.JSONB(), nullable=True))

    op.create_table(
        "role",
        _id(),
        sa.Column("modele_id", _UUID, sa.ForeignKey("modele.id"), nullable=False),
        sa.Column("cle", sa.Text(), nullable=False),
        sa.Column("libelle", sa.Text(), nullable=False),
        _created(),
        _updated(),
        sa.UniqueConstraint("modele_id", "cle", name="uq_role_modele_cle"),
    )

    op.create_table(
        "role_affectation",
        _id(),
        sa.Column("espace_id", _UUID, sa.ForeignKey("espace.id"), nullable=False),
        sa.Column("role_id", _UUID, sa.ForeignKey("role.id"), nullable=False),
        # Cible = un groupe PERSONNEL (INV-4).
        sa.Column("groupe_perso_id", _UUID, sa.ForeignKey("groupe.id"), nullable=False),
        sa.Column("source", source_affectation, nullable=False),
        # Instant DÉCLARÉ et FIGÉ (INV-3).
        sa.Column("effectif_depuis", sa.DateTime(timezone=True), nullable=False),
        # Retrait du titulaire (réversible, historique conservé).
        sa.Column("retire_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        _updated(),
    )

    op.create_table(
        "correspondance_compte",
        sa.Column("compte_id", sa.Text(), nullable=False),
        sa.Column("systeme", sa.Text(), nullable=False),
        sa.Column("cle_externe", sa.Text(), nullable=False),
        _created(),
        _updated(),
        # Unicité : un (systeme, cle_externe) désigne au plus un compte.
        sa.PrimaryKeyConstraint("systeme", "cle_externe", name="pk_correspondance_compte"),
    )


def downgrade() -> None:
    op.drop_table("correspondance_compte")
    op.drop_table("role_affectation")
    op.drop_table("role")
    op.drop_column("modele", "matrice_par_role")
    bind = op.get_bind()
    source_affectation.drop(bind, checkfirst=True)
