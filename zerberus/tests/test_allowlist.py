"""Patch 181 — Tests fuer die Telegram-User-Allowlist.

Drei Modi:
- ``open`` (Default): alle duerfen.
- ``allowlist``: nur User-IDs aus ``allowed_users`` (+ Admin immer).
- ``admin_only``: nur die ``admin_chat_id``.

Plus: Absage-Rate-Limit (1/h pro User), Vision-Pfad blockiert, denied User
triggert keinen LLM-Call.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from zerberus.modules.telegram import router as telegram_router


# ──────────────────────────────────────────────────────────────────────
#  Pure-Logic-Tests auf _check_allowlist
# ──────────────────────────────────────────────────────────────────────


class TestCheckAllowlistLogic:
    def test_open_mode_allows_all(self):
        cfg = {"allowlist_mode": "open"}
        assert telegram_router._check_allowlist(99999, cfg) is True

    def test_open_mode_default_when_key_missing(self):
        # Default ist "open" wenn allowlist_mode fehlt → kein Breaking Change
        cfg = {}
        assert telegram_router._check_allowlist(99999, cfg) is True

    def test_allowlist_mode_user_in_list(self):
        cfg = {"allowlist_mode": "allowlist", "allowed_users": [123, 456]}
        assert telegram_router._check_allowlist(123, cfg) is True
        assert telegram_router._check_allowlist(456, cfg) is True

    def test_allowlist_mode_user_not_in_list(self):
        cfg = {"allowlist_mode": "allowlist", "allowed_users": [123, 456]}
        assert telegram_router._check_allowlist(789, cfg) is False

    def test_allowlist_mode_admin_always_allowed(self):
        # Admin ist nicht in allowed_users → trotzdem True (Lock-out-Schutz).
        cfg = {
            "allowlist_mode": "allowlist",
            "allowed_users": [123, 456],
            "admin_chat_id": "999",
        }
        assert telegram_router._check_allowlist(999, cfg) is True

    def test_empty_allowlist_allows_all_safety_fallback(self):
        # Leere Liste = niemand wuerde durchkommen → Safety-Fallback: alle.
        cfg = {"allowlist_mode": "allowlist", "allowed_users": []}
        assert telegram_router._check_allowlist(123, cfg) is True

    def test_admin_only_mode_admin_passes(self):
        cfg = {"allowlist_mode": "admin_only", "admin_chat_id": "999"}
        assert telegram_router._check_allowlist(999, cfg) is True

    def test_admin_only_mode_non_admin_blocked(self):
        cfg = {"allowlist_mode": "admin_only", "admin_chat_id": "999"}
        assert telegram_router._check_allowlist(123, cfg) is False

    def test_admin_only_mode_without_admin_id_blocks_everyone(self):
        cfg = {"allowlist_mode": "admin_only", "admin_chat_id": ""}
        assert telegram_router._check_allowlist(123, cfg) is False

    def test_user_id_none_passes_through(self):
        # Service-Events ohne User-ID darf der Allowlist-Check nicht blocken.
        cfg = {"allowlist_mode": "admin_only", "admin_chat_id": "999"}
        assert telegram_router._check_allowlist(None, cfg) is True

    def test_string_admin_id_compared_correctly(self):
        # admin_chat_id liegt typischerweise als String in YAML vor; user_id
        # kommt als int aus Telegram → der Vergleich muss trotzdem matchen.
        cfg = {"allowlist_mode": "admin_only", "admin_chat_id": "999"}
        assert telegram_router._check_allowlist(999, cfg) is True


# ──────────────────────────────────────────────────────────────────────
#  Rate-Limiter fuer Absage-Nachrichten
# ──────────────────────────────────────────────────────────────────────


class TestDeniedNoticeRateLimit:
    def setup_method(self):
        telegram_router._reset_allowlist_state_for_tests()

    def test_first_call_sends(self):
        assert telegram_router._should_send_denied_notice(42) is True

    def test_second_call_within_hour_blocked(self):
        telegram_router._should_send_denied_notice(42)
        assert telegram_router._should_send_denied_notice(42) is False

    def test_different_users_independent(self):
        assert telegram_router._should_send_denied_notice(42) is True
        assert telegram_router._should_send_denied_notice(43) is True

    def test_after_window_sends_again(self, monkeypatch):
        import time
        # Erste Absage jetzt registrieren …
        telegram_router._should_send_denied_notice(42)
        # … dann simulieren, dass eine Stunde + 1 Sekunde vergangen ist.
        original_time = time.time
        offset = telegram_router._DENIED_NOTICE_INTERVAL_SECS + 1
        monkeypatch.setattr(time, "time", lambda: original_time() + offset)
        assert telegram_router._should_send_denied_notice(42) is True


# ──────────────────────────────────────────────────────────────────────
#  Integration-Tests gegen _legacy_process_update
# ──────────────────────────────────────────────────────────────────────


def _settings_with(allowlist_mode="admin_only", admin_chat_id="999",
                   allowed_users=None):
    cfg = {
        "enabled": True,
        "bot_token": "TEST_TOKEN",
        "allowlist_mode": allowlist_mode,
        "admin_chat_id": admin_chat_id,
    }
    if allowed_users is not None:
        cfg["allowed_users"] = allowed_users
    return SimpleNamespace(modules={"telegram": cfg, "rag": {"enabled": False}})


def _build_message_update(user_id: int, text: str = "Hallo") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "username": "tester"},
            "text": text,
        },
    }


class TestLegacyAllowlistIntegration:
    def setup_method(self):
        telegram_router._reset_telegram_singletons_for_tests()

    def _patch_io(self, monkeypatch):
        sends: list[dict] = []
        llm_calls: list[dict] = []

        async def fake_send(token, chat_id, text, **kwargs):
            sends.append({"chat_id": chat_id, "text": text})
            return True

        async def fake_call_llm(**kwargs):
            llm_calls.append(kwargs)
            return {"content": "Antwort.", "latency_ms": 1}

        monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)
        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        return sends, llm_calls

    def test_admin_only_blocks_non_admin(self, monkeypatch):
        sends, llm_calls = self._patch_io(monkeypatch)
        update = _build_message_update(user_id=123)
        result = asyncio.run(
            telegram_router._legacy_process_update(update, _settings_with("admin_only", "999"))
        )
        assert result.get("skipped") == "allowlist"
        # Es ging genau die Absage raus — kein LLM-Call.
        assert llm_calls == []
        assert any("freigeschaltet" in s["text"] for s in sends)

    def test_admin_only_allows_admin(self, monkeypatch):
        sends, llm_calls = self._patch_io(monkeypatch)
        update = _build_message_update(user_id=999)
        result = asyncio.run(
            telegram_router._legacy_process_update(update, _settings_with("admin_only", "999"))
        )
        # Kein Allowlist-Block — der LLM-Pfad lief durch.
        assert result.get("skipped") != "allowlist"
        assert len(llm_calls) == 1

    def test_open_mode_allows_anyone(self, monkeypatch):
        sends, llm_calls = self._patch_io(monkeypatch)
        update = _build_message_update(user_id=123)
        result = asyncio.run(
            telegram_router._legacy_process_update(
                update, _settings_with("open", admin_chat_id="999"),
            )
        )
        assert result.get("skipped") != "allowlist"
        assert len(llm_calls) == 1

    def test_denied_user_no_llm_call(self, monkeypatch):
        """Regressions-Schutz: Allowlist greift VOR LLM/Guard/RAG.

        Cost-relevant: der OpenRouter-Call darf bei einem geblockten User
        nicht passieren — sonst kann ein Spammer Credits verbrennen, auch
        wenn er die Antwort nie bekommt.
        """
        sends, llm_calls = self._patch_io(monkeypatch)
        update = _build_message_update(user_id=123)
        asyncio.run(
            telegram_router._legacy_process_update(update, _settings_with("admin_only", "999"))
        )
        assert llm_calls == []

    def test_denied_user_repeated_send_rate_limited(self, monkeypatch):
        """Zweite + dritte Nachricht innerhalb 1h → keine zweite/dritte Absage."""
        sends, _ = self._patch_io(monkeypatch)
        settings = _settings_with("admin_only", "999")
        update = _build_message_update(user_id=123)
        asyncio.run(telegram_router._legacy_process_update(update, settings))
        asyncio.run(telegram_router._legacy_process_update(update, settings))
        asyncio.run(telegram_router._legacy_process_update(update, settings))
        denial_sends = [s for s in sends if "freigeschaltet" in s["text"]]
        assert len(denial_sends) == 1

    def test_allowlist_mode_user_in_list_passes(self, monkeypatch):
        sends, llm_calls = self._patch_io(monkeypatch)
        settings = _settings_with(
            "allowlist", admin_chat_id="999", allowed_users=[123, 456],
        )
        update = _build_message_update(user_id=456)
        result = asyncio.run(telegram_router._legacy_process_update(update, settings))
        assert result.get("skipped") != "allowlist"
        assert len(llm_calls) == 1

    def test_groups_bypass_allowlist(self, monkeypatch):
        """Gruppen sind Tailscale-intern; die Allowlist gilt nur in DMs."""
        sends, llm_calls = self._patch_io(monkeypatch)
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": -100, "type": "supergroup", "title": "T"},
                "from": {"id": 123, "username": "tester"},
                "text": "Huginn was geht?",
            },
        }
        # admin_only-Mode + user 123 ist NICHT Admin — in DM blockiert.
        # Im Gruppen-Pfad darf der Allowlist-Filter NICHT greifen.
        result = asyncio.run(
            telegram_router._legacy_process_update(update, _settings_with("admin_only", "999"))
        )
        assert result.get("skipped") != "allowlist"


# ──────────────────────────────────────────────────────────────────────
#  Patch 182 — Unsupported-Media-Handler
# ──────────────────────────────────────────────────────────────────────


class TestUnsupportedMediaDetection:
    """Pure-Logic-Tests auf _detect_unsupported_media."""

    def test_voice_detected(self):
        msg = {"voice": {"file_id": "abc", "duration": 5}}
        kind, label = telegram_router._detect_unsupported_media(msg)
        assert kind == "voice"
        assert "Sprach" in label

    def test_audio_detected(self):
        msg = {"audio": {"file_id": "abc"}}
        kind, _ = telegram_router._detect_unsupported_media(msg)
        assert kind == "audio"

    def test_sticker_detected(self):
        msg = {"sticker": {"file_id": "abc"}}
        kind, _ = telegram_router._detect_unsupported_media(msg)
        assert kind == "sticker"

    def test_document_detected(self):
        msg = {"document": {"file_id": "abc", "file_name": "doc.pdf"}}
        kind, _ = telegram_router._detect_unsupported_media(msg)
        assert kind == "document"

    def test_video_note_detected(self):
        msg = {"video_note": {"file_id": "abc"}}
        kind, _ = telegram_router._detect_unsupported_media(msg)
        assert kind == "video_note"

    def test_text_only_returns_none(self):
        msg = {"text": "Hallo"}
        assert telegram_router._detect_unsupported_media(msg) is None

    def test_photo_returns_none(self):
        """Photos sind UNTERSTUETZT (Vision-Pfad) — nicht im Filter."""
        msg = {"photo": [{"file_id": "abc"}]}
        assert telegram_router._detect_unsupported_media(msg) is None


class TestUnsupportedMediaIntegration:
    def setup_method(self):
        telegram_router._reset_telegram_singletons_for_tests()

    def _patch_io(self, monkeypatch):
        sends: list[dict] = []
        llm_calls: list[dict] = []

        async def fake_send(token, chat_id, text, **kwargs):
            sends.append({"chat_id": chat_id, "text": text})
            return True

        async def fake_call_llm(**kwargs):
            llm_calls.append(kwargs)
            return {"content": "Antwort.", "latency_ms": 1}

        monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)
        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        return sends, llm_calls

    def _voice_update(self, user_id: int = 999) -> dict:
        return {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "username": "tester"},
                "voice": {"file_id": "voice123", "duration": 5},
            },
        }

    def test_voice_message_triggers_friendly_reply(self, monkeypatch):
        sends, llm_calls = self._patch_io(monkeypatch)
        result = asyncio.run(
            telegram_router._legacy_process_update(
                self._voice_update(),
                _settings_with("open", admin_chat_id="999"),
            )
        )
        assert result.get("skipped") == "unsupported_media"
        # Genau eine Absage, kein LLM-Call.
        assert llm_calls == []
        assert any("Sprachnachricht" in s["text"] for s in sends)

    def test_voice_message_no_llm_call(self, monkeypatch):
        sends, llm_calls = self._patch_io(monkeypatch)
        asyncio.run(
            telegram_router._legacy_process_update(
                self._voice_update(),
                _settings_with("open", admin_chat_id="999"),
            )
        )
        # Cost-relevant: keine OpenRouter-Token verbrennen fuer Sprachnachrichten.
        assert llm_calls == []

    def test_sticker_triggers_friendly_reply(self, monkeypatch):
        sends, _ = self._patch_io(monkeypatch)
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 999, "type": "private"},
                "from": {"id": 999, "username": "t"},
                "sticker": {"file_id": "s1"},
            },
        }
        asyncio.run(
            telegram_router._legacy_process_update(
                update, _settings_with("open", admin_chat_id="999"),
            )
        )
        assert any("Sticker" in s["text"] for s in sends)

    def test_document_triggers_friendly_reply(self, monkeypatch):
        sends, _ = self._patch_io(monkeypatch)
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 999, "type": "private"},
                "from": {"id": 999, "username": "t"},
                "document": {"file_id": "d1", "file_name": "x.pdf"},
            },
        }
        asyncio.run(
            telegram_router._legacy_process_update(
                update, _settings_with("open", admin_chat_id="999"),
            )
        )
        assert any("Dokument" in s["text"] for s in sends)

    def test_denied_user_voice_blocked_BEFORE_friendly_reply(self, monkeypatch):
        """Allowlist greift VOR Media-Check — denied user bekommt Allowlist-
        Absage, nicht die freundliche Voice-Erklaerung. Verhindert Info-Leak
        ueber den Bot-Status an User, die gar nicht reden duerfen.
        """
        sends, llm_calls = self._patch_io(monkeypatch)
        update = self._voice_update(user_id=123)  # 123 != admin 999
        result = asyncio.run(
            telegram_router._legacy_process_update(
                update, _settings_with("admin_only", admin_chat_id="999"),
            )
        )
        assert result.get("skipped") == "allowlist"
        assert llm_calls == []
        # Allowlist-Text raus, Voice-Text NICHT.
        texts = " ".join(s["text"] for s in sends)
        assert "freigeschaltet" in texts
        assert "Sprachnachricht" not in texts
