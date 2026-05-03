"""
RAG Modul – Gedächtnis & Semantische Suche (FAISS).
Patch 35: Echter FlatL2-Index, Persistenz, echte Embeddings.
"""
import asyncio
import json
import logging
import threading
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event

try:
    import faiss
    from sentence_transformers import SentenceTransformer
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False

logger = logging.getLogger(__name__)
router = APIRouter(tags=["RAG"])

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
_DIM = 384  # all-MiniLM-L6-v2 Output-Dimension

# ---------------------------------------------------------------------------
# Modul-State (Singleton)
# ---------------------------------------------------------------------------
_index: "faiss.IndexFlatL2 | None" = None
_model: "SentenceTransformer | None" = None
_metadata: list[dict] = []   # [{"text": "...", ...}, ...]  – Index i == Vektor i
_init_lock = threading.Lock()
_initialized = False

# Patch 133: Dual-Embedder-Switch. Default false → Legacy MiniLM bleibt aktiv.
# Wird zur Laufzeit aus config.yaml `modules.rag.use_dual_embedder` gelesen.
# Umschaltung setzt das Feature-Flag in config.yaml voraus + Server-Restart.
_dual_embedder: "object | None" = None
_use_dual: bool = False

# Patch 187: Bei Dual-Modus zusätzlich der EN-Index als zweite Hälfte.
# DE liegt in den globalen `_index` / `_metadata` (für Backward-Compat
# mit Reset/Health/etc.). EN-Index wird optional gehalten — fehlt er,
# wird für EN-Queries auf den DE-Index zurückgegriffen.
_en_index: "faiss.IndexFlatL2 | None" = None
_en_metadata: list[dict] = []


# ---------------------------------------------------------------------------
# Hilfsfunktionen (synchron, laufen in Thread-Pool)
# ---------------------------------------------------------------------------

def _resolve_paths(settings: Settings) -> tuple[Path, Path]:
    """Gibt (index_path, meta_path) zurück, liest Pfad aus config.yaml."""
    base = Path(settings.modules.get("rag", {}).get("vector_db_path", "./data/vectors"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "faiss.index", base / "metadata.json"


def _load_or_create_index(index_path: Path) -> "faiss.IndexFlatL2":
    if index_path.exists():
        idx = faiss.read_index(str(index_path))
        logger.info(f"📂 FAISS-Index geladen: {index_path} ({idx.ntotal} Vektoren)")
    else:
        idx = faiss.IndexFlatL2(_DIM)
        logger.info(f"🆕 Neuer FAISS FlatL2-Index erstellt (dim={_DIM})")
    return idx


def _load_metadata(meta_path: Path) -> list[dict]:
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"📂 Metadaten geladen: {meta_path} ({len(data)} Einträge)")
        return data
    return []


def _save_index(index: "faiss.IndexFlatL2", index_path: Path) -> None:
    faiss.write_index(index, str(index_path))


def _save_metadata(metadata: list[dict], meta_path: Path) -> None:
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def _init_sync(settings: Settings) -> None:
    """Initialisiert Model, Index und Metadaten. Wird genau einmal aufgerufen.

    Patch 133: Wenn `modules.rag.use_dual_embedder == true` UND die Dual-
    Indices (de.index/en.index + *_meta.json) existieren, wird der Dual-
    Embedder geladen und der Index auf die DE-Variante gezeigt (EN kann
    optional später dazugemergt werden). Ansonsten Legacy MiniLM.

    Patch 187: Wenn ein zusätzlicher en.index vorhanden ist, wird er
    parallel geladen und für EN-Queries genutzt (sprach-spezifischer
    Retrieval-Pfad). Fehlt er → Fallback auf DE-Index für EN-Queries.
    """
    global _index, _model, _metadata, _initialized, _dual_embedder, _use_dual
    global _en_index, _en_metadata
    with _init_lock:
        if _initialized:
            return

        rag_cfg = settings.modules.get("rag", {})
        _use_dual = bool(rag_cfg.get("use_dual_embedder", False))

        if _use_dual:
            # Patch 133: Dual-Embedder-Pfad
            try:
                from zerberus.modules.rag.dual_embedder import (
                    DualEmbedder, DualEmbedderConfig,
                )
                _dual_embedder = DualEmbedder(DualEmbedderConfig.from_dict(rag_cfg))
                # Lade Dual-Indices
                base = Path(rag_cfg.get("vector_db_path", "./data/vectors"))
                de_index_path = base / "de.index"
                de_meta_path = base / "de_meta.json"
                en_index_path = base / "en.index"
                en_meta_path = base / "en_meta.json"
                if de_index_path.exists() and de_meta_path.exists():
                    _index = faiss.read_index(str(de_index_path))
                    with open(de_meta_path, "r", encoding="utf-8") as f:
                        _metadata = json.load(f)
                    logger.warning(
                        f"[DUAL-187] Dual-Embedder aktiv. DE-Index: {_index.ntotal} Vektoren"
                    )
                    # Patch 187: EN-Index optional dazuladen
                    if en_index_path.exists() and en_meta_path.exists():
                        _en_index = faiss.read_index(str(en_index_path))
                        with open(en_meta_path, "r", encoding="utf-8") as f:
                            _en_metadata = json.load(f)
                        logger.warning(
                            f"[DUAL-187] EN-Index geladen: {_en_index.ntotal} Vektoren"
                        )
                    else:
                        logger.info(
                            "[DUAL-187] Kein EN-Index — EN-Queries fallen auf DE-Index zurück"
                        )
                else:
                    logger.warning(
                        "[DUAL-187] use_dual_embedder=true aber de.index fehlt — "
                        "Fallback auf Legacy MiniLM"
                    )
                    _use_dual = False
            except Exception as e:
                logger.warning(f"[DUAL-187] Dual-Init fehlgeschlagen: {e} — Fallback Legacy")
                _use_dual = False

        if not _use_dual:
            # Legacy-Pfad (Pre-Patch-133)
            model_name = rag_cfg.get("embedding_model", "all-MiniLM-L6-v2")
            from zerberus.modules.rag.device import get_rag_device
            device = get_rag_device(rag_cfg.get("device"))
            logger.info(f"🤖 Lade Embedding-Modell: {model_name} (device={device})")
            _model = SentenceTransformer(model_name, device=device)
            logger.warning(f"[GPU-111] Embedding-Modell geladen auf {device}")

            index_path, meta_path = _resolve_paths(settings)
            _index = _load_or_create_index(index_path)
            _metadata = _load_metadata(meta_path)

        _initialized = True
        logger.info("✅ RAG-Modul initialisiert")


async def _ensure_init(settings: Settings) -> None:
    """Async-Wrapper: initialisiert im Thread-Pool falls noch nicht geschehen."""
    if not _initialized:
        await asyncio.to_thread(_init_sync, settings)


# ---------------------------------------------------------------------------
# Pydantic-Modelle
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class SearchResponse(BaseModel):
    results: list
    query: str


class IndexDocumentRequest(BaseModel):
    text: str
    metadata: dict = {}


# ---------------------------------------------------------------------------
# Blocking Worker-Funktionen (laufen in Thread-Pool via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _detect_lang(text: str) -> str:
    """Wrapper um detect_language — exportiert für Tests + _encode/_search."""
    from zerberus.modules.rag.language_detector import detect_language
    return detect_language(text or "")


def _encode(text: str, language: str | None = None) -> np.ndarray:
    """Encode text. Patch 187: nutzt DualEmbedder wenn `_use_dual=True`, sonst Legacy.

    Bei Dual-Modus wird die Sprache erkannt (oder explizit übergeben), damit
    der passende sprachspezifische Embedder genutzt wird. Der erzeugte Vector
    hat die Dimension des entsprechenden Modells und passt zum sprach-
    spezifischen FAISS-Index (de.index oder en.index).
    """
    global _model, _dual_embedder
    if _use_dual and _dual_embedder is not None:
        lang = language or _detect_lang(text)
        vec_list = _dual_embedder.embed(text, language=lang)
        logger.debug(f"[RAG-187] Encode via DualEmbedder lang={lang}, dim={len(vec_list)}")
        return np.array([vec_list], dtype="float32")
    if _model is None:
        from sentence_transformers import SentenceTransformer
        from zerberus.modules.rag.device import get_rag_device
        try:
            settings = get_settings()
            rag_cfg = settings.modules.get("rag", {})
            device = get_rag_device(rag_cfg.get("device"))
        except Exception:
            device = get_rag_device(None)
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2", device=device)
        logger.warning(f"[GPU-111] Embedding-Modell lazy-geladen auf {device}")
    vec = _model.encode([text], normalize_embeddings=True)
    return vec.astype("float32")


def _add_to_index(vec: np.ndarray, text: str, extra_meta: dict, settings: Settings) -> int:
    """Fügt Vektor + Metadaten zum Index hinzu und persistiert beides."""
    _index.add(vec)
    entry = {"text": text, **extra_meta}
    _metadata.append(entry)
    index_path, meta_path = _resolve_paths(settings)
    _save_index(_index, index_path)
    _save_metadata(_metadata, meta_path)
    return _index.ntotal


def _select_index_and_meta(language: str | None) -> tuple["faiss.IndexFlatL2 | None", list[dict]]:
    """Patch 187: Wählt den passenden Index + Metadata basierend auf der Sprache.

    Bei Dual-Modus:
      - language='en' UND en_index existiert  → EN-Index
      - language='en' UND en_index fehlt      → DE-Index (Fallback)
      - sonst                                 → DE-Index (= globaler _index)

    Bei Legacy-Modus: immer der globale _index.
    """
    if _use_dual and language == "en" and _en_index is not None:
        return _en_index, _en_metadata
    return _index, _metadata


def _search_index(
    vec: np.ndarray,
    top_k: int,
    min_chunk_words: int = 0,
    query_text: str | None = None,
    rerank_enabled: bool = False,
    rerank_model: str = "",
    rerank_multiplier: int = 4,
    language: str | None = None,
) -> list[dict]:
    """Führt Nearest-Neighbor-Suche durch und gibt Ergebnisse zurück.

    Patch 88 (Fix B): Wenn `min_chunk_words > 0`, wird intern mit
    `top_k * 2` over-fetched, dann Chunks unter der Mindestlänge gefiltert,
    schließlich auf `top_k` getrimmt. Fängt kurze Residual-Chunks ab,
    die bei normalisierten MiniLM-Embeddings systematisch Rang 1 kapern.
    Wortanzahl kommt aus `metadata.word_count` (Patch 88) oder wird
    on-the-fly via `len(text.split())` berechnet.

    Patch 89 (R-03): Wenn `rerank_enabled=True`, over-fetched der FAISS
    `top_k * rerank_multiplier` Kandidaten, filtert kurze Chunks, und reicht
    die Liste an den Cross-Encoder (`zerberus.modules.rag.reranker.rerank`)
    weiter. Das Reranker-Ergebnis wird auf `top_k` getrimmt. Benötigt
    `query_text` (Cross-Encoder scored Query+Chunk-Paare).

    Patch 187: Bei Dual-Embedder-Modus wählt `language` den sprach-
    spezifischen Index (de.index / en.index). Fallback auf DE-Index wenn
    EN-Index nicht vorhanden.
    """
    target_index, target_meta = _select_index_and_meta(language)
    if target_index is None:
        return []
    n_vectors = target_index.ntotal
    if n_vectors == 0:
        return []

    # Patch 89: Over-fetch-Multiplikator bestimmen
    if rerank_enabled and query_text:
        over_fetch = max(rerank_multiplier, 2)
    elif min_chunk_words > 0:
        over_fetch = 2
    else:
        over_fetch = 1

    fetch_k = min(top_k * over_fetch, n_vectors)
    distances, indices = target_index.search(vec, fetch_k)

    results = []
    dropped = 0
    dropped_deleted = 0
    for dist, idx in zip(distances[0], indices[0]):
        if idx == -1:
            continue
        entry = target_meta[idx].copy()
        # Patch 116: Soft-deleted Chunks (per /hel/admin/rag/document DELETE)
        # werden im Retrieval übersprungen, bleiben aber physisch im Index,
        # bis der nächste vollständige Reindex läuft.
        if entry.get("deleted") is True:
            dropped_deleted += 1
            continue
        entry["score"] = float(1.0 / (1.0 + dist))
        entry["l2_distance"] = float(dist)

        if min_chunk_words > 0:
            wc = entry.get("word_count")
            if wc is None:
                wc = len(entry.get("text", "").split())
            if wc < min_chunk_words:
                dropped += 1
                continue

        results.append(entry)

    if min_chunk_words > 0 and dropped > 0:
        logger.debug(
            f"[DEBUG-88] RAG filter: over-fetched {fetch_k}, "
            f"dropped {dropped} short chunks (< {min_chunk_words} words), "
            f"kept {len(results)} for rerank/return"
        )

    # Patch 89: Cross-Encoder Rerank (optional)
    if rerank_enabled and query_text and rerank_model and results:
        from zerberus.modules.rag.reranker import rerank as _rerank
        results = _rerank(query_text, results, rerank_model, top_k)
        return results

    return results[:top_k]


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------

@router.post("/index")
async def index_document(
    req: IndexDocumentRequest,
    settings: Settings = Depends(get_settings)
):
    mod_cfg = settings.modules.get("rag", {})
    if not mod_cfg.get("enabled", False):
        return {"message": "RAG Modul deaktiviert"}

    if not RAG_AVAILABLE:
        raise HTTPException(503, "RAG-Abhängigkeiten (faiss, sentence-transformers) nicht installiert.")

    await _ensure_init(settings)

    text = req.text.strip()
    if not text:
        raise HTTPException(400, "text darf nicht leer sein.")

    logger.info(f"📚 Indexiere: {text[:80]}...")

    vec = await asyncio.to_thread(_encode, text)
    total = await asyncio.to_thread(_add_to_index, vec, text, req.metadata, settings)

    bus = get_event_bus()
    await bus.publish(Event(type="rag_indexed", data={"text_preview": text[:80], "total_vectors": total}))

    return {"status": "indexed", "text_length": len(text), "total_vectors": total}


@router.post("/search", response_model=SearchResponse)
async def semantic_search(
    req: SearchRequest,
    settings: Settings = Depends(get_settings)
):
    mod_cfg = settings.modules.get("rag", {})
    if not mod_cfg.get("enabled", False):
        return {"message": "RAG Modul deaktiviert"}

    if not RAG_AVAILABLE:
        raise HTTPException(503, "RAG-Abhängigkeiten (faiss, sentence-transformers) nicht installiert.")

    await _ensure_init(settings)

    query = req.query.strip()
    if not query:
        raise HTTPException(400, "query darf nicht leer sein.")

    min_words = int(mod_cfg.get("min_chunk_words", 0))
    rerank_enabled = bool(mod_cfg.get("rerank_enabled", False))
    rerank_model = str(mod_cfg.get("rerank_model", ""))
    rerank_multiplier = int(mod_cfg.get("rerank_multiplier", 4))
    expand_enabled = bool(mod_cfg.get("query_expansion_enabled", False))

    # Patch 97: Query Expansion — same logic as orchestrator._rag_search,
    # aber hier in der öffentlichen Such-API, damit `rag_eval.py` den
    # gesamten Produktions-Pfad misst.
    if expand_enabled:
        from zerberus.modules.rag.query_expander import expand_query
        queries = await expand_query(query, mod_cfg)
    else:
        queries = [query]

    per_query_k = req.top_k * (rerank_multiplier if rerank_enabled else 1)
    all_candidates: list[dict] = []
    seen: set[str] = set()
    # Patch 187: Sprache der ORIGINAL-Query erkennen — alle Expand-Varianten
    # nutzen denselben Sprach-Index, damit der Reranker konsistente Kandidaten
    # bekommt. (Query-Expansion paraphrasiert in derselben Sprache.)
    query_lang = _detect_lang(query) if _use_dual else None
    # Patch 211: VRAM-Slot um Embedder + Search-Loop. Hier laufen N
    # Embed-Calls (einer pro Expansion-Variante). Slot blockt nur
    # tatsaechlichen Modell-Aufruf — Search ist FAISS-CPU.
    from zerberus.core.gpu_queue import vram_slot
    async with vram_slot("embedder", timeout=30.0):
        for q in queries:
            vec = await asyncio.to_thread(_encode, q, query_lang)
            sub_hits = await asyncio.to_thread(
                _search_index,
                vec,
                per_query_k,
                min_words,
                q,
                False,          # rerank OFF per sub-query, once at end
                "",
                rerank_multiplier,
                query_lang,
            )
            for h in sub_hits:
                key = (h.get("text", "") or "")[:200]
                if key and key not in seen:
                    seen.add(key)
                    all_candidates.append(h)

    if expand_enabled:
        logger.warning(
            f"[EXPAND-97] Original: {query!r}, Expanded: {queries}, "
            f"per-query-k={per_query_k}, Post-dedup: {len(all_candidates)}"
        )

    if rerank_enabled and rerank_model and all_candidates:
        from zerberus.modules.rag.reranker import rerank as _rerank
        async with vram_slot("reranker", timeout=30.0):
            results = await asyncio.to_thread(_rerank, query, all_candidates, rerank_model, req.top_k)
    else:
        results = all_candidates[:req.top_k]

    # Patch 111: Category-Boost (Keyword-basiert)
    if results and bool(mod_cfg.get("category_boost_enabled", False)):
        from zerberus.modules.rag.category_router import (
            detect_query_category, apply_category_boost,
        )
        query_cat = detect_query_category(query)
        if query_cat:
            boost = float(mod_cfg.get("category_boost_value", 0.1))
            results = apply_category_boost(results, query_cat, boost)

    bus = get_event_bus()
    await bus.publish(Event(type="rag_search", data={"query": query[:100], "results": len(results)}))

    return SearchResponse(results=results, query=query)


@router.get("/health")
async def health_check(settings: Settings = Depends(get_settings)):
    index_size = _index.ntotal if _index is not None else 0
    return {
        "status": "ok",
        "module": "rag",
        "initialized": _initialized,
        "rag_available": RAG_AVAILABLE,
        "index_size": index_size,
    }


# ---------------------------------------------------------------------------
# Reset (wird vom Hel-Dashboard aufgerufen)
# ---------------------------------------------------------------------------

def _reset_sync(settings: Settings) -> None:
    """Setzt Index und Metadaten zurück – leert faiss.index und metadata.json."""
    global _index, _metadata, _initialized
    with _init_lock:
        index_path, meta_path = _resolve_paths(settings)
        _index = faiss.IndexFlatL2(_DIM)
        _metadata = []
        _save_index(_index, index_path)
        _save_metadata(_metadata, meta_path)
        _initialized = True
        logger.info("🗑️ RAG-Index zurückgesetzt (leer)")
