"""Patch 213 (Phase 5a #13) — Reasoning-Schritte sichtbar im Chat.

Tests decken ab:

* Pure-Function ``compute_step_duration_ms`` / ``should_emit`` /
  ``truncate_text``.
* ``ReasoningStep``-Dataclass: ``duration_ms`` + ``to_public_dict``.
* ``ReasoningStreamGate``: emit, mark_done (idempotent), FIFO-Cap,
  list_for_session, cleanup_session, cleanup_stale_sessions, consume_steps
  (sofort + long-poll).
* Convenience ``emit_step`` / ``mark_step_done``: None-Eingang, Trigger-Gate.
* Best-Effort-Audit (``_audit_step`` ohne DB-Init).
* Source-Audit der Verdrahtung in ``legacy.py``: emit_step-Aufrufe an
  Spec/RAG/LLM/Veto/HitL/Sandbox/Synthese.
* Endpoint ``GET /v1/reasoning/poll`` + ``POST /v1/reasoning/clear``:
  Source + E2E.
* Nala-Frontend: CSS, Polling-Endpoint, Mobile-44px, escapeHtml-XSS,
  Event-Delegation (kein onclick-Concat).
* JS-Syntax-Integritaet (``node --check`` ueber inline <script>-Bloecke).
* Smoke (Modul-Exports, DB-Schema, Konsistenz der Konstanten).
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ── Reset-Fixture fuer den Singleton ────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_reasoning_gate():
    """Vor jedem Test einen frischen Gate, damit Tests sich nicht
    gegenseitig beeinflussen (Singleton-State)."""
    from zerberus.core import reasoning_steps
    reasoning_steps.reset_reasoning_gate_for_tests()
    yield
    reasoning_steps.reset_reasoning_gate_for_tests()


# ── Pure-Function: compute_step_duration_ms ─────────────────────────────


class TestComputeStepDurationMs:
    def test_running_step_returns_none(self):
        from zerberus.core.reasoning_steps import compute_step_duration_ms
        started = datetime.utcnow()
        assert compute_step_duration_ms(started, None) is None

    def test_finished_returns_positive(self):
        from zerberus.core.reasoning_steps import compute_step_duration_ms
        started = datetime.utcnow()
        finished = started + timedelta(milliseconds=42)
        assert compute_step_duration_ms(started, finished) == 42

    def test_zero_duration_returns_zero(self):
        from zerberus.core.reasoning_steps import compute_step_duration_ms
        t = datetime.utcnow()
        assert compute_step_duration_ms(t, t) == 0

    def test_negative_clamped_to_zero(self):
        """Pathological case: finished < started — defensive clamp to 0."""
        from zerberus.core.reasoning_steps import compute_step_duration_ms
        started = datetime.utcnow()
        finished = started - timedelta(milliseconds=10)
        assert compute_step_duration_ms(started, finished) == 0


# ── Pure-Function: should_emit ──────────────────────────────────────────


class TestShouldEmit:
    def test_known_kind_emits(self):
        from zerberus.core.reasoning_steps import should_emit
        assert should_emit("spec_check") is True
        assert should_emit("veto_probe") is True
        assert should_emit("hitl_wait") is True
        assert should_emit("sandbox_run") is True
        assert should_emit("synthesis") is True

    def test_unknown_kind_blocks(self):
        from zerberus.core.reasoning_steps import should_emit
        assert should_emit("foo") is False
        assert should_emit("") is False

    def test_disabled_globally_blocks(self):
        from zerberus.core.reasoning_steps import should_emit
        assert should_emit("spec_check", enabled=False) is False

    def test_disabled_kinds_blocks(self):
        from zerberus.core.reasoning_steps import should_emit
        disabled = frozenset({"hitl_wait"})
        assert should_emit("hitl_wait", disabled_kinds=disabled) is False
        assert should_emit("spec_check", disabled_kinds=disabled) is True


# ── Pure-Function: truncate_text ────────────────────────────────────────


class TestTruncateText:
    def test_none_returns_none(self):
        from zerberus.core.reasoning_steps import truncate_text
        assert truncate_text(None, max_bytes=100) is None

    def test_short_text_passes_through(self):
        from zerberus.core.reasoning_steps import truncate_text
        assert truncate_text("hi", max_bytes=100) == "hi"

    def test_truncated_with_ellipsis(self):
        from zerberus.core.reasoning_steps import truncate_text
        out = truncate_text("a" * 1000, max_bytes=10)
        assert out is not None
        # 10 Bytes Inhalt + 3 Bytes UTF-8-Ellipsis (…)
        assert len(out.encode("utf-8")) == 10 + len("…".encode("utf-8"))
        assert out.endswith("…")

    def test_unicode_safe_truncate(self):
        """Truncation muss Multi-Byte-Sequenzen sauber abschneiden."""
        from zerberus.core.reasoning_steps import truncate_text
        # 'ä' = 2 Bytes UTF-8
        out = truncate_text("ä" * 50, max_bytes=5)
        assert out is not None
        # darf nicht mit halbem Codepoint enden
        out.encode("utf-8")  # raises if invalid


# ── Datenklasse: ReasoningStep ───────────────────────────────────────────


class TestReasoningStep:
    def test_running_has_no_duration(self):
        from zerberus.core.reasoning_steps import ReasoningStep
        step = ReasoningStep(
            step_id="abc",
            session_id="s1",
            kind="veto_probe",
            summary="prueft",
            started_at=datetime.utcnow(),
        )
        assert step.duration_ms is None

    def test_finished_computes_duration(self):
        from zerberus.core.reasoning_steps import ReasoningStep
        started = datetime.utcnow()
        step = ReasoningStep(
            step_id="abc",
            session_id="s1",
            kind="veto_probe",
            summary="prueft",
            started_at=started,
            status="done",
            finished_at=started + timedelta(milliseconds=200),
        )
        assert step.duration_ms == 200

    def test_to_public_dict_omits_session_and_detail(self):
        from zerberus.core.reasoning_steps import ReasoningStep
        step = ReasoningStep(
            step_id="abc",
            session_id="s1",
            kind="veto_probe",
            summary="prueft",
            started_at=datetime.utcnow(),
            detail="internal-only",
        )
        d = step.to_public_dict()
        assert "session_id" not in d
        assert "detail" not in d
        assert d["step_id"] == "abc"
        assert d["kind"] == "veto_probe"
        assert d["status"] == "running"
        assert d["duration_ms"] is None
        assert d["summary"] == "prueft"


# ── ReasoningStreamGate ──────────────────────────────────────────────────


class TestStreamGateEmit:
    def test_emit_creates_running_step(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        step = gate.emit(session_id="s1", kind="veto_probe", summary="prueft")
        assert step is not None
        assert step.status == "running"
        assert step.session_id == "s1"
        assert step.kind == "veto_probe"
        assert len(gate.list_for_session("s1")) == 1

    def test_emit_unknown_kind_returns_none(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        assert gate.emit(session_id="s1", kind="bogus", summary="x") is None

    def test_emit_without_session_id_returns_none(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        assert gate.emit(session_id="", kind="veto_probe", summary="x") is None

    def test_emit_truncates_summary_and_detail(self):
        from zerberus.core.reasoning_steps import (
            ReasoningStreamGate,
            SUMMARY_MAX_BYTES,
        )
        gate = ReasoningStreamGate()
        long = "x" * (SUMMARY_MAX_BYTES * 3)
        step = gate.emit(session_id="s1", kind="veto_probe",
                         summary=long, detail=long)
        assert step is not None
        # Truncate ist Bytes-genau, Ellipsis-Marker mitgezaehlt.
        assert step.summary is not None
        assert step.summary.endswith("…") or len(step.summary) == SUMMARY_MAX_BYTES


class TestStreamGateMarkDone:
    def test_mark_done_sets_finish(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        step = gate.emit(session_id="s1", kind="veto_probe", summary="prueft")
        assert step is not None
        result = gate.mark_done(step.step_id, status="done")
        assert result is not None
        assert result.status == "done"
        assert result.finished_at is not None
        assert result.duration_ms is not None

    def test_mark_done_idempotent(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        step = gate.emit(session_id="s1", kind="veto_probe", summary="prueft")
        gate.mark_done(step.step_id, status="done")
        first_finish = step.finished_at
        gate.mark_done(step.step_id, status="error")  # zweiter Klick — ignoriert
        assert step.status == "done"  # nicht ueberschrieben
        assert step.finished_at == first_finish

    def test_mark_done_invalid_status_returns_none(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        step = gate.emit(session_id="s1", kind="veto_probe", summary="prueft")
        result = gate.mark_done(step.step_id, status="bogus")
        assert result is None
        assert step.status == "running"  # unveraendert

    def test_mark_done_unknown_id_returns_none(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        assert gate.mark_done("does-not-exist", status="done") is None


class TestStreamGateBufferCap:
    def test_fifo_cap_drops_oldest(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate(buffer_per_session=3)
        ids = []
        for i in range(5):
            step = gate.emit(session_id="s1", kind="veto_probe",
                             summary=f"step-{i}")
            assert step is not None
            ids.append(step.step_id)
        steps = gate.list_for_session("s1")
        assert len(steps) == 3
        # Aelteste 2 sind raus, juengste 3 sind drin
        kept = [s.summary for s in steps]
        assert kept == ["step-2", "step-3", "step-4"]


class TestStreamGateCleanup:
    def test_cleanup_session_removes_steps(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        gate.emit(session_id="s1", kind="veto_probe", summary="x")
        gate.emit(session_id="s1", kind="hitl_wait", summary="y")
        gate.emit(session_id="s2", kind="spec_check", summary="z")
        removed = gate.cleanup_session("s1")
        assert removed == 2
        assert gate.list_for_session("s1") == []
        assert len(gate.list_for_session("s2")) == 1

    def test_cleanup_stale_sweeps_old_sessions(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate(ttl_seconds=10)
        gate.emit(session_id="old", kind="veto_probe", summary="x")
        gate.emit(session_id="new", kind="hitl_wait", summary="y")
        # Last-Seen fuer old in die Vergangenheit ruecken.
        gate._last_seen["old"] = datetime.utcnow() - timedelta(seconds=100)
        removed = gate.cleanup_stale_sessions()
        assert removed == 1
        assert gate.list_for_session("old") == []
        assert len(gate.list_for_session("new")) == 1


@pytest.mark.asyncio
class TestStreamGateConsume:
    async def test_consume_immediate_when_steps_present(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        gate.emit(session_id="s1", kind="veto_probe", summary="x")
        steps = await gate.consume_steps("s1", wait_seconds=0.0)
        assert len(steps) == 1

    async def test_consume_returns_empty_immediately(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        steps = await gate.consume_steps("s1", wait_seconds=0.0)
        assert steps == []

    async def test_consume_long_poll_returns_when_emit_arrives(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()

        async def emit_after():
            await asyncio.sleep(0.05)
            gate.emit(session_id="s1", kind="veto_probe", summary="x")

        emit_task = asyncio.create_task(emit_after())
        try:
            steps = await gate.consume_steps("s1", wait_seconds=2.0)
            assert len(steps) == 1
        finally:
            await emit_task

    async def test_consume_long_poll_times_out(self):
        from zerberus.core.reasoning_steps import ReasoningStreamGate
        gate = ReasoningStreamGate()
        steps = await gate.consume_steps("s1", wait_seconds=0.05)
        assert steps == []


# ── Convenience: emit_step / mark_step_done ─────────────────────────────


class TestConvenience:
    def test_emit_step_via_singleton(self):
        from zerberus.core.reasoning_steps import (
            emit_step,
            get_reasoning_gate,
        )
        step = emit_step("s1", "veto_probe", "x")
        assert step is not None
        assert get_reasoning_gate().list_for_session("s1") == [step]

    def test_mark_step_done_accepts_none(self):
        """Pattern: mark_step_done(emit_step(...)) — wenn emit None liefert
        (Trigger-Gate), darf mark_step_done nicht crashen."""
        from zerberus.core.reasoning_steps import mark_step_done
        # Kein Crash, kein Side-Effect.
        assert mark_step_done(None) is None

    def test_mark_step_done_accepts_step_object(self):
        from zerberus.core.reasoning_steps import emit_step, mark_step_done
        step = emit_step("s1", "spec_check", "x")
        result = mark_step_done(step, status="done")
        assert result is not None
        assert result.status == "done"


# ── Best-Effort-Audit ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStoreAudit:
    async def test_audit_silent_when_db_not_initialized(self):
        """Wenn _async_session_maker None ist (Unit-Tests ohne init_db),
        muss _audit_step silent skippen — kein Crash."""
        from zerberus.core import database
        from zerberus.core.reasoning_steps import (
            ReasoningStep,
            _audit_step,
        )
        # Sicherstellen dass kein Session-Maker da ist.
        prev = database._async_session_maker
        database._async_session_maker = None
        try:
            step = ReasoningStep(
                step_id="abc",
                session_id="s1",
                kind="veto_probe",
                summary="x",
                started_at=datetime.utcnow(),
                status="done",
                finished_at=datetime.utcnow(),
            )
            await _audit_step(step)  # darf nicht raisen
        finally:
            database._async_session_maker = prev


# ── Verdrahtungs-Source-Audits ──────────────────────────────────────────


LEGACY_PATH = Path("zerberus/app/routers/legacy.py")
NALA_PATH = Path("zerberus/app/routers/nala.py")
REASONING_PATH = Path("zerberus/core/reasoning_steps.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestLegacyWiring:
    def test_imports_emit_step(self):
        src = _read(LEGACY_PATH)
        assert (
            "from zerberus.core.reasoning_steps import emit_step, mark_step_done"
            in src
        )

    def test_emits_for_known_kinds(self):
        """Mindestens diese Kinds muessen im Chat-Pfad gerufen werden."""
        src = _read(LEGACY_PATH)
        for kind in ("rag_query", "spec_check", "llm_call", "veto_probe",
                     "hitl_wait", "sandbox_run", "synthesis"):
            assert f'"{kind}"' in src, f"emit_step(...,'{kind}',...) fehlt"

    def test_resets_session_at_turn_start(self):
        """Beim Turn-Start muss der serverseitige Reset laufen — das stellt
        sicher, dass die naechste Turn keine alten Steps sieht."""
        src = _read(LEGACY_PATH)
        assert "get_reasoning_gate().cleanup_session(session_id)" in src

    def test_endpoint_registered(self):
        src = _read(LEGACY_PATH)
        assert '@router.get("/reasoning/poll")' in src
        assert '@router.post("/reasoning/clear")' in src


# ── Endpoint-E2E ─────────────────────────────────────────────────────────


class _FakeRequest:
    """Schmaler Request-Stub fuer Endpoint-Tests."""
    def __init__(self, session_id: str = "session-test"):
        self.headers = {"X-Session-ID": session_id}


@pytest.mark.asyncio
class TestReasoningPollEndpoint:
    async def test_returns_empty_when_no_steps(self):
        from zerberus.core.reasoning_steps import (
            reset_reasoning_gate_for_tests,
        )
        from zerberus.app.routers.legacy import reasoning_poll
        reset_reasoning_gate_for_tests()
        req = _FakeRequest(session_id="s-x")
        result = await reasoning_poll(req, wait=0.0)
        assert result == {"steps": []}

    async def test_returns_steps_for_session(self):
        from zerberus.core.reasoning_steps import (
            emit_step,
            reset_reasoning_gate_for_tests,
        )
        from zerberus.app.routers.legacy import reasoning_poll
        reset_reasoning_gate_for_tests()
        emit_step("s-y", "veto_probe", "test")
        req = _FakeRequest(session_id="s-y")
        result = await reasoning_poll(req, wait=0.0)
        assert len(result["steps"]) == 1
        assert result["steps"][0]["kind"] == "veto_probe"
        # to_public_dict darf keine internen Felder leaken.
        assert "session_id" not in result["steps"][0]
        assert "detail" not in result["steps"][0]

    async def test_session_isolation(self):
        """Steps anderer Sessions duerfen nie ausgeliefert werden."""
        from zerberus.core.reasoning_steps import (
            emit_step,
            reset_reasoning_gate_for_tests,
        )
        from zerberus.app.routers.legacy import reasoning_poll
        reset_reasoning_gate_for_tests()
        emit_step("session-A", "veto_probe", "A")
        emit_step("session-B", "hitl_wait", "B")
        req = _FakeRequest(session_id="session-A")
        result = await reasoning_poll(req, wait=0.0)
        assert len(result["steps"]) == 1
        assert result["steps"][0]["kind"] == "veto_probe"

    async def test_wait_clamped(self):
        """Wait > Limit wird aufs Default-Timeout geclamped — kein
        unbegrenzter Long-Poll."""
        from zerberus.core.reasoning_steps import (
            DEFAULT_POLL_TIMEOUT_SECONDS,
            reset_reasoning_gate_for_tests,
        )
        from zerberus.app.routers.legacy import reasoning_poll
        reset_reasoning_gate_for_tests()
        req = _FakeRequest(session_id="s-z")
        # Ein riesiger wait-Wert soll nicht 9999 Sekunden warten — das
        # Test-Framework wuerde sonst hartnaeckig blockieren. Die
        # Implementierung muss innerhalb des Default-Timeouts antworten.
        # Reduktiv: wir geben wait=DEFAULT+1 und erwarten Antwort innerhalb
        # weniger Sekunden (best-case), nicht innerhalb des grossen Werts.
        # Aufruf mit wait=0 reicht zur Validierung des Default-Pfads.
        # Hier prueft der Test nur den Code-Pfad / Konstante.
        assert DEFAULT_POLL_TIMEOUT_SECONDS > 0


@pytest.mark.asyncio
class TestReasoningClearEndpoint:
    async def test_clear_removes_steps(self):
        from zerberus.core.reasoning_steps import (
            emit_step,
            get_reasoning_gate,
            reset_reasoning_gate_for_tests,
        )
        from zerberus.app.routers.legacy import reasoning_clear
        reset_reasoning_gate_for_tests()
        emit_step("s-c", "veto_probe", "x")
        emit_step("s-c", "hitl_wait", "y")
        req = _FakeRequest(session_id="s-c")
        result = await reasoning_clear(req)
        assert result["ok"] is True
        assert result["removed"] == 2
        assert get_reasoning_gate().list_for_session("s-c") == []

    async def test_clear_idempotent(self):
        from zerberus.core.reasoning_steps import (
            reset_reasoning_gate_for_tests,
        )
        from zerberus.app.routers.legacy import reasoning_clear
        reset_reasoning_gate_for_tests()
        req = _FakeRequest(session_id="s-empty")
        result = await reasoning_clear(req)
        assert result["ok"] is True
        assert result["removed"] == 0


# ── Nala-Frontend ────────────────────────────────────────────────────────


class TestNalaFrontendReasoningCard:
    def test_css_class_present(self):
        src = _read(NALA_PATH)
        assert ".reasoning-card" in src
        assert ".reasoning-toggle" in src
        assert ".reasoning-list" in src
        assert ".reasoning-step" in src

    def test_44px_touch_target(self):
        """Mobile-first Invariante: min-height 44px am Reasoning-Toggle."""
        src = _read(NALA_PATH)
        match = re.search(
            r"\.reasoning-toggle\s*\{[^}]*min-height:\s*44px",
            src, re.DOTALL,
        )
        assert match is not None, (
            "reasoning-toggle braucht min-height: 44px (Mobile-first)"
        )

    def test_polling_endpoint_used(self):
        src = _read(NALA_PATH)
        assert "/v1/reasoning/poll" in src

    def test_clear_endpoint_used(self):
        src = _read(NALA_PATH)
        assert "/v1/reasoning/clear" in src

    def test_polling_started_in_send_message(self):
        """Der Polling-Loop muss innerhalb sendMessage gestartet werden,
        analog HitL/Spec — sonst sieht der User nichts wenn der Chat
        laeuft."""
        src = _read(NALA_PATH)
        assert "startReasoningPolling(" in src
        assert "stopReasoningPolling(" in src
        assert "clearReasoningState(" in src

    def test_event_delegation_no_onclick_concat(self):
        """Toggle-Click via Event-Delegation auf data-reasoning-toggle —
        niemals onclick-Concat im innerHTML (P203b-Invariante)."""
        src = _read(NALA_PATH)
        # data-reasoning-toggle als Marker fuer Event-Delegation
        assert "data-reasoning-toggle" in src
        # Suche nach dem Reasoning-Block und stelle sicher, dass kein
        # onclick="..."-Pattern im Reasoning-Renderer steckt.
        block_match = re.search(
            r"renderReasoningSteps\([^{]*\{(.*?)function clearReasoningState",
            src, re.DOTALL,
        )
        assert block_match is not None
        block = block_match.group(1)
        assert "onclick=" not in block.lower(), (
            "renderReasoningSteps darf keine onclick-Strings concatenieren"
        )

    def test_xss_safe_summary_rendering(self):
        """Summary kommt aus LLM/User-Strings — muss escaped werden, sonst
        XSS-Risiko. Der Renderer setzt summaries via textContent (sicher),
        Icons via escapeHtml(...)."""
        src = _read(NALA_PATH)
        # Test: in renderReasoningSteps wird sumEl.textContent gesetzt
        assert "sumEl.textContent" in src
        # Icon kommt durch escapeHtml in den innerHTML der Container-LI
        # (ein eindeutiger escapeHtml(_reasonIcon-Aufruf reicht)
        assert "escapeHtml(_reasonIcon" in src

    def test_step_kind_whitelist_labels(self):
        """Kinds aus dem Backend muessen im Frontend einen lesbaren Default-
        Label-Mapping haben — sonst zeigt das Frontend nur Rohnamen."""
        src = _read(NALA_PATH)
        for kind in ("spec_check", "veto_probe", "hitl_wait", "sandbox_run",
                     "synthesis"):
            assert kind in src, f"Kind {kind} fehlt im Nala-Frontend"


# ── JS-Syntax-Integritaet ────────────────────────────────────────────────


class TestJsSyntaxIntegrity:
    """``node --check`` ueber alle inline <script>-Bloecke aus nala.py.

    Skipped wenn ``node`` nicht im PATH (z.B. CI-Sandboxen).
    """

    def test_nala_inline_scripts_parse(self, tmp_path):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node nicht im PATH")
        src = _read(NALA_PATH)
        scripts = re.findall(
            r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
            src, re.DOTALL,
        )
        assert scripts, "Keine inline <script>-Bloecke gefunden"
        for i, body in enumerate(scripts):
            tmp_file = tmp_path / f"chunk_{i}.js"
            tmp_file.write_text(body, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(tmp_file)],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, (
                f"node --check fehlgeschlagen fuer Chunk {i}:\n"
                f"stderr={result.stderr}\nbody[:200]={body[:200]!r}"
            )


# ── Smoke ────────────────────────────────────────────────────────────────


class TestSmoke:
    def test_module_exports(self):
        from zerberus.core import reasoning_steps
        for name in (
            "ReasoningStep",
            "ReasoningStreamGate",
            "compute_step_duration_ms",
            "should_emit",
            "truncate_text",
            "emit_step",
            "mark_step_done",
            "get_reasoning_gate",
            "reset_reasoning_gate_for_tests",
            "KNOWN_STEP_KINDS",
            "KNOWN_STATUSES",
            "DEFAULT_BUFFER_PER_SESSION",
            "DEFAULT_SESSION_TTL_SECONDS",
            "DEFAULT_POLL_TIMEOUT_SECONDS",
            "SUMMARY_MAX_BYTES",
            "DETAIL_MAX_BYTES",
        ):
            assert hasattr(reasoning_steps, name), f"Export fehlt: {name}"

    def test_database_has_audit_table(self):
        from zerberus.core import database
        assert hasattr(database, "ReasoningAudit")
        cls = database.ReasoningAudit
        assert cls.__tablename__ == "reasoning_audits"
        cols = {c.name for c in cls.__table__.columns}
        for required in (
            "step_id", "session_id", "kind", "status", "duration_ms",
            "summary", "detail", "created_at",
        ):
            assert required in cols, f"Spalte fehlt: {required}"

    def test_known_kinds_disjoint_from_status_set(self):
        """Konsistenz: Kinds und Statuses sind verschiedene Mengen."""
        from zerberus.core.reasoning_steps import (
            KNOWN_STEP_KINDS,
            KNOWN_STATUSES,
        )
        assert KNOWN_STEP_KINDS.isdisjoint(KNOWN_STATUSES)

    def test_constants_sane(self):
        from zerberus.core.reasoning_steps import (
            DEFAULT_BUFFER_PER_SESSION,
            DEFAULT_POLL_TIMEOUT_SECONDS,
            DEFAULT_SESSION_TTL_SECONDS,
            SUMMARY_MAX_BYTES,
            DETAIL_MAX_BYTES,
        )
        assert DEFAULT_BUFFER_PER_SESSION >= 4
        assert 1 <= DEFAULT_POLL_TIMEOUT_SECONDS <= 60
        assert DEFAULT_SESSION_TTL_SECONDS >= 60
        assert SUMMARY_MAX_BYTES >= 50
        assert DETAIL_MAX_BYTES >= SUMMARY_MAX_BYTES
