"""
Patch 126 - Dual-Embedder Architektur (Infrastruktur).

Verwaltet zwei Embedder-Modelle mit automatischer Spracherkennung:
- Deutsch (GPU, primär):   z.B. "T-Systems-onsite/cross-en-de-roberta-sentence-transformer"
- English/Multi (CPU):     z.B. "intfloat/multilingual-e5-large"

WICHTIG: Dieses Modul ist Infrastruktur. Der aktive FAISS-Index läuft weiter
mit dem klassischen MiniLM. Die Migration auf Dual-Embedder erfordert einen
manuellen Rebuild des Index (siehe scripts/migrate_embedder.py) — das ist
bewusst nicht automatisch.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from zerberus.modules.rag.language_detector import detect_language

logger = logging.getLogger("zerberus.rag.dual_embedder")


DEFAULT_DE_MODEL = "T-Systems-onsite/cross-en-de-roberta-sentence-transformer"
DEFAULT_EN_MODEL = "intfloat/multilingual-e5-large"


@dataclass
class DualEmbedderConfig:
    de_model: str = DEFAULT_DE_MODEL
    en_model: str = DEFAULT_EN_MODEL
    de_device: str = "cuda"   # GPU primär
    en_device: str = "cpu"    # Englisch auf CPU
    auto_detect: bool = True
    fallback_language: str = "de"

    @classmethod
    def from_dict(cls, raw: dict) -> "DualEmbedderConfig":
        embedder_cfg = (raw or {}).get("embedder", {}) or {}
        de_cfg = embedder_cfg.get("de", {}) or {}
        en_cfg = embedder_cfg.get("en", {}) or {}
        return cls(
            de_model=de_cfg.get("model", DEFAULT_DE_MODEL),
            en_model=en_cfg.get("model", DEFAULT_EN_MODEL),
            de_device=de_cfg.get("device", "cuda"),
            en_device=en_cfg.get("device", "cpu"),
            auto_detect=bool(embedder_cfg.get("auto_detect_language", True)),
            fallback_language=str(embedder_cfg.get("fallback_language", "de")),
        )


class DualEmbedder:
    """Lazy-Loading-Wrapper fuer zwei SentenceTransformer-Modelle.

    Lädt ein Modell erst beim ersten Embed-Call. Hält Modelle dauerhaft im
    Speicher (Prozess-Lifetime). Thread-sicherheit: nicht garantiert — für
    Batch-Embedding (einzelner Worker) ausgelegt.
    """

    def __init__(self, config: DualEmbedderConfig | None = None):
        self.config = config or DualEmbedderConfig()
        self._de_model: Any = None
        self._en_model: Any = None

    def _load_de(self) -> None:
        if self._de_model is not None:
            return
        from sentence_transformers import SentenceTransformer  # lazy
        logger.info(f"[DUAL-126] Lade DE-Embedder {self.config.de_model} auf {self.config.de_device}")
        self._de_model = SentenceTransformer(self.config.de_model, device=self.config.de_device)

    def _load_en(self) -> None:
        if self._en_model is not None:
            return
        from sentence_transformers import SentenceTransformer  # lazy
        logger.info(f"[DUAL-126] Lade EN-Embedder {self.config.en_model} auf {self.config.en_device}")
        self._en_model = SentenceTransformer(self.config.en_model, device=self.config.en_device)

    def embed(self, text: str, language: Optional[str] = None) -> list[float]:
        """Embedded Text mit dem passenden Modell.

        Args:
            text: Zu embeddender Text.
            language: "de"/"en" oder None (→ auto-detect).
        Returns:
            Liste von Floats (Embedding-Vektor).
        """
        if language is None and self.config.auto_detect:
            language = detect_language(text)
        elif language is None:
            language = self.config.fallback_language

        if language == "de":
            self._load_de()
            model = self._de_model
        else:
            self._load_en()
            model = self._en_model

        vec = model.encode(text, normalize_embeddings=True)
        # numpy-Array → Liste
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    def embed_batch(self, texts: list[str], language: Optional[str] = None) -> list[list[float]]:
        """Batch-Embed. Wenn language=None, wird pro Text erkannt."""
        results: list[list[float]] = []
        for t in texts:
            results.append(self.embed(t, language=language))
        return results

    def unload(self) -> None:
        """Räumt die Modelle aus dem Speicher (z.B. fuer Tests)."""
        self._de_model = None
        self._en_model = None


def build_dual_embedder_from_settings(settings: Any) -> DualEmbedder:
    """Factory aus zerberus.core.config.Settings.modules.rag — für Hel-Integration."""
    rag_cfg = settings.modules.get("rag", {}) if hasattr(settings, "modules") else {}
    return DualEmbedder(DualEmbedderConfig.from_dict(rag_cfg))
