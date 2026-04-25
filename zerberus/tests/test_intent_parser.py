"""Patch 164 — Tests fuer den Intent-Parser (JSON-Header in LLM-Antworten)."""
from __future__ import annotations

import pytest

from zerberus.core.intent import HuginnIntent
from zerberus.core.intent_parser import parse_llm_response


class TestParseValidHeader:
    def test_simple_header(self):
        raw = '{"intent": "CHAT", "effort": 2, "needs_hitl": false}\nHallo Welt'
        parsed = parse_llm_response(raw)
        assert parsed.intent is HuginnIntent.CHAT
        assert parsed.effort == 2
        assert parsed.needs_hitl is False
        assert parsed.body == "Hallo Welt"
        assert parsed.raw_header is not None

    def test_code_intent_with_hitl(self):
        raw = '{"intent": "CODE", "effort": 4, "needs_hitl": true}\n```python\nx = 1\n```'
        parsed = parse_llm_response(raw)
        assert parsed.intent is HuginnIntent.CODE
        assert parsed.effort == 4
        assert parsed.needs_hitl is True
        assert "```python" in parsed.body

    def test_fenced_header(self):
        raw = '```json\n{"intent": "SEARCH", "effort": 3, "needs_hitl": false}\n```\nIch suche...'
        parsed = parse_llm_response(raw)
        assert parsed.intent is HuginnIntent.SEARCH
        assert parsed.effort == 3
        assert "Ich suche" in parsed.body

    def test_fenced_header_case_insensitive_json(self):
        raw = '```JSON\n{"intent": "FILE", "effort": 1, "needs_hitl": true}\n```\nDatei wird gelesen.'
        parsed = parse_llm_response(raw)
        assert parsed.intent is HuginnIntent.FILE


class TestParseRobustness:
    def test_no_header_default_chat(self):
        raw = "Einfach nur Text ohne JSON-Header."
        parsed = parse_llm_response(raw)
        assert parsed.intent is HuginnIntent.CHAT
        assert parsed.effort == 3
        assert parsed.needs_hitl is False
        assert parsed.body == raw
        assert parsed.raw_header is None

    def test_broken_json_default_chat(self, caplog):
        raw = '{"intent": "CHAT", effort: 2}\nKaputtes JSON ohne Anfuehrungszeichen.'
        parsed = parse_llm_response(raw)
        # Body bleibt der ganze Text, weil JSON-Parse fehlgeschlagen ist
        assert parsed.intent is HuginnIntent.CHAT
        assert parsed.raw_header is None
        assert "Kaputtes JSON" in parsed.body

    def test_empty_response(self):
        parsed = parse_llm_response("")
        assert parsed.intent is HuginnIntent.CHAT
        assert parsed.effort == 3
        assert parsed.needs_hitl is False
        assert parsed.body == ""
        assert parsed.raw_header is None

    def test_none_response(self):
        parsed = parse_llm_response(None)  # type: ignore[arg-type]
        assert parsed.intent is HuginnIntent.CHAT
        assert parsed.body == ""

    def test_effort_clamped_to_5(self):
        raw = '{"intent": "CHAT", "effort": 99, "needs_hitl": false}\nx'
        parsed = parse_llm_response(raw)
        assert parsed.effort == 5

    def test_effort_clamped_to_1(self):
        raw = '{"intent": "CHAT", "effort": -7, "needs_hitl": false}\nx'
        parsed = parse_llm_response(raw)
        assert parsed.effort == 1

    def test_effort_non_numeric_falls_back(self):
        raw = '{"intent": "CHAT", "effort": "viel", "needs_hitl": false}\nx'
        parsed = parse_llm_response(raw)
        assert parsed.effort == 3

    def test_unknown_intent_falls_back_to_chat(self):
        raw = '{"intent": "BANANA", "effort": 2, "needs_hitl": false}\nUnbekannter Intent.'
        parsed = parse_llm_response(raw)
        assert parsed.intent is HuginnIntent.CHAT
        assert parsed.body == "Unbekannter Intent."

    def test_missing_fields_use_defaults(self):
        raw = '{"intent": "CHAT"}\nNur Intent gesetzt.'
        parsed = parse_llm_response(raw)
        assert parsed.effort == 3
        assert parsed.needs_hitl is False

    def test_json_array_at_start_treated_as_no_header(self):
        raw = '[1, 2, 3]\nLooks like JSON aber kein Objekt.'
        parsed = parse_llm_response(raw)
        # Kein ``{`` am Anfang → kein Header gefunden
        assert parsed.body.startswith("[1, 2, 3]")
        assert parsed.raw_header is None


class TestParseBodyPreservation:
    def test_body_with_newlines(self):
        raw = (
            '{"intent": "CODE", "effort": 3, "needs_hitl": false}\n'
            "Zeile 1\n"
            "Zeile 2\n"
            "\n"
            "Zeile 4 nach Leerzeile"
        )
        parsed = parse_llm_response(raw)
        assert "Zeile 1\nZeile 2" in parsed.body
        assert "Zeile 4 nach Leerzeile" in parsed.body

    def test_body_with_code_block(self):
        raw = (
            '{"intent": "CODE", "effort": 2, "needs_hitl": false}\n'
            "```python\n"
            "def foo():\n"
            "    return 42\n"
            "```"
        )
        parsed = parse_llm_response(raw)
        assert parsed.body.startswith("```python")
        assert parsed.body.endswith("```")
        assert "def foo()" in parsed.body
