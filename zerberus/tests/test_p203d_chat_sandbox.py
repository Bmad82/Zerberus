"""Patch 203d-1 (Phase 5a #5) — Tests fuer Code-Detection + Sandbox-
Roundtrip im ``/v1/chat/completions``-Endpoint.

Schichten:

1. **End-to-End ueber ``chat_completions``.** Mockt LLM-Antwort, Sandbox-
   Manager und ``execute_in_workspace``. Verifiziert dass das additive
   ``code_execution``-Feld korrekt populated wird (oder None bleibt).
2. **Source-Audit.** Logging-Tag, Imports, Schema-Feld in legacy.py.
3. **Backwards-Compat.** Ohne Header / ohne Sandbox / ohne Code-Block
   bleibt das Response-Schema unveraendert.

P203d-1 macht KEINEN zweiten LLM-Call (das ist P203d-2) und KEIN UI-
Render (P203d-3). Reicht das raw ``SandboxResult`` durch.
"""
from __future__ import annotations

import asyncio
import inspect
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures — tmp_db + Persona-Files (analog test_persona_merge.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch):
    """Frische SQLite-DB pro Test, monkeypatcht das engine-Singleton."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_p203d.db"
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
    """Settings-Cache + chdir + Persona-Files (analog
    TestE2EChatCompletionsWithProjectOverlay)."""
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


def _build_request(project_id: int | None, profile_name: str = "alice"):
    """Mock-Request: state aus JWT-Middleware + Header-Dict."""
    state = SimpleNamespace(
        profile_name=profile_name,
        permission_level="admin",
        allowed_model=None,
        temperature=None,
    )
    headers: dict[str, str] = {"X-Session-ID": "test-session"}
    if project_id is not None:
        headers["X-Active-Project-Id"] = str(project_id)
    return SimpleNamespace(state=state, headers=headers)


def _make_fake_llm(answer: str):
    """Liefert eine Fake-LLMService.call-Coroutine, die ``answer`` zurueckgibt."""
    async def fake_call(self, messages, session_id,
                        model_override=None, temperature_override=None):
        return (answer, "test-model", 1, 1, 0.0)
    return fake_call


def _make_fake_sandbox_manager(*, enabled: bool = True,
                                allowed_languages=("python", "javascript")):
    """SandboxManager-Mock mit minimaler Config-Schicht.

    Das eigentliche ``execute()`` wird nicht ueber den Manager-Pfad
    aufgerufen, weil ``execute_in_workspace`` separat gemockt wird —
    wir brauchen nur ``config.enabled`` und ``config.allowed_languages``
    fuer das Gate in legacy.py.
    """
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


# ---------------------------------------------------------------------------
# Source-Audit — Verdrahtung in legacy.py
# ---------------------------------------------------------------------------


class TestP203d1SourceAudit:
    """Stellt sicher, dass die Verdrahtung bei zukuenftigen Refactors
    nicht stillschweigend rausfaellt."""

    def _legacy_src(self):
        return (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(
            encoding="utf-8"
        )

    def test_logging_tag_present(self):
        """Logging-Tag ``[SANDBOX-203d]`` muss in legacy.py vorkommen."""
        assert "[SANDBOX-203d]" in self._legacy_src()

    def test_first_executable_block_imported(self):
        """``first_executable_block`` aus ``code_extractor`` ist verdrahtet."""
        assert "first_executable_block" in self._legacy_src()

    def test_execute_in_workspace_imported(self):
        """``execute_in_workspace`` aus ``projects_workspace`` ist verdrahtet."""
        assert "execute_in_workspace" in self._legacy_src()

    def test_get_sandbox_manager_used(self):
        """``get_sandbox_manager`` wird zum Gate auf ``config.enabled`` genutzt."""
        assert "get_sandbox_manager" in self._legacy_src()

    def test_response_schema_has_code_execution_field(self):
        """``ChatCompletionResponse`` deklariert das additive Feld."""
        from zerberus.app.routers.legacy import ChatCompletionResponse
        # Pydantic v2 model_fields, v1 __fields__ — beides toleriert
        fields = getattr(ChatCompletionResponse, "model_fields", None) or \
                 getattr(ChatCompletionResponse, "__fields__", {})
        assert "code_execution" in fields

    def test_writable_false_default_in_call_site(self):
        """Defense: ``writable`` wird im Endpoint NICHT hardcoded, sondern
        aus ``settings.projects.sandbox_writable`` gelesen (Default False
        ueber den Pydantic-Defaultwert in ``ProjectsConfig``). Bis P206
        war ``writable=False`` hardcoded; P207 hat das auf ein Setting
        umgestellt — wir pruefen jetzt, dass der Lookup korrekt im
        Source steht und der Default-Wert weiterhin False ist."""
        src = self._legacy_src()
        # Suchfenster: rund um den Sandbox-Block
        idx = src.find("[SANDBOX-203d]")
        assert idx > 0
        window = src[max(0, idx - 2500):idx + 2500]
        # P207-Konvention: writable kommt aus dem Settings-Flag.
        assert 'getattr(settings.projects, "sandbox_writable", False)' in window
        # Default in ProjectsConfig MUSS False sein — sonst rutscht ein
        # "kurz mal writable=True"-Hack durchs Default-Verhalten.
        from zerberus.core.config import ProjectsConfig
        assert ProjectsConfig().sandbox_writable is False

    def test_code_execution_field_passed_to_response(self):
        """Source-Audit: das Feld wird dem Response-Konstruktor durchgereicht."""
        src = self._legacy_src()
        assert "code_execution=code_execution_payload" in src


# ---------------------------------------------------------------------------
# End-to-End — chat_completions mit aktivem Projekt + Code-Block
# ---------------------------------------------------------------------------


class TestE2ECodeExecution:

    def _setup_common(self, monkeypatch, *, llm_answer: str,
                      sandbox_enabled: bool = True,
                      allowed_languages=("python", "javascript"),
                      sandbox_result=None,
                      execute_raises: Exception | None = None):
        """Patcht alle externen Abhaengigkeiten in einem Aufruf."""
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService
        from zerberus.core.config import get_settings

        # Patch 206: HitL-Gate ist neuer Default. P203d-1-Tests pruefen
        # nur Code-Detection + Sandbox-Roundtrip — wir bypassen das Gate,
        # damit der Sandbox-Pfad direkt durchlaeuft (Status ``bypassed``).
        monkeypatch.setattr(get_settings().projects, "hitl_enabled", False)

        monkeypatch.setattr(LLMService, "call", _make_fake_llm(llm_answer))
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        fake_mgr = _make_fake_sandbox_manager(
            enabled=sandbox_enabled,
            allowed_languages=allowed_languages,
        )
        monkeypatch.setattr(
            "zerberus.modules.sandbox.manager.get_sandbox_manager",
            lambda: fake_mgr,
        )

        captured: dict = {"calls": 0}

        async def fake_execute(*, project_id, code, language, base_dir,
                               writable=False, timeout=None):
            captured["calls"] += 1
            captured["project_id"] = project_id
            captured["code"] = code
            captured["language"] = language
            captured["base_dir"] = base_dir
            captured["writable"] = writable
            captured["timeout"] = timeout
            if execute_raises is not None:
                raise execute_raises
            return sandbox_result

        monkeypatch.setattr(
            "zerberus.core.projects_workspace.execute_in_workspace",
            fake_execute,
        )
        return captured

    def _create_project(self, **kwargs):
        from zerberus.core.projects_repo import create_project
        return asyncio.run(create_project(**kwargs))

    def _archive_project(self, project_id: int):
        from zerberus.core.projects_repo import archive_project
        return asyncio.run(archive_project(project_id))

    def _call_endpoint(self, project_id: int | None):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings

        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content="Berechne 1+1")]
        )
        request = _build_request(project_id, profile_name="alice")
        return asyncio.run(legacy_mod.chat_completions(
            request, req, get_settings()
        ))

    # ---- happy path ---------------------------------------------------

    def test_python_block_executed_and_payload_returned(self, env, monkeypatch):
        """LLM antwortet mit Python-Block, Projekt aktiv → code_execution
        ist populated und enthaelt die SandboxResult-Felder."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="Hier:\n```python\nprint(2)\n```\nFertig.",
            sandbox_result=_make_sandbox_result(stdout="2\n", exit_code=0),
        )
        proj = self._create_project(name="P1")
        resp = self._call_endpoint(proj["id"])

        assert resp.code_execution is not None
        assert resp.code_execution["language"] == "python"
        assert resp.code_execution["code"] == "print(2)"
        assert resp.code_execution["exit_code"] == 0
        assert resp.code_execution["stdout"] == "2\n"
        assert resp.code_execution["stderr"] == ""
        assert resp.code_execution["execution_time_ms"] == 42
        assert resp.code_execution["truncated"] is False
        assert resp.code_execution["error"] is None
        # Caller hat genau einen Sandbox-Aufruf abgesetzt, mit RO-Default
        assert captured["calls"] == 1
        assert captured["project_id"] == proj["id"]
        assert captured["language"] == "python"
        assert captured["writable"] is False

    def test_javascript_block_executed(self, env, monkeypatch):
        """JavaScript-Code wird genauso erkannt + ausgefuehrt wie Python."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="Try this:\n```javascript\nconsole.log('hi')\n```",
            sandbox_result=_make_sandbox_result(stdout="hi\n", exit_code=0),
        )
        proj = self._create_project(name="P2")
        resp = self._call_endpoint(proj["id"])

        assert resp.code_execution is not None
        assert resp.code_execution["language"] == "javascript"
        assert resp.code_execution["code"] == "console.log('hi')"
        assert captured["language"] == "javascript"

    def test_nonzero_exit_code_returned_in_payload(self, env, monkeypatch):
        """exit_code != 0: Payload bleibt populated, kein Crash, stderr durch."""
        self._setup_common(
            monkeypatch,
            llm_answer="```python\nimport sys; sys.exit(7)\n```",
            sandbox_result=_make_sandbox_result(
                stdout="", stderr="SystemExit: 7", exit_code=7,
            ),
        )
        proj = self._create_project(name="P3")
        resp = self._call_endpoint(proj["id"])

        assert resp.code_execution is not None
        assert resp.code_execution["exit_code"] == 7
        assert "SystemExit" in resp.code_execution["stderr"]

    # ---- skip cases (code_execution must be None) ---------------------

    def test_no_active_project_skips_sandbox(self, env, monkeypatch):
        """Ohne X-Active-Project-Id-Header: kein Sandbox-Call, Feld None."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(1)\n```",
            sandbox_result=_make_sandbox_result(stdout="1\n"),
        )
        # KEIN Projekt anlegen, kein Header → Endpoint laeuft trotzdem
        resp = self._call_endpoint(None)
        assert resp.code_execution is None
        assert captured["calls"] == 0

    def test_no_code_block_in_answer_skips_sandbox(self, env, monkeypatch):
        """Plain-Text-Antwort ohne Fence: kein Sandbox-Call."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="Das Ergebnis ist 2.",
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P4")
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is None
        assert captured["calls"] == 0

    def test_disabled_sandbox_skips_call(self, env, monkeypatch):
        """Sandbox-Config disabled: gar kein execute_in_workspace-Call."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(1)\n```",
            sandbox_enabled=False,
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P5")
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is None
        assert captured["calls"] == 0

    def test_archived_project_skips_sandbox(self, env, monkeypatch):
        """Archiviertes Projekt → Slug ist None aus persona_merge.resolve_*
        → Code-Detection-Gate blockt den Call."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(1)\n```",
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P6")
        self._archive_project(proj["id"])
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is None
        assert captured["calls"] == 0

    def test_unknown_language_block_skips_sandbox(self, env, monkeypatch):
        """Ein ```bash``` oder ```rust``` Block ist nicht in
        allowed_languages → first_executable_block returnt None."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```rust\nfn main() { println!(\"x\"); }\n```",
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P7")
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is None
        assert captured["calls"] == 0

    def test_execute_in_workspace_returns_none_keeps_payload_none(
        self, env, monkeypatch,
    ):
        """``execute_in_workspace`` → None (Slug-Reject oder Disabled
        downstream): kein Crash, code_execution bleibt None."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(1)\n```",
            sandbox_result=None,  # explizit None
        )
        proj = self._create_project(name="P8")
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is None
        # Aber: der Call wurde schon abgesetzt, also calls > 0
        assert captured["calls"] == 1

    def test_execute_in_workspace_raises_fail_open(self, env, monkeypatch):
        """Wenn die Sandbox-Pipeline crasht: Endpoint laeuft normal weiter,
        code_execution=None, kein 500-Status."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(1)\n```",
            sandbox_result=None,
            execute_raises=RuntimeError("docker daemon kaputt"),
        )
        proj = self._create_project(name="P9")
        # Kein Crash erwartet — fail-open
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is None
        # Choice-Pfad bleibt normal — die Antwort kommt unverstuemmelt durch
        assert resp.choices
        assert resp.choices[0].message.role == "assistant"
        assert captured["calls"] == 1

    # ---- backwards-compat ---------------------------------------------

    def test_response_remains_openai_compatible_without_code(
        self, env, monkeypatch,
    ):
        """Plain-Text-Response: choices-Liste + sentiment bleiben unangetastet,
        code_execution ist None — d.h. OpenAI-SDK-Clients sehen keinen
        Bruch."""
        self._setup_common(
            monkeypatch,
            llm_answer="Hallo, wie kann ich helfen?",
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P10")
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is None
        # OpenAI-Schema-Felder sind alle da
        assert resp.choices
        assert resp.choices[0].message.role == "assistant"
        assert resp.choices[0].finish_reason == "stop"
        assert resp.model == "test-model"

    def test_first_block_wins_when_multiple_blocks(self, env, monkeypatch):
        """LLM antwortet mit zwei Code-Bloecken: nur der erste wird
        ausgefuehrt (Pure-Function-Garantie aus first_executable_block)."""
        captured = self._setup_common(
            monkeypatch,
            llm_answer=(
                "Variante A:\n```python\nprint('A')\n```\n"
                "Variante B:\n```python\nprint('B')\n```"
            ),
            sandbox_result=_make_sandbox_result(stdout="A\n", exit_code=0),
        )
        proj = self._create_project(name="P11")
        resp = self._call_endpoint(proj["id"])
        assert resp.code_execution is not None
        assert resp.code_execution["code"] == "print('A')"
        assert captured["calls"] == 1
