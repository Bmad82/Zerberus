"""Tests für Patch 134 — DB-Deduplizierung."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


@pytest.fixture
def tmp_db(monkeypatch):
    """Isolierte DB pro Test."""
    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "dedup.db"
    url = f"sqlite+aiosqlite:///{db_file}"

    import zerberus.core.database as db_mod
    from zerberus.core.database import Base

    engine = create_async_engine(url, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())

    monkeypatch.setattr(db_mod, "_engine", engine)
    monkeypatch.setattr(db_mod, "_async_session_maker", sm)
    yield sm
    asyncio.run(engine.dispose())


def _insert_row(sm, profile_key, role, content, ts):
    """Helper: füge eine Row direkt via SQL ein (umgeht store_interaction Dedup-Guard)."""
    async def go():
        async with sm() as s:
            await s.execute(sa_text(
                "INSERT INTO interactions (profile_key, role, content, timestamp, integrity) "
                "VALUES (:pk, :r, :c, :t, 1.0)"
            ), {"pk": profile_key, "r": role, "c": content, "t": ts})
            await s.commit()
    asyncio.run(go())


def _count_active(sm) -> int:
    async def go():
        async with sm() as s:
            r = await s.execute(sa_text(
                "SELECT COUNT(*) FROM interactions WHERE integrity >= 0"
            ))
            return r.scalar() or 0
    return asyncio.run(go())


class TestDedupSimpleCases:
    def test_exact_duplicate_within_window_detected(self, tmp_db):
        from zerberus.utils.db_dedup import deduplicate_interactions
        now = datetime(2026, 4, 24, 12, 0, 0)
        _insert_row(tmp_db, "chris", "user", "Hallo Welt", now)
        _insert_row(tmp_db, "chris", "user", "Hallo Welt", now + timedelta(seconds=10))

        result = asyncio.run(deduplicate_interactions(dry_run=False, do_backup=False))
        assert result["scanned"] == 2
        assert result["duplicate_groups"] == 1
        assert result["removed"] == 1
        assert _count_active(tmp_db) == 1

    def test_same_content_outside_window_not_removed(self, tmp_db):
        from zerberus.utils.db_dedup import deduplicate_interactions
        now = datetime(2026, 4, 24, 12, 0, 0)
        _insert_row(tmp_db, "chris", "user", "Wie geht's", now)
        _insert_row(tmp_db, "chris", "user", "Wie geht's", now + timedelta(seconds=120))

        result = asyncio.run(deduplicate_interactions(dry_run=False, do_backup=False, window_seconds=60))
        assert result["removed"] == 0
        assert _count_active(tmp_db) == 2

    def test_different_content_not_duplicate(self, tmp_db):
        from zerberus.utils.db_dedup import deduplicate_interactions
        now = datetime(2026, 4, 24, 12, 0, 0)
        _insert_row(tmp_db, "chris", "user", "Frage A", now)
        _insert_row(tmp_db, "chris", "user", "Frage B", now + timedelta(seconds=1))

        result = asyncio.run(deduplicate_interactions(dry_run=False, do_backup=False))
        assert result["removed"] == 0

    def test_different_profile_keys_not_duplicate(self, tmp_db):
        from zerberus.utils.db_dedup import deduplicate_interactions
        now = datetime(2026, 4, 24, 12, 0, 0)
        _insert_row(tmp_db, "chris", "user", "Hallo", now)
        _insert_row(tmp_db, "jojo", "user", "Hallo", now + timedelta(seconds=5))

        result = asyncio.run(deduplicate_interactions(dry_run=False, do_backup=False))
        assert result["removed"] == 0

    def test_three_identical_messages_keep_first(self, tmp_db):
        from zerberus.utils.db_dedup import deduplicate_interactions
        now = datetime(2026, 4, 24, 12, 0, 0)
        _insert_row(tmp_db, "chris", "user", "Retry", now)
        _insert_row(tmp_db, "chris", "user", "Retry", now + timedelta(seconds=20))
        _insert_row(tmp_db, "chris", "user", "Retry", now + timedelta(seconds=40))

        result = asyncio.run(deduplicate_interactions(dry_run=False, do_backup=False))
        assert result["removed"] == 2
        assert _count_active(tmp_db) == 1


class TestDedupDryRun:
    def test_dry_run_does_not_modify(self, tmp_db):
        from zerberus.utils.db_dedup import deduplicate_interactions
        now = datetime(2026, 4, 24, 12, 0, 0)
        _insert_row(tmp_db, "chris", "user", "Dup", now)
        _insert_row(tmp_db, "chris", "user", "Dup", now + timedelta(seconds=5))

        result = asyncio.run(deduplicate_interactions(dry_run=True, do_backup=False))
        assert result["removed"] == 1
        assert result["dry_run"] is True
        # Aber die DB ist unverändert
        assert _count_active(tmp_db) == 2


class TestDedupEdgeCases:
    def test_empty_db(self, tmp_db):
        from zerberus.utils.db_dedup import deduplicate_interactions
        result = asyncio.run(deduplicate_interactions(dry_run=False, do_backup=False))
        assert result["scanned"] == 0
        assert result["removed"] == 0

    def test_backup_creates_file(self, tmp_db, tmp_path):
        """backup_db() kopiert die Quelle in ein Backup-Verzeichnis."""
        from zerberus.utils.db_dedup import backup_db
        src = tmp_path / "bunker.db"
        src.write_text("fake db content")
        out = backup_db(src, backup_dir=tmp_path / "bak")
        assert out is not None
        assert out.exists()
        assert out.read_text() == "fake db content"

    def test_backup_nonexistent_source_returns_none(self, tmp_path):
        from zerberus.utils.db_dedup import backup_db
        out = backup_db(tmp_path / "does_not_exist.db")
        assert out is None
