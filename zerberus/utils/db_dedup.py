"""
Patch 134 — Datenbank-Deduplizierung für bunker_memory.db.

Findet und entfernt (Soft-Delete) Duplikate in der interactions-Tabelle.
Duplikat-Kriterium: gleicher `profile_key` + `content` + `role` mit
timestamp-Abstand ≤ window_seconds (Default 60).

Sicherheit:
  - Automatisches Backup der gesamten DB vor jeder Aktion (Dry-Run = kein Backup)
  - Soft-Delete via Marker-Content oder integrity=-1.0 (NICHT physisch löschen)
  - Loggt jedes erkannte Duplikat mit ID + Originalreferenz
  - Dry-Run-Modus: scannt + loggt, ändert aber nichts

Hintergrund: Die Dictate-Android-Tastatur macht bei schlechtem Empfang
Retries, die identische Messages in die DB blasen. Patch 113a hatte bereits
einen 30-Sekunden-Dedup-Guard in `store_interaction()` — der Overnight-Job
ist die zweite Verteidigungslinie für Duplikate, die durch den Guard
durchrutschen (z.B. verschiedene Session-IDs, nachträglich entdeckte Muster).
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import text as sa_text

logger = logging.getLogger("zerberus.db_dedup")

DEFAULT_WINDOW_SECONDS = 60


def backup_db(source: Path, backup_dir: Path | None = None) -> Path | None:
    """Erzeugt einen Zeitstempel-Snapshot der DB. None bei Fehler."""
    if not source.exists():
        return None
    if backup_dir is None:
        backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{source.stem}_pre_dedup_{timestamp}{source.suffix}"
    try:
        shutil.copy2(source, target)
        logger.info(f"[DEDUP-134] Backup: {source} -> {target}")
        return target
    except Exception as e:
        logger.error(f"[DEDUP-134] Backup fehlgeschlagen: {e}")
        return None


def _find_duplicate_groups(rows: list[tuple]) -> list[list[tuple]]:
    """Gruppiert (id, profile_key, role, content, ts_unix) nach Duplikat-Cluster.

    Sliding-Window-Algorithmus innerhalb jeder (profile_key, role, content)-Gruppe:
    Einträge im Zeitfenster ≤ window_seconds gelten als Duplikate.
    """
    # Pre-group nach (profile_key, role, content) für O(n log n) statt O(n²)
    groups: dict = {}
    for row in rows:
        rid, pkey, role, content, ts = row
        key = (pkey or "", role or "", content or "")
        groups.setdefault(key, []).append((rid, ts))
    clusters: list[list[tuple]] = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: x[1])
        clusters.append([(rid, ts, key) for rid, ts in items])
    return clusters


async def deduplicate_interactions(
    db_path: str | Path | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    dry_run: bool = True,
    do_backup: bool = True,
) -> dict:
    """Scannt interactions und entfernt Duplikate (Soft-Delete).

    Args:
        db_path: Pfad zur SQLite-DB. None → aus settings.database.url ableiten.
        window_seconds: Zeitfenster in Sekunden. Einträge innerhalb des Fensters
                        mit identischem profile_key+role+content sind Duplikate.
        dry_run: True = kein Schreib­zugriff, nur Report.
        do_backup: True = erzeugt DB-Backup vor dem Löschen (nur wenn !dry_run).

    Returns:
        {"scanned": int, "duplicate_groups": int, "removed": int,
         "dry_run": bool, "backup": str | None}
    """
    from zerberus.core.database import _async_session_maker
    from zerberus.core.config import get_settings

    result = {
        "scanned": 0,
        "duplicate_groups": 0,
        "removed": 0,
        "dry_run": dry_run,
        "backup": None,
    }

    if _async_session_maker is None:
        logger.warning("[DEDUP-134] DB-Session-Maker nicht initialisiert")
        return result

    # 1. Backup (nur wenn echte Aktion)
    if not dry_run and do_backup:
        try:
            settings = get_settings()
            url = settings.database.url
            # sqlite+aiosqlite:///./bunker_memory.db → ./bunker_memory.db
            if "sqlite" in url and ":///" in url:
                db_file = url.split(":///", 1)[1]
                source = Path(db_file)
                bpath = backup_db(source)
                if bpath:
                    result["backup"] = str(bpath)
        except Exception as e:
            logger.error(f"[DEDUP-134] Backup-Schritt fehlgeschlagen: {e}")

    # 2. Scan — alle user/assistant Rows mit Timestamp lesen
    try:
        async with _async_session_maker() as session:
            rows = (await session.execute(sa_text(
                "SELECT id, profile_key, role, content, "
                "  CAST(strftime('%s', timestamp) AS INTEGER) as ts_unix "
                "FROM interactions "
                "WHERE role IN ('user', 'assistant') "
                "  AND (integrity IS NULL OR integrity >= 0) "  # Soft-deleted überspringen
                "ORDER BY timestamp ASC"
            ))).fetchall()
    except Exception as e:
        logger.error(f"[DEDUP-134] DB-Query fehlgeschlagen: {e}")
        return result

    result["scanned"] = len(rows)
    row_tuples = [(r[0], r[1], r[2], r[3], r[4] or 0) for r in rows]
    clusters = _find_duplicate_groups(row_tuples)

    # 3. Für jeden Cluster: Finde Duplikate im Zeitfenster
    to_remove: list[tuple[int, int]] = []  # (duplicate_id, keeps_id)
    for cluster in clusters:
        # cluster ist nach ts sortiert
        if len(cluster) < 2:
            continue
        # Zweizeiger: behalte ersten, markiere nachfolgende als Duplikat wenn im Fenster
        result["duplicate_groups"] += 1
        keep_id, keep_ts, _key = cluster[0]
        for rid, ts, _k in cluster[1:]:
            if ts - keep_ts <= window_seconds:
                to_remove.append((rid, keep_id))
                logger.info(
                    f"[DEDUP-134] Duplikat: id={rid} (ts={ts}) "
                    f"→ Original id={keep_id} (ts={keep_ts}), Δ={ts - keep_ts}s"
                )
            else:
                # außerhalb des Fensters → wird neuer Anker
                keep_id, keep_ts = rid, ts

    result["removed"] = len(to_remove)

    # 4. Soft-Delete (integrity=-1.0 als Markierung)
    if not dry_run and to_remove:
        try:
            async with _async_session_maker() as session:
                for dup_id, _ in to_remove:
                    await session.execute(sa_text(
                        "UPDATE interactions SET integrity = -1.0 WHERE id = :id"
                    ), {"id": dup_id})
                await session.commit()
            logger.warning(
                f"[DEDUP-134] {len(to_remove)} Duplikate soft-deleted (integrity=-1.0)"
            )
        except Exception as e:
            logger.error(f"[DEDUP-134] Soft-Delete fehlgeschlagen: {e}")

    return result
