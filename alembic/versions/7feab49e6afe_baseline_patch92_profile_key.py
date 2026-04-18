"""baseline_patch92_profile_key

Revision ID: 7feab49e6afe
Revises:
Create Date: 2026-04-18 14:00:38.251692

Patch 92 Baseline: Dokumentiert den aktuellen IST-Zustand der DB und fügt
die `profile_key`-Spalte + Index in `interactions` hinzu, falls nicht vorhanden.

Die Migration ist idempotent — auf bereits migrierten DBs (per init_db
Startup-Hook) passiert nichts. Auf frischen DBs legt sie die Spalte an
und kopiert profile_name → profile_key.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7feab49e6afe'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "interactions", "profile_key"):
        op.add_column(
            "interactions",
            sa.Column("profile_key", sa.String(length=100), nullable=True),
        )
        # Bestehende profile_name-Werte migrieren
        conn.exec_driver_sql(
            "UPDATE interactions SET profile_key = profile_name "
            "WHERE profile_name IS NOT NULL AND profile_name != ''"
        )
    # Index (IF NOT EXISTS via raw SQL, da op.create_index kein IF-NOT-EXISTS hat)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_interactions_profile_key "
        "ON interactions(profile_key, timestamp DESC)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP INDEX IF EXISTS idx_interactions_profile_key")
    if _has_column(conn, "interactions", "profile_key"):
        with op.batch_alter_table("interactions") as batch_op:
            batch_op.drop_column("profile_key")
