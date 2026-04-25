"""Patch 163 — Tests für Per-User Rate-Limiter + Telegram-Integration.

Deckt:
- InMemoryRateLimiter: under/over Limit, Cooldown-Persistenz/Ablauf, Sliding-Window,
  User-Isolation, Cleanup, Singleton, remaining-Count.
- Router-Integration: rate-limited User bekommt genau 1× „Sachte, Keule",
  Folge-Nachrichten still ignoriert.
- Guard-Fail-Policy: allow (Default) vs block.
- OpenRouter-Retry-Backoff: 429 → Retry → Erfolg, Exhausted → Kristallkugel,
  400 → kein Retry.
- Ausgangs-Throttle: under Limit sofort, über Limit wartet via asyncio.sleep.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from zerberus.core import rate_limiter as rl_module
from zerberus.core.rate_limiter import (
    InMemoryRateLimiter,
    RateLimitResult,
    _reset_rate_limiter_for_tests,
    get_rate_limiter,
)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Singleton-State zwischen Tests sauber halten."""
    _reset_rate_limiter_for_tests()
    yield
    _reset_rate_limiter_for_tests()


# ══════════════════════════════════════════════════════════════════
#  InMemoryRateLimiter — Verhalten
# ══════════════════════════════════════════════════════════════════


class TestInMemoryRateLimiter:
    def test_allowed_under_limit(self):
        rl = InMemoryRateLimiter(max_rpm=3, cooldown_seconds=10)
        for _ in range(3):
            res = rl.check("user1")
            assert res.allowed is True
            assert res.first_rejection is False

    def test_blocked_over_limit(self):
        rl = InMemoryRateLimiter(max_rpm=2, cooldown_seconds=10)
        rl.check("user1")
        rl.check("user1")
        res = rl.check("user1")
        assert res.allowed is False
        assert res.first_rejection is True
        assert res.retry_after == pytest.approx(10.0)
        assert res.remaining == 0

    def test_cooldown_persists_no_repeat_first_rejection(self):
        """Während Cooldown: jede Folge-Nachricht wird allowed=False, aber
        first_rejection nur EINMAL. Kein Spam-Reply."""
        rl = InMemoryRateLimiter(max_rpm=1, cooldown_seconds=10)
        rl.check("user1")
        first = rl.check("user1")
        assert first.allowed is False
        assert first.first_rejection is True
        for _ in range(5):
            follow = rl.check("user1")
            assert follow.allowed is False
            assert follow.first_rejection is False

    def test_cooldown_expires(self):
        """Nach Ablauf der Cooldown-Dauer ist der User wieder erlaubt."""
        rl = InMemoryRateLimiter(max_rpm=1, cooldown_seconds=1)
        rl.check("user1")
        rl.check("user1")  # blockt → Cooldown 1s
        time.sleep(1.1)
        res = rl.check("user1")
        assert res.allowed is True
        assert res.first_rejection is False

    def test_sliding_window_drops_old_timestamps(self, monkeypatch):
        """Timestamps älter als 60s fallen aus dem Fenster und zählen nicht mehr."""
        rl = InMemoryRateLimiter(max_rpm=2, cooldown_seconds=10)
        # Bucket per Hand mit alten Timestamps füllen
        bucket = rl._buckets["user1"]
        bucket.timestamps.extend([time.time() - 70.0, time.time() - 65.0])
        # Trotz 2 alten Einträgen: Fenster leer → erlaubt
        res = rl.check("user1")
        assert res.allowed is True
        assert res.remaining == 1

    def test_different_users_independent(self):
        rl = InMemoryRateLimiter(max_rpm=1, cooldown_seconds=10)
        rl.check("alice")
        rl.check("alice")  # blockt
        res = rl.check("bob")
        assert res.allowed is True

    def test_cleanup_stale_buckets(self):
        rl = InMemoryRateLimiter(max_rpm=10)
        rl.check("active")
        # Stale-Bucket händisch einfügen
        rl._buckets["stale"].timestamps.append(time.time() - 600.0)
        removed = rl.cleanup()
        assert removed == 1
        assert "stale" not in rl._buckets
        assert "active" in rl._buckets

    def test_remaining_count_decreases(self):
        rl = InMemoryRateLimiter(max_rpm=3, cooldown_seconds=10)
        r1 = rl.check("user1")
        r2 = rl.check("user1")
        r3 = rl.check("user1")
        assert r1.remaining == 2
        assert r2.remaining == 1
        assert r3.remaining == 0


class TestRateLimiterSingleton:
    def test_get_rate_limiter_returns_singleton(self):
        a = get_rate_limiter(max_rpm=5)
        b = get_rate_limiter(max_rpm=99)  # zweiter Aufruf-Werte werden ignoriert
        assert a is b
        assert a.max_rpm == 5  # erste Werte gewinnen

    def test_reset_helper_clears_singleton(self):
        a = get_rate_limiter(max_rpm=5)
        _reset_rate_limiter_for_tests()
        b = get_rate_limiter(max_rpm=99)
        assert a is not b
        assert b.max_rpm == 99


# ══════════════════════════════════════════════════════════════════
#  Router-Integration — rate-limited User bekommt genau 1× Reply
# ══════════════════════════════════════════════════════════════════


class _FakeSettings163:
    def __init__(self, security_dict=None):
        self.modules = {
            "telegram": {
                "enabled": True,
                "bot_token": "T",
                "admin_chat_id": "42",
            }
        }
        if security_dict is not None:
            self.security = security_dict


def _reset_router_state():
    from zerberus.modules.telegram import router as telegram_router
    telegram_router._group_manager = None
    telegram_router._hitl_manager = None
    telegram_router._bot_user_id = None


class TestRateLimitIntegration:
    def test_rate_limited_user_gets_one_message(self, monkeypatch):
        from zerberus.modules.telegram import router as telegram_router

        _reset_router_state()
        # Limit auf 1 setzen, damit der zweite Hit blockt.
        rl_module._rate_limiter = InMemoryRateLimiter(max_rpm=1, cooldown_seconds=60)

        sends = []

        async def fake_send(token, chat_id, text, **kwargs):
            sends.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
            return True

        async def fake_process_text_message(*a, **kw):
            return {"sent": True}

        monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)
        monkeypatch.setattr(telegram_router, "_process_text_message", fake_process_text_message)

        settings = _FakeSettings163()

        def _msg_update(uid: int, mid: int) -> dict:
            return {
                "update_id": uid,
                "message": {
                    "message_id": mid,
                    "chat": {"id": 100, "type": "private"},
                    "from": {"id": 99, "username": "chris"},
                    "text": "hi",
                },
            }

        # Erste Nachricht — durchlässig
        r1 = asyncio.run(telegram_router.process_update(_msg_update(1, 1), settings))
        assert r1.get("skipped") != "rate_limited"

        # Zweite Nachricht — sollte rate-limited sein, EINE „Sachte, Keule"
        r2 = asyncio.run(telegram_router.process_update(_msg_update(2, 2), settings))
        assert r2.get("skipped") == "rate_limited"
        assert any("Sachte" in s["text"] for s in sends)

        # Dritte und vierte Nachricht — auch geblockt, aber KEINE weiteren Sends
        sends_count_after_first_block = len(sends)
        asyncio.run(telegram_router.process_update(_msg_update(3, 3), settings))
        asyncio.run(telegram_router.process_update(_msg_update(4, 4), settings))
        assert len(sends) == sends_count_after_first_block

    def test_rate_limit_skips_callback_query(self, monkeypatch):
        """Callback-Queries (Admin-HitL-Klicks) dürfen jederzeit, kein Rate-Limit."""
        from zerberus.modules.telegram import router as telegram_router

        _reset_router_state()
        rl_module._rate_limiter = InMemoryRateLimiter(max_rpm=1, cooldown_seconds=60)

        async def fake_send(*a, **kw):
            return True
        monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)

        # Mehrere Callback-Updates — der RateLimit-Block triggert nicht (kein "message")
        update = {
            "update_id": 10,
            "callback_query": {
                "id": "cb",
                "from": {"id": 99},
                "data": "hitl_approve:nonexistent",
            },
        }
        # Der callback wird wegen "unknown_request" gedroppt — wir prüfen nur,
        # dass NICHTS mit rate_limited zurückkommt:
        for _ in range(5):
            r = asyncio.run(telegram_router.process_update(update, _FakeSettings163()))
            assert r.get("skipped") != "rate_limited"


# ══════════════════════════════════════════════════════════════════
#  Guard-Fail-Policy
# ══════════════════════════════════════════════════════════════════


class TestGuardFailPolicy:
    def test_resolve_default_is_allow(self):
        """Kein security-Key → Default "allow"."""
        from zerberus.modules.telegram import router as telegram_router

        class S:
            modules = {"telegram": {"enabled": True}}
        assert telegram_router._resolve_guard_fail_policy(S()) == "allow"

    def test_resolve_block(self):
        from zerberus.modules.telegram import router as telegram_router

        class S:
            modules = {"telegram": {"enabled": True}}
            security = {"guard_fail_policy": "block"}
        assert telegram_router._resolve_guard_fail_policy(S()) == "block"

    def test_guard_fail_allow_passes_response_through(self, monkeypatch):
        """Policy "allow" + Guard ERROR → Antwort wird trotzdem gesendet."""
        from zerberus.modules.telegram import router as telegram_router
        from zerberus.modules.telegram.bot import HuginnConfig

        sends = []

        async def fake_send(token, chat_id, text, **kwargs):
            sends.append({"chat_id": chat_id, "text": text})
            return True

        async def fake_call_llm(**kw):
            return {"content": "Hallo Mensch.", "latency_ms": 5}

        async def fake_run_guard(*a, **kw):
            return {"verdict": "ERROR", "reason": "openrouter down"}

        monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)
        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        monkeypatch.setattr(telegram_router, "_run_guard", fake_run_guard)

        info = {
            "chat_id": 100, "message_id": 1, "user_id": 99, "username": "chris",
            "text": "hi", "chat_type": "private", "is_forwarded": False,
            "reply_to_message": None, "photo_file_ids": [], "message_thread_id": None,
        }
        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")

        class S:
            modules = {"telegram": {"enabled": True}}
            # security weggelassen → Default "allow"

        result = asyncio.run(telegram_router._process_text_message(
            info, cfg, S(), system_prompt=""
        ))
        assert result["sent"] is True
        assert any("Hallo Mensch" in s["text"] for s in sends)

    def test_guard_fail_block_holds_response(self, monkeypatch):
        """Policy "block" + Guard ERROR → Antwort wird NICHT gesendet,
        stattdessen Sicherheits-Hinweis."""
        from zerberus.modules.telegram import router as telegram_router
        from zerberus.modules.telegram.bot import HuginnConfig

        sends = []

        async def fake_send(token, chat_id, text, **kwargs):
            sends.append({"chat_id": chat_id, "text": text})
            return True

        async def fake_call_llm(**kw):
            return {"content": "Hallo Mensch.", "latency_ms": 5}

        async def fake_run_guard(*a, **kw):
            return {"verdict": "ERROR", "reason": "openrouter down"}

        monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)
        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        monkeypatch.setattr(telegram_router, "_run_guard", fake_run_guard)

        info = {
            "chat_id": 100, "message_id": 1, "user_id": 99, "username": "chris",
            "text": "hi", "chat_type": "private", "is_forwarded": False,
            "reply_to_message": None, "photo_file_ids": [], "message_thread_id": None,
        }
        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")

        class S:
            modules = {"telegram": {"enabled": True}}
            security = {"guard_fail_policy": "block"}

        result = asyncio.run(telegram_router._process_text_message(
            info, cfg, S(), system_prompt=""
        ))
        assert result.get("reason") == "guard_fail_block"
        # Es wurde EIN Hinweis gesendet, KEIN LLM-Output
        assert len(sends) == 1
        assert "Sicherheitsprüfung" in sends[0]["text"]
        assert "Hallo Mensch" not in sends[0]["text"]


# ══════════════════════════════════════════════════════════════════
#  OpenRouter Retry + Backoff
# ══════════════════════════════════════════════════════════════════


class TestOpenRouterRetry:
    def test_retry_succeeds_after_429(self, monkeypatch):
        """Erster Call 429, zweiter Call OK → kein Fallback."""
        from zerberus.modules.telegram import router as telegram_router

        responses = [
            {"content": "", "error": "HTTP 429", "latency_ms": 10},
            {"content": "Endlich.", "latency_ms": 12},
        ]
        calls = {"n": 0}

        async def fake_call_llm(**kw):
            calls["n"] += 1
            return responses.pop(0)

        async def fake_sleep(secs):
            return None  # Tests müssen nicht real warten

        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        monkeypatch.setattr(telegram_router.asyncio, "sleep", fake_sleep)

        result = asyncio.run(telegram_router._call_llm_with_retry(
            user_message="x", model="m", system_prompt=""
        ))
        assert result["content"] == "Endlich."
        assert calls["n"] == 2

    def test_retry_exhausted(self, monkeypatch):
        """3× 429 → letzter Result mit error wird zurückgegeben."""
        from zerberus.modules.telegram import router as telegram_router

        async def fake_call_llm(**kw):
            return {"content": "", "error": "HTTP 503"}

        async def fake_sleep(secs):
            return None

        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        monkeypatch.setattr(telegram_router.asyncio, "sleep", fake_sleep)

        result = asyncio.run(telegram_router._call_llm_with_retry(
            user_message="x", model="m", system_prompt=""
        ))
        assert result["content"] == ""
        assert "503" in (result.get("error") or "")

    def test_no_retry_on_400(self, monkeypatch):
        """400 = Bad Request → KEIN Retry, sofort zurück."""
        from zerberus.modules.telegram import router as telegram_router

        calls = {"n": 0}

        async def fake_call_llm(**kw):
            calls["n"] += 1
            return {"content": "", "error": "HTTP 400 Bad Request"}

        async def fake_sleep(secs):
            return None

        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        monkeypatch.setattr(telegram_router.asyncio, "sleep", fake_sleep)

        result = asyncio.run(telegram_router._call_llm_with_retry(
            user_message="x", model="m", system_prompt=""
        ))
        assert calls["n"] == 1
        assert "400" in (result.get("error") or "")

    def test_llm_unavailable_sends_kristallkugel(self, monkeypatch):
        """Wenn _call_llm_with_retry leer + error zurückgibt, sendet
        _process_text_message die Kristallkugel-Nachricht."""
        from zerberus.modules.telegram import router as telegram_router
        from zerberus.modules.telegram.bot import HuginnConfig

        sends = []

        async def fake_send(token, chat_id, text, **kwargs):
            sends.append(text)
            return True

        async def fake_call_llm(**kw):
            return {"content": "", "error": "HTTP 503"}

        async def fake_sleep(secs):
            return None

        monkeypatch.setattr(telegram_router, "send_telegram_message", fake_send)
        monkeypatch.setattr(telegram_router, "call_llm", fake_call_llm)
        monkeypatch.setattr(telegram_router.asyncio, "sleep", fake_sleep)

        info = {
            "chat_id": 100, "message_id": 1, "user_id": 99, "username": "chris",
            "text": "hi", "chat_type": "private", "is_forwarded": False,
            "reply_to_message": None, "photo_file_ids": [], "message_thread_id": None,
        }
        cfg = HuginnConfig(enabled=True, bot_token="T", model="m")

        class S:
            modules = {"telegram": {"enabled": True}}

        result = asyncio.run(telegram_router._process_text_message(
            info, cfg, S(), system_prompt=""
        ))
        assert result.get("reason") == "llm_unavailable"
        assert any("Kristallkugel" in t for t in sends)


# ══════════════════════════════════════════════════════════════════
#  Ausgangs-Throttle (bot.py)
# ══════════════════════════════════════════════════════════════════


class TestOutgoingThrottle:
    def setup_method(self):
        from zerberus.modules.telegram import bot as bot_module
        bot_module._reset_outgoing_throttle_for_tests()

    def test_throttle_under_limit_no_wait(self, monkeypatch):
        """Unter dem 15/min-Limit: keine Wartezeit."""
        from zerberus.modules.telegram import bot as bot_module

        slept = []

        async def fake_sleep(secs):
            slept.append(secs)

        async def fake_send(token, chat_id, text, **kwargs):
            return True

        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(bot_module, "send_telegram_message", fake_send)

        async def go():
            for _ in range(5):
                await bot_module.send_telegram_message_throttled("T", 100, "hi")

        asyncio.run(go())
        assert slept == []  # 5 < 15 → kein Throttle

    def test_throttle_at_limit_waits(self, monkeypatch):
        """Bei 15 Sends in <60s: der 16. Call ruft asyncio.sleep auf."""
        from zerberus.modules.telegram import bot as bot_module

        slept = []

        async def fake_sleep(secs):
            slept.append(secs)

        async def fake_send(token, chat_id, text, **kwargs):
            return True

        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(bot_module, "send_telegram_message", fake_send)

        # Tracker händisch mit 15 frischen Timestamps füllen
        now = time.time()
        bot_module._outgoing_timestamps[200] = [now - 5.0 + i * 0.1 for i in range(15)]

        asyncio.run(bot_module.send_telegram_message_throttled("T", 200, "hi"))
        assert len(slept) == 1
        assert slept[0] > 0
