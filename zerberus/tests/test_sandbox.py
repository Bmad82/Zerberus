"""Patch 171 — Tests fuer Docker-Sandbox (Phase D, Block 1+3+4).

Pure-Python Unit-Tests (15 Faelle) + 3 Docker-Integration-Tests, die nur
laufen wenn Docker erreichbar ist (``@pytest.mark.docker``).

Deckung:
1.  SandboxResult: Dataclass-Felder korrekt
2.  Code-Extraktion: Python-Block aus Markdown extrahiert
3.  Code-Extraktion: JavaScript-Block extrahiert
4.  Code-Extraktion: Kein Code-Block + Fallback-Lang → 1 Block
5.  Code-Extraktion: Mehrere Blocks → alle extrahiert
6.  Blockliste: ``import os`` wird erkannt → blockiert
7.  Blockliste: ``import subprocess`` wird erkannt → blockiert
8.  Blockliste: ``import math`` wird NICHT blockiert (harmlos)
9.  Blockliste: ``eval()`` wird erkannt → blockiert
10. Config-Defaults: SandboxConfig hat sinnvolle Defaults
11. Config: ``enabled: false`` → execute() gibt sofort None zurueck
12. Config: Unbekannte Sprache → blockiert
13. Output-Truncation: Output > max_output_chars → truncated=True
14. Output-Format: Execution-Result-Text korrekt formatiert
15. Timeout-Result: exit_code=-1, error enthaelt "Timeout"

Docker-Integration (mark=docker, skip wenn kein Docker):
16. Live-Execute: ``print("hello")`` → stdout="hello\\n", exit_code=0
17. Live-Timeout: ``time.sleep(60)`` → Timeout
18. Live-Network: urllib.request → Fehler (--network none)
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from zerberus.core.config import SandboxConfig
from zerberus.modules.sandbox.manager import (
    BLOCKED_PATTERNS_PYTHON,
    SandboxManager,
    SandboxResult,
    find_blocked_pattern,
)
from zerberus.utils.code_extractor import (
    CodeBlock,
    extract_code_blocks,
    first_executable_block,
)


_DOCKER_AVAILABLE = False
try:
    if shutil.which("docker"):
        _r = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, timeout=3,
        )
        _DOCKER_AVAILABLE = _r.returncode == 0
except Exception:
    _DOCKER_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────
# 1 — SandboxResult dataclass
# ──────────────────────────────────────────────────────────────────────


def test_01_sandbox_result_felder():
    r = SandboxResult(
        stdout="ok\n", stderr="", exit_code=0, execution_time_ms=42,
    )
    assert r.stdout == "ok\n"
    assert r.exit_code == 0
    assert r.execution_time_ms == 42
    assert r.truncated is False
    assert r.error is None


# ──────────────────────────────────────────────────────────────────────
# 2-5 — Code-Extraktion
# ──────────────────────────────────────────────────────────────────────


def test_02_extract_python_block():
    text = "Hier dein Code:\n```python\nprint('x')\n```\nFertig."
    blocks = extract_code_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].language == "python"
    assert blocks[0].code == "print('x')"


def test_03_extract_javascript_block():
    text = "```js\nconsole.log(1)\n```"
    blocks = extract_code_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].language == "javascript"
    assert blocks[0].code == "console.log(1)"


def test_04_no_block_with_fallback():
    blocks = extract_code_blocks("nur text", fallback_language="python")
    assert len(blocks) == 1
    assert blocks[0].language == "python"
    assert blocks[0].code == "nur text"


def test_04b_no_block_without_fallback():
    blocks = extract_code_blocks("nur text")
    assert blocks == []


def test_05_multiple_blocks():
    text = "```python\na=1\n```\ntext\n```js\nb=2\n```"
    blocks = extract_code_blocks(text)
    assert len(blocks) == 2
    assert blocks[0].language == "python"
    assert blocks[1].language == "javascript"
    assert blocks[0].start_pos < blocks[1].start_pos


def test_05b_first_executable_filters_unknown():
    text = "```ruby\nputs 1\n```\n```python\nprint(1)\n```"
    block = first_executable_block(text, ["python", "javascript"])
    assert block is not None
    assert block.language == "python"


# ──────────────────────────────────────────────────────────────────────
# 6-9 — Blockliste
# ──────────────────────────────────────────────────────────────────────


def test_06_blocklist_import_os():
    assert find_blocked_pattern("import os\nprint(1)", "python") is not None


def test_07_blocklist_import_subprocess():
    assert find_blocked_pattern("import subprocess", "python") is not None


def test_08_blocklist_import_math_ok():
    assert find_blocked_pattern("import math\nprint(math.pi)", "python") is None


def test_09_blocklist_eval():
    assert find_blocked_pattern("x = eval('2+2')", "python") is not None


def test_09b_blocklist_unknown_language_no_block():
    # Ruby ist nicht in der Blockliste — keine Patterns, kein Treffer.
    assert find_blocked_pattern("system('rm -rf /')", "ruby") is None


def test_09c_blocklist_python_patterns_compile():
    """Sanity: alle Patterns sind valides Regex."""
    import re
    for pat in BLOCKED_PATTERNS_PYTHON:
        re.compile(pat)


# ──────────────────────────────────────────────────────────────────────
# 10 — Config-Defaults
# ──────────────────────────────────────────────────────────────────────


def test_10_config_defaults():
    cfg = SandboxConfig()
    assert cfg.enabled is False  # Default AUS!
    assert cfg.timeout_seconds == 30
    assert cfg.max_output_chars == 10000
    assert cfg.memory_limit == "256m"
    assert cfg.cpu_limit == 0.5
    assert "python" in cfg.allowed_languages
    assert "javascript" in cfg.allowed_languages


# ──────────────────────────────────────────────────────────────────────
# 11 — disabled → None
# ──────────────────────────────────────────────────────────────────────


def test_11_disabled_returns_none():
    mgr = SandboxManager(SandboxConfig(enabled=False))
    result = asyncio.run(mgr.execute("print(1)", "python"))
    assert result is None


# ──────────────────────────────────────────────────────────────────────
# 12 — unerlaubte Sprache
# ──────────────────────────────────────────────────────────────────────


def test_12_unallowed_language():
    mgr = SandboxManager(SandboxConfig(enabled=True, allowed_languages=["python"]))
    result = asyncio.run(mgr.execute("puts 1", "ruby"))
    assert result is not None
    assert result.exit_code == -1
    assert "nicht erlaubt" in (result.error or "")


# ──────────────────────────────────────────────────────────────────────
# 13 — Output-Truncation
# ──────────────────────────────────────────────────────────────────────


def test_13_output_truncation():
    """Mockt _run_in_container, simuliert Riesen-Output."""
    from zerberus.modules.sandbox import manager as mgr_mod

    cfg = SandboxConfig(enabled=True, max_output_chars=50)
    mgr = SandboxManager(cfg)

    big_stdout = "x" * 200
    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(big_stdout.encode(), b""))
    fake_proc.returncode = 0

    async def fake_create(*args, **kwargs):
        return fake_proc

    with patch.object(mgr_mod.asyncio, "create_subprocess_exec", side_effect=fake_create):
        result = asyncio.run(mgr.execute("print('x'*200)", "python"))

    assert result is not None
    assert result.truncated is True
    assert len(result.stdout) <= 50 + len("\n…[truncated]")


# ──────────────────────────────────────────────────────────────────────
# 14 — Output-Format (format_sandbox_result)
# ──────────────────────────────────────────────────────────────────────


def test_14_format_sandbox_result_success():
    from zerberus.modules.telegram.router import format_sandbox_result

    r = SandboxResult(stdout="hello\n", stderr="", exit_code=0, execution_time_ms=42)
    text = format_sandbox_result(r, "huginn_code.py", "python")
    assert "Ausgeführt in 42ms" in text
    assert "hello" in text
    assert "stdout" in text


def test_14b_format_sandbox_result_with_error():
    from zerberus.modules.telegram.router import format_sandbox_result

    r = SandboxResult(
        stdout="", stderr="Boom", exit_code=1, execution_time_ms=10,
    )
    text = format_sandbox_result(r, "huginn_code.py", "python")
    assert "Exit Code 1" in text
    assert "stderr" in text
    assert "Boom" in text


def test_14c_format_sandbox_result_error_field():
    from zerberus.modules.telegram.router import format_sandbox_result

    r = SandboxResult(
        stdout="", stderr="", exit_code=-1, execution_time_ms=0,
        error="Timeout nach 30s",
    )
    text = format_sandbox_result(r, "huginn_code.py", "python")
    assert text.startswith("⚠️")
    assert "Timeout" in text


# ──────────────────────────────────────────────────────────────────────
# 15 — Timeout-Result
# ──────────────────────────────────────────────────────────────────────


def test_15_timeout_result_shape():
    """Mockt asyncio.wait_for so, dass es TimeoutError wirft."""
    from zerberus.modules.sandbox import manager as mgr_mod

    cfg = SandboxConfig(enabled=True, timeout_seconds=1)
    mgr = SandboxManager(cfg)

    fake_proc = AsyncMock()
    fake_proc.wait = AsyncMock(return_value=None)

    async def fake_create(*args, **kwargs):
        return fake_proc

    real_wait_for = asyncio.wait_for
    call_state = {"first_call": True}

    async def fake_wait_for(coro, timeout):
        # Ersten Aufruf (proc.communicate) — Timeout.
        # Nachfolgende Aufrufe (proc.wait nach Cleanup) — durchlassen.
        if call_state["first_call"]:
            call_state["first_call"] = False
            try:
                coro.close()
            except Exception:
                pass
            raise asyncio.TimeoutError()
        return await real_wait_for(coro, timeout)

    rm_proc = AsyncMock()
    rm_proc.wait = AsyncMock(return_value=None)

    async def fake_create_for_rm(*args, **kwargs):
        # _force_remove_container ruft auch create_subprocess_exec auf —
        # gleiche Mock-Factory.
        return rm_proc

    # Beide Aufrufe gehen durch denselben Mock (Run + RM).
    with patch.object(mgr_mod.asyncio, "create_subprocess_exec", side_effect=fake_create_for_rm), \
         patch.object(mgr_mod.asyncio, "wait_for", side_effect=fake_wait_for):
        result = asyncio.run(mgr.execute("import time; time.sleep(60)", "python"))

    assert result is not None
    assert result.exit_code == -1
    assert "Timeout" in (result.error or "")


# ──────────────────────────────────────────────────────────────────────
# 16-18 — Docker-Integration (skip if no Docker)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.docker
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker nicht erreichbar")
def test_16_live_execute_hello():
    image = "python:3.12-slim"
    if subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True
    ).returncode != 0:
        pytest.skip(f"{image} nicht gepullt")

    cfg = SandboxConfig(enabled=True, timeout_seconds=15, python_image=image)
    mgr = SandboxManager(cfg)
    result = asyncio.run(mgr.execute("print('hello')", "python"))
    assert result is not None
    assert result.exit_code == 0
    assert "hello" in result.stdout


@pytest.mark.docker
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker nicht erreichbar")
def test_17_live_timeout():
    image = "python:3.12-slim"
    if subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True
    ).returncode != 0:
        pytest.skip(f"{image} nicht gepullt")

    cfg = SandboxConfig(enabled=True, timeout_seconds=2, python_image=image)
    mgr = SandboxManager(cfg)
    # ``time.sleep`` ist erlaubt (kein os/subprocess/socket).
    result = asyncio.run(mgr.execute("import time\ntime.sleep(60)", "python"))
    assert result is not None
    assert result.exit_code == -1
    assert "Timeout" in (result.error or "")


@pytest.mark.docker
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker nicht erreichbar")
def test_18_live_no_network():
    image = "python:3.12-slim"
    if subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True
    ).returncode != 0:
        pytest.skip(f"{image} nicht gepullt")

    cfg = SandboxConfig(enabled=True, timeout_seconds=10, python_image=image)
    mgr = SandboxManager(cfg)
    # ``socket`` ist auf der Blockliste — wir muessen das umgehen, um
    # NETWORK ohne BLOCKLIST zu testen. Daher ein Pattern, das die
    # Blockliste nicht trifft, aber dennoch Netzwerk braucht.
    code = (
        "import urllib.request as u\n"
        "try:\n"
        "    u.urlopen('http://1.1.1.1', timeout=3)\n"
        "    print('OK')\n"
        "except Exception as e:\n"
        "    print('FAIL:', type(e).__name__)\n"
    )
    result = asyncio.run(mgr.execute(code, "python"))
    assert result is not None
    assert "FAIL" in result.stdout or result.exit_code != 0
