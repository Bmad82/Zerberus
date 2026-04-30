"""Unit-Tests fuer den Ach-laber-doch-nicht-Guard — Patch 120.

Kein pytest-asyncio im venv, async-Funktionen via asyncio.run.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from zerberus import hallucination_guard as hg


# ---------------------------------------------------------------------------
# Dummy-httpx.AsyncClient
# ---------------------------------------------------------------------------

class _MockResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    def json(self):
        return self._payload


class _MockClient:
    """Ersetzt httpx.AsyncClient(...). response_or_exc: MockResponse oder Exception."""

    def __init__(self, response_or_exc):
        self._r = response_or_exc
        self.last_payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.last_payload = json
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


def _mk_choice(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSkipShortResponses:
    def test_skip_below_min_tokens(self):
        short = "Ja."  # 1 Wort, ~1.3 Tokens
        result = asyncio.run(hg.check_response("Was ist 1+1?", short))
        assert result["verdict"] == "SKIP"
        assert "zu kurz" in result["reason"].lower()
        assert result["latency_ms"] == 0

    def test_skip_just_below_threshold(self):
        # 36 Woerter ≈ 48 Tokens → unter 50 → SKIP
        resp = " ".join(["wort"] * 36)
        result = asyncio.run(hg.check_response("Frage?", resp))
        assert result["verdict"] == "SKIP"


class TestMissingApiKey:
    def test_error_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        long_resp = " ".join(["wort"] * 80)
        result = asyncio.run(hg.check_response("Frage?", long_resp))
        assert result["verdict"] == "ERROR"
        assert "API-Key" in result["reason"]


class TestVerdictParsing:
    LONG = " ".join(["wort"] * 80)  # ~107 Tokens

    def test_ok_verdict(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(
            200, _mk_choice('{"verdict": "OK", "reason": "Keine Auffaelligkeiten."}')
        )
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: _MockClient(mock_resp)):
            result = asyncio.run(hg.check_response("Frage?", self.LONG))
        assert result["verdict"] == "OK"
        assert result["reason"] == "Keine Auffaelligkeiten."
        assert result["latency_ms"] >= 0

    def test_warnung_verdict(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(
            200,
            _mk_choice('{"verdict": "WARNUNG", "reason": "Erfundene Zahl 42."}'),
        )
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: _MockClient(mock_resp)):
            result = asyncio.run(hg.check_response("Frage?", self.LONG))
        assert result["verdict"] == "WARNUNG"
        assert "42" in result["reason"]

    def test_verdict_mit_markdown_fences(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(
            200,
            _mk_choice('```json\n{"verdict": "OK", "reason": "OK."}\n```'),
        )
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: _MockClient(mock_resp)):
            result = asyncio.run(hg.check_response("Frage?", self.LONG))
        assert result["verdict"] == "OK"

    def test_malformed_json(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(200, _mk_choice("das ist kein json"))
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: _MockClient(mock_resp)):
            result = asyncio.run(hg.check_response("Frage?", self.LONG))
        assert result["verdict"] == "ERROR"
        assert "JSON-Parse" in result["reason"]


class TestHttpErrors:
    LONG = " ".join(["wort"] * 80)

    def test_http_500(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(500, {"error": "server fail"})
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: _MockClient(mock_resp)):
            result = asyncio.run(hg.check_response("Frage?", self.LONG))
        assert result["verdict"] == "ERROR"
        assert "500" in result["reason"]

    def test_timeout(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: _MockClient(httpx.TimeoutException("slow"))):
            result = asyncio.run(hg.check_response("Frage?", self.LONG))
        assert result["verdict"] == "ERROR"
        assert "Timeout" in result["reason"]

    def test_generic_exception(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: _MockClient(RuntimeError("irgendwas"))):
            result = asyncio.run(hg.check_response("Frage?", self.LONG))
        assert result["verdict"] == "ERROR"
        assert result["latency_ms"] >= 0


class TestRagContextInPrompt:
    def test_rag_context_lands_im_user_prompt(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(200, _mk_choice('{"verdict": "OK", "reason": "ok"}'))
        client = _MockClient(mock_resp)
        with patch("zerberus.hallucination_guard.httpx.AsyncClient", lambda *a, **kw: client):
            asyncio.run(hg.check_response(
                "Frage?",
                " ".join(["wort"] * 80),
                rag_context="WICHTIG: Anne war 2015 in Backnang",
            ))
        sent_messages = client.last_payload["messages"]
        user_msg = next(m for m in sent_messages if m["role"] == "user")
        assert "KONTEXT" in user_msg["content"]
        assert "Backnang" in user_msg["content"]


class TestModuleConstants:
    def test_model_ist_mistral_small(self):
        assert "mistral" in hg.GUARD_MODEL.lower()

    def test_openrouter_url(self):
        assert hg.OPENROUTER_URL.startswith("https://openrouter.ai/")

    def test_fail_open_ist_default(self):
        # Keine hartcodierte "STRICT"-Flag — fail-open ist Doku, nicht Test
        assert hg.GUARD_TIMEOUT >= 5


# ---------------------------------------------------------------------------
# Patch 158 — caller_context im Guard-Prompt
# ---------------------------------------------------------------------------

class TestCallerContextInGuardPrompt:
    """Patch 158: Der Guard bekommt optional einen `caller_context` ueber den
    Antwortenden. Damit erkennt er Persona-Elemente (Huginn-Rabe) und
    Selbstreferenzen aufs Zerberus-System als legitim, nicht als Halluzination.
    """

    LONG = " ".join(["wort"] * 80)

    def test_caller_context_landet_im_system_prompt(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(200, _mk_choice('{"verdict": "OK", "reason": "ok"}'))
        client = _MockClient(mock_resp)
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: client):
            asyncio.run(hg.check_response(
                "Frage?",
                self.LONG,
                caller_context="Der Antwortende ist Huginn, ein Rabe im Zerberus-System.",
            ))
        sys_msg = next(m for m in client.last_payload["messages"] if m["role"] == "system")
        assert "Huginn" in sys_msg["content"]
        assert "Rabe" in sys_msg["content"]
        assert "Zerberus" in sys_msg["content"]
        assert "KEINE Halluzinationen" in sys_msg["content"]

    def test_empty_caller_context_aendert_prompt_nicht(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(200, _mk_choice('{"verdict": "OK", "reason": "ok"}'))
        client = _MockClient(mock_resp)
        with patch("zerberus.hallucination_guard.httpx.AsyncClient",
                   lambda *a, **kw: client):
            asyncio.run(hg.check_response("Frage?", self.LONG))  # kein caller_context
        sys_msg = next(m for m in client.last_payload["messages"] if m["role"] == "system")
        # Ohne Kontext darf der Prompt den Kontext-Block NICHT enthalten.
        assert sys_msg["content"] == hg.GUARD_SYSTEM_PROMPT

    def test_build_system_prompt_pure(self):
        # Reiner Unit-Test auf den Prompt-Builder — kein httpx noetig.
        bare = hg._build_system_prompt()
        assert bare == hg.GUARD_SYSTEM_PROMPT
        with_ctx = hg._build_system_prompt("Persona-Text XYZ")
        assert "XYZ" in with_ctx
        assert with_ctx.startswith(hg.GUARD_SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# Patch 158 — Huginn-Guard-Verhalten (integration mit Telegram-Router)
# ---------------------------------------------------------------------------

class TestHuginnGuardBehavior:
    """Patch 158: Bei Guard-WARNUNG wird die Antwort trotzdem gesendet; der
    Admin bekommt nur noch einen Hinweis. Frueher wurde die Antwort
    komplett unterdrueckt."""

    def test_process_text_message_sendet_trotz_warnung(self, monkeypatch):
        """Mock alles weg — wir wollen nur sehen, dass send_telegram_message
        auf den User-Chat aufgerufen wird, selbst wenn der Guard WARNUNG sagt.
        """
        import zerberus.modules.telegram.router as router_mod
        from zerberus.modules.telegram.bot import HuginnConfig

        calls = []

        async def _fake_send(bot_token, chat_id, text, **kw):
            calls.append({"chat_id": chat_id, "text": text})
            return True

        async def _fake_call_llm(*args, **kwargs):
            return {"content": "KRRAA! Der Rabe antwortet.", "latency_ms": 1}

        async def _fake_guard(user_msg, assistant_msg, caller_context="", rag_context=""):
            return {"verdict": "WARNUNG", "reason": "Testgrund"}

        monkeypatch.setattr(router_mod, "send_telegram_message", _fake_send)
        monkeypatch.setattr(router_mod, "call_llm", _fake_call_llm)
        monkeypatch.setattr(router_mod, "_run_guard", _fake_guard)

        cfg = HuginnConfig(
            enabled=True,
            bot_token="T",
            admin_chat_id="999",
            allowed_group_ids=[],
            model="deepseek/deepseek-chat",
        )
        settings = SimpleNamespace(modules={"telegram": {}})
        info = {
            "chat_id": 42,
            "text": "Hallo",
            "message_id": 1,
            "username": "test",
        }
        result = asyncio.run(
            router_mod._process_text_message(info, cfg, settings)
        )
        # Antwort ging raus …
        assert result["sent"] is True
        # … sowohl an den User-Chat als auch an den Admin.
        chat_ids = [c["chat_id"] for c in calls]
        assert 42 in chat_ids, f"User-Chat 42 muss in {chat_ids} sein"
        assert "999" in chat_ids, f"Admin-Chat 999 muss in {chat_ids} sein"
        admin_call = next(c for c in calls if c["chat_id"] == "999")
        assert "Guard-Hinweis" in admin_call["text"]

    def test_process_text_message_sendet_bei_ok_ohne_admin_ping(self, monkeypatch):
        import zerberus.modules.telegram.router as router_mod
        from zerberus.modules.telegram.bot import HuginnConfig

        calls = []

        async def _fake_send(bot_token, chat_id, text, **kw):
            calls.append(chat_id)
            return True

        async def _fake_call_llm(*args, **kwargs):
            return {"content": "Antwort.", "latency_ms": 1}

        async def _fake_guard(user_msg, assistant_msg, caller_context="", rag_context=""):
            return {"verdict": "OK", "reason": "sauber"}

        monkeypatch.setattr(router_mod, "send_telegram_message", _fake_send)
        monkeypatch.setattr(router_mod, "call_llm", _fake_call_llm)
        monkeypatch.setattr(router_mod, "_run_guard", _fake_guard)

        cfg = HuginnConfig(
            enabled=True,
            bot_token="T",
            admin_chat_id="999",
            allowed_group_ids=[],
            model="deepseek/deepseek-chat",
        )
        settings = SimpleNamespace(modules={"telegram": {}})
        info = {"chat_id": 42, "text": "Hi", "message_id": 1, "username": "t"}
        asyncio.run(router_mod._process_text_message(info, cfg, settings))
        # Nur ein Call — an den User. Admin wird bei OK nicht geweckt.
        assert calls == [42]


class TestHuginnGuardContextBuilder:
    def test_context_enthaelt_persona_auszug(self):
        import zerberus.modules.telegram.router as router_mod

        ctx = router_mod._build_huginn_guard_context("MEIN_PERSONA_MARKER_XYZ")
        assert "MEIN_PERSONA_MARKER_XYZ" in ctx
        assert "Huginn" in ctx
        assert "Zerberus" in ctx

    def test_context_verkraftet_leere_persona(self):
        import zerberus.modules.telegram.router as router_mod

        ctx = router_mod._build_huginn_guard_context("")
        # Auch ohne Persona muss der Kontext noch sinnvoll sein (Raben-Hinweis
        # bleibt, der Auszug ist eben leer).
        assert "Huginn" in ctx
        assert "Zerberus" in ctx


# ---------------------------------------------------------------------------
# Patch 180 — rag_context im System-Prompt (gegen Halluzinations-False-
# Positives auf RAG-basierten Antworten) + Persona-Cap auf 800
# ---------------------------------------------------------------------------


class TestRagContextInSystemPrompt:
    """P180: Der Guard bekommt jetzt zusaetzlich das Referenz-Material, das
    dem Antwortenden zur Verfuegung stand. Vorher flaggte er Antworten, die
    Fakten aus dem RAG-Index zitierten, als Halluzination — er konnte nicht
    wissen, dass das Material echt ist.
    """

    def test_rag_context_landet_im_system_prompt(self):
        prompt = hg._build_system_prompt(
            rag_context="Zerberus ist ein FastAPI-System auf Tailscale.",
        )
        assert "Referenz-Wissen" in prompt
        assert "Zerberus" in prompt
        assert "FastAPI" in prompt
        assert "KEINE Halluzinationen" in prompt
        # Ohne caller_context bleibt der caller-Block weg.
        assert "[Kontext des Antwortenden]" not in prompt

    def test_rag_context_truncation_bei_3000_zeichen(self):
        long_context = "A" * 3000
        prompt = hg._build_system_prompt(rag_context=long_context)
        # Truncation-Marker ist da …
        assert "[... gekuerzt]" in prompt
        # … und der vollstaendige Text steht NICHT drin (nur die ersten 1500).
        assert long_context not in prompt
        # Aber die ersten 1500 Zeichen schon.
        assert "A" * 1500 in prompt

    def test_rag_context_unter_limit_unbeschnitten(self):
        short = "Zerberus + FastAPI + FAISS."
        prompt = hg._build_system_prompt(rag_context=short)
        assert short in prompt
        assert "[... gekuerzt]" not in prompt

    def test_rag_context_leer_kein_block(self):
        prompt = hg._build_system_prompt(rag_context="")
        assert "Referenz-Wissen" not in prompt
        assert prompt == hg.GUARD_SYSTEM_PROMPT

    def test_beide_kontexte_in_richtiger_reihenfolge(self):
        prompt = hg._build_system_prompt(
            caller_context="Huginn ist ein Rabe.",
            rag_context="Zerberus laeuft auf Tailscale.",
        )
        # Reihenfolge: Caller-Block ZUERST, dann Referenz-Wissen — der Guard
        # liest die Persona-Regeln vor dem Faktenmaterial.
        caller_pos = prompt.find("[Kontext des Antwortenden]")
        rag_pos = prompt.find("[Referenz-Wissen")
        assert caller_pos > -1
        assert rag_pos > -1
        assert caller_pos < rag_pos
        assert "Huginn" in prompt
        assert "Tailscale" in prompt

    def test_check_response_reicht_rag_context_an_system_prompt_durch(self, monkeypatch):
        """End-to-End: rag_context-Parameter landet auch im system-message
        (nicht nur im user-prompt wie bisher in P158/P120)."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        mock_resp = _MockResponse(200, _mk_choice('{"verdict": "OK", "reason": "ok"}'))
        client = _MockClient(mock_resp)
        with patch("zerberus.hallucination_guard.httpx.AsyncClient", lambda *a, **kw: client):
            asyncio.run(hg.check_response(
                "Frage?",
                " ".join(["wort"] * 80),
                rag_context="MARKER_RAG_FAKT_XYZ",
            ))
        sys_msg = next(m for m in client.last_payload["messages"] if m["role"] == "system")
        assert "MARKER_RAG_FAKT_XYZ" in sys_msg["content"]
        assert "Referenz-Wissen" in sys_msg["content"]


class TestHuginnGuardContextPersonaBudget:
    """P180 (Block 2): _build_huginn_guard_context schickt jetzt bis zu
    800 Zeichen Persona an den Guard (vorher 300). Der Guard muss die
    vollen Verhaltensregeln kennen, um Persona-Elemente korrekt zu
    erkennen.
    """

    def test_persona_unter_800_zeichen_landet_komplett(self):
        import zerberus.modules.telegram.router as router_mod

        persona = "Huginn-Persona-Marker-" + ("x" * 200)
        ctx = router_mod._build_huginn_guard_context(persona)
        assert persona in ctx
        assert "[... Persona gekuerzt]" not in ctx

    def test_persona_ueber_budget_wird_truncated(self):
        import zerberus.modules.telegram.router as router_mod

        persona = "X" * 5000
        ctx = router_mod._build_huginn_guard_context(persona)
        assert "[... Persona gekuerzt]" in ctx
        # Hartes Limit: insgesamt darf der Kontext ~850 Zeichen nicht
        # ueberschreiten (Budget 800 + Truncation-Marker ~25).
        assert len(ctx) <= 900

    def test_300_zeichen_persona_jetzt_komplett_drin(self):
        # Regression gegen P158 (300-Zeichen-Cap): eine 350-Zeichen-Persona
        # wurde frueher abgeschnitten, jetzt nicht mehr.
        import zerberus.modules.telegram.router as router_mod

        persona = "P158-CAP-MARKER-" + ("y" * 340)
        ctx = router_mod._build_huginn_guard_context(persona)
        assert persona in ctx


class TestRouterPasstRagContextAnGuard:
    """P180: _process_text_message reicht den RAG-Lookup-String an
    _run_guard durch. Vorher rag_context="" → False-Positives.
    """

    def test_run_guard_bekommt_rag_context_aus_lookup(self, monkeypatch):
        import zerberus.modules.telegram.router as router_mod
        from zerberus.modules.telegram.bot import HuginnConfig

        seen = {}

        async def _fake_send(bot_token, chat_id, text, **kw):
            return True

        async def _fake_call_llm(*args, **kwargs):
            return {"content": "Antwort mit RAG-Fakten.", "latency_ms": 1}

        async def _fake_rag_lookup(query, settings):
            return "RAG-CHUNK: Zerberus laeuft auf Tailscale."

        async def _fake_run_guard(user_msg, assistant_msg, caller_context="", rag_context=""):
            seen["rag_context"] = rag_context
            seen["caller_context"] = caller_context
            return {"verdict": "OK", "reason": "ok"}

        monkeypatch.setattr(router_mod, "send_telegram_message", _fake_send)
        monkeypatch.setattr(router_mod, "call_llm", _fake_call_llm)
        monkeypatch.setattr(router_mod, "_huginn_rag_lookup", _fake_rag_lookup)
        monkeypatch.setattr(router_mod, "_run_guard", _fake_run_guard)

        cfg = HuginnConfig(
            enabled=True,
            bot_token="T",
            admin_chat_id="999",
            allowed_group_ids=[],
            model="deepseek/deepseek-chat",
        )
        settings = SimpleNamespace(modules={"telegram": {}})
        info = {"chat_id": 42, "text": "Was ist Zerberus?", "message_id": 1, "username": "t"}
        asyncio.run(router_mod._process_text_message(info, cfg, settings))

        assert "Tailscale" in seen.get("rag_context", "")
        assert "Huginn" in seen.get("caller_context", "")
