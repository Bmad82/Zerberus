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
    """Initialisiert Model, Index und Metadaten. Wird genau einmal aufgerufen."""
    global _index, _model, _metadata, _initialized
    with _init_lock:
        if _initialized:
            return

        model_name = settings.modules.get("rag", {}).get("embedding_model", "all-MiniLM-L6-v2")
        logger.info(f"🤖 Lade Embedding-Modell: {model_name}")
        _model = SentenceTransformer(model_name)

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

def _encode(text: str) -> np.ndarray:
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


def _search_index(vec: np.ndarray, top_k: int) -> list[dict]:
    """Führt Nearest-Neighbor-Suche durch und gibt Ergebnisse zurück."""
    n_vectors = _index.ntotal
    if n_vectors == 0:
        return []
    k = min(top_k, n_vectors)
    distances, indices = _index.search(vec, k)
    results = []
    for dist, idx in zip(distances[0], indices[0]):
        if idx == -1:
            continue
        entry = _metadata[idx].copy()
        # L2-Distanz in ein Score-ähnliches Maß umwandeln (kleiner = besser → invertieren)
        entry["score"] = float(1.0 / (1.0 + dist))
        entry["l2_distance"] = float(dist)
        results.append(entry)
    return results


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

    vec = await asyncio.to_thread(_encode, query)
    results = await asyncio.to_thread(_search_index, vec, req.top_k)

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
