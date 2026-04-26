"""Patch 167 — Tests fuer das HitL-Hardening (Phase C, Block 1-4).

Deckt ab:
1.  Task-Lifecycle: create → pending → approve → approved
2.  Task-Lifecycle: create → pending → reject → rejected
3.  Task-Lifecycle: create → pending → timeout → expired
4.  Ownership: Requester darf bestaetigen
5.  Ownership: Admin darf bestaetigen (Override geloggt)
6.  Ownership: Fremder User wird blockiert
7.  Callback-Data-Parsing fuer UUID4-Task-IDs
8.  Callback-Data-Parsing: Unbekannte Task-ID -> Fehlermeldung
9.  Doppel-Bestaetigung: Bereits aufgeloester Task -> False
10. Timeout-Sweep: Tasks aelter als timeout werden expired
11. DB-Persistenz: Task ueberlebt simulierten Restart
12. Intent-Policy: CODE/FILE/ADMIN -> needs_hitl=True, CHAT/SEARCH/IMAGE -> False
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from zerberus.modules.telegram.hitl import (
    HitlManager,
    HitlTask,
    build_admin_keyboard,
    hitl_sweep_loop,
    parse_callback_data,
)


class _FakeHitlSettings:
    """Minimal-Settings-Stub fuer ``process_update``-Tests.

    ``process_update`` braucht nur ``settings.modules`` als Dict — der Rest
    der Settings-Struktur wird im HitL-Pfad nicht angefasst.
    """

    def __init__(self, admin_chat_id: str = "999"):
        self.modules = {
            "telegram": {
                "enabled": True,
                "bot_token": "TESTTOKEN",
                "admin_chat_id": admin_chat_id,
                "hitl": {"timeout_seconds": 300},
            }
        }


# ──────────────────────────────────────────────────────────────────────
#  Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(monkeypatch):
    """Frische SQLite-DB fuer Persistenz-Tests."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_hitl.db"
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


# ──────────────────────────────────────────────────────────────────────
#  1-3. Task-Lifecycle (approve / reject / timeout)
# ──────────────────────────────────────────────────────────────────────


class TestTaskLifecycle:
    def test_create_then_approve(self, tmp_db):
        async def run():
            mgr = HitlManager(timeout_seconds=300)
            task = await mgr.create_task(
                requester_id=42, chat_id=100, intent="CODE",
                payload={"snippet": "print('hi')"},
            )
            assert task.status == "pending"
            assert task.id and len(task.id) == 32  # UUID4 hex

            ok = await mgr.resolve_task(task.id, resolver_id=42, decision="approved")
            assert ok is True
            refreshed = await mgr.get_task(task.id)
            assert refreshed.status == "approved"
            assert refreshed.resolved_by == 42
            assert refreshed.resolved_at is not None

        asyncio.run(run())

    def test_create_then_reject(self, tmp_db):
        async def run():
            mgr = HitlManager(timeout_seconds=300)
            task = await mgr.create_task(
                requester_id=1, chat_id=2, intent="ADMIN",
            )
            ok = await mgr.resolve_task(
                task.id, resolver_id=999, decision="rejected",
                admin_comment="nope",
            )
            assert ok is True
            refreshed = await mgr.get_task(task.id)
            assert refreshed.status == "rejected"
            assert refreshed.admin_comment == "nope"

        asyncio.run(run())

    def test_create_then_timeout_via_sweep(self, tmp_db):
        async def run():
            mgr = HitlManager(timeout_seconds=1)
            task = await mgr.create_task(
                requester_id=1, chat_id=2, intent="FILE",
            )
            # Backdate created_at, sodass sie sofort als stale gilt.
            task.created_at = datetime.utcnow() - timedelta(seconds=5)
            mgr._cache[task.id] = task
            from zerberus.core.database import HitlTask as HitlTaskRow
            from sqlalchemy import update
            async with tmp_db() as session:
                await session.execute(
                    update(HitlTaskRow)
                    .where(HitlTaskRow.id == task.id)
                    .values(created_at=task.created_at)
                )
                await session.commit()

            expired = await mgr.expire_stale_tasks()
            assert any(t.id == task.id for t in expired)
            refreshed = await mgr.get_task(task.id)
            assert refreshed.status == "expired"

        asyncio.run(run())


# ──────────────────────────────────────────────────────────────────────
#  4-6. Ownership (Requester / Admin / Fremder)
# ──────────────────────────────────────────────────────────────────────


class TestOwnership:
    """Die Ownership-Pruefung selbst sitzt im Router (Callback-Pfad).
    Hier testen wir nur, dass die Manager-API ``is_admin_override`` korrekt
    durchreicht und der Resolver-ID-Eintrag stimmt."""

    def test_requester_resolves_own_task(self, tmp_db):
        async def run():
            mgr = HitlManager()
            task = await mgr.create_task(requester_id=42, chat_id=100, intent="CODE")
            ok = await mgr.resolve_task(task.id, resolver_id=42, decision="approved")
            assert ok is True
            refreshed = await mgr.get_task(task.id)
            assert refreshed.resolved_by == 42

        asyncio.run(run())

    def test_admin_override_logged(self, tmp_db, caplog):
        async def run():
            mgr = HitlManager()
            task = await mgr.create_task(requester_id=42, chat_id=100, intent="CODE")
            await mgr.resolve_task(
                task.id, resolver_id=999, decision="approved",
                is_admin_override=True,
            )

        with caplog.at_level("INFO", logger="zerberus.huginn.hitl"):
            asyncio.run(run())
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "Admin-Override" in msgs

    def test_router_blocks_foreign_user(self, tmp_db, monkeypatch):
        """End-to-End-Probe: Klick eines Fremden wird im Router geblockt."""
        from zerberus.modules.telegram import router as router_mod

        router_mod._reset_telegram_singletons_for_tests()

        sent: list[dict] = []
        answered: list[dict] = []

        async def fake_send(*a, **kw):
            sent.append({"args": a, "kwargs": kw})
            return True

        async def fake_answer(callback_query_id, bot_token, text=None, show_alert=False, timeout=10.0):
            answered.append({"text": text, "show_alert": show_alert})
            return True

        monkeypatch.setattr(router_mod, "send_telegram_message", fake_send)
        monkeypatch.setattr(router_mod, "answer_callback_query", fake_answer)

        settings = _FakeHitlSettings(admin_chat_id="999")

        async def run():
            _, mgr = router_mod._get_managers(settings)
            task = await mgr.create_task(
                requester_id=42, chat_id=100, intent="CODE",
            )
            cb_update = {
                "update_id": 1,
                "callback_query": {
                    "id": "cb1",
                    "from": {"id": 7777},  # Fremder User
                    "data": f"hitl_approve:{task.id}",
                },
            }
            result = await router_mod.process_update(cb_update, settings)
            return result, await mgr.get_task(task.id)

        result, task_after = asyncio.run(run())
        assert result.get("skipped") == "spoofing"
        assert task_after.status == "pending"
        assert any("nicht deine Anfrage" in (a["text"] or "") for a in answered)
        router_mod._reset_telegram_singletons_for_tests()


# ──────────────────────────────────────────────────────────────────────
#  7-8. Callback-Data-Parsing
# ──────────────────────────────────────────────────────────────────────


class TestCallbackParsing:
    def test_uuid_callback_parsed(self):
        import uuid
        rid = uuid.uuid4().hex
        parsed = parse_callback_data(f"hitl_approve:{rid}")
        assert parsed == {"action": "hitl_approve", "request_id": rid}
        assert len(parsed["request_id"]) == 32

    def test_unknown_task_id_handled_gracefully(self, tmp_db, monkeypatch):
        from zerberus.modules.telegram import router as router_mod

        router_mod._reset_telegram_singletons_for_tests()

        answered: list[dict] = []

        async def fake_send(*a, **kw): return True

        async def fake_answer(callback_query_id, bot_token, text=None, show_alert=False, timeout=10.0):
            answered.append({"text": text, "show_alert": show_alert})
            return True

        monkeypatch.setattr(router_mod, "send_telegram_message", fake_send)
        monkeypatch.setattr(router_mod, "answer_callback_query", fake_answer)

        settings = _FakeHitlSettings(admin_chat_id="999")

        async def run():
            cb_update = {
                "update_id": 2,
                "callback_query": {
                    "id": "cb2",
                    "from": {"id": 999},
                    "data": "hitl_approve:doesnotexist",
                },
            }
            return await router_mod.process_update(cb_update, settings)

        result = asyncio.run(run())
        assert result.get("skipped") == "unknown_request"
        assert any("unbekannt" in (a["text"] or "").lower() or
                   "abgelaufen" in (a["text"] or "").lower()
                   for a in answered)
        router_mod._reset_telegram_singletons_for_tests()


# ──────────────────────────────────────────────────────────────────────
#  9. Doppel-Bestaetigung
# ──────────────────────────────────────────────────────────────────────


class TestDoubleResolution:
    def test_resolve_twice_second_returns_false(self, tmp_db):
        async def run():
            mgr = HitlManager()
            task = await mgr.create_task(requester_id=1, chat_id=2, intent="CODE")
            ok1 = await mgr.resolve_task(task.id, resolver_id=1, decision="approved")
            ok2 = await mgr.resolve_task(task.id, resolver_id=1, decision="rejected")
            return ok1, ok2

        ok1, ok2 = asyncio.run(run())
        assert ok1 is True
        assert ok2 is False


# ──────────────────────────────────────────────────────────────────────
#  10. Timeout-Sweep
# ──────────────────────────────────────────────────────────────────────


class TestTimeoutSweep:
    def test_sweep_marks_old_tasks_expired(self, tmp_db):
        async def run():
            mgr = HitlManager(timeout_seconds=10)
            t_fresh = await mgr.create_task(requester_id=1, chat_id=10, intent="FILE")
            t_stale = await mgr.create_task(requester_id=2, chat_id=20, intent="FILE")

            # Stale-Task aelter machen — direkt in DB.
            from zerberus.core.database import HitlTask as HitlTaskRow
            from sqlalchemy import update
            stale_time = datetime.utcnow() - timedelta(seconds=60)
            async with tmp_db() as session:
                await session.execute(
                    update(HitlTaskRow)
                    .where(HitlTaskRow.id == t_stale.id)
                    .values(created_at=stale_time)
                )
                await session.commit()
            mgr._cache[t_stale.id].created_at = stale_time

            expired = await mgr.expire_stale_tasks()
            ids = {t.id for t in expired}
            assert t_stale.id in ids
            assert t_fresh.id not in ids

            after_fresh = await mgr.get_task(t_fresh.id)
            after_stale = await mgr.get_task(t_stale.id)
            assert after_fresh.status == "pending"
            assert after_stale.status == "expired"

        asyncio.run(run())

    def test_sweep_loop_callback_fires(self, tmp_db):
        async def run():
            mgr = HitlManager(timeout_seconds=10)
            task = await mgr.create_task(requester_id=1, chat_id=10, intent="FILE")
            # Stale machen
            from zerberus.core.database import HitlTask as HitlTaskRow
            from sqlalchemy import update
            stale_time = datetime.utcnow() - timedelta(seconds=60)
            async with tmp_db() as session:
                await session.execute(
                    update(HitlTaskRow)
                    .where(HitlTaskRow.id == task.id)
                    .values(created_at=stale_time)
                )
                await session.commit()
            mgr._cache[task.id].created_at = stale_time

            seen: list[str] = []

            async def on_expired(t):
                seen.append(t.id)

            loop_task = asyncio.create_task(
                hitl_sweep_loop(mgr, interval_seconds=0.05, on_expired=on_expired)
            )
            await asyncio.sleep(0.2)
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
            return seen

        seen = asyncio.run(run())
        assert len(seen) == 1


# ──────────────────────────────────────────────────────────────────────
#  11. Persistenz ueber simulierten Restart
# ──────────────────────────────────────────────────────────────────────


class TestPersistence:
    def test_task_survives_manager_restart(self, tmp_db):
        async def run():
            # Erste Manager-Instanz legt Task an.
            mgr1 = HitlManager()
            task = await mgr1.create_task(
                requester_id=42, chat_id=100, intent="CODE",
                requester_username="chris", details="rm -rf /",
                payload={"x": 1},
            )
            task_id = task.id

            # Neuer Manager simuliert Server-Restart — Cache leer, DB voll.
            mgr2 = HitlManager()
            assert task_id not in mgr2._cache
            recovered = await mgr2.get_task(task_id)
            assert recovered is not None
            assert recovered.status == "pending"
            assert recovered.requester_id == 42
            assert recovered.chat_id == 100
            assert recovered.intent == "CODE"
            assert recovered.payload == {"x": 1}
            assert recovered.requester_username == "chris"
            assert recovered.details == "rm -rf /"

        asyncio.run(run())

    def test_pending_query_survives_restart(self, tmp_db):
        async def run():
            mgr1 = HitlManager()
            t1 = await mgr1.create_task(requester_id=1, chat_id=10, intent="CODE")
            t2 = await mgr1.create_task(requester_id=2, chat_id=20, intent="FILE")
            await mgr1.resolve_task(t1.id, resolver_id=1, decision="approved")

            mgr2 = HitlManager()
            pending = await mgr2.get_pending_tasks()
            ids = {t.id for t in pending}
            assert t2.id in ids
            assert t1.id not in ids

        asyncio.run(run())


# ──────────────────────────────────────────────────────────────────────
#  12. Intent-Policy (delegiert an HitlPolicy)
# ──────────────────────────────────────────────────────────────────────


class TestIntentPolicyMatrix:
    """Sanity-Check der Patch-164-Policy unter Patch-167-Annahmen.

    Detailtests stehen in test_hitl_policy.py — hier nur die Matrix, dass
    fuer CODE/FILE/ADMIN ``button``-Bestaetigung, fuer CHAT/SEARCH/IMAGE
    keine HitL noetig ist.
    """

    def test_policy_matrix(self):
        from zerberus.core.hitl_policy import HitlPolicy
        from zerberus.core.intent import HuginnIntent
        from zerberus.core.intent_parser import ParsedResponse

        def _parsed(intent: HuginnIntent, needs_hitl: bool = True) -> ParsedResponse:
            return ParsedResponse(
                intent=intent, effort=3, needs_hitl=needs_hitl,
                body="x", raw_header={"intent": intent.value},
            )

        p = HitlPolicy()
        for intent in (HuginnIntent.CODE, HuginnIntent.FILE, HuginnIntent.ADMIN):
            assert p.evaluate(_parsed(intent, True))["needs_hitl"] is True
            assert p.evaluate(_parsed(intent, True))["hitl_type"] == "button"
        for intent in (HuginnIntent.CHAT, HuginnIntent.SEARCH, HuginnIntent.IMAGE):
            assert p.evaluate(_parsed(intent, True))["needs_hitl"] is False


# ──────────────────────────────────────────────────────────────────────
#  Bonus: Builder-Helfer mit UUID4-Tasks
# ──────────────────────────────────────────────────────────────────────


class TestBuilders:
    def test_keyboard_with_uuid(self):
        import uuid
        rid = uuid.uuid4().hex
        kb = build_admin_keyboard(rid)
        row = kb["inline_keyboard"][0]
        assert row[0]["callback_data"] == f"hitl_approve:{rid}"
        assert row[1]["callback_data"] == f"hitl_reject:{rid}"

    def test_hitl_task_legacy_aliases(self):
        t = HitlTask(id="abc", requester_id=42, chat_id=100, intent="CODE")
        assert t.request_id == "abc"
        assert t.request_type == "CODE"
        assert t.requester_chat_id == 100
        assert t.requester_user_id == 42
