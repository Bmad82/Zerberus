"""Patch 203c — Tests fuer Sandbox-Workspace-Mount + execute_in_workspace.

Schichten:

1. **SandboxManager.execute / _run_in_container — docker-args-Audit.**
   Mockt ``asyncio.create_subprocess_exec`` und prueft die uebergebenen
   docker-Argumente. Kein Docker-Daemon noetig.
2. **Mount-Validation (Existence + Directory).** Nicht-existente und
   File-Mounts werden mit ``SandboxResult.error`` abgelehnt.
3. **execute_in_workspace — Pfad-Sicherheits-Check + DB-Lookup.**
   Mockt ``projects_repo.get_project`` und ``get_sandbox_manager`` und
   verifiziert, dass der korrekte Workspace-Pfad als Mount durchgereicht
   wird.
4. **Defense-in-Depth gegen Slug-Manipulation.** Wenn ein Slug
   ``../etc`` in die DB schluepft, lehnt ``execute_in_workspace`` die
   Ausfuehrung ab.
5. **Source-Audit.** Verdrahtungs-Stellen sind im Source vorhanden.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zerberus.core.config import SandboxConfig
from zerberus.modules.sandbox.manager import SandboxManager, SandboxResult


# ---------------------------------------------------------------------------
# Helpers — Mock-Factories analog test_sandbox.py
# ---------------------------------------------------------------------------


def _capture_docker_args(captured: list):
    """Liefert eine async-Factory fuer create_subprocess_exec, die die
    uebergebenen docker-Argumente in ``captured`` ablegt und einen Mock-
    Process mit exit_code=0 zurueckgibt."""

    async def fake_create(*args, **kwargs):
        captured.append(list(args))
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
        proc.returncode = 0
        return proc

    return fake_create


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1 — docker-args ohne workspace_mount (Backwards-Compat)
# ---------------------------------------------------------------------------


def test_01_no_mount_default_args_unchanged():
    """Ohne workspace_mount darf KEIN -v / --workdir in den Args sein.
    Schuetzt den Backwards-Compat-Pfad fuer den Huginn-Pipeline-Flow."""
    from zerberus.modules.sandbox import manager as mgr_mod

    cfg = SandboxConfig(enabled=True)
    mgr = SandboxManager(cfg)

    captured: list = []
    with patch.object(
        mgr_mod.asyncio, "create_subprocess_exec",
        side_effect=_capture_docker_args(captured),
    ):
        result = _run(mgr.execute("print(1)", "python"))

    assert result is not None
    assert result.exit_code == 0
    assert len(captured) == 1
    args = captured[0]
    assert "-v" not in args
    assert "--workdir" not in args
    # Sanity: bestehende Sicherheits-Flags noch da
    assert "--network" in args
    assert "--read-only" in args


# ---------------------------------------------------------------------------
# 2 — docker-args mit workspace_mount, read-only Default
# ---------------------------------------------------------------------------


def test_02_mount_default_readonly(tmp_path):
    from zerberus.modules.sandbox import manager as mgr_mod

    workspace = tmp_path / "ws"
    workspace.mkdir()

    cfg = SandboxConfig(enabled=True)
    mgr = SandboxManager(cfg)

    captured: list = []
    with patch.object(
        mgr_mod.asyncio, "create_subprocess_exec",
        side_effect=_capture_docker_args(captured),
    ):
        result = _run(mgr.execute(
            "print(1)", "python", workspace_mount=workspace,
        ))

    assert result is not None
    assert result.exit_code == 0
    args = captured[0]
    assert "-v" in args
    v_idx = args.index("-v")
    mount_spec = args[v_idx + 1]
    # absoluter Pfad + :/workspace + :ro
    assert mount_spec.endswith(":/workspace:ro")
    assert str(workspace.resolve()) in mount_spec
    assert "--workdir" in args
    wd_idx = args.index("--workdir")
    assert args[wd_idx + 1] == "/workspace"


# ---------------------------------------------------------------------------
# 3 — docker-args mit workspace_mount + writable
# ---------------------------------------------------------------------------


def test_03_mount_writable_no_ro_suffix(tmp_path):
    from zerberus.modules.sandbox import manager as mgr_mod

    workspace = tmp_path / "ws"
    workspace.mkdir()

    cfg = SandboxConfig(enabled=True)
    mgr = SandboxManager(cfg)

    captured: list = []
    with patch.object(
        mgr_mod.asyncio, "create_subprocess_exec",
        side_effect=_capture_docker_args(captured),
    ):
        _run(mgr.execute(
            "print(1)", "python",
            workspace_mount=workspace, mount_writable=True,
        ))

    args = captured[0]
    v_idx = args.index("-v")
    mount_spec = args[v_idx + 1]
    assert mount_spec.endswith(":/workspace")
    assert not mount_spec.endswith(":ro")


# ---------------------------------------------------------------------------
# 4 — Mount-Pfad existiert nicht → Fehler-Result
# ---------------------------------------------------------------------------


def test_04_mount_nonexistent_returns_error(tmp_path):
    cfg = SandboxConfig(enabled=True)
    mgr = SandboxManager(cfg)
    missing = tmp_path / "nope"
    result = _run(mgr.execute(
        "print(1)", "python", workspace_mount=missing,
    ))
    assert result is not None
    assert result.exit_code == -1
    assert "existiert nicht" in (result.error or "")


# ---------------------------------------------------------------------------
# 5 — Mount-Pfad ist eine Datei, kein Verzeichnis → Fehler-Result
# ---------------------------------------------------------------------------


def test_05_mount_is_file_returns_error(tmp_path):
    cfg = SandboxConfig(enabled=True)
    mgr = SandboxManager(cfg)
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("ich bin eine datei")
    result = _run(mgr.execute(
        "print(1)", "python", workspace_mount=file_path,
    ))
    assert result is not None
    assert result.exit_code == -1
    assert "Verzeichnis" in (result.error or "")


# ---------------------------------------------------------------------------
# 6 — Disabled-Sandbox: Mount wird ignoriert, None bleibt None
# ---------------------------------------------------------------------------


def test_06_disabled_sandbox_returns_none_even_with_mount(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = SandboxConfig(enabled=False)
    mgr = SandboxManager(cfg)
    result = _run(mgr.execute(
        "print(1)", "python", workspace_mount=workspace,
    ))
    assert result is None


# ---------------------------------------------------------------------------
# 7 — Blocked-Pattern hat Vorrang vor Mount-Validation
# ---------------------------------------------------------------------------


def test_07_blocked_pattern_short_circuits_before_mount(tmp_path):
    cfg = SandboxConfig(enabled=True)
    mgr = SandboxManager(cfg)
    # Mount existiert nicht, ABER der Code ist auch blocked. Da
    # Blockliste vor Mount-Validation kommt, sollte "Blocked pattern" im
    # error stehen.
    missing = tmp_path / "nope"
    result = _run(mgr.execute(
        "import os\nos.system('rm -rf /')", "python",
        workspace_mount=missing,
    ))
    assert result is not None
    assert "Blocked pattern" in (result.error or "")


# ---------------------------------------------------------------------------
# 8 — execute_in_workspace: Projekt nicht gefunden → None
# ---------------------------------------------------------------------------


def test_08_execute_in_workspace_unknown_project(tmp_path):
    from zerberus.core import projects_workspace as pw

    async def fake_get(project_id):
        return None

    with patch("zerberus.core.projects_repo.get_project", side_effect=fake_get):
        result = _run(pw.execute_in_workspace(
            project_id=42, code="print(1)", language="python", base_dir=tmp_path,
        ))
    assert result is None


# ---------------------------------------------------------------------------
# 9 — execute_in_workspace: korrekter Mount-Path wird durchgereicht
# ---------------------------------------------------------------------------


def test_09_execute_in_workspace_passes_correct_mount(tmp_path):
    from zerberus.core import projects_workspace as pw

    async def fake_get(project_id):
        return {"id": 1, "slug": "demo-projekt"}

    captured: dict = {}

    async def fake_execute(*, code, language, timeout, workspace_mount, mount_writable):
        captured["mount"] = workspace_mount
        captured["writable"] = mount_writable
        captured["code"] = code
        captured["lang"] = language
        return SandboxResult(stdout="ok", stderr="", exit_code=0, execution_time_ms=1)

    fake_mgr = MagicMock()
    fake_mgr.execute = AsyncMock(side_effect=fake_execute)

    with patch("zerberus.core.projects_repo.get_project", side_effect=fake_get), \
         patch("zerberus.modules.sandbox.manager.get_sandbox_manager", return_value=fake_mgr):
        result = _run(pw.execute_in_workspace(
            project_id=1, code="print(2)", language="python", base_dir=tmp_path,
        ))

    assert result is not None
    assert result.exit_code == 0
    expected_root = tmp_path / "projects" / "demo-projekt" / "_workspace"
    assert captured["mount"].resolve() == expected_root.resolve()
    assert captured["writable"] is False  # default RO
    assert captured["code"] == "print(2)"
    assert captured["lang"] == "python"


# ---------------------------------------------------------------------------
# 10 — execute_in_workspace: writable=True wird durchgereicht
# ---------------------------------------------------------------------------


def test_10_execute_in_workspace_writable_passthrough(tmp_path):
    from zerberus.core import projects_workspace as pw

    async def fake_get(project_id):
        return {"id": 1, "slug": "demo"}

    captured: dict = {}

    async def fake_execute(*, code, language, timeout, workspace_mount, mount_writable):
        captured["writable"] = mount_writable
        return SandboxResult(stdout="", stderr="", exit_code=0, execution_time_ms=1)

    fake_mgr = MagicMock()
    fake_mgr.execute = AsyncMock(side_effect=fake_execute)

    with patch("zerberus.core.projects_repo.get_project", side_effect=fake_get), \
         patch("zerberus.modules.sandbox.manager.get_sandbox_manager", return_value=fake_mgr):
        _run(pw.execute_in_workspace(
            project_id=1, code="x=1", language="python",
            base_dir=tmp_path, writable=True,
        ))

    assert captured["writable"] is True


# ---------------------------------------------------------------------------
# 11 — execute_in_workspace legt Workspace-Ordner an, wenn fehlt
# ---------------------------------------------------------------------------


def test_11_execute_in_workspace_creates_root_if_missing(tmp_path):
    from zerberus.core import projects_workspace as pw

    async def fake_get(project_id):
        return {"id": 7, "slug": "neu"}

    async def fake_execute(*, code, language, timeout, workspace_mount, mount_writable):
        # Beim Aufruf des SandboxManagers MUSS der Workspace existieren.
        assert workspace_mount.exists()
        assert workspace_mount.is_dir()
        return SandboxResult(stdout="", stderr="", exit_code=0, execution_time_ms=1)

    fake_mgr = MagicMock()
    fake_mgr.execute = AsyncMock(side_effect=fake_execute)

    expected = tmp_path / "projects" / "neu" / "_workspace"
    assert not expected.exists()

    with patch("zerberus.core.projects_repo.get_project", side_effect=fake_get), \
         patch("zerberus.modules.sandbox.manager.get_sandbox_manager", return_value=fake_mgr):
        _run(pw.execute_in_workspace(
            project_id=7, code="x=1", language="python", base_dir=tmp_path,
        ))

    assert expected.exists()
    assert expected.is_dir()


# ---------------------------------------------------------------------------
# 12 — Defense-in-Depth: Slug versucht aus base_dir auszubrechen
# ---------------------------------------------------------------------------


def test_12_slug_traversal_rejected(tmp_path):
    """Wenn ein manipulierter Slug es trotz Sanitizer in die DB
    geschafft haette (Migrations? Manuelle Eingriffe?), MUSS der
    workspace_root-inside-base_dir-Check die Ausfuehrung ablehnen.
    """
    from zerberus.core import projects_workspace as pw

    # Die Slug-Sanitizer von projects_repo blockt das normalerweise; der
    # Test simuliert einen Bypass und verifiziert die Defense-in-Depth.
    async def fake_get(project_id):
        return {"id": 1, "slug": "../../../../etc"}

    fake_mgr = MagicMock()
    fake_mgr.execute = AsyncMock(side_effect=AssertionError("sandbox darf gar nicht aufgerufen werden!"))

    with patch("zerberus.core.projects_repo.get_project", side_effect=fake_get), \
         patch("zerberus.modules.sandbox.manager.get_sandbox_manager", return_value=fake_mgr):
        result = _run(pw.execute_in_workspace(
            project_id=1, code="print(1)", language="python", base_dir=tmp_path,
        ))
    assert result is None


# ---------------------------------------------------------------------------
# 13 — Source-Audit: Mount-Stelle in _run_in_container vorhanden
# ---------------------------------------------------------------------------


def test_13_source_audit_mount_block_in_manager():
    src = inspect.getsource(SandboxManager._run_in_container)
    # Mount-Block-Marker
    assert "workspace_mount" in src
    assert "/workspace" in src
    assert ":ro" in src
    assert "--workdir" in src
    # Logging-Tag
    assert "[SANDBOX-203c]" in src


# ---------------------------------------------------------------------------
# 14 — Source-Audit: execute_in_workspace ist async und nutzt is_inside_workspace
# ---------------------------------------------------------------------------


def test_14_source_audit_execute_in_workspace():
    from zerberus.core import projects_workspace as pw

    assert inspect.iscoroutinefunction(pw.execute_in_workspace)
    src = inspect.getsource(pw.execute_in_workspace)
    assert "is_inside_workspace" in src
    assert "workspace_root_for" in src
    assert "get_sandbox_manager" in src
    assert "[WORKSPACE-203c]" in src


# ---------------------------------------------------------------------------
# 15 — execute_in_workspace: Sandbox liefert None (disabled) → durchgereicht
# ---------------------------------------------------------------------------


def test_15_execute_in_workspace_sandbox_disabled_returns_none(tmp_path):
    from zerberus.core import projects_workspace as pw

    async def fake_get(project_id):
        return {"id": 1, "slug": "demo"}

    async def fake_execute(*, code, language, timeout, workspace_mount, mount_writable):
        return None  # sandbox disabled

    fake_mgr = MagicMock()
    fake_mgr.execute = AsyncMock(side_effect=fake_execute)

    with patch("zerberus.core.projects_repo.get_project", side_effect=fake_get), \
         patch("zerberus.modules.sandbox.manager.get_sandbox_manager", return_value=fake_mgr):
        result = _run(pw.execute_in_workspace(
            project_id=1, code="x=1", language="python", base_dir=tmp_path,
        ))
    assert result is None


# ---------------------------------------------------------------------------
# 16 — execute_in_workspace: timeout wird durchgereicht
# ---------------------------------------------------------------------------


def test_16_execute_in_workspace_timeout_passthrough(tmp_path):
    from zerberus.core import projects_workspace as pw

    async def fake_get(project_id):
        return {"id": 1, "slug": "demo"}

    captured: dict = {}

    async def fake_execute(*, code, language, timeout, workspace_mount, mount_writable):
        captured["timeout"] = timeout
        return SandboxResult(stdout="", stderr="", exit_code=0, execution_time_ms=1)

    fake_mgr = MagicMock()
    fake_mgr.execute = AsyncMock(side_effect=fake_execute)

    with patch("zerberus.core.projects_repo.get_project", side_effect=fake_get), \
         patch("zerberus.modules.sandbox.manager.get_sandbox_manager", return_value=fake_mgr):
        _run(pw.execute_in_workspace(
            project_id=1, code="x=1", language="python",
            base_dir=tmp_path, timeout=7,
        ))
    assert captured["timeout"] == 7


# ---------------------------------------------------------------------------
# 17 — Mount-Pfad ist absolut UND identisch zur resolve()-Form
# ---------------------------------------------------------------------------


def test_17_mount_path_is_absolute_resolved(tmp_path):
    """Der Mount-Spec MUSS auf den resolve()-Pfad gesetzt werden, nicht
    den Roh-Pfad — Symlinks / relative Pfade duerfen Docker nicht
    verwirren. Gilt insbesondere fuer Windows-Pfade mit kurzen 8.3-Namen.
    """
    from zerberus.modules.sandbox import manager as mgr_mod

    workspace = tmp_path / "rel_ws"
    workspace.mkdir()

    cfg = SandboxConfig(enabled=True)
    mgr = SandboxManager(cfg)

    captured: list = []
    with patch.object(
        mgr_mod.asyncio, "create_subprocess_exec",
        side_effect=_capture_docker_args(captured),
    ):
        _run(mgr.execute(
            "print(1)", "python", workspace_mount=workspace,
        ))

    args = captured[0]
    v_idx = args.index("-v")
    mount_spec = args[v_idx + 1]
    host_part = mount_spec.split(":/workspace")[0]
    # host_part muss absolut sein
    assert Path(host_part).is_absolute()
    # und resolve()-stable
    assert host_part == str(workspace.resolve(strict=False))
