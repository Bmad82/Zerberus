"""Tests für Patch 132 — strukturierter Memory-Store und Hel-Endpoints."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """In-Memory SQLite for isolation. Re-init DB gegen temp file."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_memory.db"
    url = f"sqlite+aiosqlite:///{db_file}"

    # Monkey-patch database module's session maker globals
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


class TestMemoryModel:
    def test_memory_model_registered(self):
        from zerberus.core.database import Memory
        assert Memory.__tablename__ == "memories"
        cols = {c.name for c in Memory.__table__.columns}
        expected = {
            "id", "category", "subject", "fact", "confidence",
            "source_conversation_id", "source_tag", "embedding_index",
            "extracted_at", "is_active",
        }
        assert expected.issubset(cols)


class TestStructuredStore:
    def test_insert_and_retrieve(self, tmp_db):
        from zerberus.modules.memory.extractor import _store_memory_structured

        async def run():
            row_id = await _store_memory_structured(
                fact_text="Chris arbeitet in der Luft- und Raumfahrtindustrie.",
                category="personal",
                source_tag="test_manual",
                confidence=0.95,
            )
            return row_id

        row_id = asyncio.run(run())
        assert row_id is not None and row_id > 0

    def test_exact_duplicate_skipped(self, tmp_db):
        from zerberus.modules.memory.extractor import _store_memory_structured

        async def run():
            id1 = await _store_memory_structured(
                fact_text="Nala ist eine Katze.",
                category="personal",
                source_tag="t",
            )
            id2 = await _store_memory_structured(
                fact_text="Nala ist eine Katze.",
                category="personal",
                source_tag="t",
            )
            return id1, id2

        id1, id2 = asyncio.run(run())
        assert id1 is not None
        assert id2 is None

    def test_different_category_not_duplicate(self, tmp_db):
        from zerberus.modules.memory.extractor import _store_memory_structured

        async def run():
            id1 = await _store_memory_structured(
                fact_text="Chris programmiert in Python.",
                category="technical",
                source_tag="t",
            )
            id2 = await _store_memory_structured(
                fact_text="Chris programmiert in Python.",
                category="preference",
                source_tag="t",
            )
            return id1, id2

        id1, id2 = asyncio.run(run())
        assert id1 is not None and id2 is not None
        assert id1 != id2


class TestHelMemoryEndpoints:
    def test_list_empty_db(self, tmp_db):
        from zerberus.app.routers.hel import get_memory_list
        result = asyncio.run(get_memory_list())
        assert result["memories"] == []
        assert result["count"] == 0

    def test_list_with_entries(self, tmp_db):
        from zerberus.app.routers.hel import get_memory_list
        from zerberus.modules.memory.extractor import _store_memory_structured

        async def seed():
            await _store_memory_structured("Fakt A", "personal", "t")
            await _store_memory_structured("Fakt B", "technical", "t")
            await _store_memory_structured("Fakt C", "personal", "t")

        asyncio.run(seed())
        result = asyncio.run(get_memory_list())
        assert result["count"] == 3
        # Filter nach Kategorie
        personal = asyncio.run(get_memory_list(category="personal"))
        assert personal["count"] == 2

    def test_add_manual_memory(self, tmp_db):
        from zerberus.app.routers.hel import post_memory_add, get_memory_list

        class FakeRequest:
            async def json(self):
                return {"fact": "Manueller Fakt", "category": "preference", "confidence": 0.9}

        result = asyncio.run(post_memory_add(FakeRequest()))
        assert result["status"] == "ok"
        assert result["id"] is not None

        listed = asyncio.run(get_memory_list())
        assert listed["count"] == 1
        assert listed["memories"][0]["category"] == "preference"

    def test_soft_delete(self, tmp_db):
        from zerberus.app.routers.hel import (
            post_memory_add, get_memory_list, delete_memory,
        )

        class FakeRequest:
            async def json(self):
                return {"fact": "Zum Löschen", "category": "personal"}

        row = asyncio.run(post_memory_add(FakeRequest()))
        mid = row["id"]
        asyncio.run(delete_memory(mid))
        listed = asyncio.run(get_memory_list())
        assert listed["count"] == 0  # Soft-deleted nicht mehr gelistet

    def test_stats(self, tmp_db):
        from zerberus.app.routers.hel import get_memory_stats
        from zerberus.modules.memory.extractor import _store_memory_structured

        async def seed():
            await _store_memory_structured("A", "personal", "t")
            await _store_memory_structured("B", "personal", "t")
            await _store_memory_structured("C", "technical", "t")

        asyncio.run(seed())
        stats = asyncio.run(get_memory_stats())
        assert stats["total"] == 3
        assert stats["by_category"]["personal"] == 2
        assert stats["by_category"]["technical"] == 1
