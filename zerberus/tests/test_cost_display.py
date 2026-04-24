"""Tests für Patch 136 — Kostenanzeige-Fix im Hel LLM-Tab.

Der Bug: das alte Frontend interpretierte `last_cost` als „pro 1M Tokens"
und multiplizierte mit 1_000_000. Korrekt: `last_cost` ist bereits der
tatsächliche USD-Betrag.

Fix: neue Endpoint-Felder `last_cost_usd`, `last_cost_eur`, `today_total_usd`,
`today_total_eur`, `balance_eur`. Frontend zeigt EUR direkt.
"""
from __future__ import annotations

import asyncio
import inspect
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


@pytest.fixture
def tmp_db(monkeypatch):
    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "cost.db"
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


class TestTodayTotalCost:
    def test_empty_costs_returns_zero(self, tmp_db):
        from zerberus.app.routers.hel import _get_today_total_cost
        total = asyncio.run(_get_today_total_cost())
        assert total == 0.0

    def test_costs_today_sum(self, tmp_db):
        from zerberus.app.routers.hel import _get_today_total_cost

        async def seed():
            async with tmp_db() as s:
                for amount in (0.0012, 0.0034, 0.0005):
                    await s.execute(sa_text(
                        "INSERT INTO costs (model, cost, timestamp) "
                        "VALUES ('test-model', :c, datetime('now'))"
                    ), {"c": amount})
                await s.commit()

        asyncio.run(seed())
        total = asyncio.run(_get_today_total_cost())
        assert abs(total - (0.0012 + 0.0034 + 0.0005)) < 1e-9

    def test_yesterday_not_counted(self, tmp_db):
        from zerberus.app.routers.hel import _get_today_total_cost

        async def seed():
            async with tmp_db() as s:
                await s.execute(sa_text(
                    "INSERT INTO costs (model, cost, timestamp) "
                    "VALUES ('test-model', 0.99, datetime('now', '-1 day'))"
                ))
                await s.execute(sa_text(
                    "INSERT INTO costs (model, cost, timestamp) "
                    "VALUES ('test-model', 0.01, datetime('now'))"
                ))
                await s.commit()

        asyncio.run(seed())
        total = asyncio.run(_get_today_total_cost())
        assert total == pytest.approx(0.01, abs=1e-9)


class TestBalanceEndpointShape:
    """Das Balance-Endpoint liefert die neuen Patch-136-Felder."""

    def test_error_fallback_includes_cost_fields(self, tmp_db, monkeypatch):
        """Bei OpenRouter-Fehler werden Cost-Felder trotzdem aus der DB geliefert."""
        from zerberus.app.routers.hel import get_balance
        # API-Key weg → HTTP fails oder Auth-Error → Fallback-Pfad
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = asyncio.run(get_balance())
        assert "last_cost_usd" in result
        assert "last_cost_eur" in result
        assert "today_total_usd" in result
        assert "today_total_eur" in result
        assert "fx_usd_to_eur" in result
        assert 0.5 < result["fx_usd_to_eur"] < 1.5

    def test_eur_conversion_correct(self, tmp_db, monkeypatch):
        """last_cost_eur = last_cost_usd * fx_usd_to_eur"""
        from zerberus.app.routers.hel import get_balance

        async def seed():
            async with tmp_db() as s:
                await s.execute(sa_text(
                    "INSERT INTO costs (model, cost, timestamp) "
                    "VALUES ('m', 0.01, datetime('now'))"
                ))
                await s.commit()

        asyncio.run(seed())
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = asyncio.run(get_balance())
        # last_cost_usd = 0.01, fx = 0.92 → last_cost_eur ≈ 0.0092
        assert result["last_cost_eur"] == pytest.approx(0.01 * 0.92, abs=1e-6)


class TestFrontendFix:
    """Das Frontend darf nicht mehr mit 1_000_000 multiplizieren."""

    def test_old_bug_string_removed(self):
        """Der Bug-String 'last_cost || 0) * 1_000_000' (im balance-Block) darf nicht mehr vorkommen.

        Hinweis: `* 1_000_000` ist legitim für OpenRouter-Modellpreisanzeige
        (Preis-pro-Token → Preis-pro-1M-Tokens). Der Bug war die Anwendung
        auf _tatsächliche_ Kosten einer Anfrage.
        """
        with open("zerberus/app/routers/hel.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "parseFloat(balance.last_cost || 0)" not in source, (
            "Alte last_cost-Parse im balance-Block noch vorhanden"
        )
        assert "balance.last_cost || 0) * 1_000_000" not in source
        assert "msg.cost * 1_000_000" not in source, (
            "Per-Message Cost-Display multipliziert noch mit 1_000_000"
        )

    def test_new_fields_used_in_frontend(self):
        """Neue Felder last_cost_eur + today_total_eur werden im Frontend gelesen."""
        with open("zerberus/app/routers/hel.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "last_cost_eur" in source
        assert "today_total_eur" in source


class TestBackwardCompatibility:
    """Alte Clients die `last_cost` erwarten, sollen das weiter bekommen."""

    def test_last_cost_alias_preserved(self, tmp_db, monkeypatch):
        from zerberus.app.routers.hel import get_balance
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = asyncio.run(get_balance())
        assert "last_cost" in result
        assert result["last_cost"] == result["last_cost_usd"]
