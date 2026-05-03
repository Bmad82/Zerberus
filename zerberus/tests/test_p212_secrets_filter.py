"""Patch 212 (Phase 5a #12) — Secrets-Filter-Tests.

Deckt ab:

* Pure-Function ``is_secret_key`` / ``extract_secret_values`` /
  ``mask_secrets_in_text``.
* Cache + Reload via ``load_secret_values`` / ``reset_cache_for_tests``.
* Best-Effort-Audit (``store_secret_redaction``).
* Convenience-Wrapper ``mask_and_audit`` + ``mask_and_audit_sync``.
* Source-Audit der Verdrahtung in Sandbox-Manager und Synthese-Modul.
* End-to-End-Pfad: Mock-Sandbox-Result mit Klartext-Secret in stdout →
  maskiert in der Caller-Sicht.
* Smoke (Modul-Exports, DB-Schema, Konstanten).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


# ── Reset-Fixture fuer den Cache ────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_secrets_cache():
    """Vor jedem Test einen frischen Cache, damit Tests sich nicht
    gegenseitig beeinflussen (Singleton-State)."""
    from zerberus.core import secrets_filter
    secrets_filter.reset_cache_for_tests()
    yield
    secrets_filter.reset_cache_for_tests()


# ── Pure-Function: is_secret_key ────────────────────────────────────────


class TestIsSecretKey:
    def test_known_names_match(self):
        from zerberus.core.secrets_filter import is_secret_key
        assert is_secret_key("OPENAI_API_KEY") is True
        assert is_secret_key("OPENROUTER_API_KEY") is True
        assert is_secret_key("ANTHROPIC_API_KEY") is True
        assert is_secret_key("TELEGRAM_BOT_TOKEN") is True
        assert is_secret_key("DATABASE_URL") is True

    def test_suffixes_match(self):
        from zerberus.core.secrets_filter import is_secret_key
        assert is_secret_key("MY_KEY") is True
        assert is_secret_key("foo_secret") is True  # case-insensitive
        assert is_secret_key("SOMETHING_TOKEN") is True
        assert is_secret_key("DB_PASSWORD") is True
        assert is_secret_key("X_PASS") is True
        assert is_secret_key("FOO_PASSPHRASE") is True
        assert is_secret_key("BAR_CREDENTIAL") is True

    def test_prefixes_match(self):
        from zerberus.core.secrets_filter import is_secret_key
        assert is_secret_key("API_BASE_URL") is True
        assert is_secret_key("AUTH_HEADER") is True

    def test_empty_or_none_returns_false(self):
        from zerberus.core.secrets_filter import is_secret_key
        assert is_secret_key("") is False
        assert is_secret_key("   ") is False
        # type: ignore — defensive
        assert is_secret_key(None) is False  # type: ignore[arg-type]

    def test_non_secret_keys_return_false(self):
        from zerberus.core.secrets_filter import is_secret_key
        assert is_secret_key("PATH") is False
        assert is_secret_key("USER") is False
        assert is_secret_key("LOG_LEVEL") is False
        assert is_secret_key("DEBUG") is False
        assert is_secret_key("ENVIRONMENT") is False


# ── Pure-Function: extract_secret_values ────────────────────────────────


class TestExtractSecretValues:
    def test_empty_dict_returns_empty_set(self):
        from zerberus.core.secrets_filter import extract_secret_values
        assert extract_secret_values({}) == set()

    def test_mixed_dict_only_secret_keys_extracted(self):
        from zerberus.core.secrets_filter import extract_secret_values
        env = {
            "OPENAI_API_KEY": "sk-1234567890ABCDEF",
            "PATH": "/usr/bin:/bin",
            "USER": "chris",
            "DB_PASSWORD": "p4ssw0rd-long-enough",
            "LOG_LEVEL": "INFO",
        }
        result = extract_secret_values(env)
        assert result == {"sk-1234567890ABCDEF", "p4ssw0rd-long-enough"}

    def test_short_values_filtered(self):
        from zerberus.core.secrets_filter import extract_secret_values
        env = {
            "OPENAI_API_KEY": "short",  # < 8 chars
            "DB_PASSWORD": "1234567",   # < 8 chars
            "AUTH_TOKEN": "12345678",   # exactly 8 → included
        }
        result = extract_secret_values(env)
        assert result == {"12345678"}

    def test_min_length_override(self):
        from zerberus.core.secrets_filter import extract_secret_values
        env = {"API_KEY": "abc"}
        # min_length=2 erlaubt den Wert
        assert extract_secret_values(env, min_length=2) == {"abc"}

    def test_empty_value_skipped(self):
        from zerberus.core.secrets_filter import extract_secret_values
        env = {"OPENAI_API_KEY": "", "DB_PASSWORD": None, "AUTH_TOKEN": "validvalue"}
        result = extract_secret_values(env)  # type: ignore[arg-type]
        assert result == {"validvalue"}


# ── Pure-Function: mask_secrets_in_text ─────────────────────────────────


class TestMaskSecretsInText:
    def test_empty_text_returns_unchanged(self):
        from zerberus.core.secrets_filter import mask_secrets_in_text
        out, count = mask_secrets_in_text("", {"abc"})
        assert out == ""
        assert count == 0

    def test_no_secrets_returns_unchanged(self):
        from zerberus.core.secrets_filter import mask_secrets_in_text
        out, count = mask_secrets_in_text("hello world", set())
        assert out == "hello world"
        assert count == 0

    def test_single_secret_replaced(self):
        from zerberus.core.secrets_filter import mask_secrets_in_text
        out, count = mask_secrets_in_text(
            "config: token=sk-12345abcdef end", {"sk-12345abcdef"},
        )
        assert "sk-12345abcdef" not in out
        assert "***REDACTED***" in out
        assert count == 1

    def test_multiple_occurrences_counted(self):
        from zerberus.core.secrets_filter import mask_secrets_in_text
        out, count = mask_secrets_in_text("AAA BBB AAA CCC AAA", {"AAA"})
        assert "AAA" not in out
        assert count == 3

    def test_longest_first_invariant(self):
        """Wenn ein Secret 'ABC-LONG' und ein anderes 'ABC' existiert,
        muss erst das laengere maskiert werden — sonst wuerde aus
        'ABC-LONG' --> '***REDACTED***-LONG'."""
        from zerberus.core.secrets_filter import mask_secrets_in_text
        out, _ = mask_secrets_in_text(
            "ABCDEFGH-LONG and ABCDEFGH alone",
            {"ABCDEFGH", "ABCDEFGH-LONG"},
        )
        # Beide Vorkommen sollten ein eigenes ***REDACTED*** werden
        assert out.count("***REDACTED***") == 2
        # Insbesondere darf NICHT '***REDACTED***-LONG' im Output stehen
        assert "***REDACTED***-LONG" not in out

    def test_empty_secret_in_set_skipped(self):
        from zerberus.core.secrets_filter import mask_secrets_in_text
        out, count = mask_secrets_in_text("hello", {"", "  "})
        # Whitespace-Secrets sollten NICHT alles ersetzen
        assert out == "hello"
        assert count == 0

    def test_replacement_text_not_re_replaced(self):
        """Wenn ein Secret zufaellig dem Replacement-Text gleicht, darf
        die Maskierung nicht in einer Endlosschleife landen."""
        from zerberus.core.secrets_filter import (
            mask_secrets_in_text,
            DEFAULT_REPLACEMENT,
        )
        out, count = mask_secrets_in_text(
            f"prefix {DEFAULT_REPLACEMENT} suffix", {DEFAULT_REPLACEMENT},
        )
        # Replacement gleich Secret → wir skippen (sonst Idempotenz-Problem)
        assert out == f"prefix {DEFAULT_REPLACEMENT} suffix"
        assert count == 0

    def test_custom_replacement(self):
        from zerberus.core.secrets_filter import mask_secrets_in_text
        out, count = mask_secrets_in_text(
            "value=secret-12345", {"secret-12345"}, replacement="<HIDDEN>",
        )
        assert out == "value=<HIDDEN>"
        assert count == 1


# ── Cache: load_secret_values ───────────────────────────────────────────


class TestLoadSecretValues:
    def test_uses_custom_env(self):
        from zerberus.core.secrets_filter import load_secret_values
        env = {"API_KEY": "a-very-secret-value-here"}
        result = load_secret_values(env=env, force_reload=True)
        assert "a-very-secret-value-here" in result

    def test_cache_returns_same_snapshot(self):
        from zerberus.core.secrets_filter import load_secret_values
        env1 = {"API_KEY": "first-value-long-enough"}
        first = load_secret_values(env=env1, force_reload=True)
        # Zweiter Aufruf ohne force_reload und mit anderem env → bekommt Cache
        env2 = {"API_KEY": "different-value-long-enough"}
        second = load_secret_values(env=env2)
        assert first == second
        assert "different-value-long-enough" not in second

    def test_force_reload_picks_up_new_env(self):
        from zerberus.core.secrets_filter import load_secret_values
        env1 = {"API_KEY": "first-value-long-enough"}
        load_secret_values(env=env1, force_reload=True)
        env2 = {"API_KEY": "second-value-long-enough"}
        result = load_secret_values(env=env2, force_reload=True)
        assert "second-value-long-enough" in result
        assert "first-value-long-enough" not in result


# ── Audit-Trail ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStoreAudit:
    async def test_zero_count_skips_insert(self):
        """count <= 0 → kein Insert, kein Crash."""
        from zerberus.core.secrets_filter import store_secret_redaction
        # Sollte einfach silent durchlaufen
        await store_secret_redaction(
            redaction_count=0, source="sandbox", session_id="sess-1",
        )
        await store_secret_redaction(
            redaction_count=-3, source="sandbox", session_id="sess-1",
        )

    async def test_audit_silent_when_db_not_initialized(self):
        """Wenn ``_async_session_maker`` is None — kein Crash."""
        from zerberus.core.secrets_filter import store_secret_redaction
        # In Tests ist DB i.d.R. nicht initialisiert → still durchlaufen
        await store_secret_redaction(
            redaction_count=2, source="sandbox", session_id="sess-1",
        )


# ── Convenience: mask_and_audit ─────────────────────────────────────────


@pytest.mark.asyncio
class TestMaskAndAudit:
    async def test_no_secrets_no_change(self):
        from zerberus.core.secrets_filter import mask_and_audit, load_secret_values
        # Cache mit leerem Env vorbelegen
        load_secret_values(env={}, force_reload=True)
        result = await mask_and_audit("hello world", source="sandbox")
        assert result == "hello world"

    async def test_with_secret_replaces_and_audits(self):
        from zerberus.core.secrets_filter import mask_and_audit, load_secret_values
        load_secret_values(
            env={"OPENAI_API_KEY": "sk-the-real-secret-12345"},
            force_reload=True,
        )
        result = await mask_and_audit(
            "echo: sk-the-real-secret-12345 done", source="sandbox",
            session_id="sess-99",
        )
        assert "sk-the-real-secret-12345" not in result
        assert "***REDACTED***" in result

    async def test_empty_text_returns_empty(self):
        from zerberus.core.secrets_filter import mask_and_audit
        assert await mask_and_audit("", source="sandbox") == ""
        assert await mask_and_audit(None, source="sandbox") is None  # type: ignore[arg-type]

    async def test_fail_open_on_load_error(self, monkeypatch):
        """Wenn load_secret_values crasht, kommt der Original-Text zurueck."""
        from zerberus.core import secrets_filter

        def boom(*a, **kw):
            raise RuntimeError("kaputt")

        monkeypatch.setattr(secrets_filter, "load_secret_values", boom)
        result = await secrets_filter.mask_and_audit(
            "hello world", source="sandbox",
        )
        assert result == "hello world"


class TestMaskAndAuditSync:
    def test_no_secrets_returns_text_zero_count(self):
        from zerberus.core.secrets_filter import (
            mask_and_audit_sync, load_secret_values,
        )
        load_secret_values(env={}, force_reload=True)
        out, count = mask_and_audit_sync("foo bar", source="sandbox")
        assert out == "foo bar"
        assert count == 0

    def test_with_secret_replaces(self):
        from zerberus.core.secrets_filter import (
            mask_and_audit_sync, load_secret_values,
        )
        load_secret_values(
            env={"AUTH_TOKEN": "auth-token-very-long-1234"},
            force_reload=True,
        )
        out, count = mask_and_audit_sync(
            "Bearer auth-token-very-long-1234", source="sandbox",
        )
        assert "auth-token-very-long-1234" not in out
        assert count == 1


# ── Verdrahtungs-Source-Audits ───────────────────────────────────────────


SANDBOX_MANAGER_PATH = Path("zerberus/modules/sandbox/manager.py")
SYNTHESIS_PATH = Path("zerberus/modules/sandbox/synthesis.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestSandboxWiring:
    def test_imports_mask_and_audit(self):
        src = _read(SANDBOX_MANAGER_PATH)
        assert "from zerberus.core.secrets_filter import mask_and_audit" in src

    def test_calls_mask_and_audit_with_sandbox_source(self):
        src = _read(SANDBOX_MANAGER_PATH)
        # mind. zwei Aufrufe (stdout + stderr) mit source="sandbox"
        count = src.count('source="sandbox"')
        assert count >= 2, (
            f"Erwarte >=2 Aufrufe von mask_and_audit(source='sandbox') in "
            f"manager.py, fand {count}"
        )


class TestSynthesisWiring:
    def test_imports_mask_and_audit(self):
        src = _read(SYNTHESIS_PATH)
        assert "from zerberus.core.secrets_filter import mask_and_audit" in src

    def test_calls_mask_and_audit_with_synthesis_source(self):
        src = _read(SYNTHESIS_PATH)
        assert 'source="synthesis"' in src

    def test_payload_fields_masked_pre_llm(self):
        """Die drei kritischen Felder code/stdout/stderr werden vor dem
        LLM-Call durchgewaschen."""
        src = _read(SYNTHESIS_PATH)
        # Heuristik: alle drei Feldnamen erscheinen in einem Tuple-Literal
        assert '"stdout"' in src and '"stderr"' in src and '"code"' in src
        # Maskierung MUSS vor dem LLM-Call stehen
        mask_pos = src.find("mask_and_audit")
        llm_pos = src.find("llm_service.call")
        assert 0 < mask_pos < llm_pos, (
            "Maskierungs-Aufruf muss VOR llm_service.call stehen — "
            "sonst sieht das LLM die Klartext-Secrets."
        )


# ── End-to-End: Sandbox + Synthese ──────────────────────────────────────


@pytest.mark.asyncio
class TestSynthesisIntegrationE2E:
    async def test_payload_secrets_masked_before_llm_sees_them(self):
        """Der Synthese-LLM darf den Klartext-Secret nicht sehen."""
        from zerberus.core.secrets_filter import (
            load_secret_values,
            reset_cache_for_tests,
        )
        from zerberus.modules.sandbox.synthesis import synthesize_code_output

        reset_cache_for_tests()
        load_secret_values(
            env={"OPENAI_API_KEY": "sk-leaked-real-secret-XYZ"},
            force_reload=True,
        )

        payload = {
            "language": "python",
            "code": "import os; print(os.environ['OPENAI_API_KEY'])",
            "exit_code": 0,
            "stdout": "sk-leaked-real-secret-XYZ\n",
            "stderr": "",
        }

        captured_messages: list = []

        class _MockLLM:
            async def call(self, messages, session_id):
                captured_messages.append(messages)
                # Returns die OpenAI-tuple-Form
                return ("synth ok", "mock-model", 10, 5, 0.0)

        result = await synthesize_code_output(
            user_prompt="Was kommt da raus?",
            payload=payload,
            llm_service=_MockLLM(),
            session_id="sess-e2e",
        )
        assert result == "synth ok"
        # Der Mock-LLM hat genau eine Conversation gesehen:
        assert len(captured_messages) == 1
        # In dieser Conversation darf der Klartext-Secret NICHT vorkommen
        joined = "\n".join(m["content"] for m in captured_messages[0])
        assert "sk-leaked-real-secret-XYZ" not in joined
        assert "***REDACTED***" in joined
        # Der Payload selbst wurde in-place maskiert
        assert "sk-leaked-real-secret-XYZ" not in payload["stdout"]
        assert "***REDACTED***" in payload["stdout"]

    async def test_synthesis_skips_when_no_payload(self):
        """should_synthesize=False → kein LLM-Call, kein Crash."""
        from zerberus.modules.sandbox.synthesis import synthesize_code_output

        called = False

        class _MockLLM:
            async def call(self, messages, session_id):
                nonlocal called
                called = True
                return ("", "mock", 0, 0, 0.0)

        # exit_code=0 + leerer stdout → skip
        result = await synthesize_code_output(
            user_prompt="?",
            payload={"exit_code": 0, "stdout": "", "stderr": ""},
            llm_service=_MockLLM(),
            session_id="sess",
        )
        assert result is None
        assert called is False


@pytest.mark.asyncio
class TestSandboxIntegrationE2E:
    async def test_mask_and_audit_chain_replaces_secrets(self):
        """End-to-End: Cache mit Secret laden, mask_and_audit auf
        einem Sandbox-aehnlichen stdout-Text → Secret weg."""
        from zerberus.core.secrets_filter import (
            mask_and_audit, load_secret_values, reset_cache_for_tests,
        )
        reset_cache_for_tests()
        load_secret_values(
            env={
                "OPENAI_API_KEY": "sk-AAA-secret-key-XYZ",
                "DB_PASSWORD": "very-secret-password-1",
            },
            force_reload=True,
        )
        stdout = (
            "DEBUG: connecting with key=sk-AAA-secret-key-XYZ\n"
            "DEBUG: db pass=very-secret-password-1\n"
            "result=ok"
        )
        masked = await mask_and_audit(stdout, source="sandbox")
        assert "sk-AAA-secret-key-XYZ" not in masked
        assert "very-secret-password-1" not in masked
        assert masked.count("***REDACTED***") == 2
        assert "result=ok" in masked


# ── Smoke ────────────────────────────────────────────────────────────────


class TestSmoke:
    def test_module_exports(self):
        from zerberus.core import secrets_filter
        for name in (
            "is_secret_key",
            "extract_secret_values",
            "mask_secrets_in_text",
            "load_secret_values",
            "reset_cache_for_tests",
            "store_secret_redaction",
            "mask_and_audit",
            "mask_and_audit_sync",
            "SECRET_KEY_SUFFIXES",
            "SECRET_KEY_PREFIXES",
            "SECRET_KEY_NAMES",
            "DEFAULT_REPLACEMENT",
            "MIN_SECRET_LENGTH",
        ):
            assert hasattr(secrets_filter, name), f"Export fehlt: {name}"

    def test_database_has_audit_table(self):
        from zerberus.core import database
        assert hasattr(database, "SecretRedactionAudit")
        cls = database.SecretRedactionAudit
        assert cls.__tablename__ == "secret_redactions"
        cols = {c.name for c in cls.__table__.columns}
        for required in ("redaction_count", "source", "session_id", "created_at"):
            assert required in cols, f"Spalte fehlt: {required}"

    def test_constants_consistency(self):
        from zerberus.core.secrets_filter import (
            SECRET_KEY_SUFFIXES,
            SECRET_KEY_PREFIXES,
            SECRET_KEY_NAMES,
            DEFAULT_REPLACEMENT,
            MIN_SECRET_LENGTH,
        )
        # Suffixes + Prefixes upper-case
        assert all(s.startswith("_") and s.isupper() for s in SECRET_KEY_SUFFIXES)
        assert all(p.endswith("_") and p.isupper() for p in SECRET_KEY_PREFIXES)
        # Names upper-case
        assert all(n == n.upper() for n in SECRET_KEY_NAMES)
        # Replacement nicht leer + erkennbar
        assert "REDACTED" in DEFAULT_REPLACEMENT
        # Min-Length sinnvoll
        assert MIN_SECRET_LENGTH >= 4
