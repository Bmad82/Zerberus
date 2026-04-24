"""
Patch 138 (B-004): Cleanup-Script für Test-Profil Sessions.

Löscht aus der DB alle Interaktionen, die unter einem Test-Profil (is_test=true
in config.yaml) gespeichert wurden. Läuft standardmäßig im Dry-Run — mit
`--execute` wirklich ausführen.

Usage:
  venv/Scripts/python.exe scripts/cleanup_test_sessions.py          # Dry-Run
  venv/Scripts/python.exe scripts/cleanup_test_sessions.py --execute

WICHTIG: bunker_memory.db VORHER sichern (siehe CLAUDE_ZERBERUS.md).
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select

# Projekt-Root in PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zerberus.core.database import _async_session_maker, Interaction, init_db  # noqa: E402


def _test_profile_keys() -> list[str]:
    cfg_path = ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    profiles = data.get("profiles", {}) or {}
    return [k for k, v in profiles.items() if v and v.get("is_test", False)]


async def _list_affected(test_keys: list[str]) -> list[tuple[str, int]]:
    """Liste (profile_key, anzahl_interaktionen)."""
    async with _async_session_maker() as session:
        rows = []
        for key in test_keys:
            stmt = select(Interaction).where(Interaction.profile_key == key)
            result = await session.execute(stmt)
            count = len(result.scalars().all())
            rows.append((key, count))
        return rows


async def _delete_for_keys(test_keys: list[str]) -> int:
    async with _async_session_maker() as session:
        total = 0
        for key in test_keys:
            stmt = select(Interaction).where(Interaction.profile_key == key)
            result = await session.execute(stmt)
            items = list(result.scalars().all())
            for it in items:
                await session.delete(it)
                total += 1
        await session.commit()
        return total


def _backup_db() -> Path:
    src = ROOT / "bunker_memory.db"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = ROOT / f"bunker_memory_backup_patch138_{ts}.db"
    shutil.copy2(src, dst)
    return dst


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Wirklich löschen (sonst Dry-Run)")
    args = parser.parse_args()

    await init_db()
    test_keys = _test_profile_keys()
    if not test_keys:
        print("Keine Test-Profile mit is_test=true in config.yaml gefunden.")
        return

    print(f"Test-Profile: {test_keys}")
    affected = await _list_affected(test_keys)
    for key, count in affected:
        print(f"  {key}: {count} Interaktionen")
    total = sum(c for _, c in affected)
    print(f"Summe: {total} Interaktionen")

    if total == 0:
        print("Nichts zu tun.")
        return

    if not args.execute:
        print("\nDRY-RUN — keine Änderung. Mit --execute wirklich ausführen.")
        return

    backup = _backup_db()
    print(f"\n✅ DB gesichert: {backup}")

    deleted = await _delete_for_keys(test_keys)
    print(f"✅ {deleted} Interaktionen gelöscht.")


if __name__ == "__main__":
    asyncio.run(main())
