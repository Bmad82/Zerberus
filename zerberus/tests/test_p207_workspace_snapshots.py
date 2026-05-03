"""Patch 207 (Phase 5a #9 + #10) — Tests fuer Workspace-Snapshots,
Diff und Rollback.

Schichten:

1. **Pure-Function** — ``build_workspace_manifest``, ``diff_snapshots``,
   ``_looks_text``, ``_is_safe_member``, ``_build_unified_diff``,
   ``DiffEntry.to_public_dict``.
2. **Sync-FS** — ``materialize_snapshot``, ``restore_snapshot``,
   ``snapshot_dir_for``, Tar-Path-Traversal-Defense.
3. **DB-Schicht** — ``store_snapshot_row``/``load_snapshot_row`` +
   ``snapshot_workspace_async``/``rollback_snapshot_async``.
4. **Endpoint** — ``POST /v1/workspace/rollback``: ok/restore_failed/
   project_mismatch/unknown_snapshot/snapshots_disabled.
5. **Source-Audit legacy.py** — Verdrahtung writable+snapshot+diff.
6. **Source-Audit nala.py** — Diff-Renderer, Rollback-Funktion, CSS,
   44x44 Touch-Target, escapeHtml-Usage.
7. **End-to-End** — chat_completions mit writable + Snapshot-Trigger.
8. **JS-Integrity** — ``node --check`` (analog P203b/P203d-3/P206).
9. **Smoke** — Flags, DB-Tabelle, /nala/-Endpoint.

Was die Tests NICHT pruefen:
  - dass die echte Docker-Sandbox tatsaechlich Code ausfuehrt (P203c)
  - dass der Diff-Algorithmus auf 100k-Zeilen-Files performant ist
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_gate():
    """Frischer HitL-Gate-Singleton pro Test (legacy E2E nutzt das)."""
    from zerberus.core.hitl_chat import reset_chat_hitl_gate
    reset_chat_hitl_gate()
    yield
    reset_chat_hitl_gate()


@pytest.fixture
def tmp_db(monkeypatch):
    """Frische SQLite-DB pro Test."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_p207.db"
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
def env(tmp_path, monkeypatch, tmp_db):
    """Settings-Cache + chdir + Persona-Files (analog P206)."""
    from zerberus.core.config import get_settings

    get_settings()  # Singleton vor chdir befuellen
    monkeypatch.chdir(tmp_path)
    Path("system_prompt_alice.json").write_text(
        '{"prompt": "Du bist Alice."}', encoding="utf-8",
    )
    Path("system_prompt.json").write_text(
        '{"prompt": "Default."}', encoding="utf-8",
    )
    return tmp_path


def _build_request(project_id, session_id="s-test", profile_name="alice"):
    state = SimpleNamespace(
        profile_name=profile_name,
        permission_level="admin",
        allowed_model=None,
        temperature=None,
    )
    headers = {"X-Session-ID": session_id}
    if project_id is not None:
        headers["X-Active-Project-Id"] = str(project_id)
    return SimpleNamespace(state=state, headers=headers)


def _make_fake_llm(answer):
    async def fake_call(self, messages, session_id,
                        model_override=None, temperature_override=None):
        return (answer, "test-model", 1, 1, 0.0)
    return fake_call


def _make_fake_sandbox_manager(*, enabled=True,
                                allowed_languages=("python", "javascript")):
    cfg = SimpleNamespace(
        enabled=enabled,
        allowed_languages=list(allowed_languages),
    )
    mgr = MagicMock()
    mgr.config = cfg
    return mgr


def _make_sandbox_result(*, stdout="", stderr="", exit_code=0,
                         execution_time_ms=42, truncated=False, error=None):
    from zerberus.modules.sandbox.manager import SandboxResult
    return SandboxResult(
        stdout=stdout, stderr=stderr, exit_code=exit_code,
        execution_time_ms=execution_time_ms,
        truncated=truncated, error=error,
    )


def _write_workspace(workspace_root: Path, files: dict[str, bytes]):
    """Helper: schreibt eine Datei-Map in den Workspace."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        target = workspace_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


# ---------------------------------------------------------------------------
# 1) Pure-Function-Schicht
# ---------------------------------------------------------------------------


class TestSnapshotDirFor:
    def test_path_layout(self, tmp_path):
        from zerberus.core.projects_snapshots import snapshot_dir_for
        result = snapshot_dir_for("demo", tmp_path)
        assert result == tmp_path / "projects" / "demo" / "_snapshots"

    def test_pure_function_no_fs(self, tmp_path):
        """Funktion legt nichts an — Pure-Function."""
        from zerberus.core.projects_snapshots import snapshot_dir_for
        snapshot_dir_for("foo", tmp_path)
        # Verzeichnis darf NICHT existieren
        assert not (tmp_path / "projects" / "foo").exists()


class TestLooksText:
    def test_empty_is_text(self):
        from zerberus.core.projects_snapshots import _looks_text
        assert _looks_text(b"") is True

    def test_pure_text(self):
        from zerberus.core.projects_snapshots import _looks_text
        assert _looks_text(b"hello world\n") is True

    def test_null_byte_is_binary(self):
        from zerberus.core.projects_snapshots import _looks_text
        assert _looks_text(b"abc\x00def") is False

    def test_utf8_text_with_umlauts(self):
        from zerberus.core.projects_snapshots import _looks_text
        assert _looks_text("öäü ßabc".encode("utf-8")) is True


class TestBuildWorkspaceManifest:
    def test_empty_workspace_returns_empty(self, tmp_path):
        from zerberus.core.projects_snapshots import build_workspace_manifest
        ws = tmp_path / "ws"
        assert build_workspace_manifest(ws) == {}

    def test_basic_manifest(self, tmp_path):
        from zerberus.core.projects_snapshots import build_workspace_manifest
        ws = tmp_path / "ws"
        _write_workspace(ws, {
            "a.py": b"print(1)\n",
            "b/c.txt": b"hello\n",
        })
        m = build_workspace_manifest(ws)
        assert set(m.keys()) == {"a.py", "b/c.txt"}
        assert m["a.py"]["size"] == len(b"print(1)\n")
        assert m["a.py"]["binary"] is False
        assert m["a.py"]["content"] == "print(1)\n"
        # Hash-Check
        assert isinstance(m["a.py"]["hash"], str) and len(m["a.py"]["hash"]) == 64

    def test_binary_file_no_content(self, tmp_path):
        from zerberus.core.projects_snapshots import build_workspace_manifest
        ws = tmp_path / "ws"
        _write_workspace(ws, {"img.bin": b"\x00\x01\x02\x03"})
        m = build_workspace_manifest(ws)
        assert m["img.bin"]["binary"] is True
        assert "content" not in m["img.bin"]

    def test_include_content_false(self, tmp_path):
        from zerberus.core.projects_snapshots import build_workspace_manifest
        ws = tmp_path / "ws"
        _write_workspace(ws, {"a.txt": b"hi"})
        m = build_workspace_manifest(ws, include_content=False)
        assert m["a.txt"]["hash"]
        assert "content" not in m["a.txt"]

    def test_large_text_skipped_for_content(self, tmp_path, monkeypatch):
        """Files > DIFF_TEXT_MAX_BYTES bekommen Hash + Size, kein Content."""
        from zerberus.core import projects_snapshots as snaps_mod
        monkeypatch.setattr(snaps_mod, "DIFF_TEXT_MAX_BYTES", 16)
        from zerberus.core.projects_snapshots import build_workspace_manifest
        ws = tmp_path / "ws"
        _write_workspace(ws, {"big.txt": b"x" * 100})
        m = build_workspace_manifest(ws)
        assert m["big.txt"]["size"] == 100
        assert "content" not in m["big.txt"]


class TestDiffSnapshots:
    def test_identical_returns_empty(self):
        from zerberus.core.projects_snapshots import diff_snapshots
        manifest = {
            "a.py": {"hash": "h1", "size": 10, "binary": False, "content": "x"},
        }
        entries = diff_snapshots(manifest, dict(manifest))
        assert entries == []

    def test_added_file(self):
        from zerberus.core.projects_snapshots import diff_snapshots
        before = {}
        after = {"new.py": {"hash": "h", "size": 5, "binary": False}}
        entries = diff_snapshots(before, after)
        assert len(entries) == 1
        assert entries[0].path == "new.py"
        assert entries[0].status == "added"
        assert entries[0].size_after == 5
        assert entries[0].size_before == 0

    def test_deleted_file(self):
        from zerberus.core.projects_snapshots import diff_snapshots
        before = {"old.py": {"hash": "h", "size": 7, "binary": False}}
        after = {}
        entries = diff_snapshots(before, after)
        assert len(entries) == 1
        assert entries[0].status == "deleted"
        assert entries[0].size_before == 7
        assert entries[0].size_after == 0

    def test_modified_with_inline_diff(self):
        from zerberus.core.projects_snapshots import diff_snapshots
        before = {
            "a.py": {
                "hash": "h1", "size": 10, "binary": False,
                "content": "line1\nline2\n",
            }
        }
        after = {
            "a.py": {
                "hash": "h2", "size": 11, "binary": False,
                "content": "line1\nline2-changed\n",
            }
        }
        entries = diff_snapshots(before, after)
        assert len(entries) == 1
        e = entries[0]
        assert e.status == "modified"
        assert e.unified_diff is not None
        assert "-line2" in e.unified_diff
        assert "+line2-changed" in e.unified_diff
        assert "a/a.py" in e.unified_diff
        assert "b/a.py" in e.unified_diff

    def test_modified_binary_no_unified_diff(self):
        from zerberus.core.projects_snapshots import diff_snapshots
        before = {"x.bin": {"hash": "h1", "size": 10, "binary": True}}
        after = {"x.bin": {"hash": "h2", "size": 12, "binary": True}}
        entries = diff_snapshots(before, after)
        assert entries[0].binary is True
        assert entries[0].unified_diff is None

    def test_modified_text_without_content_no_diff(self):
        """Wenn Manifest ohne content kommt, dann kein Inline-Diff."""
        from zerberus.core.projects_snapshots import diff_snapshots
        before = {"a.py": {"hash": "h1", "size": 5, "binary": False}}
        after = {"a.py": {"hash": "h2", "size": 5, "binary": False}}
        entries = diff_snapshots(before, after)
        assert entries[0].status == "modified"
        assert entries[0].unified_diff is None

    def test_sorted_by_path(self):
        from zerberus.core.projects_snapshots import diff_snapshots
        before = {}
        after = {
            "z.py": {"hash": "h", "size": 1, "binary": False},
            "a.py": {"hash": "h", "size": 1, "binary": False},
            "m/x.py": {"hash": "h", "size": 1, "binary": False},
        }
        entries = diff_snapshots(before, after)
        # added-Section ist sortiert
        assert [e.path for e in entries] == ["a.py", "m/x.py", "z.py"]

    def test_to_public_dict_keys(self):
        from zerberus.core.projects_snapshots import DiffEntry
        d = DiffEntry(path="a.py", status="added", size_after=5).to_public_dict()
        assert set(d.keys()) == {
            "path", "status", "size_before", "size_after", "binary",
            "unified_diff",
        }


class TestUnifiedDiff:
    def test_unified_diff_format(self):
        from zerberus.core.projects_snapshots import _build_unified_diff
        d = _build_unified_diff("a\nb\n", "a\nB\n", "x.py")
        assert "--- a/x.py" in d
        assert "+++ b/x.py" in d
        assert "-b" in d
        assert "+B" in d


class TestIsSafeMember:
    def _make_member(self, name, isfile=True, issym=False, islnk=False):
        m = tarfile.TarInfo(name=name)
        m.size = 5
        if issym:
            m.type = tarfile.SYMTYPE
        elif islnk:
            m.type = tarfile.LNKTYPE
        elif isfile:
            m.type = tarfile.REGTYPE
        return m

    def test_normal_file_ok(self, tmp_path):
        from zerberus.core.projects_snapshots import _is_safe_member
        m = self._make_member("a/b.py")
        assert _is_safe_member(m, tmp_path) is True

    def test_absolute_path_blocked(self, tmp_path):
        from zerberus.core.projects_snapshots import _is_safe_member
        m = self._make_member("/etc/passwd")
        assert _is_safe_member(m, tmp_path) is False

    def test_dotdot_blocked(self, tmp_path):
        from zerberus.core.projects_snapshots import _is_safe_member
        m = self._make_member("../../etc/passwd")
        assert _is_safe_member(m, tmp_path) is False

    def test_symlink_blocked(self, tmp_path):
        from zerberus.core.projects_snapshots import _is_safe_member
        m = self._make_member("link", isfile=False, issym=True)
        assert _is_safe_member(m, tmp_path) is False

    def test_hardlink_blocked(self, tmp_path):
        from zerberus.core.projects_snapshots import _is_safe_member
        m = self._make_member("link", isfile=False, islnk=True)
        assert _is_safe_member(m, tmp_path) is False


# ---------------------------------------------------------------------------
# 2) Sync-FS-Schicht — Snapshot, Restore, Tar-Format
# ---------------------------------------------------------------------------


class TestMaterializeSnapshot:
    def test_returns_none_for_missing_workspace(self, tmp_path):
        from zerberus.core.projects_snapshots import materialize_snapshot
        result = materialize_snapshot(
            workspace_root=tmp_path / "nope",
            snapshot_root=tmp_path / "snaps",
            label="test",
        )
        assert result is None

    def test_writes_tar_with_files(self, tmp_path):
        from zerberus.core.projects_snapshots import materialize_snapshot
        ws = tmp_path / "ws"
        _write_workspace(ws, {"a.py": b"x = 1\n", "b/c.txt": b"hi\n"})
        snaps = tmp_path / "snaps"

        result = materialize_snapshot(
            workspace_root=ws,
            snapshot_root=snaps,
            label="before_run",
        )
        assert result is not None
        assert result["label"] == "before_run"
        assert result["file_count"] == 2
        assert result["total_bytes"] == len(b"x = 1\n") + len(b"hi\n")
        archive = Path(result["archive_path"])
        assert archive.exists()
        # Tar enthaelt beide Files
        with tarfile.open(str(archive)) as t:
            names = sorted(t.getnames())
            assert names == ["a.py", "b/c.txt"]

    def test_explicit_snapshot_id(self, tmp_path):
        from zerberus.core.projects_snapshots import materialize_snapshot
        ws = tmp_path / "ws"
        _write_workspace(ws, {"a.py": b"x"})
        result = materialize_snapshot(
            workspace_root=ws,
            snapshot_root=tmp_path / "snaps",
            label="manual",
            snapshot_id="deadbeef" * 4,
        )
        assert result["id"] == "deadbeef" * 4
        assert Path(result["archive_path"]).name == ("deadbeef" * 4) + ".tar"

    def test_manifest_in_result(self, tmp_path):
        from zerberus.core.projects_snapshots import materialize_snapshot
        ws = tmp_path / "ws"
        _write_workspace(ws, {"a.py": b"x = 1\n"})
        result = materialize_snapshot(
            workspace_root=ws, snapshot_root=tmp_path / "snaps", label="t",
        )
        assert "a.py" in result["manifest"]
        assert result["manifest"]["a.py"]["content"] == "x = 1\n"


class TestRestoreSnapshot:
    def test_returns_none_if_archive_missing(self, tmp_path):
        from zerberus.core.projects_snapshots import restore_snapshot
        result = restore_snapshot(
            workspace_root=tmp_path / "ws",
            archive_path=tmp_path / "nope.tar",
        )
        assert result is None

    def test_restore_recreates_files(self, tmp_path):
        from zerberus.core.projects_snapshots import (
            materialize_snapshot, restore_snapshot,
        )
        ws = tmp_path / "ws"
        _write_workspace(ws, {"a.py": b"original\n", "sub/b.txt": b"bee\n"})
        snap = materialize_snapshot(
            workspace_root=ws,
            snapshot_root=tmp_path / "snaps",
            label="before",
        )
        # Workspace mutieren: a.py loeschen, b.txt aendern, c neu
        (ws / "a.py").unlink()
        (ws / "sub" / "b.txt").write_bytes(b"changed\n")
        (ws / "new.py").write_bytes(b"added\n")

        result = restore_snapshot(
            workspace_root=ws,
            archive_path=Path(snap["archive_path"]),
        )
        assert result is not None
        assert result["file_count"] == 2
        # Restored-Stand muss exakt before-Stand sein
        assert (ws / "a.py").read_bytes() == b"original\n"
        assert (ws / "sub" / "b.txt").read_bytes() == b"bee\n"
        # new.py darf nicht mehr da sein
        assert not (ws / "new.py").exists()

    def test_restore_blocks_traversal_member(self, tmp_path):
        """Path-Traversal-Member werden uebersprungen, der Rest extrahiert."""
        from zerberus.core.projects_snapshots import restore_snapshot
        ws = tmp_path / "ws"
        ws.mkdir()
        # Tar mit boesem Member bauen
        tar_path = tmp_path / "evil.tar"
        with tarfile.open(str(tar_path), "w") as t:
            # Boese: ../escape.txt
            evil = tarfile.TarInfo(name="../escape.txt")
            evil.size = 5
            evil.type = tarfile.REGTYPE
            import io
            t.addfile(evil, io.BytesIO(b"hello"))
            # Gut: legit.txt
            ok = tarfile.TarInfo(name="legit.txt")
            ok.size = 4
            ok.type = tarfile.REGTYPE
            t.addfile(ok, io.BytesIO(b"good"))

        result = restore_snapshot(workspace_root=ws, archive_path=tar_path)
        # Nur 1 File extrahiert (boeser geblockt)
        assert result["file_count"] == 1
        assert (ws / "legit.txt").read_bytes() == b"good"
        # Eltern-Dir des boesen Members darf NICHT existieren
        assert not (tmp_path / "escape.txt").exists()


# ---------------------------------------------------------------------------
# 3) DB-Schicht (store/load + Convenience)
# ---------------------------------------------------------------------------


class TestStoreLoadSnapshotRow:
    def test_store_and_load_roundtrip(self, tmp_db):
        from zerberus.core.projects_snapshots import (
            store_snapshot_row, load_snapshot_row,
        )
        snap_id = "a" * 32
        db_id = asyncio.run(store_snapshot_row(
            project_id=42,
            project_slug="demo",
            label="before_run",
            snapshot_id=snap_id,
            archive_path="/tmp/x.tar",
            file_count=3,
            total_bytes=99,
            pending_id="p" * 32,
            parent_snapshot_id=None,
        ))
        assert db_id is not None
        row = asyncio.run(load_snapshot_row(snap_id))
        assert row is not None
        assert row["snapshot_id"] == snap_id
        assert row["project_id"] == 42
        assert row["project_slug"] == "demo"
        assert row["label"] == "before_run"
        assert row["archive_path"] == "/tmp/x.tar"
        assert row["file_count"] == 3
        assert row["total_bytes"] == 99
        assert row["pending_id"] == "p" * 32
        assert row["parent_snapshot_id"] is None

    def test_load_unknown_returns_none(self, tmp_db):
        from zerberus.core.projects_snapshots import load_snapshot_row
        row = asyncio.run(load_snapshot_row("nope"))
        assert row is None

    def test_store_silent_skip_without_db(self, monkeypatch):
        import zerberus.core.database as db_mod
        from zerberus.core.projects_snapshots import store_snapshot_row
        monkeypatch.setattr(db_mod, "_async_session_maker", None)
        result = asyncio.run(store_snapshot_row(
            project_id=1, project_slug="x", label="t",
            snapshot_id="s", archive_path="/a", file_count=0, total_bytes=0,
        ))
        assert result is None


class TestSnapshotWorkspaceAsync:
    def test_creates_snap_and_db_row(self, env, tmp_db):
        from zerberus.core.projects_repo import create_project
        from zerberus.core.projects_snapshots import (
            snapshot_workspace_async, snapshot_dir_for, load_snapshot_row,
        )
        from zerberus.core.projects_workspace import workspace_root_for
        from zerberus.core.config import get_settings

        proj = asyncio.run(create_project(name="P-snap"))
        base = Path(get_settings().projects.data_dir)
        ws_root = workspace_root_for(proj["slug"], base)
        _write_workspace(ws_root, {"hi.py": b"print('hi')\n"})

        result = asyncio.run(snapshot_workspace_async(
            project_id=proj["id"], base_dir=base, label="before_run",
            pending_id="abc",
        ))
        assert result is not None
        assert result["label"] == "before_run"
        assert result["file_count"] == 1
        assert "hi.py" in result["manifest"]
        assert Path(result["archive_path"]).exists()
        assert result["db_row_id"] is not None
        # DB-Lookup
        row = asyncio.run(load_snapshot_row(result["id"]))
        assert row is not None
        assert row["project_id"] == proj["id"]
        assert row["pending_id"] == "abc"

    def test_returns_none_for_missing_project(self, env, tmp_db):
        from zerberus.core.projects_snapshots import snapshot_workspace_async
        from zerberus.core.config import get_settings
        result = asyncio.run(snapshot_workspace_async(
            project_id=99999, base_dir=Path(get_settings().projects.data_dir),
            label="t",
        ))
        assert result is None


class TestRollbackSnapshotAsync:
    def test_happy_path_restores_files(self, env, tmp_db):
        from zerberus.core.projects_repo import create_project
        from zerberus.core.projects_snapshots import (
            snapshot_workspace_async, rollback_snapshot_async,
        )
        from zerberus.core.projects_workspace import workspace_root_for
        from zerberus.core.config import get_settings

        proj = asyncio.run(create_project(name="P-rb"))
        base = Path(get_settings().projects.data_dir)
        ws = workspace_root_for(proj["slug"], base)
        _write_workspace(ws, {"a.py": b"original\n"})

        snap = asyncio.run(snapshot_workspace_async(
            project_id=proj["id"], base_dir=base, label="before",
        ))
        # Workspace zerstoeren
        (ws / "a.py").write_bytes(b"changed\n")
        (ws / "extra.py").write_bytes(b"new\n")

        result = asyncio.run(rollback_snapshot_async(
            snapshot_id=snap["id"], base_dir=base,
            expected_project_id=proj["id"],
        ))
        assert result is not None
        assert result["snapshot_id"] == snap["id"]
        assert result["project_id"] == proj["id"]
        assert (ws / "a.py").read_bytes() == b"original\n"
        assert not (ws / "extra.py").exists()

    def test_rejects_project_mismatch(self, env, tmp_db):
        from zerberus.core.projects_repo import create_project
        from zerberus.core.projects_snapshots import (
            snapshot_workspace_async, rollback_snapshot_async,
        )
        from zerberus.core.projects_workspace import workspace_root_for
        from zerberus.core.config import get_settings

        proj_a = asyncio.run(create_project(name="P-A"))
        base = Path(get_settings().projects.data_dir)
        ws_a = workspace_root_for(proj_a["slug"], base)
        _write_workspace(ws_a, {"a.py": b"orig\n"})
        snap_a = asyncio.run(snapshot_workspace_async(
            project_id=proj_a["id"], base_dir=base, label="t",
        ))

        # Rollback mit project_id=99 (nicht der Eigentuemer)
        result = asyncio.run(rollback_snapshot_async(
            snapshot_id=snap_a["id"], base_dir=base,
            expected_project_id=99,
        ))
        assert result is None

    def test_rejects_unknown_snapshot(self, env, tmp_db):
        from zerberus.core.projects_snapshots import rollback_snapshot_async
        from zerberus.core.config import get_settings
        result = asyncio.run(rollback_snapshot_async(
            snapshot_id="bogus", base_dir=Path(get_settings().projects.data_dir),
        ))
        assert result is None


# ---------------------------------------------------------------------------
# 4) Endpoint POST /v1/workspace/rollback
# ---------------------------------------------------------------------------


class TestWorkspaceRollbackEndpoint:
    def test_ok_path(self, env, tmp_db):
        from zerberus.core.projects_repo import create_project
        from zerberus.core.projects_snapshots import snapshot_workspace_async
        from zerberus.core.projects_workspace import workspace_root_for
        from zerberus.core.config import get_settings
        from zerberus.app.routers.legacy import (
            workspace_rollback, WorkspaceRollbackRequest,
        )

        proj = asyncio.run(create_project(name="P-ep"))
        base = Path(get_settings().projects.data_dir)
        ws = workspace_root_for(proj["slug"], base)
        _write_workspace(ws, {"a.py": b"orig\n"})
        snap = asyncio.run(snapshot_workspace_async(
            project_id=proj["id"], base_dir=base, label="before",
        ))

        # Mutieren + Rollback via Endpunkt
        (ws / "a.py").write_bytes(b"mutiert\n")
        req = WorkspaceRollbackRequest(
            snapshot_id=snap["id"], project_id=proj["id"],
        )
        resp = asyncio.run(workspace_rollback(req, get_settings()))
        assert resp.ok is True
        assert resp.snapshot_id == snap["id"]
        assert resp.project_id == proj["id"]
        assert resp.project_slug == proj["slug"]
        assert (ws / "a.py").read_bytes() == b"orig\n"

    def test_unknown_snapshot_returns_restore_failed(self, env, tmp_db):
        from zerberus.app.routers.legacy import (
            workspace_rollback, WorkspaceRollbackRequest,
        )
        from zerberus.core.config import get_settings
        req = WorkspaceRollbackRequest(snapshot_id="bogus", project_id=1)
        resp = asyncio.run(workspace_rollback(req, get_settings()))
        assert resp.ok is False
        assert resp.error == "restore_failed"

    def test_project_mismatch(self, env, tmp_db):
        from zerberus.core.projects_repo import create_project
        from zerberus.core.projects_snapshots import snapshot_workspace_async
        from zerberus.core.projects_workspace import workspace_root_for
        from zerberus.core.config import get_settings
        from zerberus.app.routers.legacy import (
            workspace_rollback, WorkspaceRollbackRequest,
        )

        proj_a = asyncio.run(create_project(name="A"))
        base = Path(get_settings().projects.data_dir)
        ws = workspace_root_for(proj_a["slug"], base)
        _write_workspace(ws, {"f.py": b"x"})
        snap = asyncio.run(snapshot_workspace_async(
            project_id=proj_a["id"], base_dir=base, label="t",
        ))

        req = WorkspaceRollbackRequest(
            snapshot_id=snap["id"], project_id=99999,
        )
        resp = asyncio.run(workspace_rollback(req, get_settings()))
        assert resp.ok is False
        # project_mismatch landet als restore_failed (rollback_snapshot_async
        # liefert None bei mismatch)
        assert resp.error == "restore_failed"

    def test_snapshots_disabled_blocks(self, env, tmp_db, monkeypatch):
        from zerberus.app.routers.legacy import (
            workspace_rollback, WorkspaceRollbackRequest,
        )
        from zerberus.core.config import get_settings
        s = get_settings()
        monkeypatch.setattr(s.projects, "snapshots_enabled", False)
        req = WorkspaceRollbackRequest(snapshot_id="x", project_id=1)
        resp = asyncio.run(workspace_rollback(req, s))
        assert resp.ok is False
        assert resp.error == "snapshots_disabled"


# ---------------------------------------------------------------------------
# 5) Source-Audit legacy.py — Verdrahtung
# ---------------------------------------------------------------------------


class TestLegacySourceAudit:
    def _src(self) -> str:
        return (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(
            encoding="utf-8"
        )

    def test_logging_tag_present(self):
        assert "[SNAPSHOT-207]" in self._src()

    def test_imports_snapshot_helpers(self):
        src = self._src()
        assert "snapshot_workspace_async" in src
        assert "diff_snapshots" in src

    def test_writable_flag_read_from_settings(self):
        src = self._src()
        assert 'getattr(settings.projects, "sandbox_writable"' in src

    def test_snapshots_enabled_flag_read(self):
        src = self._src()
        assert 'getattr(settings.projects, "snapshots_enabled"' in src

    def test_writable_passed_to_execute_in_workspace(self):
        """Argument writable=_writable steht im execute-Call."""
        src = self._src()
        idx = src.find("execute_in_workspace(")
        # Es gibt zwei Aufrufe — Convenience + im legacy. Wir suchen den
        # mit writable=_writable.
        assert "writable=_writable" in src

    def test_diff_field_in_payload(self):
        src = self._src()
        assert 'code_execution_payload["diff"]' in src

    def test_before_snapshot_id_in_payload(self):
        src = self._src()
        assert 'code_execution_payload["before_snapshot_id"]' in src

    def test_after_snapshot_id_in_payload(self):
        src = self._src()
        assert 'code_execution_payload["after_snapshot_id"]' in src

    def test_rollback_endpoint_registered(self):
        from zerberus.app.routers.legacy import router
        paths = {r.path for r in router.routes}
        assert "/v1/workspace/rollback" in paths

    def test_rollback_endpoint_has_pydantic_models(self):
        from zerberus.app.routers.legacy import (
            WorkspaceRollbackRequest, WorkspaceRollbackResponse,
        )
        assert "snapshot_id" in WorkspaceRollbackRequest.model_fields
        assert "project_id" in WorkspaceRollbackRequest.model_fields
        assert "ok" in WorkspaceRollbackResponse.model_fields
        assert "error" in WorkspaceRollbackResponse.model_fields


# ---------------------------------------------------------------------------
# 6) Source-Audit nala.py — Diff-Renderer + Rollback
# ---------------------------------------------------------------------------


class TestNalaSourceAudit:
    def _src(self) -> str:
        return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(
            encoding="utf-8"
        )

    def test_render_diff_card_defined(self):
        assert "function renderDiffCard(" in self._src()

    def test_colorize_unified_diff_defined(self):
        assert "function colorizeUnifiedDiff(" in self._src()

    def test_rollback_workspace_defined(self):
        src = self._src()
        assert "function rollbackWorkspace(" in src or \
            "async function rollbackWorkspace(" in src

    def test_rollback_posts_to_endpoint(self):
        src = self._src()
        idx = src.find("function rollbackWorkspace(")
        assert idx > 0
        window = src[idx:idx + 2500]
        assert "/v1/workspace/rollback" in window
        assert "snapshot_id" in window
        assert "project_id" in window

    def test_render_code_execution_calls_diff_renderer(self):
        src = self._src()
        idx = src.find("function renderCodeExecution(")
        assert idx > 0
        window = src[idx:idx + 6000]
        assert "renderDiffCard(" in window
        # Der Aufruf ist gated auf before_snapshot_id (kein Diff-Render
        # bei P206-only-Backends).
        assert "before_snapshot_id" in window

    def test_diff_card_css_present(self):
        src = self._src()
        for cls in (".diff-card", ".diff-list", ".diff-entry",
                    ".diff-rollback", ".diff-resolved",
                    ".diff-status.diff-added", ".diff-status.diff-modified",
                    ".diff-status.diff-deleted"):
            assert cls in src, f"CSS-Klasse {cls} fehlt"

    def test_touch_target_44px_in_diff_rollback(self):
        src = self._src()
        idx = src.find(".diff-rollback {")
        assert idx > 0
        window = src[idx:idx + 700]
        assert "min-height: 44px" in window
        assert "min-width: 44px" in window

    def test_xss_escape_on_diff_path(self):
        """Pfad-Strings im Renderer MUESSEN durch escapeHtml — Defense
        gegen Pfade mit HTML-Sonderzeichen (theoretisch moeglich)."""
        src = self._src()
        idx = src.find("function renderDiffCard(")
        assert idx > 0
        body = src[idx:idx + 6000]
        # Mindestens einmal escapeHtml(path) oder escapeHtml(... path
        assert "escapeHtml(path)" in body or "escapeHtml(String(entry.path" in body

    def test_xss_escape_on_unified_diff_text(self):
        """Inline-Diff-Inhalt MUSS durch escapeHtml laufen — Code-Inhalt
        des Users plus LLM-Output, beides nicht trustworthy."""
        src = self._src()
        idx = src.find("function colorizeUnifiedDiff(")
        assert idx > 0
        body = src[idx:idx + 1500]
        assert "escapeHtml(line)" in body

    def test_rollback_button_disabled_when_no_changes(self):
        """Wenn diff.length === 0, rollback button disabled (kein
        Sinn-Click). Plus wenn kein activeProjectId."""
        src = self._src()
        idx = src.find("function renderDiffCard(")
        body = src[idx:idx + 6000]
        assert "rollbackBtn.disabled = true" in body
        # Bedingung: !activeProjectId || diff.length === 0
        assert "diff.length" in body and "activeProjectId" in body

    def test_diff_render_skipped_when_skipped_payload(self):
        """Wenn codeExec.skipped=true (HitL rejected), darf KEIN Diff
        gerendert werden — es gab gar keinen Run."""
        src = self._src()
        idx = src.find("function renderCodeExecution(")
        body = src[idx:idx + 6000]
        # Im Diff-Trigger-Block: !skipped && Array.isArray(...) && before_snapshot_id
        assert re.search(
            r"!\s*skipped\s*&&\s*Array\.isArray\(codeExec\.diff\)",
            body,
        ) is not None


# ---------------------------------------------------------------------------
# 7) End-to-End — chat_completions mit writable + Snapshots
# ---------------------------------------------------------------------------


class TestE2EWritableSandboxAndDiff:
    """Stellt sicher dass writable=True den Snapshot-Pfad triggert und
    der Diff in der Response landet. Sandbox + execute_in_workspace
    werden gemockt — wir muessen den Workspace selbst mutieren, damit
    der Diff was zu sehen kriegt."""

    def _setup_common(self, monkeypatch, *, llm_answer,
                       writable=True, snapshots_enabled=True,
                       hitl_decision="approved",
                       sandbox_result=None):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService
        from zerberus.core.config import get_settings

        monkeypatch.setattr(LLMService, "call", _make_fake_llm(llm_answer))
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        settings = get_settings()
        monkeypatch.setattr(settings.projects, "hitl_enabled", True)
        monkeypatch.setattr(settings.projects, "hitl_timeout_seconds", 1)
        monkeypatch.setattr(settings.projects, "sandbox_writable", writable)
        monkeypatch.setattr(settings.projects, "snapshots_enabled", snapshots_enabled)

        fake_mgr = _make_fake_sandbox_manager(enabled=True)
        monkeypatch.setattr(
            "zerberus.modules.sandbox.manager.get_sandbox_manager",
            lambda: fake_mgr,
        )

        captured = {"writable_seen": None, "executed": False}

        async def fake_execute(*, project_id, code, language, base_dir,
                               writable=False, timeout=None):
            captured["writable_seen"] = writable
            captured["executed"] = True
            captured["project_id"] = project_id
            # Workspace mutieren waehrend "Code lief", damit der after-
            # Snapshot etwas zum diffen hat
            from zerberus.core.projects_workspace import workspace_root_for
            from zerberus.core.projects_repo import get_project
            proj = await get_project(project_id)
            if proj is not None:
                ws = workspace_root_for(proj["slug"], base_dir)
                ws.mkdir(parents=True, exist_ok=True)
                (ws / "generated.py").write_bytes(b"print('written by sandbox')\n")
            return sandbox_result

        monkeypatch.setattr(
            "zerberus.core.projects_workspace.execute_in_workspace",
            fake_execute,
        )

        from zerberus.core.hitl_chat import ChatHitlGate

        async def fake_wait(self, pending_id, timeout):
            p = self._pendings.get(pending_id)
            if p is not None:
                p.status = hitl_decision
            return hitl_decision

        monkeypatch.setattr(ChatHitlGate, "wait_for_decision", fake_wait)
        return captured

    def _create_project(self, **kwargs):
        from zerberus.core.projects_repo import create_project
        return asyncio.run(create_project(**kwargs))

    def _call_endpoint(self, project_id, session_id="s1"):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings
        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content="lege fuer mich an")]
        )
        request = _build_request(project_id, session_id=session_id)
        return asyncio.run(legacy_mod.chat_completions(
            request, req, get_settings(),
        ))

    def test_writable_true_triggers_snapshots_and_diff(self, env, monkeypatch):
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nopen('/workspace/generated.py','w').write('x')\n```",
            writable=True,
            sandbox_result=_make_sandbox_result(stdout=""),
        )
        proj = self._create_project(name="P-write")
        resp = self._call_endpoint(proj["id"])

        assert captured["executed"] is True
        assert captured["writable_seen"] is True
        # Diff/Snapshot-Felder sind da
        assert resp.code_execution is not None
        assert "diff" in resp.code_execution
        assert "before_snapshot_id" in resp.code_execution
        assert "after_snapshot_id" in resp.code_execution
        # Mindestens 1 Diff-Entry (added: generated.py)
        diffs = resp.code_execution["diff"]
        added_entries = [d for d in diffs if d["status"] == "added"]
        assert any(d["path"] == "generated.py" for d in added_entries)

    def test_writable_false_no_snapshot_pfad(self, env, monkeypatch):
        """RO-Default: keine Snapshot/Diff-Felder in der Response."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(1)\n```",
            writable=False,
            sandbox_result=_make_sandbox_result(stdout="1\n"),
        )
        proj = self._create_project(name="P-ro")
        resp = self._call_endpoint(proj["id"])

        assert captured["writable_seen"] is False
        assert resp.code_execution is not None
        # Keine Diff-Felder in der Response (P206-Verhalten)
        assert "diff" not in resp.code_execution
        assert "before_snapshot_id" not in resp.code_execution

    def test_snapshots_disabled_no_diff_even_when_writable(self, env, monkeypatch):
        """Master-Switch off: writable laeuft, aber kein Snapshot."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nopen('/workspace/x','w').write('1')\n```",
            writable=True,
            snapshots_enabled=False,
            sandbox_result=_make_sandbox_result(stdout=""),
        )
        proj = self._create_project(name="P-nosnap")
        resp = self._call_endpoint(proj["id"])

        assert captured["writable_seen"] is True
        assert resp.code_execution is not None
        assert "diff" not in resp.code_execution

    def test_rejected_no_snapshot(self, env, monkeypatch):
        """HitL rejected → kein Sandbox-Run → kein Snapshot."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nrm_rf()\n```",
            writable=True,
            hitl_decision="rejected",
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P-rejsnap")
        resp = self._call_endpoint(proj["id"])

        assert captured["executed"] is False  # gar nicht erst aufgerufen
        assert resp.code_execution is not None
        assert resp.code_execution["skipped"] is True
        assert "diff" not in resp.code_execution


# ---------------------------------------------------------------------------
# 8) JS-Integrity (analog P203b/P203d-3/P206)
# ---------------------------------------------------------------------------


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node nicht im PATH")
class TestJsSyntaxIntegrity:
    """Lesson aus P203b: ein einziger SyntaxError im inline <script> killt
    alle JS-Funktionen. P207 fuegt Diff-Renderer + Rollback-Funktion +
    colorizeUnifiedDiff hinzu — ``node --check`` muss ueber alle Bloecke
    laufen."""

    def test_alle_inline_scripts_parsen(self):
        from zerberus.app.routers.nala import NALA_HTML

        scripts = re.findall(
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
            NALA_HTML,
            re.DOTALL,
        )
        assert scripts
        with tempfile.TemporaryDirectory() as td:
            for i, body in enumerate(scripts):
                p = Path(td) / f"nala_p207_{i}.js"
                p.write_bytes(body.encode("utf-8", errors="surrogatepass"))
                proc = subprocess.run(
                    ["node", "--check", str(p)],
                    capture_output=True,
                    text=True,
                )
                assert proc.returncode == 0, (
                    f"Inline <script>-Block #{i} hat Syntax-Fehler:\n"
                    f"{proc.stderr}"
                )


# ---------------------------------------------------------------------------
# 9) Smoke
# ---------------------------------------------------------------------------


class TestSmoke:
    def test_config_has_snapshot_flags(self):
        from zerberus.core.config import get_settings
        s = get_settings()
        assert hasattr(s.projects, "sandbox_writable")
        assert hasattr(s.projects, "snapshots_enabled")
        assert isinstance(s.projects.sandbox_writable, bool)
        assert isinstance(s.projects.snapshots_enabled, bool)

    def test_database_has_workspace_snapshots_table(self):
        from zerberus.core.database import WorkspaceSnapshot
        cols = {c.name for c in WorkspaceSnapshot.__table__.columns}
        for needed in ("snapshot_id", "project_id", "project_slug", "label",
                       "archive_path", "file_count", "total_bytes",
                       "pending_id", "parent_snapshot_id", "created_at"):
            assert needed in cols, f"Spalte {needed} fehlt"

    def test_nala_endpoint_renders_diff_pieces(self):
        from zerberus.app.routers.nala import nala_interface

        body = asyncio.run(nala_interface())
        assert "function renderDiffCard" in body
        assert ".diff-card" in body
        assert "/v1/workspace/rollback" in body

    def test_legacy_module_exports_rollback_models(self):
        from zerberus.app.routers import legacy
        assert hasattr(legacy, "WorkspaceRollbackRequest")
        assert hasattr(legacy, "WorkspaceRollbackResponse")
        assert hasattr(legacy, "workspace_rollback")
