"""
RAG Device-Detection — Patch 111.

Zentrale Helper für GPU/CPU-Auswahl von Bi-Encoder (MiniLM) und
Cross-Encoder (bge-reranker). Fail-Safe: bei CUDA-Problemen oder zu
wenig VRAM automatisch auf CPU zurückfallen.

Config (`config.yaml modules.rag.device`): "auto" | "cuda" | "cpu".
- "auto" (Default): CUDA wenn verfügbar UND >= `min_free_vram_gb` frei.
- "cuda": erzwinge CUDA (Fallback auf CPU bei ImportError/kein CUDA).
- "cpu":  erzwinge CPU.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MIN_FREE_VRAM_GB = 2.0  # MiniLM (~0.5) + bge-reranker (~1.0) + Puffer


def _cuda_state() -> tuple[bool, float, float, str]:
    """Liest CUDA-Status (available, free_gb, total_gb, device_name).

    Isoliert in eigener Funktion, damit Tests sie mocken können ohne
    `torch` als Test-Dependency zu brauchen.
    """
    try:
        import torch
    except ImportError:
        return False, 0.0, 0.0, ""
    if not torch.cuda.is_available():
        return False, 0.0, 0.0, ""
    try:
        free, total = torch.cuda.mem_get_info()
        free_gb = free / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        name = torch.cuda.get_device_name(0)
        return True, free_gb, total_gb, name
    except Exception as e:
        logger.warning(f"[GPU-111] CUDA-Status konnte nicht gelesen werden: {e}")
        return False, 0.0, 0.0, ""


def get_rag_device(config_override: str | None = None) -> str:
    """Bestimmt das beste verfügbare Device für RAG-Modelle.

    Args:
        config_override: "auto" | "cuda" | "cpu" aus `modules.rag.device`.
                         None oder "auto" → Auto-Detection.

    Returns:
        "cuda" oder "cpu" — direkt an SentenceTransformer/CrossEncoder
        als `device=` übergebbar.
    """
    override = (config_override or "auto").strip().lower()

    if override == "cpu":
        logger.info("[GPU-111] Device per Config erzwungen: cpu")
        return "cpu"

    available, free_gb, total_gb, name = _cuda_state()

    if override == "cuda":
        if available:
            logger.warning(f"[GPU-111] Device per Config erzwungen: cuda ({name}, {free_gb:.1f}/{total_gb:.1f} GB frei)")
            return "cuda"
        logger.warning("[GPU-111] Config verlangt cuda, aber kein CUDA verfügbar — Fallback auf cpu")
        return "cpu"

    # override == "auto"
    if not available:
        logger.info("[GPU-111] Kein CUDA verfügbar — CPU-Modus")
        return "cpu"

    if free_gb < _MIN_FREE_VRAM_GB:
        logger.warning(f"[GPU-111] Nur {free_gb:.1f} GB VRAM frei (< {_MIN_FREE_VRAM_GB} GB) — Fallback auf cpu")
        return "cpu"

    logger.warning(f"[GPU-111] CUDA aktiv: {name}, {free_gb:.1f}/{total_gb:.1f} GB frei")
    return "cuda"


def log_gpu_status() -> None:
    """Einmaliger Startup-Log-Eintrag zu GPU-Zustand."""
    available, free_gb, total_gb, name = _cuda_state()
    if available:
        logger.warning(f"[GPU-111] GPU: {name}, {free_gb:.1f}/{total_gb:.1f} GB frei")
    else:
        logger.info("[GPU-111] Kein CUDA verfügbar — alle RAG-Modelle laufen auf CPU")
