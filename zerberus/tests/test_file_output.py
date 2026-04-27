"""Patch 168 — Tests fuer Datei-Output, MIME-Whitelist, send_document und
Aufwands-Kalibrierung.

Deckt 17 Faelle aus dem Patch-Spec ab:
1.  Format-Erkennung: FILE + Markdown-Content → ``.md``
2.  Format-Erkennung: CODE + Python-Content → ``.py``
3.  Format-Erkennung: CODE + JavaScript-Content → ``.js``
4.  Format-Erkennung: CODE + unerkannt → ``.txt``
5.  Routing: CHAT + 500 Zeichen → Text (kein Datei)
6.  Routing: CHAT + 3000 Zeichen → Datei als Fallback
7.  Routing: FILE → immer Datei
8.  Routing: CODE → immer Datei
9.  Size-Limit: 11 MB Content → Fehler
10. MIME-Whitelist: ``.py`` erlaubt, ``.exe`` blockiert
11. send_document: API-Call-Format korrekt (multipart, caption, reply_to)
12. send_document: Timeout-Handling
13. Effort-Routing: ``effort=1`` → kein Persona-Zusatz
14. Effort-Routing: ``effort=5`` → Sarkasmus-Marker im Prompt
15. Effort+HitL: ``effort=5`` + FILE → HitL-Rueckfrage vor Generierung
16. Vorschau-Text: Datei-Caption korrekt formatiert
17. Guard auf Datei: Content durchlaeuft Guard-Check vor Versand
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zerberus.modules.telegram.bot import (
    EFFORT_CALIBRATION,
    build_effort_modifier,
    build_huginn_system_prompt,
    send_document,
)
from zerberus.utils.file_output import (
    CHAT_FILE_THRESHOLD,
    EXTENSION_BLOCKLIST,
    MAX_FILE_SIZE_BYTES,
    MIME_WHITELIST,
    build_file_caption,
    determine_file_format,
    is_extension_allowed,
    should_send_as_file,
    validate_file_size,
)


# ──────────────────────────────────────────────────────────────────────
#  1-4. Format-Erkennung
# ──────────────────────────────────────────────────────────────────────


class TestFormatDetection:
    def test_file_with_markdown_returns_md(self):
        content = "# Header\n\n- Eintrag 1\n- Eintrag 2\n\n**fett**"
        filename, mime = determine_file_format("FILE", content)
        assert filename == "huginn_antwort.md"
        assert mime == "text/markdown"

    def test_code_with_python_returns_py(self):
        content = "import os\n\ndef hello():\n    return 'hi'\n"
        filename, mime = determine_file_format("CODE", content)
        assert filename == "huginn_code.py"
        assert mime == "text/x-python"

    def test_code_with_javascript_returns_js(self):
        content = "const sum = (a, b) => a + b;\nconsole.log(sum(1, 2));"
        filename, mime = determine_file_format("CODE", content)
        assert filename == "huginn_code.js"
        assert mime == "text/javascript"

    def test_code_with_unrecognized_language_returns_txt(self):
        # Enthaelt keine Python/JS/SQL-Hints → Defensive-Default .txt
        content = "<html>\n<body>Hallo</body>\n</html>"
        filename, mime = determine_file_format("CODE", content)
        assert filename == "huginn_code.txt"
        assert mime == "text/plain"

    def test_code_with_sql_returns_sql(self):
        content = "SELECT id, name FROM users WHERE active = 1;"
        filename, mime = determine_file_format("CODE", content)
        assert filename == "huginn_code.sql"
        assert mime == "text/x-sql"

    def test_file_with_plain_text_returns_txt(self):
        content = "Einfach nur ein paar Saetze ohne Markdown."
        filename, mime = determine_file_format("FILE", content)
        assert filename == "huginn_antwort.txt"
        assert mime == "text/plain"


# ──────────────────────────────────────────────────────────────────────
#  5-8. Routing (Text vs. Datei)
# ──────────────────────────────────────────────────────────────────────


class TestRouting:
    def test_chat_short_returns_text(self):
        # 500 Zeichen → unter Threshold → Text
        assert should_send_as_file("CHAT", 500) is False

    def test_chat_long_returns_file(self):
        # 3000 Zeichen → ueber Threshold (2000) → Datei-Fallback
        assert should_send_as_file("CHAT", 3000) is True

    def test_file_intent_always_file(self):
        assert should_send_as_file("FILE", 10) is True
        assert should_send_as_file("FILE", 100_000) is True

    def test_code_intent_always_file(self):
        assert should_send_as_file("CODE", 10) is True
        assert should_send_as_file("CODE", 100_000) is True

    def test_other_intents_never_file(self):
        for intent in ("SEARCH", "IMAGE", "ADMIN", "UNKNOWN", ""):
            assert should_send_as_file(intent, 10) is False
            # Selbst lange Search-Antworten gehen als Text — Datei-Fallback
            # ist nur fuer CHAT relevant.
            assert should_send_as_file(intent, 100_000) is False

    def test_threshold_constant_is_2000(self):
        # Schutz gegen unbeabsichtigtes Verschieben — Patch 168 ist auf
        # 2000 fixiert (Lesbarkeit auf dem Handy).
        assert CHAT_FILE_THRESHOLD == 2000


# ──────────────────────────────────────────────────────────────────────
#  9. Size-Limit
# ──────────────────────────────────────────────────────────────────────


class TestSizeLimit:
    def test_size_limit_constant_is_10mb(self):
        assert MAX_FILE_SIZE_BYTES == 10 * 1024 * 1024

    def test_under_limit_passes(self):
        ten_kb = b"x" * (10 * 1024)
        assert validate_file_size(ten_kb) is True

    def test_over_limit_fails(self):
        eleven_mb = b"x" * (11 * 1024 * 1024)
        assert validate_file_size(eleven_mb) is False

    def test_exactly_at_limit_passes(self):
        at_limit = b"x" * MAX_FILE_SIZE_BYTES
        assert validate_file_size(at_limit) is True

    def test_one_byte_over_limit_fails(self):
        over = b"x" * (MAX_FILE_SIZE_BYTES + 1)
        assert validate_file_size(over) is False


# ──────────────────────────────────────────────────────────────────────
#  10. MIME-Whitelist
# ──────────────────────────────────────────────────────────────────────


class TestMimeWhitelist:
    def test_python_extension_allowed(self):
        assert is_extension_allowed("huginn_code.py") is True

    def test_markdown_extension_allowed(self):
        assert is_extension_allowed("huginn_antwort.md") is True

    def test_text_extension_allowed(self):
        assert is_extension_allowed("huginn_code.txt") is True

    def test_exe_extension_blocked(self):
        assert is_extension_allowed("malicious.exe") is False

    def test_shell_script_blocked(self):
        for ext in (".sh", ".bat", ".cmd", ".ps1", ".dll"):
            filename = f"baddie{ext}"
            assert is_extension_allowed(filename) is False, (
                f"Extension {ext} sollte blockiert sein"
            )

    def test_blocklist_overrides_unknown(self):
        # .so taucht in EXTENSION_BLOCKLIST auf, nicht in Whitelist → False.
        assert is_extension_allowed("lib.so") is False
        assert ".so" in EXTENSION_BLOCKLIST

    def test_empty_filename_blocked(self):
        assert is_extension_allowed("") is False

    def test_whitelist_has_expected_extensions(self):
        # Patch-Spec listet diese Endungen explizit.
        for ext in (".txt", ".md", ".py", ".js", ".ts", ".sql",
                    ".json", ".yaml", ".csv"):
            assert ext in MIME_WHITELIST, f"{ext} fehlt in Whitelist"


# ──────────────────────────────────────────────────────────────────────
#  11-12. send_document
# ──────────────────────────────────────────────────────────────────────


class TestSendDocument:
    def test_send_document_calls_api_with_multipart(self):
        """Test 11: API-Call-Format korrekt (multipart/form-data + chat_id +
        caption + reply_to). Wir mocken httpx.AsyncClient und pruefen die
        an client.post uebergebenen Argumente."""
        captured: Dict[str, Any] = {}

        class _FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"ok": True}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, url, data=None, files=None, **kw):
                captured["url"] = url
                captured["data"] = data
                captured["files"] = files
                return _FakeResponse()

        async def run():
            with patch("zerberus.modules.telegram.bot.httpx.AsyncClient", _FakeClient):
                ok = await send_document(
                    bot_token="TESTTOKEN",
                    chat_id=42,
                    content=b"hello world",
                    filename="huginn_code.py",
                    caption="📄 `huginn_code.py` — 1 Zeilen Python",
                    reply_to_message_id=999,
                    mime_type="text/x-python",
                )
            return ok

        ok = asyncio.run(run())
        assert ok is True
        # URL-Pfad endet auf sendDocument
        assert captured["url"].endswith("/sendDocument")
        # Form-Data-Body
        data = captured["data"]
        assert data["chat_id"] == "42"
        assert data["caption"].startswith("📄")
        assert data["reply_to_message_id"] == "999"
        # Files-Tuple: (filename, bytes, mime)
        files = captured["files"]
        fn, content, mime = files["document"]
        assert fn == "huginn_code.py"
        assert content == b"hello world"
        assert mime == "text/x-python"

    def test_send_document_handles_timeout(self):
        """Test 12: Timeout liefert False (kein crash, kein Hang)."""
        import httpx

        class _TimeoutClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, *a, **kw):
                raise httpx.TimeoutException("simulated")

        async def run():
            with patch("zerberus.modules.telegram.bot.httpx.AsyncClient", _TimeoutClient):
                return await send_document(
                    bot_token="T",
                    chat_id=1,
                    content=b"x",
                    filename="huginn_code.py",
                )

        assert asyncio.run(run()) is False

    def test_send_document_empty_content_skipped(self):
        async def run():
            return await send_document(
                bot_token="T", chat_id=1, content=b"",
                filename="huginn_code.py",
            )

        assert asyncio.run(run()) is False

    def test_send_document_no_token_skipped(self):
        async def run():
            return await send_document(
                bot_token="", chat_id=1, content=b"hi",
                filename="huginn_code.py",
            )

        assert asyncio.run(run()) is False


# ──────────────────────────────────────────────────────────────────────
#  13-14. Effort-Routing (Persona-Modifikator)
# ──────────────────────────────────────────────────────────────────────


class TestEffortCalibration:
    def test_effort_1_no_modifier(self):
        # Test 13: effort=1 → leerer Modifier
        assert build_effort_modifier(1) == ""
        assert build_effort_modifier(2) == ""

    def test_effort_3_neutral_comment(self):
        modifier = build_effort_modifier(3)
        assert modifier
        assert "neutral" in modifier.lower() or "kurz" in modifier.lower()

    def test_effort_5_sarcasm_marker(self):
        # Test 14: effort=5 → Sarkasmus-Marker present
        modifier = build_effort_modifier(5)
        assert "sarkastisch" in modifier.lower()
        assert "sicher" in modifier.lower()  # "frage ob er sich sicher ist"

    def test_effort_4_sarcasm_marker(self):
        # effort=4 hat auch Sarkasmus-Charakter, aber ohne "sicher"-Frage
        modifier = build_effort_modifier(4)
        assert modifier
        assert "genervt" in modifier.lower() or "sarkastisch" in modifier.lower()

    def test_invalid_effort_returns_empty(self):
        assert build_effort_modifier(None) == ""
        assert build_effort_modifier("garbage") == ""  # type: ignore[arg-type]

    def test_universal_calibration_in_default_prompt(self):
        # Live-Pfad: build_huginn_system_prompt(persona) ohne Effort-Param
        # bekommt die universale EFFORT_CALIBRATION-Sektion eingehaengt.
        prompt = build_huginn_system_prompt("Du bist Huginn.")
        assert "Aufwands-Kalibrierung" in prompt
        assert "effort" in prompt.lower()
        # Auch der Intent-Header muss noch im Prompt stehen (Patch 164).
        assert "JSON-Header" in prompt

    def test_explicit_effort_5_replaces_universal(self):
        # Wird ein konkreter Effort uebergeben, kommt nur die passende
        # Modifier-Zeile in den Prompt — nicht die universale Sektion.
        prompt = build_huginn_system_prompt("Du bist Huginn.", effort=5)
        assert "sarkastisch" in prompt.lower()
        # Universale Sektion (mit Header) ist NICHT mit drin.
        assert "Aufwands-Kalibrierung" not in prompt

    def test_explicit_effort_1_omits_modifier(self):
        prompt = build_huginn_system_prompt("Du bist Huginn.", effort=1)
        # effort=1 → kein Modifier. Persona + Intent-Instruction reichen.
        assert "sarkastisch" not in prompt.lower()
        assert "Aufwands-Kalibrierung" not in prompt
        assert "JSON-Header" in prompt


# ──────────────────────────────────────────────────────────────────────
#  15. Effort + HitL (FILE + effort=5)
# ──────────────────────────────────────────────────────────────────────


class TestFileHitlGate:
    def test_effort5_file_triggers_hitl_question(self, monkeypatch):
        """Test 15: FILE + effort=5 → Rueckfrage via HitL-Button.

        Wir schalten den Datei-Output-Pfad direkt mit ``_send_as_file`` an,
        mocken send_telegram_message + send_document und pruefen, dass
        die Rueckfrage rausgeht und ``send_document`` NICHT direkt
        aufgerufen wurde (sondern erst nach Approval im Background-Task).
        """
        from zerberus.modules.telegram import router as router_mod
        from zerberus.modules.telegram.bot import HuginnConfig

        router_mod._reset_telegram_singletons_for_tests()

        sent_messages: List[Dict[str, Any]] = []
        sent_docs: List[Dict[str, Any]] = []

        async def fake_send_msg(*a, **kw):
            sent_messages.append({"args": a, "kwargs": kw})
            return True

        async def fake_send_doc(*a, **kw):
            sent_docs.append({"args": a, "kwargs": kw})
            return True

        monkeypatch.setattr(router_mod, "send_telegram_message", fake_send_msg)
        monkeypatch.setattr(router_mod, "send_document", fake_send_doc)

        class _Settings:
            modules = {"telegram": {"enabled": True, "bot_token": "T",
                                    "admin_chat_id": "999",
                                    "hitl": {"timeout_seconds": 300}}}

        cfg = HuginnConfig.from_dict(_Settings.modules["telegram"])
        info = {
            "chat_id": 100, "user_id": 42, "username": "chris",
            "message_id": 1, "message_thread_id": None,
        }
        # Nicht-trivialer Markdown-Body, damit determine_file_format auf .md
        # routet (FILE-Branch).
        answer = "# Big Doc\n\n" + ("Ein langer Text. " * 100)

        async def run():
            kind, ok = await router_mod._send_as_file(
                answer=answer,
                intent_str="FILE",
                effort=5,
                info=info,
                cfg=cfg,
                settings=_Settings(),
            )
            # Background-Task soll laufen — kurz yielden, damit er die
            # Rueckfrage absetzt.
            await asyncio.sleep(0.05)
            return kind, ok

        kind, ok = asyncio.run(run())
        try:
            # _send_as_file selbst meldet "hitl_pending" + True (Rueckfrage geht raus).
            assert kind == "hitl_pending"
            assert ok is True
            # Mindestens eine Nachricht: die HitL-Rueckfrage.
            assert sent_messages, "HitL-Rueckfrage wurde nicht gesendet"
            question_text = sent_messages[0]["args"][2] if len(sent_messages[0]["args"]) > 2 else sent_messages[0]["kwargs"].get("text", "")
            assert "Riesenakt" in question_text or "sicher" in question_text.lower()
            # Inline-Keyboard (✅/❌) muss dabei sein.
            kw = sent_messages[0]["kwargs"]
            assert "reply_markup" in kw
            assert "inline_keyboard" in kw["reply_markup"]
            # send_document darf erst NACH approval feuern — hier noch nicht.
            assert not sent_docs, (
                "send_document wurde aufgerufen, obwohl HitL noch ungeklaert ist"
            )
        finally:
            router_mod._reset_telegram_singletons_for_tests()

    def test_effort5_file_skips_hitl_for_low_effort(self, monkeypatch):
        """effort < 5 + FILE → direkter Datei-Versand, KEINE Rueckfrage."""
        from zerberus.modules.telegram import router as router_mod
        from zerberus.modules.telegram.bot import HuginnConfig

        router_mod._reset_telegram_singletons_for_tests()
        sent_docs: List[Dict[str, Any]] = []

        async def fake_send_msg(*a, **kw):
            return True

        async def fake_send_doc(*a, **kw):
            sent_docs.append({"args": a, "kwargs": kw})
            return True

        monkeypatch.setattr(router_mod, "send_telegram_message", fake_send_msg)
        monkeypatch.setattr(router_mod, "send_document", fake_send_doc)

        class _Settings:
            modules = {"telegram": {"enabled": True, "bot_token": "T",
                                    "hitl": {"timeout_seconds": 300}}}

        cfg = HuginnConfig.from_dict(_Settings.modules["telegram"])
        info = {"chat_id": 100, "user_id": 42, "username": "chris",
                "message_id": 1, "message_thread_id": None}

        async def run():
            return await router_mod._send_as_file(
                answer="# kleines doc\n\ntext",
                intent_str="FILE",
                effort=2,
                info=info,
                cfg=cfg,
                settings=_Settings(),
            )

        try:
            kind, ok = asyncio.run(run())
            assert kind == "file"
            assert ok is True
            assert len(sent_docs) == 1
        finally:
            router_mod._reset_telegram_singletons_for_tests()


# ──────────────────────────────────────────────────────────────────────
#  16. Vorschau-Text (Caption-Format)
# ──────────────────────────────────────────────────────────────────────


class TestCaption:
    def test_caption_for_code_python(self):
        content = "import os\n\ndef hello():\n    pass\n"
        caption = build_file_caption("CODE", content, "huginn_code.py")
        assert "huginn_code.py" in caption
        assert "Python" in caption
        # Zeilenanzahl: 4 Zeilen (count + 1)
        assert "4 Zeilen" in caption or "4" in caption

    def test_caption_for_code_javascript(self):
        content = "const x = 1;\nconst y = 2;\n"
        caption = build_file_caption("CODE", content, "huginn_code.js")
        assert "huginn_code.js" in caption
        assert "JavaScript" in caption

    def test_caption_for_file_markdown(self):
        content = "# Header\n\nText"
        caption = build_file_caption("FILE", content, "huginn_antwort.md")
        assert "huginn_antwort.md" in caption
        assert "Markdown" in caption

    def test_caption_for_chat_fallback_explains(self):
        content = "lange chat antwort " * 200
        caption = build_file_caption("CHAT", content, "huginn_antwort.md")
        assert "zu lang" in caption.lower() or "lang" in caption.lower()
        assert "huginn_antwort.md" in caption

    def test_caption_within_telegram_limit(self):
        # Auch bei extrem langen Inhalten bleibt die Caption ≤ 1024 Zeichen.
        content = "x" * 100_000
        caption = build_file_caption("FILE", content, "huginn_antwort.md")
        assert len(caption) <= 1024


# ──────────────────────────────────────────────────────────────────────
#  17. Guard-Check auf Datei-Inhalt
# ──────────────────────────────────────────────────────────────────────


class TestGuardOnFileContent:
    """Pipeline-Test: bevor eine Datei rausgeht, MUSS der Guard auf den
    Body gelaufen sein. ``_process_text_message`` ruft ``_run_guard``
    auf dem geparsten Body — der wird unveraendert in den Datei-Output-
    Pfad weitergereicht. Wir verifizieren die Aufruf-Sequenz."""

    def test_guard_runs_before_file_send(self, monkeypatch):
        from zerberus.modules.telegram import router as router_mod
        from zerberus.modules.telegram.bot import HuginnConfig

        router_mod._reset_telegram_singletons_for_tests()

        guard_calls: List[Dict[str, Any]] = []
        sent_docs: List[Dict[str, Any]] = []

        async def fake_guard(user_msg, assistant_msg, caller_context=""):
            guard_calls.append({
                "user_msg": user_msg,
                "assistant_msg": assistant_msg,
            })
            return {"verdict": "OK", "reason": "", "latency_ms": 1}

        async def fake_call_llm(**kwargs):
            # LLM liefert FILE-Intent + Markdown-Body (laenger als 2000 ZS,
            # damit auch der CHAT-Fallback nicht greifen wuerde).
            body = "# Header\n\n" + ("Ein langer Markdown-Text. " * 100)
            return {
                "content": '{"intent": "FILE", "effort": 2, "needs_hitl": false}\n' + body,
                "usage": {},
                "latency_ms": 5,
            }

        async def fake_send_msg(*a, **kw):
            return True

        async def fake_send_doc(*a, **kw):
            sent_docs.append({"args": a, "kwargs": kw})
            return True

        monkeypatch.setattr(router_mod, "_run_guard", fake_guard)
        monkeypatch.setattr(router_mod, "_call_llm_with_retry", fake_call_llm)
        monkeypatch.setattr(router_mod, "send_telegram_message", fake_send_msg)
        monkeypatch.setattr(router_mod, "send_document", fake_send_doc)

        class _Settings:
            modules = {"telegram": {"enabled": True, "bot_token": "T",
                                    "model": "test/test"}}
            security = {}

        info = {
            "chat_id": 100, "user_id": 42, "username": "chris",
            "message_id": 1, "message_thread_id": None,
            "text": "Schreib mir einen Artikel ueber KI",
            "chat_type": "private", "is_forwarded": False,
            "reply_to_message": None, "photo_file_ids": [],
        }
        cfg = HuginnConfig.from_dict(_Settings.modules["telegram"])

        async def run():
            return await router_mod._process_text_message(
                info, cfg, _Settings(), system_prompt="",
            )

        try:
            result = asyncio.run(run())
            # Guard wurde aufgerufen mit dem Body (ohne JSON-Header).
            assert guard_calls, "Guard wurde nicht aufgerufen"
            assistant_msg = guard_calls[0]["assistant_msg"]
            assert "{" not in assistant_msg.split("\n")[0], (
                "Guard hat den JSON-Header gesehen (sollte gestrippt sein)"
            )
            assert "Header" in assistant_msg
            # Datei wurde gesendet
            assert len(sent_docs) == 1
            assert result["sent"] is True
            assert result["kind"] == "file"
        finally:
            router_mod._reset_telegram_singletons_for_tests()
