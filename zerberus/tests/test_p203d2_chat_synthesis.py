"""Patch 203d-2 (Phase 5a #5) — Tests fuer Output-Synthese im Chat.

Schichten:

1. **Pure-Function-Tests** ueber ``zerberus.modules.sandbox.synthesis``:
   - ``should_synthesize`` Trigger-Gate
   - ``_truncate`` Bytes-genau
   - ``build_synthesis_messages`` deterministisch
   - ``SYNTH_LOG_TAG`` und Konstanten
2. **Async-Wrapper-Tests** ueber ``synthesize_code_output``:
   - Happy-Path mit Fake-LLM
   - Fail-Open bei Crash, leerem Output, falschem Format
   - Skip wenn Trigger nicht zustimmt
3. **End-to-End ueber ``chat_completions``**:
   - LLM gibt Code-Block, Sandbox liefert stdout, Synthese ersetzt answer
   - exit_code != 0 → Synthese erklaert Fehler
   - Synthese-LLM crashed → Original-Answer bleibt (fail-open)
   - Kein Code-Block → keine Synthese
   - Code-Block + leerer Output → keine Synthese (skip)
4. **Source-Audit** auf legacy.py:
   - Synthese-Import vorhanden
   - Synthese wird zwischen P203d-1-Block und Assistant-Insert gerufen
   - ``store_interaction("assistant", ...)`` ist NACH dem Synthese-Block
   - Marker ``[SYNTH-203d-2]`` vorhanden

Mock-Pattern: angelehnt an ``test_p203d_chat_sandbox.py`` — fake LLM via
``LLMService.call``-monkeypatch, Sandbox-Pfad gemockt, ``execute_in_workspace``
gemockt.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Pure-Function-Tests — should_synthesize
# ---------------------------------------------------------------------------


class TestShouldSynthesize:
    """Trigger-Gate testen — keine Synthese fuer skip-Faelle."""

    def test_none_returns_false(self):
        from zerberus.modules.sandbox.synthesis import should_synthesize
        assert should_synthesize(None) is False

    def test_non_dict_returns_false(self):
        from zerberus.modules.sandbox.synthesis import should_synthesize
        assert should_synthesize("not-a-dict") is False
        assert should_synthesize(42) is False
        assert should_synthesize([{"exit_code": 0}]) is False

    def test_missing_exit_code_returns_false(self):
        from zerberus.modules.sandbox.synthesis import should_synthesize
        assert should_synthesize({"stdout": "x"}) is False

    def test_exit_code_none_returns_false(self):
        from zerberus.modules.sandbox.synthesis import should_synthesize
        assert should_synthesize({"exit_code": None, "stdout": "x"}) is False

    def test_exit_zero_with_empty_stdout_returns_false(self):
        from zerberus.modules.sandbox.synthesis import should_synthesize
        assert should_synthesize({"exit_code": 0, "stdout": ""}) is False
        assert should_synthesize({"exit_code": 0, "stdout": "   \n  "}) is False
        assert should_synthesize({"exit_code": 0}) is False

    def test_exit_zero_with_stdout_returns_true(self):
        from zerberus.modules.sandbox.synthesis import should_synthesize
        assert should_synthesize({"exit_code": 0, "stdout": "42\n"}) is True

    def test_exit_nonzero_returns_true_even_with_empty_stderr(self):
        """Crash ohne stderr (Sandbox-Timeout o.ae.) — trotzdem Synthese."""
        from zerberus.modules.sandbox.synthesis import should_synthesize
        assert should_synthesize({"exit_code": 1, "stdout": "", "stderr": ""}) is True

    def test_exit_nonzero_with_stderr_returns_true(self):
        from zerberus.modules.sandbox.synthesis import should_synthesize
        payload = {"exit_code": 7, "stdout": "", "stderr": "ZeroDivisionError"}
        assert should_synthesize(payload) is True


# ---------------------------------------------------------------------------
# Pure-Function-Tests — _truncate
# ---------------------------------------------------------------------------


class TestTruncate:

    def test_short_text_returns_unchanged(self):
        from zerberus.modules.sandbox.synthesis import _truncate
        assert _truncate("hallo") == "hallo"

    def test_empty_text_returns_unchanged(self):
        from zerberus.modules.sandbox.synthesis import _truncate
        assert _truncate("") == ""

    def test_at_limit_returns_unchanged(self):
        from zerberus.modules.sandbox.synthesis import _truncate
        text = "a" * 5000  # exakt 5000 Bytes
        assert _truncate(text, limit=5000) == text

    def test_over_limit_truncates_with_marker(self):
        from zerberus.modules.sandbox.synthesis import _truncate, TRUNCATED_MARKER
        text = "x" * 6000
        out = _truncate(text, limit=100)
        assert out.endswith(TRUNCATED_MARKER)
        # Kern (ohne Marker) ist <= 100 Bytes
        body = out[: -len(TRUNCATED_MARKER)]
        assert len(body.encode("utf-8")) <= 100

    def test_multibyte_truncate_does_not_crash(self):
        """Truncate mitten in einem Multi-Byte-UTF-8-Zeichen darf nicht crashen."""
        from zerberus.modules.sandbox.synthesis import _truncate, TRUNCATED_MARKER
        # Jeder Eintrag ist 3 Bytes UTF-8 (CJK-Char), Limit 4 Bytes → schneidet
        # mitten im zweiten Zeichen ab.
        text = "字" * 100
        out = _truncate(text, limit=4)
        assert out.endswith(TRUNCATED_MARKER)


# ---------------------------------------------------------------------------
# Pure-Function-Tests — build_synthesis_messages
# ---------------------------------------------------------------------------


class TestBuildSynthesisMessages:

    def _payload(self, **overrides):
        base = {
            "language": "python",
            "code": "print(2+2)",
            "exit_code": 0,
            "stdout": "4\n",
            "stderr": "",
            "execution_time_ms": 12,
            "truncated": False,
            "error": None,
        }
        base.update(overrides)
        return base

    def test_returns_list_of_two_messages(self):
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages("Was ist 2+2?", self._payload())
        assert isinstance(msgs, list)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_user_msg_contains_original_prompt(self):
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages("Frage X", self._payload())
        assert "Frage X" in msgs[1]["content"]

    def test_user_msg_contains_code_block(self):
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages("?", self._payload(code="print(2+2)"))
        body = msgs[1]["content"]
        assert "```python" in body
        assert "print(2+2)" in body

    def test_user_msg_contains_stdout_when_present(self):
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages("?", self._payload(stdout="4\n"))
        assert "stdout:" in msgs[1]["content"]
        assert "4\n" in msgs[1]["content"]

    def test_user_msg_omits_stdout_section_when_empty(self):
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages(
            "?",
            self._payload(stdout="", stderr="boom", exit_code=1),
        )
        # stderr ist da, stdout nicht
        assert "stderr:" in msgs[1]["content"]
        assert "stdout:" not in msgs[1]["content"]

    def test_user_msg_contains_exit_code_in_marker(self):
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages("?", self._payload(exit_code=7))
        assert "exit_code: 7" in msgs[1]["content"]

    def test_user_msg_marker_disjoint_from_other_bridges(self):
        """Marker ``[CODE-EXECUTION]`` darf nicht substring-uebrlappen mit
        den anderen LLM-Brueckenmarkern (P199 PROJEKT-RAG, P197 PROJEKT-
        KONTEXT, P204 PROSODIE)."""
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages("?", self._payload())
        body = msgs[1]["content"]
        assert "[CODE-EXECUTION" in body
        assert "[/CODE-EXECUTION]" in body
        # Disjunktheit zu anderen Brueckenmarkern
        assert "PROJEKT-RAG" not in "[CODE-EXECUTION]"
        assert "PROJEKT-KONTEXT" not in "[CODE-EXECUTION]"
        assert "PROSODIE" not in "[CODE-EXECUTION]"

    def test_system_prompt_says_no_floskeln(self):
        """Der System-Prompt soll explizit gegen Code-Wiederholung
        argumentieren — Format-Konsistenz Test."""
        from zerberus.modules.sandbox.synthesis import build_synthesis_messages
        msgs = build_synthesis_messages("?", self._payload())
        sys = msgs[0]["content"].lower()
        assert "wiederhole" in sys
        assert "menschenlesbar" in sys

    def test_truncates_huge_stdout(self):
        """Mega-Output (>5KB) wird vor dem Embedding in den Prompt gekuerzt."""
        from zerberus.modules.sandbox.synthesis import (
            build_synthesis_messages,
            TRUNCATED_MARKER,
        )
        huge = "x" * 10000
        msgs = build_synthesis_messages("?", self._payload(stdout=huge))
        assert TRUNCATED_MARKER in msgs[1]["content"]
        # Aber Body insgesamt ist drastisch kuerzer als 10000+x
        assert len(msgs[1]["content"]) < 8000


# ---------------------------------------------------------------------------
# Async-Wrapper-Tests — synthesize_code_output
# ---------------------------------------------------------------------------


class TestSynthesizeCodeOutput:

    def _good_payload(self):
        return {
            "language": "python",
            "code": "print(2+2)",
            "exit_code": 0,
            "stdout": "4\n",
            "stderr": "",
            "execution_time_ms": 12,
            "truncated": False,
            "error": None,
        }

    def _crash_payload(self):
        return {
            "language": "python",
            "code": "1/0",
            "exit_code": 1,
            "stdout": "",
            "stderr": "ZeroDivisionError",
            "execution_time_ms": 5,
            "truncated": False,
            "error": None,
        }

    def _fake_llm(self, answer="Das Ergebnis ist 4."):
        class _FakeLLM:
            async def call(self, messages, session_id, **kwargs):
                self.last_messages = messages
                self.last_session = session_id
                return (answer, "fake-model", 5, 8, 0.0)
        return _FakeLLM()

    def test_skip_when_payload_is_none(self):
        from zerberus.modules.sandbox.synthesis import synthesize_code_output
        llm = self._fake_llm()
        result = asyncio.run(synthesize_code_output("Frage", None, llm, "s1"))
        assert result is None

    def test_skip_when_exit0_and_no_stdout(self):
        from zerberus.modules.sandbox.synthesis import synthesize_code_output
        llm = self._fake_llm()
        payload = self._good_payload()
        payload["stdout"] = ""
        result = asyncio.run(synthesize_code_output("Frage", payload, llm, "s1"))
        assert result is None

    def test_happy_path_returns_synthesized_text(self):
        from zerberus.modules.sandbox.synthesis import synthesize_code_output
        llm = self._fake_llm(answer="Das Ergebnis ist 4.")
        result = asyncio.run(
            synthesize_code_output("Was ist 2+2?", self._good_payload(), llm, "s1")
        )
        assert result == "Das Ergebnis ist 4."
        # LLM wurde mit dem User-Prompt gerufen
        user_msg = next(m for m in llm.last_messages if m["role"] == "user")
        assert "Was ist 2+2?" in user_msg["content"]

    def test_synthesis_runs_on_nonzero_exit(self):
        from zerberus.modules.sandbox.synthesis import synthesize_code_output
        llm = self._fake_llm(answer="Du teilst durch null.")
        result = asyncio.run(
            synthesize_code_output("Berechne 1/0", self._crash_payload(), llm, "s1")
        )
        assert result == "Du teilst durch null."

    def test_fail_open_when_llm_raises(self):
        from zerberus.modules.sandbox.synthesis import synthesize_code_output

        class _BoomLLM:
            async def call(self, messages, session_id, **kwargs):
                raise RuntimeError("LLM-Backend tot")

        result = asyncio.run(
            synthesize_code_output("?", self._good_payload(), _BoomLLM(), "s1")
        )
        assert result is None

    def test_fail_open_when_llm_returns_empty_string(self):
        from zerberus.modules.sandbox.synthesis import synthesize_code_output
        llm = self._fake_llm(answer="")
        result = asyncio.run(
            synthesize_code_output("?", self._good_payload(), llm, "s1")
        )
        assert result is None

    def test_fail_open_when_llm_returns_whitespace_only(self):
        from zerberus.modules.sandbox.synthesis import synthesize_code_output
        llm = self._fake_llm(answer="   \n   ")
        result = asyncio.run(
            synthesize_code_output("?", self._good_payload(), llm, "s1")
        )
        assert result is None

    def test_fail_open_when_llm_returns_non_tuple(self):
        """LLM-Service-API ist 5-Tuple. Falls jemand das aufweicht, defense-
        in-depth: kein Crash."""
        from zerberus.modules.sandbox.synthesis import synthesize_code_output

        class _WeirdLLM:
            async def call(self, messages, session_id, **kwargs):
                return None

        result = asyncio.run(
            synthesize_code_output("?", self._good_payload(), _WeirdLLM(), "s1")
        )
        assert result is None


# ---------------------------------------------------------------------------
# Source-Audit — legacy.py-Verdrahtung
# ---------------------------------------------------------------------------


class TestP203d2SourceAudit:

    def _src(self):
        return (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(
            encoding="utf-8"
        )

    def _synthesis_src(self):
        return (
            ROOT / "zerberus" / "modules" / "sandbox" / "synthesis.py"
        ).read_text(encoding="utf-8")

    def test_synthesis_module_exists_and_exports_helpers(self):
        from zerberus.modules.sandbox import synthesis
        assert callable(synthesis.should_synthesize)
        assert callable(synthesis.build_synthesis_messages)
        assert callable(synthesis.synthesize_code_output)
        assert hasattr(synthesis, "SYNTH_LOG_TAG")
        assert synthesis.SYNTH_LOG_TAG == "[SYNTH-203d-2]"

    def test_legacy_imports_synthesize_code_output(self):
        """Verdrahtung: legacy.py muss synthesize_code_output rufen."""
        assert "synthesize_code_output" in self._src()

    def test_legacy_has_synth_log_tag_for_failopen(self):
        """Fail-Open-Pfad logged mit dem disjunkten Tag."""
        assert "[SYNTH-203d-2]" in self._src()

    def test_legacy_synth_call_passes_user_prompt_and_payload(self):
        """Source-Audit: der Aufruf muss user_prompt + payload + llm_service
        + session_id durchreichen."""
        src = self._src()
        # Suchfenster: zwischen [SANDBOX-203d] und der Sentiment-Stelle
        idx = src.find("synthesize_code_output(")
        assert idx > 0
        window = src[idx:idx + 600]
        assert "user_prompt=" in window
        assert "payload=" in window
        assert "llm_service=" in window
        assert "session_id=" in window

    def test_assistant_store_interaction_after_synthesis(self):
        """Defense: ``store_interaction("assistant", answer, ...)`` muss
        NACH dem Synthese-Aufruf passieren, damit der gespeicherte Text
        der finale Output ist (nicht der Roh-Output mit Code-Block).

        Pattern: synthesize_code_output(...)-Position MUSS vor
        store_interaction("assistant", ...)-Position liegen."""
        src = self._src()
        synth_idx = src.find("synthesize_code_output(")
        # Es gibt mehrere store_interaction-Aufrufe — wir brauchen den
        # mit "assistant" als ersten Argument im chat_completions-Block.
        assistant_store_idx = src.find('store_interaction("assistant", answer')
        assert synth_idx > 0, "Synthese-Aufruf nicht gefunden"
        assert assistant_store_idx > 0, "Assistant-Store nicht gefunden"
        assert synth_idx < assistant_store_idx, (
            "Reihenfolge-Bruch: store_interaction(assistant) muss NACH der "
            "Synthese passieren"
        )

    def test_user_store_interaction_before_sandbox_block(self):
        """User-Insert ist getrennt vom Assistant-Insert — User passiert
        FRUEH, Assistant SPAET. Das stellt sicher, dass auch bei einem
        Synthese-Crash zumindest die User-Eingabe in der DB landet."""
        src = self._src()
        user_store_idx = src.find('store_interaction("user", last_user_msg')
        sandbox_block_idx = src.find("[SANDBOX-203d]")
        assert user_store_idx > 0
        assert sandbox_block_idx > 0
        assert user_store_idx < sandbox_block_idx

    def test_synthesis_module_has_truncate_limit_constant(self):
        """Defense gegen unbeabsichtigtes Aufweichen: SYNTH_MAX_OUTPUT_BYTES
        ist als Konstante im Modul definiert."""
        from zerberus.modules.sandbox.synthesis import SYNTH_MAX_OUTPUT_BYTES
        assert isinstance(SYNTH_MAX_OUTPUT_BYTES, int)
        assert SYNTH_MAX_OUTPUT_BYTES > 0


# ---------------------------------------------------------------------------
# E2E — chat_completions mit Synthese
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch):
    """Frische SQLite-DB pro Test, monkeypatcht das engine-Singleton.

    Pattern aus ``test_p203d_chat_sandbox.py`` uebernommen.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_p203d2.db"
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
    """Settings + chdir + Persona-Files."""
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


def _build_request(project_id, profile_name="alice"):
    state = SimpleNamespace(
        profile_name=profile_name,
        permission_level="admin",
        allowed_model=None,
        temperature=None,
    )
    headers = {"X-Session-ID": "test-session"}
    if project_id is not None:
        headers["X-Active-Project-Id"] = str(project_id)
    return SimpleNamespace(state=state, headers=headers)


def _make_two_step_llm(answers):
    """Fake-LLMService.call der je nach Aufruf-Index unterschiedliche
    Antworten gibt. ``answers[0]`` ist der erste Call (Code-Erzeugung),
    ``answers[1]`` ist der Synthese-Call.
    """
    counter = {"i": 0, "messages": []}

    async def fake_call(self, messages, session_id,
                        model_override=None, temperature_override=None):
        idx = counter["i"]
        counter["messages"].append(messages)
        counter["i"] += 1
        if idx >= len(answers):
            answer = answers[-1]
        else:
            answer = answers[idx]
        return (answer, "test-model", 1, 1, 0.0)

    return fake_call, counter


def _make_fake_sandbox_manager(*, enabled=True,
                                allowed_languages=("python", "javascript")):
    cfg = SimpleNamespace(enabled=enabled, allowed_languages=list(allowed_languages))
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


class TestE2ESynthesis:

    def _setup(self, monkeypatch, *, llm_answers, sandbox_result=None,
               sandbox_enabled=True, allowed_languages=("python", "javascript"),
               execute_raises=None):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService
        from zerberus.core.config import get_settings

        # Patch 206: HitL-Gate ist neuer Default. Synthese-E2E-Tests sind
        # NICHT ueber HitL — wir bypassen das Gate, damit der Sandbox-Pfad
        # weiterhin direkt durchlaeuft (Status ``bypassed`` im Audit).
        monkeypatch.setattr(get_settings().projects, "hitl_enabled", False)
        # Patch 209: Veto-Probe ist neuer Default fuer nicht-triviale Code-
        # Bloecke. Synthese-E2E-Tests sind NICHT ueber Veto — wir
        # deaktivieren den Veto-Pfad, damit der LLM-Call-Counter
        # unveraendert (Code + Synthese = 2 Calls) bleibt.
        monkeypatch.setattr(get_settings().projects, "code_veto_enabled", False)

        fake_call, counter = _make_two_step_llm(llm_answers)
        monkeypatch.setattr(LLMService, "call", fake_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        fake_mgr = _make_fake_sandbox_manager(
            enabled=sandbox_enabled, allowed_languages=allowed_languages,
        )
        monkeypatch.setattr(
            "zerberus.modules.sandbox.manager.get_sandbox_manager",
            lambda: fake_mgr,
        )

        captured = {"sandbox_calls": 0}

        async def fake_execute(*, project_id, code, language, base_dir,
                               writable=False, timeout=None):
            captured["sandbox_calls"] += 1
            if execute_raises is not None:
                raise execute_raises
            return sandbox_result

        monkeypatch.setattr(
            "zerberus.core.projects_workspace.execute_in_workspace",
            fake_execute,
        )

        return counter, captured

    def _create_project(self, **kwargs):
        from zerberus.core.projects_repo import create_project
        return asyncio.run(create_project(**kwargs))

    def _call_endpoint(self, project_id):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings

        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content="Was ist 2+2?")]
        )
        request = _build_request(project_id, profile_name="alice")
        return asyncio.run(legacy_mod.chat_completions(
            request, req, get_settings()
        ))

    # ---- happy paths ------------------------------------------------------

    def test_synthesis_replaces_answer_when_code_executed(self, env, monkeypatch):
        """Code-Block in LLM-Antwort + erfolgreiche Sandbox-Execution
        → Synthese-Call wird gemacht, answer wird ersetzt durch den
        Synthese-Output."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=[
                "```python\nprint(2+2)\n```",       # Erst-Call: Code
                "Das Ergebnis ist 4.",              # Zweit-Call: Synthese
            ],
            sandbox_result=_make_sandbox_result(stdout="4\n", exit_code=0),
        )
        proj = self._create_project(name="P1")
        resp = self._call_endpoint(proj["id"])

        # answer wurde durch Synthese ersetzt
        assert resp.choices[0].message.content == "Das Ergebnis ist 4."
        # zweimal wurde das LLM gerufen
        assert counter["i"] == 2
        # einmal wurde die Sandbox aufgerufen
        assert captured["sandbox_calls"] == 1
        # code_execution-Feld ist da (P203d-1-Backwards-Compat)
        assert resp.code_execution is not None
        assert resp.code_execution["stdout"] == "4\n"

    def test_synthesis_explains_error_on_nonzero_exit(self, env, monkeypatch):
        """exit_code != 0 → Synthese erklaert den Fehler."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=[
                "```python\n1/0\n```",
                "Du teilst durch null — fuege eine Pruefung hinzu.",
            ],
            sandbox_result=_make_sandbox_result(
                stdout="", stderr="ZeroDivisionError: division by zero",
                exit_code=1,
            ),
        )
        proj = self._create_project(name="P-err")
        resp = self._call_endpoint(proj["id"])

        assert "teilst durch null" in resp.choices[0].message.content
        assert counter["i"] == 2
        assert resp.code_execution["exit_code"] == 1

    def test_synthesis_uses_user_prompt(self, env, monkeypatch):
        """Der Synthese-LLM-Call enthaelt die urspruengliche User-Frage."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=[
                "```python\nprint(42)\n```",
                "Antwort.",
            ],
            sandbox_result=_make_sandbox_result(stdout="42\n", exit_code=0),
        )
        proj = self._create_project(name="P-prompt")
        self._call_endpoint(proj["id"])

        # Zweiter Call (Synthese) hat eine User-Message mit "Was ist 2+2?"
        synth_msgs = counter["messages"][1]
        user_msg = next(m for m in synth_msgs if m["role"] == "user")
        assert "Was ist 2+2?" in user_msg["content"]

    # ---- skip cases — kein zweiter LLM-Call ------------------------------

    def test_no_synthesis_without_code_block(self, env, monkeypatch):
        """Plain-Text-Antwort → kein Synthese-Call (nur 1 LLM-Call)."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=["Das Ergebnis ist 4.", "(should never run)"],
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P-nocode")
        resp = self._call_endpoint(proj["id"])

        assert resp.choices[0].message.content == "Das Ergebnis ist 4."
        assert counter["i"] == 1  # nur Erst-Call
        assert captured["sandbox_calls"] == 0
        assert resp.code_execution is None

    def test_no_synthesis_when_exit0_and_empty_stdout(self, env, monkeypatch):
        """Code laeuft erfolgreich aber produziert keine Ausgabe (z.B.
        ``x = 1`` ohne print) → Synthese skipt, answer bleibt Original."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=[
                "```python\nx = 1\n```",
                "(should never run)",
            ],
            sandbox_result=_make_sandbox_result(stdout="", exit_code=0),
        )
        proj = self._create_project(name="P-empty")
        resp = self._call_endpoint(proj["id"])

        # Original-Answer (mit Code-Block) bleibt
        assert "```python" in resp.choices[0].message.content
        # Nur ein LLM-Call (Erstantwort)
        assert counter["i"] == 1
        # code_execution-Feld trotzdem populated (P203d-1-Pfad lief)
        assert resp.code_execution is not None
        assert resp.code_execution["exit_code"] == 0
        assert resp.code_execution["stdout"] == ""

    def test_no_synthesis_when_no_active_project(self, env, monkeypatch):
        """Ohne Projekt-Header → kein Sandbox-Pfad → keine Synthese."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=["```python\nprint(1)\n```", "(never)"],
            sandbox_result=_make_sandbox_result(stdout="1\n"),
        )
        resp = self._call_endpoint(None)

        assert "```python" in resp.choices[0].message.content
        assert counter["i"] == 1
        assert captured["sandbox_calls"] == 0
        assert resp.code_execution is None

    def test_no_synthesis_when_sandbox_disabled(self, env, monkeypatch):
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=["```python\nprint(1)\n```", "(never)"],
            sandbox_enabled=False,
            sandbox_result=_make_sandbox_result(),
        )
        proj = self._create_project(name="P-disabled")
        resp = self._call_endpoint(proj["id"])

        assert counter["i"] == 1
        assert resp.code_execution is None

    # ---- fail-open --------------------------------------------------------

    def test_synthesis_failure_keeps_original_answer(self, env, monkeypatch):
        """Synthese-LLM crasht im zweiten Call → fail-open, Original-Answer
        (mit Code-Block) bleibt + code_execution ist da."""
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService
        from zerberus.core.config import get_settings

        # Patch 206: HitL bypassen — Test ist nicht ueber HitL
        monkeypatch.setattr(get_settings().projects, "hitl_enabled", False)
        # Patch 209: Veto bypassen — Test ist nicht ueber Veto
        monkeypatch.setattr(get_settings().projects, "code_veto_enabled", False)

        # Eigener LLM, der beim zweiten Call crasht.
        counter = {"i": 0}

        async def crashing_call(self, messages, session_id,
                                model_override=None, temperature_override=None):
            counter["i"] += 1
            if counter["i"] == 1:
                return (
                    "```python\nprint(1)\n```",
                    "test-model", 1, 1, 0.0,
                )
            raise RuntimeError("Synthese-Backend tot")

        monkeypatch.setattr(LLMService, "call", crashing_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        fake_mgr = _make_fake_sandbox_manager()
        monkeypatch.setattr(
            "zerberus.modules.sandbox.manager.get_sandbox_manager",
            lambda: fake_mgr,
        )

        async def fake_execute(*, project_id, code, language, base_dir,
                               writable=False, timeout=None):
            return _make_sandbox_result(stdout="1\n", exit_code=0)

        monkeypatch.setattr(
            "zerberus.core.projects_workspace.execute_in_workspace",
            fake_execute,
        )

        proj = self._create_project(name="P-failopen")
        resp = self._call_endpoint(proj["id"])

        # Original-Answer (mit Code-Block) blieb erhalten
        assert "```python" in resp.choices[0].message.content
        # Zweiter Call wurde versucht, aber gecrashed
        assert counter["i"] == 2
        # code_execution ist trotzdem da — Frontend kann den Roh-Output
        # selbst rendern (P203d-3 Fallback)
        assert resp.code_execution is not None
        assert resp.code_execution["stdout"] == "1\n"

    def test_synthesis_returns_empty_keeps_original(self, env, monkeypatch):
        """Wenn der Synthese-LLM einen leeren String zurueckgibt: fail-open,
        Original bleibt."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=[
                "```python\nprint(1)\n```",
                "",  # leer
            ],
            sandbox_result=_make_sandbox_result(stdout="1\n", exit_code=0),
        )
        proj = self._create_project(name="P-empty-synth")
        resp = self._call_endpoint(proj["id"])

        assert "```python" in resp.choices[0].message.content
        assert resp.code_execution is not None

    # ---- backwards-compat -------------------------------------------------

    def test_choices_field_remains_openai_compatible(self, env, monkeypatch):
        """OpenAI-Schema-Felder (choices, model, finish_reason) bleiben
        nach P203d-2 unangetastet."""
        counter, captured = self._setup(
            monkeypatch,
            llm_answers=["```python\nprint(1)\n```", "Synthesized."],
            sandbox_result=_make_sandbox_result(stdout="1\n", exit_code=0),
        )
        proj = self._create_project(name="P-compat")
        resp = self._call_endpoint(proj["id"])

        assert resp.choices
        assert resp.choices[0].message.role == "assistant"
        assert resp.choices[0].finish_reason == "stop"
        assert resp.model == "test-model"
