"""Projection partielle (L2 étape 9) — extension type_evenement.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-02

ADDITIF : une valeur d'enum 'projection_partielle' sur type_evenement (même patron que
'montage'/'demontage', migration 0004). Sert à AUDITER (INV-6) qu'un droit désiré porte
un additionnel non projetable en ACL Nextcloud (CLASSEMENT/TÉLÉCHARGEMENT — aucun verbe
ACL) : l'enforcement ne change pas, la perte devient VISIBLE au journal des événements.
journal_acces (journal des DROITS) INTOUCHÉ.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE doit sortir de la transaction de migration (Postgres).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE type_evenement ADD VALUE IF NOT EXISTS 'projection_partielle'")


def downgrade() -> None:
    # Postgres ne retire pas une valeur d'enum sans recréer le type ; valeur inoffensive,
    # on la laisse (même décision que 0004).
    pass
