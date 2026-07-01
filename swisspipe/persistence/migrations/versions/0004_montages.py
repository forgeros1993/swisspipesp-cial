"""Montages (L2 étape 2) — table montage + enum etat_montage + extension type_evenement.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-01

ADDITIF au socle : ne touche NI journal_acces (droits, INV-6) NI les entités L1/étape 1.
On ajoute :
- deux valeurs à `type_evenement` ('montage', 'demontage') — extension de NOTRE journal
  d'événements ; le journal des droits reste séparé et intouché.
- l'enum `etat_montage` ('actif', 'archive') — un montage s'archive (réversible), pas de
  suppression dure (§3).
- la table `montage` (§4.4) : OÙ (hôte + chemin) + PLAFOND (matrice L1 en jsonb) + portée
  (jsonb) + consentement (deux clés) + état. AUCUN bénéficiaire (INV-1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

etat_montage = postgresql.ENUM("actif", "archive", name="etat_montage", create_type=False)

_UUID = postgresql.UUID(as_uuid=True)
_PK = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE doit sortir de la transaction de migration (Postgres).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE type_evenement ADD VALUE IF NOT EXISTS 'montage'")
        op.execute("ALTER TYPE type_evenement ADD VALUE IF NOT EXISTS 'demontage'")

    bind = op.get_bind()
    etat_montage.create(bind, checkfirst=True)

    op.create_table(
        "montage",
        sa.Column("id", _UUID, primary_key=True, server_default=_PK),
        # OÙ : l'instance montée (transverse) et son hôte (dimensionnel OU personnel).
        sa.Column("espace_transverse_id", _UUID, sa.ForeignKey("espace.id"), nullable=False),
        sa.Column("espace_hote_id", _UUID, sa.ForeignKey("espace.id"), nullable=False),
        sa.Column("chemin_hote", sa.Text(), nullable=False),
        # Portée (§5.5) : fenêtre = ensemble de chemins, en jsonb {"chemins": [...]}.
        sa.Column("portee", postgresql.JSONB(), nullable=False),
        # PLAFOND : Matrice L1 sérialisée (pas de nouveau type).
        sa.Column("matrice_plafond", postgresql.JSONB(), nullable=False),
        # Deux clés : consentement de l'hôte (auteur, PAS un bénéficiaire).
        sa.Column("consenti_par", sa.Text(), nullable=False),
        sa.Column("consenti_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etat", etat_montage, server_default=sa.text("'actif'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_NOW,
            onupdate=_NOW,
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("montage")
    bind = op.get_bind()
    etat_montage.drop(bind, checkfirst=True)
    # NB : Postgres ne sait pas retirer une valeur d'enum ('montage'/'demontage' de
    # type_evenement) sans recréer le type ; on les laisse (inoffensives, additives).
