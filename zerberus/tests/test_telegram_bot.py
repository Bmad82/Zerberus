"""
Patch 123 - Tests fuer den Telegram-Bot Huginn.

Prueft:
- Config-Parsing (from_dict)
- Message-Info-Extraktion
- Group-Decision-Logik (direct_name, mention, reply, autonomous)
- HitL: Approve/Reject/Timeout
- Smart-Interjection SKIP-Erkennung
- Admin-Message-Formatting
"""
import asyncio
import time

import pytest

from zerberus.modules.telegram.bot import (
    HuginnConfig,
    extract_message_info,
    format_code_response,
    is_bot_mentioned,
    was_bot_added_to_group,
)
from zerberus.modules.telegram.group_handler import (
    GroupManager,
    build_smart_interjection_prompt,
    is_skip_response,
    should_respond_in_group,
)
from zerberus.modules.telegram.hitl import (
    HitlManager,
    build_admin_keyboard,
    build_admin_message,
    build_group_decision_message,
    build_group_waiting_message,
    parse_callback_data,
)


# ----- Config -----

class TestHuginnConfig:
    def test_from_dict_defaults(self):
        cfg = HuginnConfig.from_dict({})
        assert cfg.enabled is False
        assert cfg.model == "deepseek/deepseek-chat"
        assert cfg.max_response_length == 4000

    def test_from_dict_custom(self):
        cfg = HuginnConfig.from_dict({
            "enabled": True,
            "bot_token": "123:abc",
            "admin_chat_id": "42",
            "allowed_group_ids": [100, 200],
            "model": "anthropic/claude-3",
            "max_response_length": 2000,
        })
        assert cfg.enabled is True
        assert cfg.bot_token == "123:abc"
        assert cfg.admin_chat_id == "42"
        assert cfg.allowed_group_ids == [100, 200]
        assert cfg.model == "anthropic/claude-3"


# ----- Message-Info -----

class TestMessageInfo:
    def test_extract_private_message(self):
        update = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 99, "username": "chris"},
                "text": "Hallo Huginn",
            },
        }
        info = extract_message_info(update)
        assert info["chat_id"] == 42
        assert info["chat_type"] == "private"
        assert info["user_id"] == 99
        assert info["username"] == "chris"
        assert info["text"] == "Hallo Huginn"

    def test_extract_group_message(self):
        update = {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "group", "title": "Testgruppe"},
                "from": {"id": 99, "username": "chris"},
                "text": "Moin",
            },
        }
        info = extract_message_info(update)
        assert info["chat_type"] == "group"
        assert info["chat_title"] == "Testgruppe"

    def test_extract_photo_file_ids(self):
        update = {
            "update_id": 3,
            "message": {
                "message_id": 12,
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 2},
                "photo": [{"file_id": "ABC"}, {"file_id": "DEF"}],
                "caption": "Was siehst du?",
            },
        }
        info = extract_message_info(update)
        assert info["photo_file_ids"] == ["ABC", "DEF"]
        assert info["text"] == "Was siehst du?"

    def test_empty_update_returns_none(self):
        assert extract_message_info({"update_id": 1}) is None


# ----- Bot-Mentioned -----

class TestIsBotMentioned:
    def test_username_mention(self):
        assert is_bot_mentioned("Hey @HuginnBot was meinst du?") is True

    def test_name_in_text(self):
        assert is_bot_mentioned("Huginn, schau mal her") is True

    def test_no_mention(self):
        assert is_bot_mentioned("Jojo und ich beschweren uns") is False

    def test_empty(self):
        assert is_bot_mentioned("") is False


# ----- Added-To-Group -----

class TestAddedToGroup:
    def test_bot_added(self):
        info = {"new_chat_members": [{"id": 999, "username": "HuginnBot"}]}
        assert was_bot_added_to_group(info, 999) is True

    def test_other_user_added(self):
        info = {"new_chat_members": [{"id": 100}]}
        assert was_bot_added_to_group(info, 999) is False

    def test_no_new_members(self):
        assert was_bot_added_to_group({}, 999) is False


# ----- Format-Code -----

class TestFormatCodeResponse:
    def test_short_is_unchanged(self):
        assert format_code_response("kurz") == "kurz"

    def test_long_is_truncated(self):
        text = "x" * 5000
        result = format_code_response(text)
        assert len(result) <= 4100
        assert "gekuerzt" in result


# ----- Group-Decisions -----

class TestShouldRespond:
    def _base_info(self, text: str, chat_id: int = -100) -> dict:
        return {
            "text": text,
            "chat_id": chat_id,
            "chat_type": "group",
            "username": "someone",
            "reply_to_message": None,
        }

    def test_direct_name_triggers(self):
        gm = GroupManager()
        decision = should_respond_in_group(
            self._base_info("Huginn, was meinst du?"),
            behavior={"respond_to_name": True},
            group_manager=gm,
        )
        assert decision["respond"] is True
        assert decision["reason"] == "direct_name"
        assert decision["needs_llm_decision"] is False

    def test_mention_triggers(self):
        gm = GroupManager()
        decision = should_respond_in_group(
            self._base_info("@HuginnBot hilf mal"),
            behavior={"respond_to_mention": True, "respond_to_name": False},
            group_manager=gm,
        )
        assert decision["respond"] is True
        assert decision["reason"] == "mention"

    def test_reply_to_bot_triggers(self):
        gm = GroupManager()
        info = self._base_info("ja stimmt")
        info["reply_to_message"] = {"from": {"id": 999}}
        decision = should_respond_in_group(
            info,
            behavior={"respond_to_direct_reply": True},
            group_manager=gm,
            bot_user_id=999,
        )
        assert decision["respond"] is True
        assert decision["reason"] == "reply"

    def test_autonomous_when_enabled_and_no_cooldown(self):
        gm = GroupManager(cooldown_seconds=300)
        decision = should_respond_in_group(
            self._base_info("random plauder"),
            behavior={
                "autonomous_interjection": True,
                "interjection_trigger": "smart",
                "respond_to_name": True,
            },
            group_manager=gm,
        )
        assert decision["respond"] is True
        assert decision["reason"] == "autonomous"
        assert decision["needs_llm_decision"] is True

    def test_autonomous_cooldown_skips(self):
        gm = GroupManager(cooldown_seconds=300)
        gm.mark_interjection(-100)
        decision = should_respond_in_group(
            self._base_info("random plauder"),
            behavior={"autonomous_interjection": True, "interjection_trigger": "smart"},
            group_manager=gm,
        )
        assert decision["respond"] is False

    def test_autonomous_off_skips(self):
        gm = GroupManager()
        decision = should_respond_in_group(
            self._base_info("random plauder"),
            behavior={"autonomous_interjection": False},
            group_manager=gm,
        )
        assert decision["respond"] is False

    def test_empty_text_skips(self):
        gm = GroupManager()
        decision = should_respond_in_group(
            self._base_info(""),
            behavior={"autonomous_interjection": True, "interjection_trigger": "smart"},
            group_manager=gm,
        )
        assert decision["respond"] is False


class TestGroupManager:
    def test_record_and_fetch_messages(self):
        gm = GroupManager()
        gm.record_message(1, "alice", "hi")
        gm.record_message(1, "bob", "moin")
        text = gm.recent_messages_text(1)
        assert "alice: hi" in text
        assert "bob: moin" in text

    def test_cooldown_expires(self):
        gm = GroupManager(cooldown_seconds=1)
        gm.mark_interjection(1)
        assert gm.cooldown_active(1) is True
        time.sleep(1.1)
        assert gm.cooldown_active(1) is False


class TestSmartInterjection:
    def test_skip_response_true_on_skip(self):
        assert is_skip_response("SKIP") is True
        assert is_skip_response("skip") is True
        assert is_skip_response("  SKIP  ") is True
        assert is_skip_response('"SKIP"') is True

    def test_skip_response_false_on_content(self):
        assert is_skip_response("Ich wuerde was sagen wollen") is False

    def test_build_prompt_includes_recent(self):
        prompt = build_smart_interjection_prompt("alice: hi\nbob: moin")
        assert "alice: hi" in prompt
        assert "bob: moin" in prompt


# ----- HitL -----

class TestHitlManager:
    def test_create_request(self):
        hm = HitlManager()
        req = hm.create_request("code_execution", 42, "chris", "print('hi')")
        assert req.status == "pending"
        assert req.request_id
        assert hm.get(req.request_id).details == "print('hi')"

    def test_approve_sets_status(self):
        hm = HitlManager()
        req = hm.create_request("code_execution", 42, "chris", "details")
        assert hm.approve(req.request_id) is True
        assert hm.get(req.request_id).status == "approved"

    def test_reject_sets_status(self):
        hm = HitlManager()
        req = hm.create_request("code_execution", 42, "chris", "details")
        assert hm.reject(req.request_id, admin_comment="nein") is True
        stored = hm.get(req.request_id)
        assert stored.status == "rejected"
        assert stored.admin_comment == "nein"

    def test_approve_unknown_returns_false(self):
        hm = HitlManager()
        assert hm.approve("unknown") is False

    def test_wait_for_decision_timeout(self):
        async def go():
            hm = HitlManager(timeout_seconds=1)
            req = hm.create_request("code_execution", 42, "chris", "details")
            status = await hm.wait_for_decision(req.request_id, timeout=0.1)
            return status
        status = asyncio.run(go())
        assert status == "timeout"

    def test_wait_for_decision_approved(self):
        async def go():
            hm = HitlManager(timeout_seconds=5)
            req = hm.create_request("code_execution", 42, "chris", "details")
            # Approve nach einem kleinen Delay
            async def approver():
                await asyncio.sleep(0.05)
                hm.approve(req.request_id)
            task = asyncio.create_task(approver())
            status = await hm.wait_for_decision(req.request_id, timeout=2)
            await task
            return status
        assert asyncio.run(go()) == "approved"


class TestHitlHelpers:
    def test_keyboard_contains_both_buttons(self):
        kb = build_admin_keyboard("abc123")
        row = kb["inline_keyboard"][0]
        assert any("Freigeben" in b["text"] for b in row)
        assert any("Ablehnen" in b["text"] for b in row)
        assert all("abc123" in b["callback_data"] for b in row)

    def test_parse_callback_data_approve(self):
        p = parse_callback_data("hitl_approve:abc123")
        assert p == {"action": "hitl_approve", "request_id": "abc123"}

    def test_parse_callback_data_reject(self):
        p = parse_callback_data("hitl_reject:xyz")
        assert p["action"] == "hitl_reject"
        assert p["request_id"] == "xyz"

    def test_parse_callback_data_invalid(self):
        assert parse_callback_data("") is None
        assert parse_callback_data("other:stuff") is None

    def test_admin_message_has_id(self):
        from zerberus.modules.telegram.hitl import HitlRequest
        req = HitlRequest(
            request_id="abc",
            request_type="code_execution",
            requester_chat_id=42,
            requester_username="chris",
            details="details",
        )
        msg = build_admin_message(req)
        assert "abc" in msg
        assert "chris" in msg

    def test_decision_message_variants(self):
        from zerberus.modules.telegram.hitl import HitlRequest
        req = HitlRequest(
            request_id="abc", request_type="x", requester_chat_id=1,
            requester_username="u", details="d",
        )
        req.status = "approved"
        assert "freigegeben" in build_group_decision_message(req)
        req.status = "rejected"
        req.admin_comment = "foo"
        assert "abgelehnt" in build_group_decision_message(req)
        req.status = "timeout"
        assert "abgebrochen" in build_group_decision_message(req).lower()

    def test_group_waiting_message(self):
        from zerberus.modules.telegram.hitl import HitlRequest
        req = HitlRequest(
            request_id="abc", request_type="x", requester_chat_id=1,
            requester_username="u", details="d",
        )
        msg = build_group_waiting_message(req)
        assert "Admin" in msg
        assert "abc" in msg


# ══════════════════════════════════════════════════════════════════
#  Patch 155: Long-Polling Tests
# ══════════════════════════════════════════════════════════════════

class TestLongPolling:
    """Tests fuer get_me(), get_updates() und long_polling_loop()."""

    def test_get_updates_no_token(self):
        """Kein bot_token → leere Liste, kein HTTP-Call."""
        from zerberus.modules.telegram.bot import get_updates
        result = asyncio.run(get_updates("", offset=0))
        assert result == []

    def test_get_me_no_token(self):
        """Kein bot_token → None."""
        from zerberus.modules.telegram.bot import get_me
        result = asyncio.run(get_me(""))
        assert result is None

    def test_get_updates_parses_response(self, monkeypatch):
        """getUpdates ruft POST auf getUpdates, parst result-Array."""
        from zerberus.modules.telegram import bot as bot_module

        calls = {}

        class FakeResp:
            status_code = 200
            text = ""
            def json(self):
                return {"ok": True, "result": [
                    {"update_id": 1, "message": {"text": "hi"}},
                    {"update_id": 2, "message": {"text": "ho"}},
                ]}

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None):
                calls["url"] = url
                calls["json"] = json
                return FakeResp()

        monkeypatch.setattr(bot_module.httpx, "AsyncClient", FakeClient)
        result = asyncio.run(bot_module.get_updates("TOKEN", offset=42, timeout=30))
        assert len(result) == 2
        assert result[0]["update_id"] == 1
        # Offset + timeout + allowed_updates werden durchgereicht
        assert calls["json"]["offset"] == 42
        assert calls["json"]["timeout"] == 30
        assert "message" in calls["json"]["allowed_updates"]
        assert "callback_query" in calls["json"]["allowed_updates"]
        assert "getUpdates" in calls["url"]

    def test_get_updates_timeout_returns_empty(self, monkeypatch):
        """httpx.TimeoutException → [] ohne Warning-Log (normaler Long-Poll-Ablauf)."""
        from zerberus.modules.telegram import bot as bot_module
        import httpx

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None):
                raise httpx.TimeoutException("long-poll idle")

        monkeypatch.setattr(bot_module.httpx, "AsyncClient", FakeClient)
        result = asyncio.run(bot_module.get_updates("TOKEN", offset=0))
        assert result == []

    def test_get_updates_http_error_returns_empty(self, monkeypatch):
        """HTTP 500 → [] (Loop macht weiter)."""
        from zerberus.modules.telegram import bot as bot_module

        class FakeResp:
            status_code = 500
            text = "oops"

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None):
                return FakeResp()

        monkeypatch.setattr(bot_module.httpx, "AsyncClient", FakeClient)
        result = asyncio.run(bot_module.get_updates("TOKEN", offset=0))
        assert result == []

    def test_long_polling_loop_calls_delete_webhook(self, monkeypatch):
        """Beim Start MUSS der alte Webhook entfernt werden (Telegram 409 sonst)."""
        from zerberus.modules.telegram import bot as bot_module

        deregistered = {"called": False}

        async def fake_deregister(token, timeout=10.0):
            deregistered["called"] = True
            return True

        async def fake_get_updates(*args, **kwargs):
            # Direkt CancelledError werfen, damit der Loop nach einem Aufruf endet
            raise asyncio.CancelledError()

        monkeypatch.setattr(bot_module, "deregister_webhook", fake_deregister)
        monkeypatch.setattr(bot_module, "get_updates", fake_get_updates)

        async def dummy_handler(update):
            pass

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bot_module.long_polling_loop("TOKEN", dummy_handler))
        assert deregistered["called"], "deregister_webhook wurde nicht aufgerufen"

    def test_long_polling_loop_advances_offset(self, monkeypatch):
        """Nach Handler-Aufruf wird offset auf update_id+1 gesetzt."""
        from zerberus.modules.telegram import bot as bot_module

        offsets_seen = []
        update_batches = [
            [{"update_id": 10, "message": {"text": "a"}}],
            [{"update_id": 17, "message": {"text": "b"}}],
        ]

        async def fake_deregister(token, timeout=10.0):
            return True

        async def fake_get_updates(token, offset=0, timeout=30, allowed_updates=None):
            offsets_seen.append(offset)
            if update_batches:
                return update_batches.pop(0)
            raise asyncio.CancelledError()

        monkeypatch.setattr(bot_module, "deregister_webhook", fake_deregister)
        monkeypatch.setattr(bot_module, "get_updates", fake_get_updates)

        handled = []

        async def handler(update):
            handled.append(update["update_id"])

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bot_module.long_polling_loop("TOKEN", handler))
        assert handled == [10, 17]
        assert offsets_seen == [0, 11, 18]  # startet 0, dann update_id+1

    def test_long_polling_handler_exception_does_not_break_loop(self, monkeypatch):
        """Handler-Exception → wird geloggt, offset schreitet trotzdem fort."""
        from zerberus.modules.telegram import bot as bot_module

        async def fake_deregister(token, timeout=10.0):
            return True

        batches = [[{"update_id": 5, "message": {"text": "x"}}]]

        async def fake_get_updates(token, offset=0, timeout=30, allowed_updates=None):
            if batches:
                return batches.pop(0)
            raise asyncio.CancelledError()

        monkeypatch.setattr(bot_module, "deregister_webhook", fake_deregister)
        monkeypatch.setattr(bot_module, "get_updates", fake_get_updates)

        async def bad_handler(update):
            raise RuntimeError("boom")

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bot_module.long_polling_loop("TOKEN", bad_handler))
        # Keine Assertion nötig — Test gilt als bestanden, wenn kein RuntimeError propagiert

    def test_long_polling_loop_no_token_exits_silently(self):
        """Ohne bot_token → Funktion kehrt sofort zurück, ohne Fehler."""
        from zerberus.modules.telegram.bot import long_polling_loop

        async def handler(update):
            pass

        # Kein Exception, kein Hang — läuft durch
        asyncio.run(long_polling_loop("", handler))

    def test_startup_huginn_polling_mode_creates_task(self, monkeypatch):
        """mode=polling → startup_huginn erzeugt einen Background-Task + cached bot_user_id."""
        from zerberus.modules.telegram import router as telegram_router
        from zerberus.modules.telegram import bot as bot_module

        class FakeSettings:
            modules = {"telegram": {
                "enabled": True,
                "bot_token": "T",
                "mode": "polling",
            }}

        async def fake_get_me(token, timeout=10.0):
            return {"id": 999, "username": "FakeBot"}

        loop_started = {"flag": False}

        async def fake_loop(bot_token, handler, poll_timeout=30, error_backoff=5.0):
            loop_started["flag"] = True
            # Einmal yielden, damit der Task den Code erreicht, dann weitergeben
            await asyncio.sleep(0.01)
            # Zurueckkehren = Task done — Test-freundlich
            return

        monkeypatch.setattr(bot_module, "get_me", fake_get_me)
        monkeypatch.setattr(telegram_router, "get_me", fake_get_me)
        monkeypatch.setattr(bot_module, "long_polling_loop", fake_loop)
        monkeypatch.setattr(telegram_router, "long_polling_loop", fake_loop)

        async def run():
            task = await telegram_router.startup_huginn(FakeSettings())
            assert task is not None, "startup_huginn hat keinen Task zurueckgegeben"
            assert isinstance(task, asyncio.Task), "Rueckgabe ist keine Task"
            # _bot_user_id sollte gecacht sein
            assert telegram_router._bot_user_id == 999, (
                f"_bot_user_id nicht gecacht (ist {telegram_router._bot_user_id})"
            )
            # Task ausrunnen lassen
            await task
            assert loop_started["flag"], "fake_loop wurde nie aufgerufen"
            assert task.done()

        asyncio.run(run())

    def test_startup_huginn_webhook_mode_returns_none(self, monkeypatch):
        """mode=webhook → kein Polling-Task, alter Pfad greift."""
        from zerberus.modules.telegram import router as telegram_router
        from zerberus.modules.telegram import bot as bot_module

        class FakeSettings:
            modules = {"telegram": {
                "enabled": True,
                "bot_token": "T",
                "mode": "webhook",
                "webhook_url": "https://real.example.com/webhook",
            }}

        async def fake_get_me(token, timeout=10.0):
            return {"id": 1, "username": "B"}

        registered = {"called": False}

        async def fake_register(token, url, timeout=10.0):
            registered["called"] = True
            return True

        monkeypatch.setattr(bot_module, "get_me", fake_get_me)
        monkeypatch.setattr(telegram_router, "get_me", fake_get_me)
        monkeypatch.setattr(telegram_router, "register_webhook", fake_register)

        result = asyncio.run(telegram_router.startup_huginn(FakeSettings()))
        assert result is None, "mode=webhook darf keinen Task zurueckgeben"
        assert registered["called"], "register_webhook wurde nicht aufgerufen"

    def test_startup_huginn_disabled_returns_none(self):
        """enabled=false → None, kein Task."""
        from zerberus.modules.telegram import router as telegram_router

        class FakeSettings:
            modules = {"telegram": {"enabled": False}}

        result = asyncio.run(telegram_router.startup_huginn(FakeSettings()))
        assert result is None
