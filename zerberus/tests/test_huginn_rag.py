"""Patch 178 — Tests fuer Huginns RAG-Integration mit Category-Filter.

Prueft:
- RAG-Lookup wird vor dem LLM-Call aufgerufen
- Category-Filter (Default: nur ``system``-Chunks erreichen den LLM-Kontext)
- Datenschutz: ``personal``/``narrative``/etc. werden hart gefiltert
- Konfigurierbarkeit: ``rag_enabled=false`` schaltet den Pfad ab
- Konfigurierbarkeit: ``rag_allowed_categories`` erweitert den Filter
- Graceful Degradation: RAG-Exception bricht den LLM-Call NICHT ab
- Leeres RAG-Ergebnis: LLM wird trotzdem aufgerufen (Fastlane-Fallback)
- System-Wissens-Block taucht im an das LLM gesendeten Prompt auf
"""
import asyncio

import pytest

from zerberus.modules.telegram import router as telegram_router
from zerberus.modules.telegram.bot import HuginnConfig


def _reset_state():
    telegram_router._group_manager = None
    telegram_router._hitl_manager = None
    telegram_router._bot_user_id = None
    from zerberus.core import rate_limiter as rl_module
    rl_module._reset_rate_limiter_for_tests()


class _Settings:
    def __init__(self, telegram_overrides=None, rag_enabled=True):
        tg = {"enabled": True}
        if telegram_overrides:
            tg.update(telegram_overrides)
        self.modules = {
            "telegram": tg,
            "rag": {"enabled": rag_enabled},
        }


def _info(text="Was ist Zerberus?"):
    return {
        "chat_id": 100, "message_id": 1, "user_id": 99, "username": "tester",
        "text": text, "chat_type": "private", "is_forwarded": False,
        "reply_to_message": None, "photo_file_ids": [], "message_thread_id": None,
    }


def _patch_telegram_io(monkeypatch, llm_response="Krraa! Antwort."):
    sends: list[str] = []
    llm_calls: list[dict] = []

    async def fake_send(token, chat_id, text, **kwargs):
        sends.append(text)
        return True

    async def fake_call_llm(**kw):
        llm_calls.append(kw)
        return {"content": llm_response, "latency_ms": 5}

    async def fake_run_guard(user_msg, assistant_msg, caller_context=""):
        return {"verdict": "OK"}

    monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)
    monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
    monkeypatch.setattr(telegram_router, "_run_guard", fake_run_guard)
    return sends, llm_calls


# ──────────────────────────────────────────────────────────────────────
#  TestRagLookupFunction — direkt auf _huginn_rag_lookup
# ──────────────────────────────────────────────────────────────────────


class TestRagLookupFunction:
    """Direkt-Tests auf ``_huginn_rag_lookup`` ohne Telegram-Flow."""

    def test_disabled_returns_empty(self, monkeypatch):
        """``rag_enabled=False`` → kein Lookup, kein Aufruf von _search_index."""
        called = {"flag": False}

        def fake_search(*a, **kw):
            called["flag"] = True
            return []

        monkeypatch.setattr(
            "zerberus.modules.rag.router._search_index", fake_search,
        )
        settings = _Settings(telegram_overrides={"rag_enabled": False})
        result = asyncio.run(telegram_router._huginn_rag_lookup("Test", settings))
        assert result == ""
        assert called["flag"] is False

    def test_rag_module_disabled_returns_empty(self):
        """``modules.rag.enabled=False`` → leerer String, keine Exception."""
        settings = _Settings(rag_enabled=False)
        result = asyncio.run(telegram_router._huginn_rag_lookup("Test", settings))
        assert result == ""

    def test_empty_query_returns_empty(self):
        settings = _Settings()
        result = asyncio.run(telegram_router._huginn_rag_lookup("", settings))
        assert result == ""
        result = asyncio.run(telegram_router._huginn_rag_lookup("   ", settings))
        assert result == ""

    def test_only_system_chunks_pass_filter(self, monkeypatch):
        """Mock-Search liefert system + personal + narrative; nur system kommt durch."""
        async def fake_init(settings):
            return None

        def fake_encode(text):
            import numpy as np
            return np.zeros((1, 384), dtype="float32")

        def fake_search(vec, top_k):
            return [
                {"text": "Zerberus ist ein KI-System.", "category": "system"},
                {"text": "Chris hat Geburtstag im Maerz.",  "category": "personal"},
                {"text": "Es war einmal ein Drache.",       "category": "narrative"},
                {"text": "Architektur: FastAPI + SQLite.",  "category": "system"},
            ]

        monkeypatch.setattr("zerberus.modules.rag.router._ensure_init", fake_init)
        monkeypatch.setattr("zerberus.modules.rag.router._encode", fake_encode)
        monkeypatch.setattr("zerberus.modules.rag.router._search_index", fake_search)
        monkeypatch.setattr("zerberus.modules.rag.router.RAG_AVAILABLE", True)

        settings = _Settings()
        result = asyncio.run(telegram_router._huginn_rag_lookup("Was ist Zerberus", settings))
        assert "Zerberus ist ein KI-System." in result
        assert "Architektur: FastAPI + SQLite." in result
        assert "Geburtstag" not in result
        assert "Drache" not in result

    def test_no_system_chunks_returns_empty(self, monkeypatch):
        """Wenn nur personal/narrative-Chunks zurueckkommen, leerer Output."""
        async def fake_init(settings):
            return None

        def fake_encode(text):
            import numpy as np
            return np.zeros((1, 384), dtype="float32")

        def fake_search(vec, top_k):
            return [
                {"text": "Chris hat Geburtstag.", "category": "personal"},
                {"text": "Es war einmal.",        "category": "narrative"},
            ]

        monkeypatch.setattr("zerberus.modules.rag.router._ensure_init", fake_init)
        monkeypatch.setattr("zerberus.modules.rag.router._encode", fake_encode)
        monkeypatch.setattr("zerberus.modules.rag.router._search_index", fake_search)
        monkeypatch.setattr("zerberus.modules.rag.router.RAG_AVAILABLE", True)

        settings = _Settings()
        result = asyncio.run(telegram_router._huginn_rag_lookup("Erzaehl von Chris", settings))
        assert result == ""

    def test_custom_allowed_categories(self, monkeypatch):
        """``rag_allowed_categories=['system', 'reference']`` laesst auch reference durch."""
        async def fake_init(settings):
            return None

        def fake_encode(text):
            import numpy as np
            return np.zeros((1, 384), dtype="float32")

        def fake_search(vec, top_k):
            return [
                {"text": "System-Chunk.",     "category": "system"},
                {"text": "Reference-Chunk.",  "category": "reference"},
                {"text": "Personal-Chunk.",   "category": "personal"},
            ]

        monkeypatch.setattr("zerberus.modules.rag.router._ensure_init", fake_init)
        monkeypatch.setattr("zerberus.modules.rag.router._encode", fake_encode)
        monkeypatch.setattr("zerberus.modules.rag.router._search_index", fake_search)
        monkeypatch.setattr("zerberus.modules.rag.router.RAG_AVAILABLE", True)

        settings = _Settings(telegram_overrides={
            "rag_allowed_categories": ["system", "reference"],
        })
        result = asyncio.run(telegram_router._huginn_rag_lookup("foo", settings))
        assert "System-Chunk." in result
        assert "Reference-Chunk." in result
        assert "Personal-Chunk." not in result

    def test_exception_returns_empty(self, monkeypatch):
        """Exception im RAG-Stack → leerer String, kein Re-Raise."""
        async def fake_init(settings):
            raise RuntimeError("FAISS kaputt")

        monkeypatch.setattr("zerberus.modules.rag.router._ensure_init", fake_init)
        monkeypatch.setattr("zerberus.modules.rag.router.RAG_AVAILABLE", True)

        settings = _Settings()
        result = asyncio.run(telegram_router._huginn_rag_lookup("foo", settings))
        assert result == ""

    def test_rag_unavailable_returns_empty(self, monkeypatch):
        monkeypatch.setattr("zerberus.modules.rag.router.RAG_AVAILABLE", False)
        settings = _Settings()
        result = asyncio.run(telegram_router._huginn_rag_lookup("foo", settings))
        assert result == ""


# ──────────────────────────────────────────────────────────────────────
#  TestInjectRagContext — Prompt-Bauklotz
# ──────────────────────────────────────────────────────────────────────


class TestInjectRagContext:
    def test_empty_context_unchanged(self):
        prompt = "Du bist Huginn."
        result = telegram_router._inject_rag_context(prompt, "")
        assert result == prompt

    def test_none_prompt_with_context(self):
        result = telegram_router._inject_rag_context(None, "FAKT.")
        assert "FAKT." in result
        assert "Systemwissen" in result

    def test_block_appended(self):
        prompt = "Du bist Huginn."
        ctx = "Zerberus ist ein KI-System."
        result = telegram_router._inject_rag_context(prompt, ctx)
        assert result.startswith(prompt)
        assert "Zerberus ist ein KI-System." in result
        assert "--- Systemwissen" in result
        assert "Ende Systemwissen" in result


# ──────────────────────────────────────────────────────────────────────
#  TestProcessTextMessageRagFlow — End-to-End durch _process_text_message
# ──────────────────────────────────────────────────────────────────────


class TestProcessTextMessageRagFlow:
    """Verifiziert dass RAG-Kontext im LLM-System-Prompt landet."""

    def test_system_chunks_reach_llm_prompt(self, monkeypatch):
        _reset_state()
        sends, llm_calls = _patch_telegram_io(
            monkeypatch,
            llm_response='{"intent": "CHAT", "effort": 2, "needs_hitl": false}\nAlles klar.',
        )

        async def fake_lookup(query, settings):
            return "Zerberus ist Patch 178. 965 Tests gruen."

        monkeypatch.setattr(telegram_router, "_huginn_rag_lookup", fake_lookup)

        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")
        result = asyncio.run(telegram_router._process_text_message(
            _info("Was ist Zerberus?"), cfg, _Settings(), system_prompt="Persona-Text",
        ))
        assert result["sent"] is True
        assert len(llm_calls) == 1
        sp = llm_calls[0].get("system_prompt", "")
        assert "Zerberus ist Patch 178." in sp
        assert "965 Tests gruen." in sp
        assert "Persona-Text" in sp
        assert "Systemwissen" in sp

    def test_empty_rag_context_falls_back_to_persona(self, monkeypatch):
        """Kein RAG-Kontext → Persona-Prompt unveraendert (kein Block angehaengt)."""
        _reset_state()
        sends, llm_calls = _patch_telegram_io(
            monkeypatch,
            llm_response='{"intent": "CHAT", "effort": 2, "needs_hitl": false}\nOk.',
        )

        async def fake_lookup(query, settings):
            return ""

        monkeypatch.setattr(telegram_router, "_huginn_rag_lookup", fake_lookup)

        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")
        result = asyncio.run(telegram_router._process_text_message(
            _info("Hi"), cfg, _Settings(), system_prompt="Persona-Pur",
        ))
        assert result["sent"] is True
        sp = llm_calls[0].get("system_prompt", "")
        assert "Systemwissen" not in sp
        assert "Persona-Pur" in sp

    def test_rag_exception_does_not_block_llm(self, monkeypatch):
        """RAG wirft → LLM wird trotzdem aufgerufen, User bekommt Antwort."""
        _reset_state()
        sends, llm_calls = _patch_telegram_io(
            monkeypatch,
            llm_response='{"intent": "CHAT", "effort": 2, "needs_hitl": false}\nFunktioniert.',
        )

        async def fake_lookup(query, settings):
            # Wir simulieren das End-Verhalten: bei Exception liefert
            # _huginn_rag_lookup intern einen leeren String. Dieser Test
            # haelt das Vertragsverhalten fest — der Caller sieht nur "".
            return ""

        monkeypatch.setattr(telegram_router, "_huginn_rag_lookup", fake_lookup)

        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")
        result = asyncio.run(telegram_router._process_text_message(
            _info("Test"), cfg, _Settings(), system_prompt="P",
        ))
        assert result["sent"] is True
        assert len(llm_calls) == 1

    def test_personal_chunks_never_reach_llm_via_default_filter(self, monkeypatch):
        """Datenschutz-Test: ``personal`` darf nicht in den LLM-Prompt rutschen.

        Wir mocken den vollen RAG-Stack (ensure_init/encode/search), damit der
        Category-Filter wirklich greift. Der LLM-Call darf KEINEN Inhalt der
        personal-Chunks im System-Prompt haben.
        """
        _reset_state()
        sends, llm_calls = _patch_telegram_io(
            monkeypatch,
            llm_response='{"intent": "CHAT", "effort": 2, "needs_hitl": false}\nOk.',
        )

        async def fake_init(settings):
            return None

        def fake_encode(text):
            import numpy as np
            return np.zeros((1, 384), dtype="float32")

        def fake_search(vec, top_k):
            return [
                {"text": "Chris hat geheime Notiz: PIN 4711.", "category": "personal"},
                {"text": "Tagebuch: gestern war schwer.",       "category": "personal"},
                {"text": "Roman-Kapitel: Der Drache erwachte.", "category": "narrative"},
            ]

        monkeypatch.setattr("zerberus.modules.rag.router._ensure_init", fake_init)
        monkeypatch.setattr("zerberus.modules.rag.router._encode", fake_encode)
        monkeypatch.setattr("zerberus.modules.rag.router._search_index", fake_search)
        monkeypatch.setattr("zerberus.modules.rag.router.RAG_AVAILABLE", True)

        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")
        result = asyncio.run(telegram_router._process_text_message(
            _info("Erzaehl mir was ueber Chris"), cfg, _Settings(),
            system_prompt="Persona",
        ))
        assert result["sent"] is True
        sp = llm_calls[0].get("system_prompt", "")
        assert "PIN 4711" not in sp
        assert "Tagebuch" not in sp
        assert "Drache erwachte" not in sp
        assert "Systemwissen" not in sp

    def test_rag_disabled_skips_lookup(self, monkeypatch):
        """``rag_enabled=False`` → _huginn_rag_lookup wird zwar aufgerufen,
        liefert aber sofort leeren String, LLM-Prompt enthaelt keinen Block."""
        _reset_state()
        sends, llm_calls = _patch_telegram_io(
            monkeypatch,
            llm_response='{"intent": "CHAT", "effort": 2, "needs_hitl": false}\nFastlane.',
        )

        # Hier KEIN Mock auf _huginn_rag_lookup — wir wollen den echten Code-Pfad
        # mit deaktiviertem Flag treffen.
        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")
        settings = _Settings(telegram_overrides={"rag_enabled": False})
        result = asyncio.run(telegram_router._process_text_message(
            _info("Was ist Zerberus"), cfg, settings, system_prompt="Persona",
        ))
        assert result["sent"] is True
        sp = llm_calls[0].get("system_prompt", "")
        assert "Systemwissen" not in sp
