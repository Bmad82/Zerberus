"""Patch 208 (Phase 5a #8) — Tests fuer den Spec-Contract-/Ambiguity-Check
vor dem Haupt-LLM-Call.

Schichten:

1. **Pure-Function-Schicht** — ``compute_ambiguity_score``,
   ``should_ask_clarification``, ``build_spec_probe_messages``,
   ``build_clarification_block``, ``enrich_user_message``.
2. **Async-Wrapper** — ``run_spec_probe`` mit Mock-LLM, fail-open auf
   leerer/ungueltiger Response.
3. **ChatSpecGate** — Pending-/Resolve-/Wait-Mechanik analog
   ``ChatHitlGate`` (P206), aber mit drei Decision-Werten.
4. **Audit-Trail** — ``store_clarification_audit`` schreibt in
   ``clarifications``, truncated bei langen Texten, silent skip ohne DB.
5. **Endpoints** — ``GET /v1/spec/poll`` + ``POST /v1/spec/resolve``
   ueber direkte Funktion-Aufrufe.
6. **Source-Audit legacy.py** — Logging-Tag, Imports, Verdrahtung,
   Cancelled-Pfad, Audit-Aufruf.
7. **Source-Audit nala.py** — JS-Funktionen, CSS-Klassen, 44x44 Touch-
   Target, escapeHtml-Usage, sendMessage-Verdrahtung.
8. **End-to-End** — chat_completions mit Mock-LLM und Spec-Probe-Pfad
   (nicht-ambig/ambig+answered/ambig+bypassed/ambig+cancelled).
9. **JS-Integrity** — ``node --check`` ueber alle inline <script>-Bloecke
   (analog P206/P207, skipped wenn node fehlt).
10. **Marker-Uniqueness** — [KLARSTELLUNG] substring-disjunkt zu allen
    anderen LLM-Markern (PROJEKT-RAG, PROSODIE, CODE-EXECUTION etc.).
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
    from zerberus.core.spec_check import reset_chat_spec_gate
    reset_chat_spec_gate()
    yield
    reset_chat_spec_gate()


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_p208.db"
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


def _build_request(session_id: str = "s-test", profile_name: str = "alice"):
    state = SimpleNamespace(
        profile_name=profile_name,
        permission_level="admin",
        allowed_model=None,
        temperature=None,
    )
    headers: dict[str, str] = {"X-Session-ID": session_id}
    return SimpleNamespace(state=state, headers=headers)


# ---------------------------------------------------------------------------
# 1) Pure-Function: compute_ambiguity_score
# ---------------------------------------------------------------------------


class TestComputeAmbiguityScore:
    def test_empty_message_is_max_ambig(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        assert compute_ambiguity_score("") == 1.0
        assert compute_ambiguity_score(None) == 1.0
        assert compute_ambiguity_score("   ") == 1.0

    def test_score_in_range(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        for msg in ["foo", "Schreib mir Code in Python", "Hello world test test test"]:
            score = compute_ambiguity_score(msg)
            assert 0.0 <= score <= 1.0

    def test_short_message_gets_length_penalty(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        very_short = compute_ambiguity_score("mach das")
        long_msg = compute_ambiguity_score(
            "Bitte schreibe mir eine Python-Funktion, die eine Liste von "
            "ganzen Zahlen entgegennimmt und die Summe als Integer "
            "zurueckgibt, mit Tests."
        )
        assert very_short > long_msg

    def test_voice_source_adds_penalty(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        msg = "Schreib mir was in Python"
        text_score = compute_ambiguity_score(msg, source="text")
        voice_score = compute_ambiguity_score(msg, source="voice")
        assert voice_score > text_score
        assert voice_score - text_score >= 0.15

    def test_code_verb_without_language_penalty(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        # "schreib" ist ein Code-Verb, aber keine Sprache genannt
        score_no_lang = compute_ambiguity_score(
            "Schreib mir bitte eine Funktion fuer das Backend"
        )
        score_with_lang = compute_ambiguity_score(
            "Schreib mir bitte eine Python-Funktion fuer das Backend"
        )
        assert score_no_lang > score_with_lang

    def test_pronoun_density_penalty_short_msg(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        # Viele Pronomen ohne klares Antezedens
        score_pronouns = compute_ambiguity_score("Mach das so wie diesen es")
        score_clear = compute_ambiguity_score(
            "Erstelle eine Python-Klasse User mit Feld email als String"
        )
        assert score_pronouns > score_clear

    def test_clear_specific_message_low_score(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        msg = (
            "Schreibe eine Python-Funktion namens parse_csv, die einen Pfad "
            "als Input nimmt und eine Liste von Dicts als Output liefert. "
            "Parameter path: str, return: List[dict]."
        )
        assert compute_ambiguity_score(msg) < 0.5

    def test_score_clamped_to_one(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        # Maximaler Worst-Case
        score = compute_ambiguity_score("mach es", source="voice")
        assert score <= 1.0
        assert score >= 0.7  # Sollte ziemlich hoch sein

    def test_io_hints_lower_score(self):
        from zerberus.core.spec_check import compute_ambiguity_score
        without_io = compute_ambiguity_score("Schreib mir Python-Code")
        with_io = compute_ambiguity_score(
            "Schreib mir Python-Code mit input string und return list"
        )
        assert with_io < without_io


# ---------------------------------------------------------------------------
# 2) Pure-Function: should_ask_clarification
# ---------------------------------------------------------------------------


class TestShouldAskClarification:
    def test_above_threshold_returns_true(self):
        from zerberus.core.spec_check import should_ask_clarification
        assert should_ask_clarification(0.7, threshold=0.65) is True

    def test_below_threshold_returns_false(self):
        from zerberus.core.spec_check import should_ask_clarification
        assert should_ask_clarification(0.5, threshold=0.65) is False

    def test_exactly_at_threshold_returns_true(self):
        from zerberus.core.spec_check import should_ask_clarification
        assert should_ask_clarification(0.65, threshold=0.65) is True

    def test_invalid_score_returns_false(self):
        from zerberus.core.spec_check import should_ask_clarification
        assert should_ask_clarification("not-a-number") is False  # type: ignore
        assert should_ask_clarification(None) is False  # type: ignore

    def test_custom_threshold(self):
        from zerberus.core.spec_check import should_ask_clarification
        assert should_ask_clarification(0.55, threshold=0.5) is True
        assert should_ask_clarification(0.55, threshold=0.6) is False


# ---------------------------------------------------------------------------
# 3) Pure-Function: build_spec_probe_messages
# ---------------------------------------------------------------------------


class TestBuildSpecProbeMessages:
    def test_returns_list_of_two_dicts(self):
        from zerberus.core.spec_check import build_spec_probe_messages
        msgs = build_spec_probe_messages("foo")
        assert isinstance(msgs, list)
        assert len(msgs) == 2
        assert all(isinstance(m, dict) for m in msgs)

    def test_first_message_is_system(self):
        from zerberus.core.spec_check import build_spec_probe_messages, SPEC_PROBE_SYSTEM
        msgs = build_spec_probe_messages("foo")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == SPEC_PROBE_SYSTEM

    def test_second_message_is_user_with_safe_content(self):
        from zerberus.core.spec_check import build_spec_probe_messages
        msgs = build_spec_probe_messages("Bau mir was")
        assert msgs[1]["role"] == "user"
        # User-Message ist eingebettet
        assert "Bau mir was" in msgs[1]["content"]

    def test_empty_input_does_not_crash(self):
        from zerberus.core.spec_check import build_spec_probe_messages
        msgs = build_spec_probe_messages("")
        assert len(msgs) == 2
        assert msgs[1]["role"] == "user"

    def test_system_prompt_constrains_to_one_question(self):
        from zerberus.core.spec_check import SPEC_PROBE_SYSTEM
        # Sicherstellen dass der System-Prompt das Verhalten festlegt
        assert "EINE" in SPEC_PROBE_SYSTEM or "eine" in SPEC_PROBE_SYSTEM.lower()
        assert "Frage" in SPEC_PROBE_SYSTEM or "frage" in SPEC_PROBE_SYSTEM.lower()


# ---------------------------------------------------------------------------
# 4) Pure-Function: enrich_user_message + build_clarification_block
# ---------------------------------------------------------------------------


class TestEnrichUserMessage:
    def test_marker_present(self):
        from zerberus.core.spec_check import (
            enrich_user_message,
            CLARIFICATION_MARKER_OPEN,
            CLARIFICATION_MARKER_CLOSE,
        )
        out = enrich_user_message("Bau was", "Welche Sprache?", "Python bitte")
        assert CLARIFICATION_MARKER_OPEN in out
        assert CLARIFICATION_MARKER_CLOSE in out

    def test_original_preserved(self):
        from zerberus.core.spec_check import enrich_user_message
        out = enrich_user_message("Bau was", "Welche Sprache?", "Python")
        assert out.startswith("Bau was")

    def test_question_and_answer_in_block(self):
        from zerberus.core.spec_check import enrich_user_message
        out = enrich_user_message("foo", "Was genau?", "Eine Funktion")
        assert "Was genau?" in out
        assert "Eine Funktion" in out

    def test_empty_question_and_answer_returns_original(self):
        from zerberus.core.spec_check import enrich_user_message
        out = enrich_user_message("foo", "", "")
        assert out == "foo"

    def test_only_answer_still_builds_block(self):
        from zerberus.core.spec_check import enrich_user_message
        out = enrich_user_message("foo", "", "trotzdem")
        assert "trotzdem" in out

    def test_build_clarification_block_returns_string(self):
        from zerberus.core.spec_check import build_clarification_block
        block = build_clarification_block("q", "a")
        assert isinstance(block, str)
        assert len(block) > 0


# ---------------------------------------------------------------------------
# 5) Marker-Uniqueness — substring-disjunkt zu existierenden Markern
# ---------------------------------------------------------------------------


class TestMarkerUniqueness:
    def test_clarification_marker_disjoint_from_others(self):
        from zerberus.core.spec_check import (
            CLARIFICATION_MARKER_OPEN,
            CLARIFICATION_MARKER_CLOSE,
        )
        # Existierende Marker aus anderen Patches
        other_markers = [
            "[PROJEKT-RAG",
            "[/PROJEKT-RAG",
            "[PROJEKT-KONTEXT",
            "[/PROJEKT-KONTEXT",
            "[PROSODIE",
            "[/PROSODIE",
            "[CODE-EXECUTION",
            "[/CODE-EXECUTION",
            "[AKTIVE-PERSONA",
        ]
        for marker in other_markers:
            assert marker not in CLARIFICATION_MARKER_OPEN
            assert marker not in CLARIFICATION_MARKER_CLOSE
            assert CLARIFICATION_MARKER_OPEN not in marker
            assert CLARIFICATION_MARKER_CLOSE not in marker

    def test_marker_format_klarstellung(self):
        from zerberus.core.spec_check import (
            CLARIFICATION_MARKER_OPEN,
            CLARIFICATION_MARKER_CLOSE,
        )
        assert CLARIFICATION_MARKER_OPEN == "[KLARSTELLUNG]"
        assert CLARIFICATION_MARKER_CLOSE == "[/KLARSTELLUNG]"


# ---------------------------------------------------------------------------
# 6) Async-Wrapper: run_spec_probe
# ---------------------------------------------------------------------------


class TestRunSpecProbe:
    def test_happy_path_returns_question(self):
        from zerberus.core.spec_check import run_spec_probe

        class FakeLLM:
            async def call(self, messages, session_id, **kwargs):
                return ("Welche Sprache soll der Code haben?", "model", 1, 1, 0.0)

        result = asyncio.run(run_spec_probe(
            "Bau mir was", FakeLLM(), session_id="s1",
        ))
        assert result == "Welche Sprache soll der Code haben?"

    def test_strips_whitespace(self):
        from zerberus.core.spec_check import run_spec_probe

        class FakeLLM:
            async def call(self, messages, session_id, **kwargs):
                return ("  Was genau?  ", "model", 1, 1, 0.0)

        result = asyncio.run(run_spec_probe(
            "x", FakeLLM(), session_id="s1",
        ))
        assert result == "Was genau?"

    def test_empty_response_returns_none(self):
        from zerberus.core.spec_check import run_spec_probe

        class FakeLLM:
            async def call(self, messages, session_id, **kwargs):
                return ("", "model", 1, 1, 0.0)

        result = asyncio.run(run_spec_probe(
            "x", FakeLLM(), session_id="s1",
        ))
        assert result is None

    def test_whitespace_only_response_returns_none(self):
        from zerberus.core.spec_check import run_spec_probe

        class FakeLLM:
            async def call(self, messages, session_id, **kwargs):
                return ("   \n  ", "model", 1, 1, 0.0)

        result = asyncio.run(run_spec_probe(
            "x", FakeLLM(), session_id="s1",
        ))
        assert result is None

    def test_llm_crash_returns_none_fail_open(self):
        from zerberus.core.spec_check import run_spec_probe

        class CrashLLM:
            async def call(self, messages, session_id, **kwargs):
                raise RuntimeError("LLM kaputt")

        result = asyncio.run(run_spec_probe(
            "x", CrashLLM(), session_id="s1",
        ))
        assert result is None

    def test_non_tuple_result_returns_none(self):
        from zerberus.core.spec_check import run_spec_probe

        class WeirdLLM:
            async def call(self, messages, session_id, **kwargs):
                return "not-a-tuple"

        result = asyncio.run(run_spec_probe(
            "x", WeirdLLM(), session_id="s1",
        ))
        assert result is None

    def test_truncates_very_long_question(self):
        from zerberus.core.spec_check import run_spec_probe, SPEC_PROBE_MAX_BYTES

        class LongLLM:
            async def call(self, messages, session_id, **kwargs):
                return ("x" * (SPEC_PROBE_MAX_BYTES + 100), "m", 1, 1, 0.0)

        result = asyncio.run(run_spec_probe(
            "x", LongLLM(), session_id="s1",
        ))
        assert result is not None
        assert len(result.encode("utf-8")) <= SPEC_PROBE_MAX_BYTES


# ---------------------------------------------------------------------------
# 7) ChatSpecGate Unit
# ---------------------------------------------------------------------------


class TestChatSpecGate:
    def _make_gate(self):
        from zerberus.core.spec_check import ChatSpecGate
        return ChatSpecGate()

    def test_create_pending_returns_uuid_and_pending_status(self):
        gate = self._make_gate()
        pending = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            original_message="hi", question="Was?", score=0.7, source="text",
        ))
        assert pending.id and len(pending.id) == 32  # UUID4 hex
        assert pending.status == "pending"
        assert pending.session_id == "s1"
        assert pending.score == 0.7
        assert pending.source == "text"

    def test_to_public_dict_keys(self):
        gate = self._make_gate()
        pending = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="demo",
            original_message="hi", question="Was?", score=0.7, source="voice",
        ))
        d = pending.to_public_dict()
        expected = {
            "id", "session_id", "project_id", "project_slug",
            "original_message", "question", "score", "source",
            "created_at",
        }
        assert set(d.keys()) == expected
        assert d["score"] == 0.7
        assert d["source"] == "voice"

    def test_list_for_session_filters_other_sessions(self):
        gate = self._make_gate()
        p1 = asyncio.run(gate.create_pending(
            session_id="s1", project_id=1, project_slug="d",
            original_message="a", question="?", score=0.7, source="text",
        ))
        asyncio.run(gate.create_pending(
            session_id="s2", project_id=2, project_slug="o",
            original_message="b", question="?", score=0.7, source="text",
        ))
        my = gate.list_for_session("s1")
        assert len(my) == 1
        assert my[0].id == p1.id
        assert gate.list_for_session("") == []

    def test_resolve_answered_with_text(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        ok = asyncio.run(gate.resolve(
            p.id, "answered", answer_text="Python bitte",
        ))
        assert ok is True
        pending = gate.get(p.id)
        assert pending.status == "answered"
        assert pending.answer_text == "Python bitte"

    def test_resolve_answered_without_text_rejected(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        # Leerer answer_text → False
        assert asyncio.run(gate.resolve(p.id, "answered", answer_text="")) is False
        assert asyncio.run(gate.resolve(p.id, "answered", answer_text="   ")) is False
        assert asyncio.run(gate.resolve(p.id, "answered", answer_text=None)) is False
        assert gate.get(p.id).status == "pending"

    def test_resolve_bypassed_no_text_needed(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        ok = asyncio.run(gate.resolve(p.id, "bypassed"))
        assert ok is True
        assert gate.get(p.id).status == "bypassed"

    def test_resolve_cancelled(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        ok = asyncio.run(gate.resolve(p.id, "cancelled"))
        assert ok is True
        assert gate.get(p.id).status == "cancelled"

    def test_resolve_invalid_decision_returns_false(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        # Unbekannte Decision → False
        assert asyncio.run(gate.resolve(p.id, "approved")) is False
        assert asyncio.run(gate.resolve(p.id, "rejected")) is False
        assert gate.get(p.id).status == "pending"

    def test_resolve_session_mismatch_blocks(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        assert asyncio.run(gate.resolve(
            p.id, "bypassed", session_id="s2-attacker",
        )) is False
        assert gate.get(p.id).status == "pending"

    def test_wait_for_decision_resolves_immediately_when_set(self):
        gate = self._make_gate()

        async def scenario():
            p = await gate.create_pending(
                session_id="s1", project_id=None, project_slug=None,
                original_message="x", question="?", score=0.7, source="text",
            )
            await gate.resolve(p.id, "bypassed")
            return await gate.wait_for_decision(p.id, timeout=5)

        assert asyncio.run(scenario()) == "bypassed"

    def test_wait_for_decision_times_out(self):
        gate = self._make_gate()

        async def scenario():
            p = await gate.create_pending(
                session_id="s1", project_id=None, project_slug=None,
                original_message="x", question="?", score=0.7, source="text",
            )
            return await gate.wait_for_decision(p.id, timeout=0.05)

        assert asyncio.run(scenario()) == "timeout"

    def test_cleanup_removes_pending(self):
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        gate.cleanup(p.id)
        assert gate.get(p.id) is None

    def test_answer_text_truncated(self):
        from zerberus.core.spec_check import SPEC_ANSWER_MAX_BYTES
        gate = self._make_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.7, source="text",
        ))
        big = "y" * (SPEC_ANSWER_MAX_BYTES + 500)
        asyncio.run(gate.resolve(p.id, "answered", answer_text=big))
        truncated = gate.get(p.id).answer_text
        assert truncated is not None
        assert len(truncated.encode("utf-8")) <= SPEC_ANSWER_MAX_BYTES


# ---------------------------------------------------------------------------
# 8) Audit-Trail
# ---------------------------------------------------------------------------


class TestStoreClarificationAudit:
    def test_writes_row_with_all_fields(self, tmp_db):
        from zerberus.core.spec_check import store_clarification_audit
        from zerberus.core.database import Clarification
        from sqlalchemy import select

        asyncio.run(store_clarification_audit(
            pending_id="abc123",
            session_id="s-test",
            project_id=7,
            project_slug="demo",
            original_message="bau was",
            question="Welche Sprache?",
            answer_text="Python",
            score=0.78,
            source="voice",
            status="answered",
        ))

        async def query():
            async with tmp_db() as session:
                rows = (await session.execute(select(Clarification))).scalars().all()
                return rows

        rows = asyncio.run(query())
        assert len(rows) == 1
        row = rows[0]
        assert row.pending_id == "abc123"
        assert row.session_id == "s-test"
        assert row.project_id == 7
        assert row.project_slug == "demo"
        assert row.original_message == "bau was"
        assert row.question == "Welche Sprache?"
        assert row.answer_text == "Python"
        assert abs((row.score or 0.0) - 0.78) < 1e-6
        assert row.source == "voice"
        assert row.status == "answered"
        assert row.resolved_at is not None

    def test_truncates_long_message(self, tmp_db):
        from zerberus.core.spec_check import (
            store_clarification_audit,
            AUDIT_MAX_TEXT_BYTES,
        )
        from zerberus.core.database import Clarification
        from sqlalchemy import select

        big = "x" * (AUDIT_MAX_TEXT_BYTES + 2000)
        asyncio.run(store_clarification_audit(
            pending_id=None,
            session_id="s",
            project_id=None,
            project_slug=None,
            original_message=big,
            question=big,
            answer_text=big,
            score=0.9,
            source="text",
            status="bypassed",
        ))

        async def query():
            async with tmp_db() as session:
                rows = (await session.execute(select(Clarification))).scalars().all()
                return rows

        rows = asyncio.run(query())
        assert len(rows) == 1
        row = rows[0]
        # gekuerzt (mit Marker)
        assert "[gekuerzt]" in (row.original_message or "")
        assert "[gekuerzt]" in (row.question or "")
        assert "[gekuerzt]" in (row.answer_text or "")

    def test_silent_skip_without_db_setup(self, monkeypatch):
        import zerberus.core.database as db_mod
        from zerberus.core.spec_check import store_clarification_audit

        # Wenn _async_session_maker None ist, soll der Helper nichts tun
        # (kein Crash, kein Side-Effect).
        monkeypatch.setattr(db_mod, "_async_session_maker", None)
        # Sollte nicht crashen
        asyncio.run(store_clarification_audit(
            pending_id="x", session_id="s",
            project_id=None, project_slug=None,
            original_message="m", question="q", answer_text=None,
            score=0.7, source="text", status="timeout",
        ))


# ---------------------------------------------------------------------------
# 9) Endpoints — direkte Funktion-Aufrufe
# ---------------------------------------------------------------------------


class TestSpecPollResolveEndpoints:
    def test_poll_returns_none_when_empty(self):
        from zerberus.app.routers.legacy import spec_poll, SpecPollResponse
        req = SimpleNamespace(headers={"X-Session-ID": "s-empty"})
        result = asyncio.run(spec_poll(req))
        assert isinstance(result, SpecPollResponse)
        assert result.pending is None

    def test_poll_returns_pending_for_session(self):
        from zerberus.app.routers.legacy import spec_poll
        from zerberus.core.spec_check import get_chat_spec_gate
        gate = get_chat_spec_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s-poll", project_id=None, project_slug=None,
            original_message="x", question="Was?", score=0.8, source="text",
        ))
        req = SimpleNamespace(headers={"X-Session-ID": "s-poll"})
        result = asyncio.run(spec_poll(req))
        assert result.pending is not None
        assert result.pending["id"] == p.id

    def test_poll_does_not_leak_other_sessions(self):
        from zerberus.app.routers.legacy import spec_poll
        from zerberus.core.spec_check import get_chat_spec_gate
        gate = get_chat_spec_gate()
        asyncio.run(gate.create_pending(
            session_id="s-other", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.8, source="text",
        ))
        req = SimpleNamespace(headers={"X-Session-ID": "s-mine"})
        result = asyncio.run(spec_poll(req))
        assert result.pending is None

    def test_resolve_answered_happy(self):
        from zerberus.app.routers.legacy import spec_resolve, SpecResolveRequest
        from zerberus.core.spec_check import get_chat_spec_gate
        gate = get_chat_spec_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.8, source="text",
        ))
        req = SpecResolveRequest(
            pending_id=p.id, decision="answered",
            session_id="s1", answer_text="Python",
        )
        result = asyncio.run(spec_resolve(
            req, SimpleNamespace(headers={"X-Session-ID": "s1"}),
        ))
        assert result.ok is True
        assert result.decision == "answered"
        assert gate.get(p.id).answer_text == "Python"

    def test_resolve_bypassed(self):
        from zerberus.app.routers.legacy import spec_resolve, SpecResolveRequest
        from zerberus.core.spec_check import get_chat_spec_gate
        gate = get_chat_spec_gate()
        p = asyncio.run(gate.create_pending(
            session_id="s1", project_id=None, project_slug=None,
            original_message="x", question="?", score=0.8, source="text",
        ))
        req = SpecResolveRequest(
            pending_id=p.id, decision="bypassed", session_id="s1",
        )
        result = asyncio.run(spec_resolve(
            req, SimpleNamespace(headers={"X-Session-ID": "s1"}),
        ))
        assert result.ok is True
        assert gate.get(p.id).status == "bypassed"

    def test_resolve_unknown_id_returns_ok_false(self):
        from zerberus.app.routers.legacy import spec_resolve, SpecResolveRequest
        req = SpecResolveRequest(
            pending_id="nonexistent", decision="bypassed",
            session_id="s1",
        )
        result = asyncio.run(spec_resolve(
            req, SimpleNamespace(headers={}),
        ))
        assert result.ok is False
        assert result.decision is None


# ---------------------------------------------------------------------------
# 10) Source-Audit legacy.py
# ---------------------------------------------------------------------------


class TestLegacySourceAudit:
    @pytest.fixture
    def src(self):
        path = ROOT / "zerberus" / "app" / "routers" / "legacy.py"
        return path.read_text(encoding="utf-8")

    def test_logging_tag_present(self, src):
        assert "[SPEC-208]" in src

    def test_imports_spec_check_module(self, src):
        assert "from zerberus.core.spec_check import" in src
        assert "compute_ambiguity_score" in src
        assert "should_ask_clarification" in src
        assert "run_spec_probe" in src
        assert "enrich_user_message" in src
        assert "get_chat_spec_gate" in src

    def test_audit_helper_imported(self, src):
        assert "store_clarification_audit" in src

    def test_endpoint_routes_registered(self, src):
        assert "/spec/poll" in src
        assert "/spec/resolve" in src

    def test_pydantic_models_exported(self, src):
        assert "class SpecResolveRequest" in src
        assert "class SpecPollResponse" in src
        assert "class SpecResolveResponse" in src

    def test_score_computed_with_source_kwarg(self, src):
        # source-Kwarg muss durchgereicht werden (text/voice-Disjunktion)
        assert "compute_ambiguity_score(" in src
        assert "source=spec_source_value" in src

    def test_threshold_read_from_settings(self, src):
        assert "spec_check_threshold" in src

    def test_timeout_read_from_settings(self, src):
        assert "spec_check_timeout_seconds" in src

    def test_enabled_flag_check(self, src):
        assert "spec_check_enabled" in src

    def test_cancelled_branch_early_returns(self, src):
        # Der cancelled-Pfad muss eine Hinweis-Antwort liefern und NICHT
        # den teuren LLM-Call durchlaufen.
        assert "spec-cancelled" in src
        # Hinweis-Text muss im Source stehen
        assert "verworfen" in src.lower() or "abgebrochen" in src.lower()

    def test_answered_branch_enriches_message(self, src):
        assert "enrich_user_message(" in src
        # req.messages muss aktualisiert werden, damit messages_for_llm
        # den enriched Text sieht
        assert "_m.content = last_user_msg" in src or "m.content = last_user_msg" in src

    def test_voice_source_detected_from_prosody_header(self, src):
        # Source-Detection nutzt die P204-Header
        assert "X-Prosody-Context" in src
        assert "X-Prosody-Consent" in src

    def test_audit_call_for_non_cancelled(self, src):
        # Bei answered/bypassed/timeout/error muss am Ende
        # store_clarification_audit aufgerufen werden
        assert "store_clarification_audit(" in src


# ---------------------------------------------------------------------------
# 11) Source-Audit nala.py
# ---------------------------------------------------------------------------


class TestNalaSourceAudit:
    @pytest.fixture
    def src(self):
        path = ROOT / "zerberus" / "app" / "routers" / "nala.py"
        return path.read_text(encoding="utf-8")

    def test_js_functions_defined(self, src):
        assert "function startSpecPolling(" in src
        assert "function renderSpecCard(" in src
        assert "async function resolveSpecPending(" in src
        assert "function clearSpecState(" in src
        assert "function stopSpecPolling(" in src

    def test_resolve_post_to_correct_endpoint(self, src):
        assert "'/v1/spec/resolve'" in src

    def test_poll_get_to_correct_endpoint(self, src):
        assert "'/v1/spec/poll'" in src

    def test_send_message_starts_spec_polling(self, src):
        assert "clearSpecState();" in src
        assert "startSpecPolling(" in src

    def test_css_classes_present(self, src):
        for cls in [
            ".spec-card",
            ".spec-card-header",
            ".spec-question",
            ".spec-answer-input",
            ".spec-answer-btn",
            ".spec-bypass-btn",
            ".spec-cancel-btn",
            ".spec-resolved",
            ".spec-card.spec-answered",
            ".spec-card.spec-bypassed",
            ".spec-card.spec-cancelled",
        ]:
            assert cls in src, f"CSS-Klasse {cls} fehlt"

    def test_44px_touch_target(self, src):
        # Min 44x44 fuer alle Buttons
        match = re.search(
            r"\.spec-actions\s+button\s*\{[^}]*min-height:\s*44px[^}]*\}",
            src, re.DOTALL,
        )
        assert match is not None, "44px min-height fehlt fuer .spec-actions button"

    def test_escape_html_used_in_render(self, src):
        # Die renderSpecCard-Funktion muss escapeHtml fuer User-/LLM-Text
        # nutzen — entweder via escapeHtml() oder textContent (auch sicher).
        # Wir suchen die Render-Funktion und checken dass mindestens eines
        # der beiden Pattern auftaucht.
        match = re.search(
            r"function renderSpecCard\(.*?\n\s*\}\n", src, re.DOTALL,
        )
        assert match is not None, "renderSpecCard-Funktion nicht gefunden"
        body = match.group(0)
        # Original-Message + Question via textContent (XSS-safe by default)
        assert "textContent" in body, (
            "renderSpecCard muss textContent fuer User-Strings nutzen"
        )
        # innerHTML fuer Header darf nur escaped Werte enthalten
        if "innerHTML" in body:
            assert "escapeHtml(" in body, (
                "innerHTML im Render-Body braucht escapeHtml-Aufruf"
            )

    def test_textarea_for_answer(self, src):
        # Mehrzeilige Antwort → textarea, nicht input
        assert "createElement('textarea')" in src

    def test_three_decision_buttons(self, src):
        # answered / bypassed / cancelled
        assert "'answered'" in src
        assert "'bypassed'" in src
        assert "'cancelled'" in src

    def test_card_persists_after_click_audit_trail(self, src):
        # Post-Klick-State-Klassen
        assert "spec-answered" in src
        assert "spec-bypassed" in src
        assert "spec-cancelled" in src


# ---------------------------------------------------------------------------
# 12) End-to-End — chat_completions mit Spec-Probe-Pfad
# ---------------------------------------------------------------------------


def _make_two_step_llm(probe_question: str, final_answer: str):
    """Zwei-Step-LLM: erster Call ist Probe, zweiter Call ist Haupt-Antwort."""
    calls = {"n": 0}

    async def fake_call(self, messages, session_id,
                        model_override=None, temperature_override=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return (probe_question, "probe-model", 1, 1, 0.0)
        return (final_answer, "main-model", 1, 1, 0.0)

    return fake_call, calls


class TestE2ESpecCheck:
    def test_non_ambig_message_skips_probe(self, env, monkeypatch):
        """Klare Message → kein Probe-Call → direkte Antwort."""
        from zerberus.app.routers import legacy
        from zerberus.app.routers.legacy import (
            chat_completions, ChatCompletionRequest, Message,
        )
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService

        clear_msg = (
            "Schreibe eine Python-Funktion namens parse_csv mit "
            "Parameter path: str und return List[dict] als Output. "
            "Bitte mit Tests."
        )
        # Mock LLM — nur ein Call (Haupt-Pfad)
        calls = {"n": 0}

        async def fake_call(self, messages, session_id, **kwargs):
            calls["n"] += 1
            return ("Hier ist die Funktion: ...", "main-model", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)

        req = SimpleNamespace(
            state=SimpleNamespace(profile_name="alice", permission_level="admin"),
            headers={"X-Session-ID": "s-clear"},
        )
        chat_req = ChatCompletionRequest(
            messages=[Message(role="user", content=clear_msg)]
        )
        settings = get_settings()
        result = asyncio.run(chat_completions(req, chat_req, settings))
        # Ein Call → kein Probe-Pfad
        assert calls["n"] == 1
        assert "Funktion" in result.choices[0].message.content

    def test_ambig_message_with_bypassed_decision(self, env, monkeypatch):
        """Ambige Message → Probe → Bypassed → Haupt-LLM mit Original."""
        from zerberus.app.routers import legacy
        from zerberus.app.routers.legacy import (
            chat_completions, ChatCompletionRequest, Message,
        )
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService
        from zerberus.core.spec_check import get_chat_spec_gate

        # Zwei-Step-LLM: Probe + Haupt
        fake_call, call_counter = _make_two_step_llm(
            probe_question="Welche Programmiersprache?",
            final_answer="Bypass-Antwort",
        )
        monkeypatch.setattr(LLMService, "call", fake_call)

        # Resolver-Task: wartet kurz, dann bypass
        async def resolver():
            await asyncio.sleep(0.1)
            gate = get_chat_spec_gate()
            for _ in range(40):
                pendings = gate.list_for_session("s-ambig")
                if pendings:
                    await gate.resolve(pendings[0].id, "bypassed")
                    return
                await asyncio.sleep(0.05)

        async def scenario():
            req = SimpleNamespace(
                state=SimpleNamespace(profile_name="alice", permission_level="admin"),
                headers={"X-Session-ID": "s-ambig"},
            )
            chat_req = ChatCompletionRequest(
                messages=[Message(role="user", content="bau das")]
            )
            settings = get_settings()
            resolver_task = asyncio.create_task(resolver())
            try:
                result = await chat_completions(req, chat_req, settings)
            finally:
                resolver_task.cancel()
            return result

        result = asyncio.run(scenario())
        # Zwei Calls: Probe + Haupt
        assert call_counter["n"] == 2
        assert "Bypass-Antwort" in result.choices[0].message.content

    def test_ambig_message_with_answered_enriches_prompt(self, env, monkeypatch):
        """Ambige Message → Probe → Answered → Haupt-LLM sieht Klarstellung."""
        from zerberus.app.routers.legacy import (
            chat_completions, ChatCompletionRequest, Message,
        )
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService
        from zerberus.core.spec_check import get_chat_spec_gate

        seen_messages = []
        calls = {"n": 0}

        async def fake_call(self, messages, session_id, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return ("Welche Sprache?", "probe-model", 1, 1, 0.0)
            seen_messages.append([dict(m) for m in messages])
            return ("Antwort mit Klarstellung", "main-model", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)

        async def resolver():
            await asyncio.sleep(0.1)
            gate = get_chat_spec_gate()
            for _ in range(40):
                pendings = gate.list_for_session("s-answered")
                if pendings:
                    await gate.resolve(
                        pendings[0].id, "answered",
                        answer_text="Python bitte",
                    )
                    return
                await asyncio.sleep(0.05)

        async def scenario():
            req = SimpleNamespace(
                state=SimpleNamespace(profile_name="alice", permission_level="admin"),
                headers={"X-Session-ID": "s-answered"},
            )
            chat_req = ChatCompletionRequest(
                messages=[Message(role="user", content="bau das")]
            )
            settings = get_settings()
            resolver_task = asyncio.create_task(resolver())
            try:
                result = await chat_completions(req, chat_req, settings)
            finally:
                resolver_task.cancel()
            return result

        result = asyncio.run(scenario())
        assert calls["n"] == 2
        # Haupt-Call sah die Klarstellung
        assert seen_messages, "Haupt-LLM-Call wurde nicht erfasst"
        last_user = next(
            (m for m in reversed(seen_messages[0]) if m["role"] == "user"),
            None,
        )
        assert last_user is not None
        assert "[KLARSTELLUNG]" in last_user["content"]
        assert "Python bitte" in last_user["content"]

    def test_ambig_message_with_cancelled_skips_main_llm(self, env, monkeypatch):
        """Ambige Message → Probe → Cancelled → Hinweis-Antwort, kein Haupt-Call."""
        from zerberus.app.routers.legacy import (
            chat_completions, ChatCompletionRequest, Message,
        )
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService
        from zerberus.core.spec_check import get_chat_spec_gate

        calls = {"n": 0}

        async def fake_call(self, messages, session_id, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return ("Welche Sprache?", "probe-model", 1, 1, 0.0)
            return ("SHOULD-NOT-RUN", "main-model", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)

        async def resolver():
            await asyncio.sleep(0.1)
            gate = get_chat_spec_gate()
            for _ in range(40):
                pendings = gate.list_for_session("s-cancel")
                if pendings:
                    await gate.resolve(pendings[0].id, "cancelled")
                    return
                await asyncio.sleep(0.05)

        async def scenario():
            req = SimpleNamespace(
                state=SimpleNamespace(profile_name="alice", permission_level="admin"),
                headers={"X-Session-ID": "s-cancel"},
            )
            chat_req = ChatCompletionRequest(
                messages=[Message(role="user", content="bau das")]
            )
            settings = get_settings()
            resolver_task = asyncio.create_task(resolver())
            try:
                result = await chat_completions(req, chat_req, settings)
            finally:
                resolver_task.cancel()
            return result

        result = asyncio.run(scenario())
        # Nur Probe lief, Haupt wurde geskippt
        assert calls["n"] == 1
        assert result.model == "spec-cancelled"
        # Hinweis-Text fuer User
        assert "verworfen" in result.choices[0].message.content.lower() or \
               "abgebrochen" in result.choices[0].message.content.lower() or \
               "genauer" in result.choices[0].message.content.lower()

    def test_disabled_flag_skips_spec_check(self, env, monkeypatch):
        """spec_check_enabled=False → kein Probe, direkter LLM-Call."""
        from zerberus.app.routers.legacy import (
            chat_completions, ChatCompletionRequest, Message,
        )
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService

        calls = {"n": 0}

        async def fake_call(self, messages, session_id, **kwargs):
            calls["n"] += 1
            return ("direkt", "main-model", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)

        settings = get_settings()
        # Setting via monkeypatch
        monkeypatch.setattr(settings.projects, "spec_check_enabled", False)

        req = SimpleNamespace(
            state=SimpleNamespace(profile_name="alice", permission_level="admin"),
            headers={"X-Session-ID": "s-disabled"},
        )
        chat_req = ChatCompletionRequest(
            messages=[Message(role="user", content="bau das")]
        )
        result = asyncio.run(chat_completions(req, chat_req, settings))
        # Nur Haupt-LLM, kein Probe
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 13) JS-Syntax-Integrity (analog P206/P207)
# ---------------------------------------------------------------------------


class TestJsSyntaxIntegrity:
    def test_node_check_passes_on_inline_scripts(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node nicht im PATH — JS-Integrity-Check uebersprungen")
        from zerberus.app.routers.nala import NALA_HTML
        scripts = re.findall(r"<script>(.*?)</script>", NALA_HTML, re.DOTALL)
        assert len(scripts) > 0, "kein inline <script> in NALA_HTML gefunden"
        for i, script in enumerate(scripts):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8",
            ) as f:
                f.write(script)
                tmpname = f.name
            try:
                r = subprocess.run(
                    [node, "--check", tmpname],
                    capture_output=True, text=True, timeout=15,
                )
                assert r.returncode == 0, (
                    f"node --check fail in NALA inline <script> #{i}: "
                    f"stderr={r.stderr!r}"
                )
            finally:
                Path(tmpname).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 14) Smoke
# ---------------------------------------------------------------------------


class TestSmoke:
    def test_config_flags_present(self):
        from zerberus.core.config import ProjectsConfig
        cfg = ProjectsConfig()
        assert hasattr(cfg, "spec_check_enabled")
        assert hasattr(cfg, "spec_check_threshold")
        assert hasattr(cfg, "spec_check_timeout_seconds")
        assert isinstance(cfg.spec_check_enabled, bool)
        assert isinstance(cfg.spec_check_threshold, float)
        assert isinstance(cfg.spec_check_timeout_seconds, int)
        # Default-Werte plausibel
        assert 0.0 <= cfg.spec_check_threshold <= 1.0
        assert cfg.spec_check_timeout_seconds > 0

    def test_clarifications_table_exists(self, tmp_db):
        from zerberus.core.database import Clarification
        cols = {c.name for c in Clarification.__table__.columns}
        expected = {
            "id", "pending_id", "session_id", "project_id", "project_slug",
            "original_message", "question", "answer_text", "score", "source",
            "status", "created_at", "resolved_at",
        }
        assert expected.issubset(cols), f"fehlende Spalten: {expected - cols}"

    def test_endpoints_registered(self):
        from zerberus.app.routers.legacy import router
        paths = {r.path for r in router.routes}
        # Router hat prefix="/v1" — die Routes laufen mit /v1/-Prefix.
        assert "/v1/spec/poll" in paths
        assert "/v1/spec/resolve" in paths

    def test_singleton_module_exports(self):
        from zerberus.core import spec_check
        assert hasattr(spec_check, "compute_ambiguity_score")
        assert hasattr(spec_check, "should_ask_clarification")
        assert hasattr(spec_check, "build_spec_probe_messages")
        assert hasattr(spec_check, "run_spec_probe")
        assert hasattr(spec_check, "enrich_user_message")
        assert hasattr(spec_check, "ChatSpecGate")
        assert hasattr(spec_check, "ChatSpecPending")
        assert hasattr(spec_check, "get_chat_spec_gate")
        assert hasattr(spec_check, "reset_chat_spec_gate")
        assert hasattr(spec_check, "store_clarification_audit")
