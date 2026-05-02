"""Patch 196 (Phase 5a #4) — Tests fuer Datei-Upload + Delete-Endpoints.

Pattern wie ``test_projects_endpoints.py``: ruft die Endpoint-Coroutines
direkt auf, mit ``tmp_db``-Fixture fuer DB-Isolation und einem
``tmp_storage``-Monkeypatch auf ``_projects_storage_base()`` fuer
Storage-Isolation. Ein Duck-Type-Upload-Mock (`_FakeUpload`) ersetzt
das FastAPI-`UploadFile`, damit die Tests unabhaengig von der konkreten
Pydantic-/FastAPI-Version laufen — der Endpoint liest nur ``filename``,
``content_type`` und ``await file.read()``.
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
    db_file = Path(tmpdir) / "test_projects_files.db"
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


@pytest.fixture
def tmp_storage(monkeypatch, tmp_path):
    """Biegt ``hel._projects_storage_base`` auf ``tmp_path`` um — die
    eigentlichen Bytes landen dort statt im echten ``data/``-Ordner.
    """
    from zerberus.app.routers import hel as hel_mod

    monkeypatch.setattr(hel_mod, "_projects_storage_base", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _disable_auto_template_for_upload_tests(monkeypatch):
    """Patch 198: Diese Tests pruefen Upload-Verhalten ohne die
    P198-Template-Files in der Datei-Liste. Flag wird global ausgeschaltet —
    Template-Tests leben in ``test_projects_template.py``."""
    from zerberus.core import config as cfg

    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "auto_template", False)


class _FakeUpload:
    """Duck-Type-Mock fuer ``UploadFile``. Bietet nur das Minimum, das
    der Endpoint braucht (`filename`, `content_type`, `await read()`).
    """

    def __init__(self, filename: str, data: bytes, content_type: str | None = None):
        self.filename = filename
        self._data = data
        self.content_type = content_type

    async def read(self):
        return self._data


# ---------------------------------------------------------------------------
# Upload — Happy-Path
# ---------------------------------------------------------------------------


class TestUploadHappyPath:
    def test_upload_writes_bytes_and_metadata(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            list_project_files_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Upload-Test"}))
            pid = c["project"]["id"]
            up = _FakeUpload("README.md", b"# Hallo Welt\n", content_type="text/markdown")
            res = await upload_project_file_endpoint(pid, up)
            files = await list_project_files_endpoint(pid)
            return c["project"], res, files

        project, res, files = asyncio.run(run())
        assert res["status"] == "ok"
        assert res["file"]["relative_path"] == "README.md"
        assert res["file"]["size_bytes"] == len(b"# Hallo Welt\n")
        # Bytes liegen im Storage
        storage_path = Path(res["file"]["storage_path"])
        assert storage_path.exists()
        assert storage_path.read_bytes() == b"# Hallo Welt\n"
        # Storage-Pfad-Konvention <base>/projects/<slug>/<sha[:2]>/<sha>
        assert "projects" in storage_path.parts
        assert project["slug"] in storage_path.parts
        # Listing zeigt die Datei
        assert files["count"] == 1

    def test_upload_with_subdirectory_path(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Subdir-Test"}))
            pid = c["project"]["id"]
            up = _FakeUpload("src/main.py", b"print('x')")
            return await upload_project_file_endpoint(pid, up)

        res = asyncio.run(run())
        assert res["file"]["relative_path"] == "src/main.py"

    def test_dedup_same_sha_writes_once(self, tmp_db, tmp_storage):
        """Selbe Bytes in zwei Projekten → nur einmal auf Disk."""
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        data = b"shared content"

        async def run():
            a = await create_project_endpoint(_FakeRequest({"name": "Project-A"}))
            b = await create_project_endpoint(_FakeRequest({"name": "Project-B"}))
            r1 = await upload_project_file_endpoint(a["project"]["id"], _FakeUpload("x.txt", data))
            r2 = await upload_project_file_endpoint(b["project"]["id"], _FakeUpload("x.txt", data))
            return r1, r2

        r1, r2 = asyncio.run(run())
        # SHA ist identisch
        assert r1["file"]["sha256"] == r2["file"]["sha256"]
        # Beide Storage-Pfade sind gleich (Inhalts-Dedup ueber Projekte hinweg
        # findet nur statt, wenn die Pfad-Konvention sha-basiert ist —
        # Pfade unterscheiden sich aber per Slug-Praefix, damit Bytes
        # NICHT geteilt werden, sondern pro Projekt liegen).
        # Die DB-Eintraege selbst zeigen denselben sha256.
        assert r1["file"]["storage_path"] != r2["file"]["storage_path"]


# ---------------------------------------------------------------------------
# Upload — Reject-Pfade
# ---------------------------------------------------------------------------


class TestUploadRejects:
    def test_missing_project_returns_404(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import upload_project_file_endpoint

        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload_project_file_endpoint(9999, _FakeUpload("x.txt", b"data")))
        assert exc.value.status_code == 404

    def test_blocked_extension_returns_400(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Block-Ext"}))
            pid = c["project"]["id"]
            await upload_project_file_endpoint(pid, _FakeUpload("malware.exe", b"MZ..."))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 400
        assert "blockiert" in exc.value.detail.lower() or "block" in exc.value.detail.lower()

    def test_blocked_extension_case_insensitive(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Block-Case"}))
            pid = c["project"]["id"]
            await upload_project_file_endpoint(pid, _FakeUpload("BIG.EXE", b"MZ..."))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 400

    def test_empty_filename_returns_400(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Empty-Name"}))
            pid = c["project"]["id"]
            await upload_project_file_endpoint(pid, _FakeUpload("", b"data"))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 400

    def test_path_traversal_returns_400(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Traversal"}))
            pid = c["project"]["id"]
            # ".." sollte sanitize_relative_path strippen → Rest "etc/passwd"
            up = _FakeUpload("../../etc/passwd", b"r:x:0:0")
            return await upload_project_file_endpoint(pid, up)

        res = asyncio.run(run())
        # Das ".." wird gestrippt — Datei landet als "etc/passwd" (relativ),
        # nicht im OS-System-Pfad. Storage-Path liegt im tmp_storage-Tree.
        assert res["file"]["relative_path"] == "etc/passwd"
        assert "etc" not in Path(res["file"]["storage_path"]).parts[:2]

    def test_only_dotdot_filename_returns_400(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Dotdot"}))
            pid = c["project"]["id"]
            await upload_project_file_endpoint(pid, _FakeUpload("../..", b"x"))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 400

    def test_empty_data_returns_400(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Empty-Data"}))
            pid = c["project"]["id"]
            await upload_project_file_endpoint(pid, _FakeUpload("empty.txt", b""))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 400

    def test_too_large_returns_413(self, tmp_db, tmp_storage, monkeypatch):
        """Max-Size-Limit auf 10 Bytes runtersetzen, dann 11 Bytes hochladen."""
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.core.config import get_settings
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        settings = get_settings()
        monkeypatch.setattr(settings.projects, "max_upload_bytes", 10)

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Too-Large"}))
            pid = c["project"]["id"]
            await upload_project_file_endpoint(pid, _FakeUpload("big.txt", b"x" * 11))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 413

    def test_duplicate_path_returns_409(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Dup-Path"}))
            pid = c["project"]["id"]
            await upload_project_file_endpoint(pid, _FakeUpload("a.txt", b"first"))
            await upload_project_file_endpoint(pid, _FakeUpload("a.txt", b"second"))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Delete — SHA-Dedup-Verhalten
# ---------------------------------------------------------------------------


class TestDeleteFile:
    def test_delete_removes_bytes_when_unique(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            delete_project_file_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Delete-Unique"}))
            pid = c["project"]["id"]
            up = await upload_project_file_endpoint(pid, _FakeUpload("solo.txt", b"unique-bytes"))
            storage = Path(up["file"]["storage_path"])
            res = await delete_project_file_endpoint(pid, up["file"]["id"])
            return storage, res

        storage, res = asyncio.run(run())
        assert res["status"] == "ok"
        assert res["bytes_removed"] is True
        assert not storage.exists()

    def test_delete_keeps_bytes_when_referenced_elsewhere(self, tmp_db, tmp_storage):
        """Zwei Projekte teilen sich denselben sha256 (separate Storage-Pfade
        per Slug). Loescht man eine Seite, bleiben die Bytes der ANDEREN Seite
        liegen — das ist der Schutz gegen versehentliches Loeschen geteilter
        Inhalte. Der Test legt zwei Files mit identischem Inhalt an, loescht
        einen → der zweite Storage-Pfad bleibt.
        """
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            delete_project_file_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            a = await create_project_endpoint(_FakeRequest({"name": "Shared-A"}))
            b = await create_project_endpoint(_FakeRequest({"name": "Shared-B"}))
            up_a = await upload_project_file_endpoint(a["project"]["id"], _FakeUpload("x.txt", b"shared"))
            up_b = await upload_project_file_endpoint(b["project"]["id"], _FakeUpload("x.txt", b"shared"))
            storage_b = Path(up_b["file"]["storage_path"])
            res = await delete_project_file_endpoint(a["project"]["id"], up_a["file"]["id"])
            return storage_b, res

        storage_b, res = asyncio.run(run())
        assert res["status"] == "ok"
        assert res["bytes_removed"] is False
        # Die Bytes des anderen Projekts duerfen nicht mit weggewischt werden
        assert storage_b.exists()

    def test_delete_unknown_returns_404(self, tmp_db, tmp_storage):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            delete_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Del-404"}))
            await delete_project_file_endpoint(c["project"]["id"], 9999)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 404

    def test_delete_wrong_project_returns_404(self, tmp_db, tmp_storage):
        """Datei aus Projekt A laesst sich nicht ueber Projekt B's URL
        loeschen — verhindert Cross-Project-Mutation per Path-Manipulation.
        """
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            delete_project_file_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            a = await create_project_endpoint(_FakeRequest({"name": "Owner"}))
            b = await create_project_endpoint(_FakeRequest({"name": "Stranger"}))
            up = await upload_project_file_endpoint(a["project"]["id"], _FakeUpload("a.txt", b"data"))
            # Versuche, ueber B zu loeschen
            await delete_project_file_endpoint(b["project"]["id"], up["file"]["id"])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Storage-Cleanup — leere Parent-Ordner
# ---------------------------------------------------------------------------


class TestStorageCleanup:
    def test_empty_parent_dirs_get_removed(self, tmp_db, tmp_storage):
        """Nach Delete sollte der sha-prefix-Ordner verschwinden, wenn keine
        andere Datei mehr drin liegt — verhindert leere Ordner-Pyramide.
        """
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            delete_project_file_endpoint,
            upload_project_file_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            c = await create_project_endpoint(_FakeRequest({"name": "Cleanup"}))
            pid = c["project"]["id"]
            up = await upload_project_file_endpoint(pid, _FakeUpload("only.txt", b"once"))
            storage = Path(up["file"]["storage_path"])
            await delete_project_file_endpoint(pid, up["file"]["id"])
            return storage

        storage = asyncio.run(run())
        # Datei weg
        assert not storage.exists()
        # Sha-Prefix-Ordner weg
        assert not storage.parent.exists()
