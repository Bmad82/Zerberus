"""Patch 203a (Phase 5a #5, Vorbereitung) — Tests fuer Project-Workspace-Layout.

Schichten:
- **Pure-Function**: ``workspace_root_for``, ``is_inside_workspace``.
- **Sync FS**: ``materialize_file``, ``remove_file``, ``wipe_workspace`` —
  mit echtem ``tmp_path``, weil das hier die einzige Logik ist (kein DB).
- **Async DB**: ``materialize_file_async``, ``remove_file_async``,
  ``sync_workspace`` — mit ``tmp_db``-Fixture analog zu test_projects_*.
- **Endpoint-Integration**: Upload/Delete/Delete-Project/Template
  triggern den Workspace.
- **Source-Audit**: Verdrahtungs-Stellen sind im Source vorhanden.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures (analog test_projects_template.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_projects_workspace.db"
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
    """Biegt ``hel._projects_storage_base`` auf ``tmp_path`` um."""
    from zerberus.app.routers import hel as hel_mod

    monkeypatch.setattr(hel_mod, "_projects_storage_base", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def workspace_enabled(monkeypatch):
    from zerberus.core import config as cfg

    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "workspace_enabled", True)
    # RAG/Template-Flags abstellen, damit Tests nicht durch fehlenden
    # Embedder oder Template-Bytes fehlschlagen.
    monkeypatch.setattr(s.projects, "rag_enabled", False)
    monkeypatch.setattr(s.projects, "auto_template", False)
    return s


@pytest.fixture
def workspace_disabled(monkeypatch):
    from zerberus.core import config as cfg

    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "workspace_enabled", False)
    monkeypatch.setattr(s.projects, "rag_enabled", False)
    monkeypatch.setattr(s.projects, "auto_template", False)
    return s


# ---------------------------------------------------------------------------
# Pure-Function-Tests
# ---------------------------------------------------------------------------


class TestPureFunctions:
    def test_workspace_root_for_layout(self, tmp_path):
        from zerberus.core.projects_workspace import workspace_root_for

        ws = workspace_root_for("my-slug", tmp_path)
        assert ws == tmp_path / "projects" / "my-slug" / "_workspace"

    def test_workspace_root_for_no_io(self, tmp_path):
        # Pure: Aufruf darf KEIN Verzeichnis anlegen.
        from zerberus.core.projects_workspace import workspace_root_for

        ws = workspace_root_for("ghost", tmp_path)
        assert not ws.exists()

    def test_is_inside_workspace_normal_path(self, tmp_path):
        from zerberus.core.projects_workspace import is_inside_workspace

        root = tmp_path / "ws"
        root.mkdir()
        target = root / "subdir" / "file.py"
        assert is_inside_workspace(target, root) is True

    def test_is_inside_workspace_traversal(self, tmp_path):
        from zerberus.core.projects_workspace import is_inside_workspace

        root = tmp_path / "ws"
        root.mkdir()
        # `../../etc/passwd`-style Pfad — resolved zeigt aus dem Workspace raus
        target = root / ".." / ".." / "escape.txt"
        assert is_inside_workspace(target, root) is False

    def test_is_inside_workspace_root_itself(self, tmp_path):
        from zerberus.core.projects_workspace import is_inside_workspace

        root = tmp_path / "ws"
        root.mkdir()
        # Wurzel ist per Konvention "innerhalb" — relative_to(root) == ".".
        assert is_inside_workspace(root, root) is True


# ---------------------------------------------------------------------------
# materialize_file (Sync)
# ---------------------------------------------------------------------------


class TestMaterializeFile:
    def _make_source(self, dir_: Path, content: bytes = b"hello") -> Path:
        src = dir_ / "src" / "00" / "abc"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(content)
        return src

    def test_creates_target_in_workspace(self, tmp_path):
        from zerberus.core.projects_workspace import materialize_file, workspace_root_for

        src = self._make_source(tmp_path, b"alpha")
        ws = workspace_root_for("p1", tmp_path)
        method = materialize_file(ws, "main.py", src)
        target = ws / "main.py"
        assert target.exists()
        assert target.read_bytes() == b"alpha"
        assert method in {"hardlink", "copy"}

    def test_creates_nested_dirs(self, tmp_path):
        from zerberus.core.projects_workspace import materialize_file, workspace_root_for

        src = self._make_source(tmp_path, b"deep")
        ws = workspace_root_for("p1", tmp_path)
        materialize_file(ws, "src/utils/helper.py", src)
        assert (ws / "src" / "utils" / "helper.py").exists()

    def test_idempotent_second_call_is_noop(self, tmp_path):
        from zerberus.core.projects_workspace import materialize_file, workspace_root_for

        src = self._make_source(tmp_path, b"same")
        ws = workspace_root_for("p1", tmp_path)
        first = materialize_file(ws, "f.txt", src)
        second = materialize_file(ws, "f.txt", src)
        assert first in {"hardlink", "copy"}
        # Idempotenz: Inode/Size matcht → None.
        assert second is None

    def test_traversal_rejected(self, tmp_path):
        from zerberus.core.projects_workspace import materialize_file, workspace_root_for

        src = self._make_source(tmp_path, b"x")
        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        method = materialize_file(ws, "../../etc/passwd", src)
        assert method is None
        # Der Bypass-Versuch hat NICHTS ausserhalb des Workspaces erzeugt.
        assert not (tmp_path / "etc" / "passwd").exists()

    def test_missing_source_returns_none(self, tmp_path):
        from zerberus.core.projects_workspace import materialize_file, workspace_root_for

        ws = workspace_root_for("p1", tmp_path)
        method = materialize_file(ws, "f.txt", tmp_path / "ghost.bin")
        assert method is None
        assert not (ws / "f.txt").exists()

    def test_copy_fallback_when_hardlink_fails(self, tmp_path, monkeypatch):
        """``os.link`` raised → Pfad muss auf ``shutil.copy2`` ausweichen."""
        from zerberus.core import projects_workspace

        src = self._make_source(tmp_path, b"copyme")
        ws = projects_workspace.workspace_root_for("p1", tmp_path)

        def _broken_link(*args, **kwargs):
            raise OSError("simulated cross-fs")

        monkeypatch.setattr(projects_workspace.os, "link", _broken_link)
        method = projects_workspace.materialize_file(ws, "x.bin", src)
        assert method == "copy"
        assert (ws / "x.bin").read_bytes() == b"copyme"


# ---------------------------------------------------------------------------
# remove_file
# ---------------------------------------------------------------------------


class TestRemoveFile:
    def test_removes_existing_file(self, tmp_path):
        from zerberus.core.projects_workspace import remove_file, workspace_root_for

        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        target = ws / "f.txt"
        target.write_text("x")
        assert remove_file(ws, "f.txt") is True
        assert not target.exists()

    def test_cleans_empty_parent_dirs_up_to_root(self, tmp_path):
        from zerberus.core.projects_workspace import remove_file, workspace_root_for

        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        nested = ws / "a" / "b" / "c"
        nested.mkdir(parents=True)
        (nested / "f.txt").write_text("x")
        remove_file(ws, "a/b/c/f.txt")
        assert not (ws / "a").exists()
        # Workspace-Wurzel bleibt erhalten.
        assert ws.exists()

    def test_keeps_non_empty_parent(self, tmp_path):
        from zerberus.core.projects_workspace import remove_file, workspace_root_for

        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "a").mkdir()
        (ws / "a" / "f1.txt").write_text("x")
        (ws / "a" / "f2.txt").write_text("y")
        remove_file(ws, "a/f1.txt")
        assert (ws / "a").exists()
        assert (ws / "a" / "f2.txt").exists()

    def test_missing_file_returns_false(self, tmp_path):
        from zerberus.core.projects_workspace import remove_file, workspace_root_for

        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        assert remove_file(ws, "ghost.txt") is False

    def test_traversal_rejected(self, tmp_path):
        from zerberus.core.projects_workspace import remove_file, workspace_root_for

        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        # Datei ausserhalb anlegen
        outside = tmp_path / "secret.txt"
        outside.write_text("dont touch me")
        assert remove_file(ws, "../../secret.txt") is False
        assert outside.exists()


# ---------------------------------------------------------------------------
# wipe_workspace
# ---------------------------------------------------------------------------


class TestWipeWorkspace:
    def test_removes_existing_workspace(self, tmp_path):
        from zerberus.core.projects_workspace import (
            workspace_root_for,
            wipe_workspace,
        )

        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "deep" / "tree").mkdir(parents=True)
        (ws / "deep" / "tree" / "f").write_text("x")
        assert wipe_workspace(ws) is True
        assert not ws.exists()

    def test_idempotent_when_missing(self, tmp_path):
        from zerberus.core.projects_workspace import (
            workspace_root_for,
            wipe_workspace,
        )

        ws = workspace_root_for("ghost", tmp_path)
        assert wipe_workspace(ws) is False  # nichts zu tun

    def test_rejects_wrong_dirname(self, tmp_path):
        """Sicherheits-Check: Pfad muss auf ``_workspace`` enden."""
        from zerberus.core.projects_workspace import wipe_workspace

        evil = tmp_path / "important_data"
        evil.mkdir()
        (evil / "do_not_delete.txt").write_text("important")
        assert wipe_workspace(evil) is False
        assert (evil / "do_not_delete.txt").exists()


# ---------------------------------------------------------------------------
# sync_workspace (async, DB)
# ---------------------------------------------------------------------------


class TestSyncWorkspace:
    def _bytes_to_storage(self, base: Path, slug: str, content: bytes) -> tuple[str, Path]:
        """Schreibt ``content`` in den SHA-Storage und gibt (sha, path) zurueck."""
        from zerberus.core.projects_repo import compute_sha256, storage_path_for

        sha = compute_sha256(content)
        target = storage_path_for(slug, sha, base)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return sha, target

    def test_unknown_project_returns_zeros(self, tmp_db, tmp_path):
        from zerberus.core.projects_workspace import sync_workspace

        result = asyncio.run(sync_workspace(9999, tmp_path))
        assert result == {"materialized": 0, "removed": 0, "skipped": 0}

    def test_materializes_all_files(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import (
            sync_workspace,
            workspace_root_for,
        )

        async def run():
            project = await projects_repo.create_project("Sync Project")
            slug = project["slug"]
            for rel, body in [("a.py", b"print('a')"), ("docs/b.md", b"# B")]:
                sha, path = self._bytes_to_storage(tmp_path, slug, body)
                await projects_repo.register_file(
                    project_id=project["id"],
                    relative_path=rel,
                    sha256=sha,
                    size_bytes=len(body),
                    storage_path=str(path),
                )
            return project, await sync_workspace(project["id"], tmp_path)

        project, result = asyncio.run(run())
        ws = workspace_root_for(project["slug"], tmp_path)
        assert result["materialized"] == 2
        assert result["removed"] == 0
        assert (ws / "a.py").exists()
        assert (ws / "docs" / "b.md").exists()

    def test_idempotent_second_call(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import sync_workspace

        async def run():
            project = await projects_repo.create_project("Idem Project")
            sha, path = self._bytes_to_storage(tmp_path, project["slug"], b"data")
            await projects_repo.register_file(
                project_id=project["id"],
                relative_path="single.txt",
                sha256=sha,
                size_bytes=4,
                storage_path=str(path),
            )
            first = await sync_workspace(project["id"], tmp_path)
            second = await sync_workspace(project["id"], tmp_path)
            return first, second

        first, second = asyncio.run(run())
        assert first["materialized"] == 1
        assert second["materialized"] == 0
        assert second["removed"] == 0
        assert second["skipped"] == 1

    def test_removes_orphans(self, tmp_db, tmp_path):
        """Workspace hat eine Datei, die NICHT (mehr) in der DB ist —
        sync räumt sie weg."""
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import (
            sync_workspace,
            workspace_root_for,
        )

        async def run():
            project = await projects_repo.create_project("Orphan Project")
            ws = workspace_root_for(project["slug"], tmp_path)
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "orphan.txt").write_text("nicht in DB")
            return project, await sync_workspace(project["id"], tmp_path)

        project, result = asyncio.run(run())
        ws = workspace_root_for(project["slug"], tmp_path)
        assert result["removed"] == 1
        assert not (ws / "orphan.txt").exists()


# ---------------------------------------------------------------------------
# Async-Convenience-Wrapper
# ---------------------------------------------------------------------------


class TestAsyncWrappers:
    def test_materialize_file_async(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import (
            materialize_file_async,
            workspace_root_for,
        )
        from zerberus.core.projects_repo import compute_sha256, storage_path_for

        async def run():
            project = await projects_repo.create_project("Wrap")
            sha = compute_sha256(b"abc")
            target = storage_path_for(project["slug"], sha, tmp_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"abc")
            f = await projects_repo.register_file(
                project_id=project["id"],
                relative_path="hello.txt",
                sha256=sha,
                size_bytes=3,
                storage_path=str(target),
            )
            return project, await materialize_file_async(
                project_id=project["id"],
                file_id=f["id"],
                base_dir=tmp_path,
            )

        project, method = asyncio.run(run())
        ws = workspace_root_for(project["slug"], tmp_path)
        assert method in {"hardlink", "copy"}
        assert (ws / "hello.txt").read_bytes() == b"abc"

    def test_materialize_file_async_unknown_file(self, tmp_db, tmp_path):
        from zerberus.core.projects_workspace import materialize_file_async

        result = asyncio.run(
            materialize_file_async(project_id=1, file_id=9999, base_dir=tmp_path)
        )
        assert result is None

    def test_remove_file_async(self, tmp_path):
        from zerberus.core.projects_workspace import (
            remove_file_async,
            workspace_root_for,
        )

        ws = workspace_root_for("p1", tmp_path)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "kill.txt").write_text("x")
        ok = asyncio.run(remove_file_async("p1", "kill.txt", tmp_path))
        assert ok is True
        assert not (ws / "kill.txt").exists()


# ---------------------------------------------------------------------------
# Endpoint-Integration
# ---------------------------------------------------------------------------


class TestEndpointIntegration:
    def test_upload_endpoint_materializes_workspace(
        self, tmp_db, tmp_storage, workspace_enabled
    ):
        from zerberus.app.routers.hel import upload_project_file_endpoint
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import workspace_root_for

        async def run():
            proj = await projects_repo.create_project("Upload Test")
            pid = proj["id"]
            slug = proj["slug"]

            class _UF:
                def __init__(self, name, data, ct=None):
                    self.filename = name
                    self._data = data
                    self.content_type = ct

                async def read(self):
                    return self._data

            up = await upload_project_file_endpoint(
                pid, _UF("script.py", b"print('hi')", "text/x-python")
            )
            return slug, up

        slug, up = asyncio.run(run())
        assert up["status"] == "ok"
        ws = workspace_root_for(slug, tmp_storage)
        assert (ws / "script.py").exists()
        assert (ws / "script.py").read_bytes() == b"print('hi')"

    def test_delete_file_endpoint_removes_workspace_file(
        self, tmp_db, tmp_storage, workspace_enabled
    ):
        from zerberus.app.routers.hel import (
            upload_project_file_endpoint,
            delete_project_file_endpoint,
        )
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import workspace_root_for

        async def run():
            class _UF:
                def __init__(self, name, data):
                    self.filename = name
                    self._data = data
                    self.content_type = None

                async def read(self):
                    return self._data

            proj = await projects_repo.create_project("Delete Test")
            pid = proj["id"]
            slug = proj["slug"]
            up = await upload_project_file_endpoint(
                pid, _UF("doomed.txt", b"bye")
            )
            file_id = up["file"]["id"]
            await delete_project_file_endpoint(pid, file_id)
            return slug

        slug = asyncio.run(run())
        ws = workspace_root_for(slug, tmp_storage)
        assert not (ws / "doomed.txt").exists()

    def test_delete_project_endpoint_wipes_workspace(
        self, tmp_db, tmp_storage, workspace_enabled
    ):
        from zerberus.app.routers.hel import (
            upload_project_file_endpoint,
            delete_project_endpoint,
        )
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import workspace_root_for

        async def run():
            class _UF:
                def __init__(self, name, data):
                    self.filename = name
                    self._data = data
                    self.content_type = None

                async def read(self):
                    return self._data

            proj = await projects_repo.create_project("Wipe Test")
            pid = proj["id"]
            slug = proj["slug"]
            await upload_project_file_endpoint(pid, _UF("a.txt", b"a"))
            ws = workspace_root_for(slug, tmp_storage)
            assert ws.exists()
            await delete_project_endpoint(pid)
            return ws

        ws = asyncio.run(run())
        assert not ws.exists()

    def test_template_materialize_creates_workspace_files(
        self, tmp_db, tmp_path, monkeypatch
    ):
        """``materialize_template`` muss die Skelett-Files in den
        Workspace spiegeln (Templates landen im SHA-Storage; ohne
        Workspace-Spiegel waeren sie nur unter Hash-Pfaden begehbar)."""
        from zerberus.core import config as cfg
        from zerberus.core import projects_repo, projects_template
        from zerberus.core.projects_workspace import workspace_root_for

        s = cfg.get_settings()
        monkeypatch.setattr(s.projects, "rag_enabled", False)
        monkeypatch.setattr(s.projects, "workspace_enabled", True)

        async def run():
            project = await projects_repo.create_project("Tpl Workspace")
            await projects_template.materialize_template(project, tmp_path)
            return project

        project = asyncio.run(run())
        ws = workspace_root_for(project["slug"], tmp_path)
        bible_name = (
            projects_template.PROJECT_BIBLE_FILENAME_TEMPLATE.format(
                slug_upper=project["slug"].upper()
            )
        )
        assert (ws / bible_name).exists()
        assert (ws / projects_template.README_FILENAME).exists()


class TestWorkspaceDisabled:
    """Wenn ``workspace_enabled=False`` → keine Workspace-Files."""

    def test_upload_skips_workspace_when_disabled(
        self, tmp_db, tmp_storage, workspace_disabled
    ):
        from zerberus.app.routers.hel import upload_project_file_endpoint
        from zerberus.core import projects_repo
        from zerberus.core.projects_workspace import workspace_root_for

        async def run():
            class _UF:
                def __init__(self, name, data):
                    self.filename = name
                    self._data = data
                    self.content_type = None

                async def read(self):
                    return self._data

            proj = await projects_repo.create_project("Off")
            pid = proj["id"]
            slug = proj["slug"]
            await upload_project_file_endpoint(pid, _UF("a.txt", b"a"))
            return slug

        slug = asyncio.run(run())
        ws = workspace_root_for(slug, tmp_storage)
        assert not (ws / "a.txt").exists()


# ---------------------------------------------------------------------------
# Source-Audit-Tests — Verdrahtungs-Stellen sind im Code vorhanden
# ---------------------------------------------------------------------------


class TestSourceAudit:
    def _read(self, mod) -> str:
        return Path(inspect.getfile(mod)).read_text(encoding="utf-8")

    def test_hel_upload_calls_workspace_helper(self):
        from zerberus.app.routers import hel as hel_mod

        src = self._read(hel_mod)
        assert "projects_workspace" in src
        assert "materialize_file_async" in src

    def test_hel_delete_file_calls_remove(self):
        from zerberus.app.routers import hel as hel_mod

        src = self._read(hel_mod)
        assert "remove_file_async" in src

    def test_hel_delete_project_calls_wipe(self):
        from zerberus.app.routers import hel as hel_mod

        src = self._read(hel_mod)
        assert "wipe_workspace" in src

    def test_template_calls_workspace(self):
        from zerberus.core import projects_template as tpl_mod

        src = self._read(tpl_mod)
        assert "materialize_file_async" in src
        assert "projects_workspace" in src

    def test_workspace_module_uses_workspace_dirname(self):
        from zerberus.core import projects_workspace as ws_mod

        src = self._read(ws_mod)
        # Die Konstante wird in workspace_root_for + wipe_workspace verwendet.
        assert src.count("WORKSPACE_DIRNAME") >= 2
