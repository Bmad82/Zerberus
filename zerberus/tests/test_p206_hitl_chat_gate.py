"""Patch 206 (Phase 5a #6) — Tests fuer das HitL-Gate vor Sandbox-
Code-Execution im ``/v1/chat/completions``-Pfad.

Schichten:

1. **ChatHitlGate Unit** — Pure-async Pending-/Resolve-/Wait-Mechanik
   ohne DB. asyncio.Event-Verhalten + Cross-Session-Block.
2. **Audit-Trail** — ``store_code_execution_audit`` schreibt in
   ``code_executions``, truncated bei langen Texten, silent skip ohne DB.
3. **Endpoints** — ``GET /v1/hitl/poll`` + ``POST /v1/hitl/resolve``
   ueber direkte Funktion-Aufrufe (TestClient overhead vermieden).
4. **Source-Audit legacy.py** — Logging-Tag, Imports, Synthese-Skip-Gate.
5. **Source-Audit nala.py** — JS-Funktionen, sendMessage-Verdrahtung,
   CSS-Klassen, 44x44 Touch-Target, escapeHtml-Usage, Skipped-Renderer.
6. **End-to-End** — chat_completions mit gemocktem ``wait_for_decision``:
   approved/rejected/timeout/bypassed-Pfade verifiziert.
7. **JS-Integrity** — ``node --check`` ueber alle inline <script>-Bloecke
   (analog P203b/P203d-3, skipped wenn node fehlt).

Was die Tests NICHT pruefen (Kollaterale aus anderen Patches):
  - dass die Sandbox tatsaechlich Code ausfuehrt (P203c)
  - die Synthese-Antwort selbst (P203d-2)
  - die Render-Optik der Code/Output-Card (P203d-3)
"""
from __future__ import annotations

import asyncio
import inspect
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_gate():
    """Frischer Gate-Singleton pro Test."""
    from zerberus.core.hitl_chat import reset_chat_hitl_gate
    reset_chat_hitl_gate()
    yield
    reset_chat_hitl_gate()


@pytest.fixture
def tmp_db(monkeypatch):
    """Frische SQLite-DB pro Test, monkeypatcht das engine-Singleton."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_p206.db"
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
    """Settings-Cache + chdir + Persona-Files."""
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


def _build_request(project_id: int | None, session_id: str = "s-test",
                    profile_name: str = "alice"):
    state = SimpleNamespace(
        profile_name=profile_name,
        permission_level="admin",
        allowed_model=None,
        temperature=None,
    )
    headers: dict[str, str] = {"X-Session-ID": session_id}
    if project_id is not None:
        headers["X-Active-Project-Id"] = str(project_id)
    return SimpleNamespace(state=state, headers=headers)


def _make_fake_llm(answer: str):
    async def fake_call(self, messages, session_id,
                        model_override=None, temperature_override=None):
        return (answer, "test-model", 1, 1, 0.0)
    return fake_call


def _make_fake_sandbox_manager(*, enabled: bool = True,
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


# ---------------------------------------------------------------------------
# 1) ChatHitlGate Unit
# ---------------------------------------------------------------------------


class TestChatHitlGate:
    def _make_gate(self):
        from zerberus.core.hitl_chat import ChatHitlGate
        return ChatHitlGate()

    def test_create_pending_returns_uuid_and_pending_status(self):
        gate = self._make_gate()
        pending = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="print(1)", language="python",
        ))
        assert pending.id and len(pending.id) == 32  # UUID4 hex
        assert pending.status == "pending"
        assert pending.session_id == "s1"
        assert pending.project_id == 1
        assert pending.code == "print(1)"
        assert pending.language == "python"

    def test_to_public_dict_keys(self):
        gate = self._make_gate()
        pending = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="x = 1", language="python",
        ))
        d = pending.to_public_dict()
        assert set(d.keys()) == {
            "id", "session_id", "project_id", "project_slug",
            "code", "language", "created_at",
        }
        # Status absichtlich NICHT im public dict — implizit "pending"
        assert "status" not in d

    def test_list_for_session_filters_other_sessions(self):
        gate = self._make_gate()
        p1 = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        asyncio.run(gate.create_pending(
            session_id="s2", project_id=2, project_slug="other",
            code="b", language="python",
        ))
        my_pendings = gate.list_for_session("s1")
        assert len(my_pendings) == 1
        assert my_pendings[0].id == p1.id
        assert gate.list_for_session("s2-not-mine") == []
        # Empty session_id liefert leere Liste (keine Cross-Session-Leakage)
        assert gate.list_for_session("") == []

    def test_resolve_approved_flips_status(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        ok = asyncio.run(gate.resolve(p.id, "approved"))
        assert ok is True
        assert gate.get(p.id).status == "approved"

    def test_resolve_rejected_flips_status(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        ok = asyncio.run(gate.resolve(p.id, "rejected"))
        assert ok is True
        assert gate.get(p.id).status == "rejected"

    def test_resolve_invalid_decision_returns_false(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        assert asyncio.run(gate.resolve(p.id, "maybe")) is False
        assert gate.get(p.id).status == "pending"

    def test_resolve_unknown_id_returns_false(self):
        gate = self._make_gate()
        assert asyncio.run(gate.resolve("nonexistent", "approved")) is False

    def test_resolve_already_resolved_returns_false(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        asyncio.run(gate.resolve(p.id, "approved"))
        # Zweiter Resolve-Versuch ist idempotent (False, kein Crash)
        assert asyncio.run(gate.resolve(p.id, "rejected")) is False
        assert gate.get(p.id).status == "approved"

    def test_resolve_session_mismatch_blocks(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        # Cross-Session: andere session_id darf nicht resolven
        assert asyncio.run(gate.resolve(
            p.id, "approved", session_id="s2-attacker",
        )) is False
        assert gate.get(p.id).status == "pending"
        # Korrekte Session funktioniert weiterhin
        assert asyncio.run(gate.resolve(
            p.id, "approved", session_id="s1",
        )) is True

    def test_wait_for_decision_returns_immediately_when_resolved(self):
        gate = self._make_gate()

        async def scenario():
            p = await gate.create_pending(
                session_id="s1", project_id=1, project_slug="demo",
                code="a", language="python",
            )
            await gate.resolve(p.id, "approved")
            # Wait sollte sofort returnen, kein Block
            return await gate.wait_for_decision(p.id, timeout=5)

        assert asyncio.run(scenario()) == "approved"

    def test_wait_for_decision_blocks_then_resolves(self):
        gate = self._make_gate()

        async def scenario():
            p = await gate.create_pending(
                session_id="s1", project_id=1, project_slug="demo",
                code="a", language="python",
            )

            async def resolve_after_delay():
                await asyncio.sleep(0.05)
                await gate.resolve(p.id, "rejected")

            asyncio.create_task(resolve_after_delay())
            return await gate.wait_for_decision(p.id, timeout=2)

        assert asyncio.run(scenario()) == "rejected"

    def test_wait_for_decision_times_out_and_sets_status(self):
        gate = self._make_gate()

        async def scenario():
            p = await gate.create_pending(
                session_id="s1", project_id=1, project_slug="demo",
                code="a", language="python",
            )
            result = await gate.wait_for_decision(p.id, timeout=0.05)
            return result, gate.get(p.id).status

        result, final_status = asyncio.run(scenario())
        assert result == "timeout"
        assert final_status == "timeout"

    def test_wait_for_decision_unknown_id(self):
        gate = self._make_gate()
        result = asyncio.run(gate.wait_for_decision("nonexistent", timeout=0.05))
        assert result == "unknown"

    def test_cleanup_removes_pending(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        gate.cleanup(p.id)
        assert gate.get(p.id) is None
        assert gate.list_for_session("s1") == []


# ---------------------------------------------------------------------------
# 2) Audit-Trail
# ---------------------------------------------------------------------------


class TestStoreCodeExecutionAudit:
    def test_writes_row_with_all_fields(self, tmp_db):
        from zerberus.core.hitl_chat import store_code_execution_audit
        from zerberus.core.database import CodeExecution
        from sqlalchemy import select

        payload = {
            "language": "python", "code": "print(1)",
            "exit_code": 0, "stdout": "1\n", "stderr": "",
            "execution_time_ms": 42, "truncated": False,
            "error": None, "skipped": False,
        }
        asyncio.run(store_code_execution_audit(
            session_id="s-test", project_id=7, project_slug="demo",
            payload=payload, pending_id="abc123", hitl_status="approved",
        ))

        async def query():
            async with tmp_db() as session:
                rows = (await session.execute(select(CodeExecution))).scalars().all()
                return rows

        rows = asyncio.run(query())
        assert len(rows) == 1
        row = rows[0]
        assert row.pending_id == "abc123"
        assert row.session_id == "s-test"
        assert row.project_id == 7
        assert row.project_slug == "demo"
        assert row.language == "python"
        assert row.exit_code == 0
        assert row.execution_time_ms == 42
        assert row.truncated == 0
        assert row.skipped == 0
        assert row.hitl_status == "approved"
        assert row.code_text == "print(1)"
        assert row.stdout_text == "1\n"
        assert row.stderr_text == ""
        assert row.error_text is None
        assert row.resolved_at is not None  # approved → resolved

    def test_truncates_long_text(self, tmp_db):
        from zerberus.core.hitl_chat import (
            store_code_execution_audit,
            AUDIT_MAX_TEXT_BYTES,
        )
        from zerberus.core.database import CodeExecution
        from sqlalchemy import select

        long_stdout = "x" * (AUDIT_MAX_TEXT_BYTES + 1000)
        payload = {
            "language": "python", "code": "print('long')",
            "exit_code": 0, "stdout": long_stdout, "stderr": "",
            "execution_time_ms": 100, "truncated": True,
            "error": None, "skipped": False,
        }
        asyncio.run(store_code_execution_audit(
            session_id="s", project_id=1, project_slug="d",
            payload=payload, pending_id=None, hitl_status="bypassed",
        ))

        async def query():
            async with tmp_db() as session:
                rows = (await session.execute(select(CodeExecution))).scalars().all()
                return rows

        rows = asyncio.run(query())
        assert len(rows) == 1
        # Truncated-Marker am Ende
        assert "[gekuerzt]" in rows[0].stdout_text
        # Innerhalb-Limit (Marker mit eingerechnet)
        assert len(rows[0].stdout_text.encode("utf-8")) <= \
            AUDIT_MAX_TEXT_BYTES + 50

    def test_silent_skip_when_db_not_initialized(self, monkeypatch):
        # tmp_db NICHT eingebunden — _async_session_maker = None
        import zerberus.core.database as db_mod
        from zerberus.core.hitl_chat import store_code_execution_audit
        monkeypatch.setattr(db_mod, "_async_session_maker", None)
        # darf nicht crashen
        asyncio.run(store_code_execution_audit(
            session_id="s", project_id=1, project_slug="d",
            payload={"language": "python", "code": "x", "exit_code": 0},
            pending_id=None, hitl_status="bypassed",
        ))


# ---------------------------------------------------------------------------
# 3) Endpoints
# ---------------------------------------------------------------------------


class TestHitlEndpoints:
    def test_poll_empty_returns_none(self):
        from zerberus.app.routers.legacy import hitl_poll
        request = SimpleNamespace(headers={"X-Session-ID": "s-test"})
        resp = asyncio.run(hitl_poll(request))
        assert resp.pending is None

    def test_poll_returns_pending_for_session(self):
        from zerberus.app.routers.legacy import hitl_poll
        from zerberus.core.hitl_chat import get_chat_hitl_gate

        gate = get_chat_hitl_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s-test", project_id=1, project_slug="demo",
            code="print(1)", language="python",
        ))
        request = SimpleNamespace(headers={"X-Session-ID": "s-test"})
        resp = asyncio.run(hitl_poll(request))
        assert resp.pending is not None
        assert resp.pending["id"] == p.id
        assert resp.pending["language"] == "python"
        assert resp.pending["code"] == "print(1)"

    def test_poll_filters_other_sessions(self):
        from zerberus.app.routers.legacy import hitl_poll
        from zerberus.core.hitl_chat import get_chat_hitl_gate

        gate = get_chat_hitl_gate()
        asyncio.run(gate.create_pending(
            session_id="s-other", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        request = SimpleNamespace(headers={"X-Session-ID": "s-mine"})
        resp = asyncio.run(hitl_poll(request))
        assert resp.pending is None

    def test_resolve_approved_returns_ok(self):
        from zerberus.app.routers.legacy import (
            hitl_resolve, HitlResolveRequest,
        )
        from zerberus.core.hitl_chat import get_chat_hitl_gate

        gate = get_chat_hitl_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s-test", project_id=1, project_slug="demo",
            code="a", language="python",
        ))
        req = HitlResolveRequest(
            pending_id=p.id, decision="approved", session_id="s-test",
        )
        request = SimpleNamespace(headers={"X-Session-ID": "s-test"})
        resp = asyncio.run(hitl_resolve(req, request))
        assert resp.ok is True
        assert resp.decision == "approved"
        assert gate.get(p.id).status == "approved"

    def test_resolve_unknown_id(self):
        from zerberus.app.routers.legacy import (
            hitl_resolve, HitlResolveRequest,
        )
        req = HitlResolveRequest(pending_id="bogus", decision="approved")
        request = SimpleNamespace(headers={"X-Session-ID": "s"})
        resp = asyncio.run(hitl_resolve(req, request))
        assert resp.ok is False
        assert resp.decision is None

    def test_resolve_invalid_decision(self):
        from zerberus.app.routers.legacy import (
            hitl_resolve, HitlResolveRequest,
        )
        from zerberus.core.hitl_chat import get_chat_hitl_gate

        gate = get_chat_hitl_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s", project_id=1, project_slug="d",
            code="a", language="python",
        ))
        req = HitlResolveRequest(pending_id=p.id, decision="maybe")
        request = SimpleNamespace(headers={"X-Session-ID": "s"})
        resp = asyncio.run(hitl_resolve(req, request))
        assert resp.ok is False
        assert gate.get(p.id).status == "pending"

    def test_resolve_cross_session_blocked(self):
        from zerberus.app.routers.legacy import (
            hitl_resolve, HitlResolveRequest,
        )
        from zerberus.core.hitl_chat import get_chat_hitl_gate

        gate = get_chat_hitl_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s-owner", project_id=1, project_slug="d",
            code="a", language="python",
        ))
        # Body session_id ist "attacker" — darf nicht durchgehen
        req = HitlResolveRequest(
            pending_id=p.id, decision="approved",
            session_id="s-attacker",
        )
        request = SimpleNamespace(headers={})
        resp = asyncio.run(hitl_resolve(req, request))
        assert resp.ok is False
        assert gate.get(p.id).status == "pending"


# ---------------------------------------------------------------------------
# 4) Source-Audit legacy.py
# ---------------------------------------------------------------------------


class TestLegacySourceAudit:
    def _src(self) -> str:
        return (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(
            encoding="utf-8"
        )

    def test_logging_tag_present(self):
        assert "[HITL-206]" in self._src()

    def test_imports_chat_hitl_gate(self):
        assert "get_chat_hitl_gate" in self._src()

    def test_create_pending_called(self):
        assert "create_pending" in self._src()

    def test_wait_for_decision_called(self):
        assert "wait_for_decision" in self._src()

    def test_cleanup_called(self):
        # In-Memory-Store muss aufgeraeumt werden — sonst Memory-Leak.
        src = self._src()
        idx = src.find("[HITL-206] decision")
        assert idx > 0
        window = src[max(0, idx - 1500):idx + 500]
        assert "_gate.cleanup(" in window

    def test_synthesis_skip_for_skipped_payload(self):
        """Synthese darf NICHT laufen wenn HitL den Block geblockt hat —
        sonst fragt das LLM auf einem leeren stdout/stderr nach."""
        src = self._src()
        # Suchfenster: rund um den synthesize_code_output-Aufruf
        idx = src.find("synthesize_code_output")
        assert idx > 0
        window = src[max(0, idx - 600):idx + 200]
        assert 'not code_execution_payload.get("skipped")' in window or \
            "not code_execution_payload.get('skipped')" in window

    def test_audit_call_after_assistant_store(self):
        src = self._src()
        idx_assistant = src.find('store_interaction("assistant"')
        assert idx_assistant > 0
        idx_audit = src.find("store_code_execution_audit")
        assert idx_audit > idx_assistant, \
            "Audit-Schreibung MUSS nach store_interaction passieren"

    def test_response_has_code_execution_field(self):
        from zerberus.app.routers.legacy import ChatCompletionResponse
        fields = getattr(ChatCompletionResponse, "model_fields", None) or \
                 getattr(ChatCompletionResponse, "__fields__", {})
        assert "code_execution" in fields

    def test_endpoints_registered(self):
        from zerberus.app.routers.legacy import router
        paths = {r.path for r in router.routes}
        assert "/v1/hitl/poll" in paths
        assert "/v1/hitl/resolve" in paths


# ---------------------------------------------------------------------------
# 5) Source-Audit nala.py — JS, CSS, Touch-Target, Renderer-Skip-State
# ---------------------------------------------------------------------------


class TestNalaSourceAudit:
    def _src(self) -> str:
        return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(
            encoding="utf-8"
        )

    def test_start_hitl_polling_defined(self):
        assert "function startHitlPolling(" in self._src()

    def test_render_hitl_card_defined(self):
        assert "function renderHitlCard(" in self._src()

    def test_resolve_hitl_pending_defined(self):
        assert "function resolveHitlPending(" in self._src() or \
            "async function resolveHitlPending(" in self._src()

    def test_clear_hitl_state_defined(self):
        assert "function clearHitlState(" in self._src()

    def test_send_message_starts_polling(self):
        src = self._src()
        # Im sendMessage-Body sollte startHitlPolling aufgerufen werden
        idx_send = src.find("async function sendMessage(")
        assert idx_send > 0
        idx_finally = src.find("// Patch 206: HitL-Polling stoppen")
        assert idx_finally > idx_send
        # Window: vom sendMessage-Header bis zum stop-Block
        window = src[idx_send:idx_finally + 200]
        assert "startHitlPolling(" in window
        assert "stopHitlPolling()" in window

    def test_resolve_posts_to_v1_hitl_resolve(self):
        src = self._src()
        idx = src.find("function resolveHitlPending(")
        assert idx > 0
        window = src[idx:idx + 1500]
        assert "/v1/hitl/resolve" in window
        # Body enthaelt pending_id + decision
        assert "pending_id" in window
        assert "decision" in window
        # session_id wird mitgeschickt — Defense-in-Depth
        assert "session_id" in window

    def test_poll_endpoint_used(self):
        src = self._src()
        assert "/v1/hitl/poll" in src

    def test_xss_escape_in_render_hitl_card(self):
        """Code-Vorschau in der Confirm-Karte MUSS escaped sein."""
        src = self._src()
        idx = src.find("function renderHitlCard(")
        assert idx > 0
        window = src[idx:idx + 2200]
        # innerHTML mit Code-Inhalt MUSS escapeHtml verwenden
        assert "escapeHtml(String(pending.code" in window
        # Sprach-Tag ebenso
        assert "escapeHtml(lang)" in window or "escapeHtml(String(pending.language" in window

    def test_css_hitl_card_present(self):
        src = self._src()
        for cls in (".hitl-card", ".hitl-actions", ".hitl-approve",
                    ".hitl-reject", ".hitl-resolved"):
            assert cls in src, f"CSS-Klasse {cls} fehlt"

    def test_touch_target_44px_in_hitl_actions(self):
        """Mobile-first: HitL-Buttons brauchen 44x44 px Touch-Target."""
        src = self._src()
        idx = src.find(".hitl-actions button {")
        assert idx > 0
        window = src[idx:idx + 600]
        assert "min-height: 44px" in window
        assert "min-width: 44px" in window

    def test_render_code_execution_handles_skipped_state(self):
        """Renderer reagiert auf ``codeExec.skipped`` — Skip-Badge statt
        regulaerem exit-Code-Badge."""
        src = self._src()
        idx = src.find("function renderCodeExecution(")
        assert idx > 0
        window = src[idx:idx + 4500]
        assert "codeExec.skipped" in window
        # Skip-Badge-Variante
        assert "exit-skipped" in window
        # Reason kommt im Banner (errorMsg) — nicht doppelt rendern
        assert "exit-skipped" in src

    def test_pending_code_only_via_escape_html(self):
        """Defense-in-Depth gegen XSS: jedes Auftreten von ``pending.code``
        im Renderer-Body MUSS als Argument von ``escapeHtml(`` stehen.
        Verboten waere z.B. ``innerHTML = '<pre>' + pending.code + ...``.
        """
        src = self._src()
        idx = src.find("function renderHitlCard(")
        assert idx > 0
        body = src[idx:idx + 2500]
        # Alle Vorkommen von ``pending.code`` muessen direkt nach
        # ``escapeHtml(String(`` (oder ``escapeHtml(``) auftauchen.
        for m in re.finditer(r"pending\.code", body):
            window_before = body[max(0, m.start() - 80):m.start()]
            assert "escapeHtml(" in window_before, (
                f"pending.code wird nicht escaped — Kontext: "
                f"{body[max(0, m.start() - 40):m.end() + 20]!r}"
            )


# ---------------------------------------------------------------------------
# 6) End-to-End — chat_completions Pfade
# ---------------------------------------------------------------------------


class TestE2EHitlGateInChat:

    def _setup_common(self, monkeypatch, *, llm_answer: str,
                       hitl_decision: str = "approved",
                       hitl_enabled: bool = True,
                       sandbox_enabled: bool = True,
                       sandbox_result=None):
        """Patcht LLM, Sandbox-Manager, execute_in_workspace + den Gate.
        Liefert ``captured`` zur Beobachtung des Sandbox-Calls."""
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService
        from zerberus.core.config import get_settings

        monkeypatch.setattr(LLMService, "call", _make_fake_llm(llm_answer))
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        settings = get_settings()
        monkeypatch.setattr(settings.projects, "hitl_enabled", hitl_enabled)
        # Schneller Timeout — wenn Decision == "timeout" wollen wir nicht
        # 60s warten. Wir mocken wait_for_decision sowieso.
        monkeypatch.setattr(settings.projects, "hitl_timeout_seconds", 1)

        fake_mgr = _make_fake_sandbox_manager(enabled=sandbox_enabled)
        monkeypatch.setattr(
            "zerberus.modules.sandbox.manager.get_sandbox_manager",
            lambda: fake_mgr,
        )

        captured: dict = {"sandbox_calls": 0, "wait_calls": 0}

        async def fake_execute(*, project_id, code, language, base_dir,
                               writable=False, timeout=None):
            captured["sandbox_calls"] += 1
            captured["project_id"] = project_id
            captured["code"] = code
            captured["language"] = language
            return sandbox_result

        monkeypatch.setattr(
            "zerberus.core.projects_workspace.execute_in_workspace",
            fake_execute,
        )

        # Gate-Decision mocken: wait_for_decision liefert immer
        # ``hitl_decision`` ohne wirklich zu warten.
        from zerberus.core.hitl_chat import ChatHitlGate

        async def fake_wait(self, pending_id, timeout):
            captured["wait_calls"] += 1
            captured["wait_timeout"] = timeout
            # Pending auf den gemockten Status flippen, damit get() konsistent ist
            p = self._pendings.get(pending_id)
            if p is not None:
                p.status = hitl_decision
            return hitl_decision

        monkeypatch.setattr(ChatHitlGate, "wait_for_decision", fake_wait)
        return captured

    def _create_project(self, **kwargs):
        from zerberus.core.projects_repo import create_project
        return asyncio.run(create_project(**kwargs))

    def _call_endpoint(self, project_id: int | None, session_id: str = "s1"):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings

        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(
                role="user", content="Berechne 1+1",
            )]
        )
        request = _build_request(project_id, session_id=session_id,
                                  profile_name="alice")
        return asyncio.run(legacy_mod.chat_completions(
            request, req, get_settings()
        ))

    # -- approved --

    def test_approved_runs_sandbox_and_populates_payload(self, env, monkeypatch):
        captured = self._setup_common(
            monkeypatch,
            llm_answer="Hier:\n```python\nprint(2)\n```",
            hitl_decision="approved",
            sandbox_result=_make_sandbox_result(stdout="2\n", exit_code=0),
        )
        proj = self._create_project(name="P-approved")
        resp = self._call_endpoint(proj["id"])

        assert captured["sandbox_calls"] == 1
        assert captured["wait_calls"] == 1
        assert resp.code_execution is not None
        assert resp.code_execution["skipped"] is False
        assert resp.code_execution["hitl_status"] == "approved"
        assert resp.code_execution["exit_code"] == 0
        assert resp.code_execution["stdout"] == "2\n"

    # -- rejected --

    def test_rejected_skips_sandbox_and_marks_payload(self, env, monkeypatch):
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint('rm -rf')\n```",
            hitl_decision="rejected",
            sandbox_result=_make_sandbox_result(stdout="bad", exit_code=0),
        )
        proj = self._create_project(name="P-reject")
        resp = self._call_endpoint(proj["id"])

        # Sandbox darf NICHT laufen
        assert captured["sandbox_calls"] == 0
        assert captured["wait_calls"] == 1
        # Payload zeigt Skip
        assert resp.code_execution is not None
        assert resp.code_execution["skipped"] is True
        assert resp.code_execution["hitl_status"] == "rejected"
        assert resp.code_execution["exit_code"] == -1
        assert "abgebrochen" in (resp.code_execution["error"] or "").lower()
        # Code wurde durchgereicht zum Frontend
        assert resp.code_execution["code"] == "print('rm -rf')"

    # -- timeout --

    def test_timeout_skips_sandbox_and_marks_payload(self, env, monkeypatch):
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nimport time; time.sleep(99999)\n```",
            hitl_decision="timeout",
            sandbox_result=_make_sandbox_result(stdout="x"),
        )
        proj = self._create_project(name="P-timeout")
        resp = self._call_endpoint(proj["id"])

        assert captured["sandbox_calls"] == 0
        assert resp.code_execution is not None
        assert resp.code_execution["skipped"] is True
        assert resp.code_execution["hitl_status"] == "timeout"
        assert "timeout" in (resp.code_execution["error"] or "").lower()

    # -- bypassed (hitl_enabled = False) --

    def test_bypassed_runs_sandbox_directly(self, env, monkeypatch):
        captured = self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(3)\n```",
            hitl_enabled=False,
            sandbox_result=_make_sandbox_result(stdout="3\n", exit_code=0),
        )
        proj = self._create_project(name="P-bypass")
        resp = self._call_endpoint(proj["id"])

        # wait_for_decision DARF NICHT aufgerufen werden bei hitl_enabled=False
        assert captured["wait_calls"] == 0
        assert captured["sandbox_calls"] == 1
        assert resp.code_execution is not None
        assert resp.code_execution["skipped"] is False
        assert resp.code_execution["hitl_status"] == "bypassed"

    # -- audit-row geschrieben --

    def test_audit_row_written_on_approved(self, env, monkeypatch, tmp_db):
        self._setup_common(
            monkeypatch,
            llm_answer="```python\nprint(1)\n```",
            hitl_decision="approved",
            sandbox_result=_make_sandbox_result(stdout="1\n"),
        )
        proj = self._create_project(name="P-audit")
        self._call_endpoint(proj["id"], session_id="s-audit")

        from zerberus.core.database import CodeExecution
        from sqlalchemy import select

        async def query():
            async with tmp_db() as session:
                rows = (await session.execute(select(CodeExecution))).scalars().all()
                return rows

        rows = asyncio.run(query())
        assert len(rows) == 1
        assert rows[0].hitl_status == "approved"
        assert rows[0].session_id == "s-audit"
        assert rows[0].project_id == proj["id"]
        assert rows[0].language == "python"
        assert rows[0].skipped == 0

    def test_audit_row_written_on_rejected(self, env, monkeypatch, tmp_db):
        self._setup_common(
            monkeypatch,
            llm_answer="```python\nbad()\n```",
            hitl_decision="rejected",
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P-audit-reject")
        self._call_endpoint(proj["id"], session_id="s-audit-r")

        from zerberus.core.database import CodeExecution
        from sqlalchemy import select

        async def query():
            async with tmp_db() as session:
                rows = (await session.execute(select(CodeExecution))).scalars().all()
                return rows

        rows = asyncio.run(query())
        assert len(rows) == 1
        assert rows[0].hitl_status == "rejected"
        assert rows[0].skipped == 1


# ---------------------------------------------------------------------------
# 7) JS-Integrity (analog P203b/P203d-3)
# ---------------------------------------------------------------------------


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node nicht im PATH")
class TestJsSyntaxIntegrity:
    """Lesson aus P203b/P203d-3: ein einziger SyntaxError im inline
    <script> killt alle Funktionen. P206 fuegt mehrere neue JS-Funktionen
    hinzu (HitL-Polling/Render/Resolve) — wir lassen ``node --check``
    ueber alle Bloecke laufen."""

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
                p = Path(td) / f"nala_p206_{i}.js"
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
# 8) Smoke
# ---------------------------------------------------------------------------


class TestSmoke:
    def test_config_has_hitl_flags(self):
        from zerberus.core.config import get_settings
        s = get_settings()
        assert hasattr(s.projects, "hitl_enabled")
        assert hasattr(s.projects, "hitl_timeout_seconds")
        assert isinstance(s.projects.hitl_enabled, bool)
        assert isinstance(s.projects.hitl_timeout_seconds, int)

    def test_database_has_code_executions_table(self):
        from zerberus.core.database import CodeExecution
        # SQLAlchemy-Model existiert + hat den HitL-Status-Spalte
        cols = {c.name for c in CodeExecution.__table__.columns}
        assert "hitl_status" in cols
        assert "pending_id" in cols
        assert "session_id" in cols
        assert "project_id" in cols
        assert "skipped" in cols

    def test_nala_endpoint_renders_hitl_pieces(self):
        from zerberus.app.routers.nala import nala_interface

        body = asyncio.run(nala_interface())
        assert "function renderHitlCard" in body
        assert ".hitl-card" in body
        assert "/v1/hitl/poll" in body
        assert "/v1/hitl/resolve" in body
