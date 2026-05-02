"""Patch 194 (Phase 5a #1) — Tests fuer Hel-Endpoints ``/admin/projects/*``.

Ruft die Endpoint-Coroutines direkt auf (gleiches Muster wie
``test_huginn_config_endpoint.py``). Spart einen TestClient/ASGI-Setup
und testet trotzdem die HTTPException-Pfade. ``verify_admin``-Dependency
wird dabei umgangen (Endpoints sind reine Python-Funktionen).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_projects_endpoints.db"
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


class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


# ----- list / create --------------------------------------------------------


class TestListAndCreate:
    def test_empty_list(self, tmp_db):
        from zerberus.app.routers.hel import list_projects_endpoint

        res = asyncio.run(list_projects_endpoint())
        assert res == {"projects": [], "count": 0}

    def test_create_then_list(self, tmp_db):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            list_projects_endpoint,
        )

        async def run():
            created = await create_project_endpoint(
                _FakeRequest({"name": "Mein Projekt", "description": "kurze Beschreibung"})
            )
            listing = await list_projects_endpoint()
            return created, listing

        created, listing = asyncio.run(run())
        assert created["status"] == "ok"
        assert created["project"]["slug"] == "mein-projekt"
        assert listing["count"] == 1

    def test_create_with_overlay(self, tmp_db):
        from zerberus.app.routers.hel import create_project_endpoint

        overlay = {"system_addendum": "Sei knapp", "tone_hints": ["technisch"]}
        res = asyncio.run(
            create_project_endpoint(
                _FakeRequest({"name": "Overlay-Projekt", "persona_overlay": overlay})
            )
        )
        assert res["project"]["persona_overlay"] == overlay

    def test_create_empty_name_raises_400(self, tmp_db):
        from zerberus.app.routers.hel import create_project_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(create_project_endpoint(_FakeRequest({"name": "  "})))
        assert exc.value.status_code == 400

    def test_create_invalid_overlay_type_raises_400(self, tmp_db):
        from zerberus.app.routers.hel import create_project_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                create_project_endpoint(
                    _FakeRequest({"name": "X", "persona_overlay": "string statt dict"})
                )
            )
        assert exc.value.status_code == 400

    def test_list_archived_filter(self, tmp_db):
        from zerberus.app.routers.hel import (
            archive_project_endpoint,
            create_project_endpoint,
            list_projects_endpoint,
        )

        async def run():
            await create_project_endpoint(_FakeRequest({"name": "Active"}))
            archived = await create_project_endpoint(_FakeRequest({"name": "Archived"}))
            await archive_project_endpoint(archived["project"]["id"])
            visible = await list_projects_endpoint()
            with_archived = await list_projects_endpoint(include_archived=True)
            return visible, with_archived

        visible, with_archived = asyncio.run(run())
        assert visible["count"] == 1
        assert with_archived["count"] == 2


# ----- get / update / archive / delete --------------------------------------


class TestGetUpdateArchive:
    def test_get_existing(self, tmp_db):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            get_project_endpoint,
        )

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Get-Test"}))
            return await get_project_endpoint(c["project"]["id"])

        res = asyncio.run(run())
        assert res["project"]["name"] == "Get-Test"

    def test_get_missing_raises_404(self, tmp_db):
        from zerberus.app.routers.hel import get_project_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_project_endpoint(9999))
        assert exc.value.status_code == 404

    def test_patch_partial(self, tmp_db):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            update_project_endpoint,
        )

        async def run():
            c = await create_project_endpoint(
                _FakeRequest({"name": "Original", "description": "alt"})
            )
            return await update_project_endpoint(
                c["project"]["id"],
                _FakeRequest({"name": "Neu"}),
            )

        res = asyncio.run(run())
        assert res["project"]["name"] == "Neu"
        assert res["project"]["description"] == "alt"  # nicht angefasst

    def test_patch_invalid_overlay_raises_400(self, tmp_db):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            update_project_endpoint,
        )

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "X"}))
            await update_project_endpoint(
                c["project"]["id"],
                _FakeRequest({"persona_overlay": "string statt dict"}),
            )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 400

    def test_patch_missing_raises_404(self, tmp_db):
        from zerberus.app.routers.hel import update_project_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(update_project_endpoint(9999, _FakeRequest({"name": "x"})))
        assert exc.value.status_code == 404

    def test_archive_unarchive_roundtrip(self, tmp_db):
        from zerberus.app.routers.hel import (
            archive_project_endpoint,
            create_project_endpoint,
            unarchive_project_endpoint,
        )

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Toggle"}))
            pid = c["project"]["id"]
            a = await archive_project_endpoint(pid)
            u = await unarchive_project_endpoint(pid)
            return a, u

        a, u = asyncio.run(run())
        assert a["project"]["is_archived"] is True
        assert u["project"]["is_archived"] is False

    def test_archive_missing_raises_404(self, tmp_db):
        from zerberus.app.routers.hel import archive_project_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(archive_project_endpoint(9999))
        assert exc.value.status_code == 404

    def test_hard_delete(self, tmp_db):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            delete_project_endpoint,
            get_project_endpoint,
        )

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Doomed"}))
            pid = c["project"]["id"]
            ok = await delete_project_endpoint(pid)
            try:
                await get_project_endpoint(pid)
                gone = False
            except HTTPException as e:
                gone = e.status_code == 404
            return ok, gone

        ok, gone = asyncio.run(run())
        assert ok == {"status": "ok"}
        assert gone is True

    def test_delete_missing_raises_404(self, tmp_db):
        from zerberus.app.routers.hel import delete_project_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(delete_project_endpoint(9999))
        assert exc.value.status_code == 404


# ----- files endpoint -------------------------------------------------------


class TestFilesEndpoint:
    def test_list_files_empty(self, tmp_db):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            list_project_files_endpoint,
        )

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "No-Files"}))
            return await list_project_files_endpoint(c["project"]["id"])

        res = asyncio.run(run())
        assert res == {"files": [], "count": 0}

    def test_list_files_for_missing_project_raises_404(self, tmp_db):
        from zerberus.app.routers.hel import list_project_files_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(list_project_files_endpoint(9999))
        assert exc.value.status_code == 404

    def test_list_files_after_register(self, tmp_db):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            list_project_files_endpoint,
        )
        from zerberus.core import projects_repo

        sha = "9" * 64

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Has-Files"}))
            pid = c["project"]["id"]
            await projects_repo.register_file(
                project_id=pid,
                relative_path="src/main.py",
                sha256=sha,
                size_bytes=128,
                storage_path="x",
                mime_type="text/x-python",
            )
            return await list_project_files_endpoint(pid)

        res = asyncio.run(run())
        assert res["count"] == 1
        assert res["files"][0]["relative_path"] == "src/main.py"
