"""
RAG Cross-Encoder Reranker – Patch 89 (R-03).

Zweite Retrieval-Stufe: nimmt FAISS-Top-N und bewertet jeden Kandidaten
mit voller Query+Chunk-Token-Attention. Cross-Encoder sind bei exakten
Matches und Eigennamen deutlich stärker als Bi-Encoder wie MiniLM.

Fail-Safe: Bei Modell-/Inference-Fehler fällt das Modul auf die
ursprüngliche FAISS-Reihenfolge zurück — RAG bleibt immer funktional.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_reranker = None  # lazy-loaded globaler Cache (analog _model in router.py)
_reranker_model_name: str | None = None


def _load_reranker(model_name: str):
    """Lazy-Load Guard analog _encode() in router.py.

    Modell-Wechsel zur Laufzeit wird unterstützt: Wenn model_name sich
    ändert, wird das neue Modell geladen und der Cache aktualisiert.

    Patch 111: Device-Auswahl via `get_rag_device()` aus config.yaml
    (`modules.rag.device`). CrossEncoder nimmt `device` direkt als
    Konstruktor-Parameter (sentence-transformers >= 2.2).
    """
    global _reranker, _reranker_model_name
    if _reranker is None or _reranker_model_name != model_name:
        from sentence_transformers import CrossEncoder
        from zerberus.modules.rag.device import get_rag_device
        try:
            from zerberus.core.config import get_settings
            rag_cfg = get_settings().modules.get("rag", {})
            device = get_rag_device(rag_cfg.get("device"))
        except Exception:
            device = get_rag_device(None)
        logger.info(f"[DEBUG-89] Loading cross-encoder: {model_name} (device={device})")
        _reranker = CrossEncoder(model_name, max_length=512, device=device)
        _reranker_model_name = model_name
        logger.warning(f"[GPU-111] Reranker geladen auf {device}")
    return _reranker


def rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    model_name: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    """Bewertet die FAISS-Kandidaten mit Cross-Encoder und sortiert neu.

    Args:
        query: Suchanfrage
        candidates: Liste aus `_search_index()` — jeder Eintrag hat
                    mindestens `text`, ggf. `score`, `l2_distance`, weitere
                    Metadaten.
        model_name: HuggingFace-Model-ID für CrossEncoder.
        top_k: maximale Anzahl der zurückgegebenen Kandidaten.

    Returns:
        Liste der Top-`top_k` Kandidaten nach Rerank-Score absteigend.
        Jeder Kandidat bekommt zusätzlich `rerank_score`.
        Bei Fehler: candidates[:top_k] in ursprünglicher Reihenfolge
        (Fail-Safe, damit RAG nicht komplett ausfällt).
    """
    if not candidates:
        return []
    try:
        model = _load_reranker(model_name)
        pairs = [[query, c.get("text", "")] for c in candidates]
        scores = model.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
        logger.info(
            f"[DEBUG-89] Reranked {len(candidates)} candidates → top {min(top_k, len(ranked))}. "
            f"Top-score: {ranked[0]['rerank_score']:.3f}"
        )
        return ranked[:top_k]
    except Exception as e:
        logger.warning(
            f"[DEBUG-89] Rerank failed ({type(e).__name__}: {e}), "
            f"falling back to FAISS order"
        )
        return candidates[:top_k]
