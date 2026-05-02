"""patch194_projects

Revision ID: b03fbb0bd5e3
Revises: 7feab49e6afe
Create Date: 2026-05-02 00:00:00.000000

Patch 194 (Phase 5a #1): Projekte als Entitaet.

Legt ``projects`` und ``project_files`` in ``bunker_memory.db`` an
(Decision 1, 2026-05-01: keine separate SQLite-DB). Idempotent — auf
DBs, die das Schema schon ueber ``init_db`` (Startup-Hook) bekommen
haben, passiert nichts.

Soft-delete via ``projects.is_archived``; harte Loeschung kaskadiert
ueber den Repo-Layer (kein FK-Cascade in der Tabellen-Definition,
weil die Models bewusst dependency-frei sind).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b03fbb0bd5e3"
down_revision: Union[str, None] = "7feab49e6afe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, table: str) -> bool:
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:n",
        {"n": table},
    ).fetchall()
    return bool(rows)


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "projects"):
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("persona_overlay", sa.Text(), nullable=True),
            sa.Column("is_archived", sa.Integer(), server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_slug ON projects(slug)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_projects_is_archived "
        "ON projects(is_archived, updated_at DESC)"
    )

    if not _has_table(conn, "project_files"):
        op.create_table(
            "project_files",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("relative_path", sa.String(length=500), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("mime_type", sa.String(length=100), nullable=True),
            sa.Column("storage_path", sa.String(length=500), nullable=False),
            sa.Column("uploaded_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "project_id", "relative_path",
                name="uq_project_files_project_path",
            ),
        )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_project_files_project "
        "ON project_files(project_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_project_files_sha "
        "ON project_files(sha256)"
    )
    # uq_project_files_project_path wird durch UniqueConstraint im
    # create_table oben angelegt — kein separater CREATE INDEX noetig.


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP INDEX IF EXISTS idx_project_files_sha")
    conn.exec_driver_sql("DROP INDEX IF EXISTS idx_project_files_project")
    if _has_table(conn, "project_files"):
        op.drop_table("project_files")
    conn.exec_driver_sql("DROP INDEX IF EXISTS idx_projects_is_archived")
    conn.exec_driver_sql("DROP INDEX IF EXISTS uq_projects_slug")
    if _has_table(conn, "projects"):
        op.drop_table("projects")
