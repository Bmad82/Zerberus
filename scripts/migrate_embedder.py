"""
Patch 129 — Migriert den FAISS-Index vom MiniLM-Singlemodell auf den Dual-Embedder.

Flow:
  1. Backup des aktuellen Index (faiss.index + metadata.json).
  2. Initialisiert den DualEmbedder (DE auf GPU, EN auf CPU).
  3. Liest alle aktiven (nicht soft-deleted) Chunks aus der Metadata.
  4. Erkennt die Sprache pro Chunk (language_detector) — NICHT aus System-Prompt,
     sondern aus dem tatsaechlichen content.
  5. Baut zwei getrennte Indizes (de.index / en.index) mit den jeweiligen
     Embeddings. Stores drei Dateien pro Sprache: index + metadata + language.json.
  6. Validierungs-Queries gegen jeden Index.

Aufruf:
    venv\\Scripts\\python.exe scripts\\migrate_embedder.py --dry-run
    venv\\Scripts\\python.exe scripts\\migrate_embedder.py --execute

Bei --dry-run wird nichts geschrieben; man bekommt nur die Statistik.

ACHTUNG: Der Aufruf ohne --dry-run ist destruktiv (neue Datenstruktur). Das
Backup wird trotzdem angelegt. Die bestehenden faiss.index / metadata.json
bleiben erhalten — nur neue Dateien (de.index, en.index, *_meta.json) werden
zusätzlich geschrieben, der aktive Retriever liest weiterhin aus faiss.index,
bis Chris die Umschaltung in config.yaml vollzieht.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

# Stelle sicher dass der Project-Root im Path liegt
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("migrate_embedder")

VECTORS_DIR = ROOT / "data" / "vectors"
BACKUP_ROOT = ROOT / "data" / "backups"


def backup_current_index() -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    target = BACKUP_ROOT / f"pre_patch129_{timestamp}"
    target.mkdir(parents=True, exist_ok=True)
    for fname in ("faiss.index", "metadata.json"):
        src = VECTORS_DIR / fname
        if src.exists():
            shutil.copy2(src, target / fname)
            logger.info(f"Backup: {src} -> {target / fname}")
    return target


def load_metadata() -> list[dict]:
    meta_path = VECTORS_DIR / "metadata.json"
    if not meta_path.exists():
        logger.error(f"Keine Metadata unter {meta_path} gefunden.")
        return []
    with open(meta_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info(f"Metadata geladen: {len(data)} Eintraege.")
    return data


def categorize_by_language(chunks: list[dict]) -> dict[str, list[dict]]:
    from zerberus.modules.rag.language_detector import detect_language
    buckets: dict[str, list[dict]] = {"de": [], "en": []}
    for m in chunks:
        if m.get("deleted") is True:
            continue
        text = m.get("text", "") or ""
        if not text.strip():
            continue
        lang = detect_language(text)
        buckets.setdefault(lang, []).append(m)
    return buckets


def embed_and_build(buckets: dict[str, list[dict]], dry_run: bool = True) -> dict[str, dict]:
    """Erzeugt pro Sprache eine (index, metadata)-Struktur."""
    summary: dict[str, dict] = {}

    if dry_run:
        for lang, items in buckets.items():
            summary[lang] = {"count": len(items), "dimension": "?", "index_path": "(dry-run)"}
        return summary

    import numpy as np
    import faiss
    from zerberus.modules.rag.dual_embedder import DualEmbedder, DualEmbedderConfig

    dual = DualEmbedder(DualEmbedderConfig())

    for lang, items in buckets.items():
        if not items:
            logger.info(f"[{lang}] Keine Chunks — ueberspringe.")
            summary[lang] = {"count": 0, "dimension": None, "index_path": None}
            continue

        # Lade Modell + erstelle Index mit korrekter Dimension
        first_vec = dual.embed(items[0].get("text", ""), language=lang)
        dim = len(first_vec)
        idx = faiss.IndexFlatL2(dim)
        logger.info(f"[{lang}] Erzeuge Index (dim={dim}) fuer {len(items)} Chunks")

        # Alle Chunks embedden
        vectors = [first_vec]
        for i, m in enumerate(items[1:], start=1):
            text = m.get("text", "") or ""
            vec = dual.embed(text, language=lang)
            vectors.append(vec)
            if (i + 1) % 50 == 0:
                logger.info(f"[{lang}] Embedded {i + 1}/{len(items)}")

        arr = np.array(vectors, dtype="float32")
        idx.add(arr)

        # Persist
        index_path = VECTORS_DIR / f"{lang}.index"
        meta_path = VECTORS_DIR / f"{lang}_meta.json"
        faiss.write_index(idx, str(index_path))
        # Meta ohne 'deleted' speichern — die Migration bereinigt physisch
        cleaned_meta = [
            {k: v for k, v in m.items() if k != "deleted"}
            for m in items
        ]
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(cleaned_meta, f, ensure_ascii=False, indent=2)

        logger.info(f"[{lang}] Geschrieben: {index_path} + {meta_path}")
        summary[lang] = {"count": len(items), "dimension": dim, "index_path": str(index_path)}

    return summary


def validate_indices(summary: dict[str, dict], probe_queries: dict[str, str]) -> None:
    """Kleiner Sanity-Check: 3-Nearest-Neighbour fuer ein paar Probe-Queries."""
    try:
        import numpy as np
        import faiss
        from zerberus.modules.rag.dual_embedder import DualEmbedder, DualEmbedderConfig
    except Exception as e:
        logger.warning(f"Validierung uebersprungen (Import-Fehler): {e}")
        return

    dual = DualEmbedder(DualEmbedderConfig())
    for lang, info in summary.items():
        if info.get("index_path") in (None, "(dry-run)"):
            continue
        query = probe_queries.get(lang, "test")
        idx = faiss.read_index(info["index_path"])
        vec = np.array([dual.embed(query, language=lang)], dtype="float32")
        distances, indices = idx.search(vec, k=min(3, idx.ntotal))
        logger.info(f"[{lang}] Probe-Query '{query}': top-k indices={indices[0].tolist()} "
                    f"distances={[round(float(d), 3) for d in distances[0]]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migriert FAISS-Index auf Dual-Embedder.")
    parser.add_argument("--execute", action="store_true",
                        help="Migration tatsaechlich durchfuehren (Default: Dry-Run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur Statistik ausgeben (Default wenn --execute fehlt)")
    args = parser.parse_args()

    dry_run = not args.execute
    mode = "DRY-RUN" if dry_run else "EXECUTE"
    logger.info(f"=== Patch 129 Migration ({mode}) ===")

    chunks = load_metadata()
    if not chunks:
        return 1

    buckets = categorize_by_language(chunks)
    for lang, items in buckets.items():
        logger.info(f"Sprache {lang}: {len(items)} Chunks")

    if not dry_run:
        backup_current_index()

    summary = embed_and_build(buckets, dry_run=dry_run)

    logger.info("=== Summary ===")
    for lang, info in summary.items():
        logger.info(f"  {lang}: {info}")

    if not dry_run:
        probe = {
            "de": "Was ist die Rosendornen-Sammlung?",
            "en": "What is the system architecture?",
        }
        validate_indices(summary, probe)
        logger.info("Migration abgeschlossen. Alte faiss.index + metadata.json bleiben "
                    "unveraendert — der Live-Retriever nutzt sie weiter bis config.yaml "
                    "auf 'dual' umgestellt wird.")
    else:
        logger.info("Dry-Run ohne Schreibzugriff. Mit --execute ausfuehren um Migration durchzufuehren.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
