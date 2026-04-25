"""Patch 164 — Tests fuer das HuginnIntent-Enum."""
from __future__ import annotations

import pytest

from zerberus.core.intent import HuginnIntent


class TestIntentFromStr:
    def test_valid_uppercase(self):
        assert HuginnIntent.from_str("CHAT") is HuginnIntent.CHAT
        assert HuginnIntent.from_str("CODE") is HuginnIntent.CODE
        assert HuginnIntent.from_str("FILE") is HuginnIntent.FILE
        assert HuginnIntent.from_str("SEARCH") is HuginnIntent.SEARCH
        assert HuginnIntent.from_str("IMAGE") is HuginnIntent.IMAGE
        assert HuginnIntent.from_str("ADMIN") is HuginnIntent.ADMIN

    def test_case_insensitive(self):
        assert HuginnIntent.from_str("chat") is HuginnIntent.CHAT
        assert HuginnIntent.from_str("Code") is HuginnIntent.CODE

    def test_invalid_falls_back_to_chat(self):
        assert HuginnIntent.from_str("BANANA") is HuginnIntent.CHAT
        assert HuginnIntent.from_str("EXECUTE") is HuginnIntent.CHAT  # Rosa-Future

    def test_none_falls_back_to_chat(self):
        assert HuginnIntent.from_str(None) is HuginnIntent.CHAT

    def test_empty_falls_back_to_chat(self):
        assert HuginnIntent.from_str("") is HuginnIntent.CHAT

    def test_str_value_matches_name(self):
        # str-Enum: Wert == Name (wichtig fuer JSON-Serialisierung im Header)
        assert HuginnIntent.CHAT.value == "CHAT"
        assert HuginnIntent.CODE.value == "CODE"
