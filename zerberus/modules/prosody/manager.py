"""
Patch 188 — Prosody-Foundation (Gemma 4 E2B Infrastruktur).

NICHT der vollständige Audio-Sentiment-Pfad, nur das Fundament:
  - Config-Schema (`ProsodyConfig`)
  - `ProsodyManager` mit Healthcheck + Stub-Analyse
  - VRAM-Check via `zerberus.modules.rag.device._cuda_state`
  - Lazy-Load-Pattern für das Gemma-Modell (Stub bis Folge-Patch)

Das echte Modell-Loading + die Audio-Pipeline kommen in einem späteren
Patch wenn Chris das Modell heruntergeladen hat (~3 GB Q4_K_M GGUF).

Logging-Tags:
  [PROSODY-188]      Startup, Healthcheck
  [PROSODY-STUB-188] Stub-Analyse-Aufrufe (analyze() ohne geladenes Modell)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProsodyConfig:
    """Konfiguration für die Prosodie-Pipeline (Gemma 4 E2B).

    Defaults werden im Code gehalten (statt nur in config.yaml), weil
    config.yaml gitignored ist — sonst würden die Werte nach `git clone`
    fehlen.
    """
    enabled: bool = False
    model_path: str = ""          # Pfad zur GGUF-Datei (leer = nicht geladen)
    device: str = "cuda"          # "cuda" / "cpu"
    vram_threshold_gb: float = 2.0  # min freier VRAM zum Laden
    output_format: str = "json"   # "json" / "text"

    @classmethod
    def from_dict(cls, raw: dict) -> "ProsodyConfig":
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            model_path=str(raw.get("model_path", "")),
            device=str(raw.get("device", "cuda")),
            vram_threshold_gb=float(raw.get("vram_threshold_gb", 2.0)),
            output_format=str(raw.get("output_format", "json")),
        )


_STUB_FIELDS = ("mood", "tempo", "confidence", "valence", "arousal", "dominance", "source")


class ProsodyManager:
    """Verwaltet das Gemma-4-E2B-Modell für Prosodie-Analyse.

    Patch 188 ist FUNDAMENT: `analyze()` gibt einen neutralen Stub zurück,
    bis das Modell tatsächlich geladen wird (Folge-Patch). `healthcheck()`
    meldet ehrlich was Sache ist (disabled / no_model / model_not_found /
    not_enough_vram / ok).
    """

    def __init__(self, config: ProsodyConfig | None = None):
        self.config = config or ProsodyConfig()
        self._model: Any = None

    # ---------------------------------------------------------------
    # Health
    # ---------------------------------------------------------------
    async def healthcheck(self) -> dict:
        """Strukturierter Status für Startup-Banner + /health-Aggregator.

        Liefert immer ein dict mit `ok` + `reason` + ggf. `vram_free_gb`.
        Nutzt `_cuda_state()` aus dem RAG-Device-Helper (P111), damit der
        VRAM-Check zentral bleibt.
        """
        if not self.config.enabled:
            return {"ok": False, "reason": "disabled"}

        if not self.config.model_path:
            return {"ok": False, "reason": "no_model"}

        if not Path(self.config.model_path).exists():
            return {"ok": False, "reason": "model_not_found", "path": self.config.model_path}

        # VRAM-Check
        vram_free_gb = 0.0
        if self.config.device == "cuda":
            try:
                from zerberus.modules.rag.device import _cuda_state
                available, free_gb, total_gb, _name = _cuda_state()
                vram_free_gb = float(free_gb)
                if not available:
                    return {"ok": False, "reason": "no_cuda"}
                if free_gb < self.config.vram_threshold_gb:
                    return {
                        "ok": False,
                        "reason": "not_enough_vram",
                        "vram_free_gb": vram_free_gb,
                        "vram_threshold_gb": self.config.vram_threshold_gb,
                    }
            except Exception as e:
                logger.warning(f"[PROSODY-188] VRAM-Check fehlgeschlagen: {e}")
                return {"ok": False, "reason": "vram_check_failed", "error": str(e)[:120]}

        return {
            "ok": True,
            "loaded": self._model is not None,
            "device": self.config.device,
            "model_path": self.config.model_path,
            "vram_free_gb": vram_free_gb,
        }

    # ---------------------------------------------------------------
    # Analyse
    # ---------------------------------------------------------------
    async def analyze(self, audio_bytes: bytes) -> dict:
        """Analysiert Audio-Bytes und gibt Prosodie-Metadaten zurück.

        Returns:
            dict mit den Feldern:
              mood, tempo, confidence, valence, arousal, dominance, source

        STUB: Solange `self._model is None`, wird ein neutraler Default
        zurückgegeben (`source="stub"`). Der echte Pfad kommt in P189+.
        """
        if not self._model:
            logger.debug("[PROSODY-STUB-188] analyze() ohne Modell — Stub-Antwort")
            return {
                "mood": "neutral",
                "tempo": "normal",
                "confidence": 0.0,
                "valence": 0.5,
                "arousal": 0.5,
                "dominance": 0.5,
                "source": "stub",
            }
        # Echter Pfad: Audio-Bytes durch Gemma-4-E2B-Audio-Encoder schicken,
        # Prosodie-Features extrahieren, in das obige Schema mappen.
        # Folge-Patch P189+: hier kommt der GGUF-Inferenz-Code rein.
        raise NotImplementedError("Gemma-4-E2B-Analyse-Pfad in Folge-Patch")  # pragma: no cover

    # ---------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------
    def _load_model(self) -> None:
        """Lazy-Load Gemma 4 E2B. Stub bis Folge-Patch.

        WICHTIG: Diesen Aufruf nur durchführen wenn `healthcheck()` ok ist
        (VRAM reicht, Modell-Datei vorhanden). Sonst crasht der Load oder
        verdrängt andere Modelle aus dem VRAM (siehe VRAM-Tetris-Plan).
        """
        if self._model is not None:
            return
        # Folge-Patch P189+: hier llama-cpp-python / transformers / etc.
        # Aktuell explizit: nicht implementiert (kein Stub-Modell laden!)
        logger.info("[PROSODY-188] _load_model() — Stub, kein Modell wird geladen (Folge-Patch)")


# -------------------------------------------------------------------
# Factory
# -------------------------------------------------------------------

_singleton: ProsodyManager | None = None


def get_prosody_manager(settings: Any | None = None) -> ProsodyManager:
    """Factory + Singleton. Liest die Config aus `settings.modules.prosody`.

    `settings=None` nutzt `ProsodyConfig()` (alle Defaults).
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    if settings is None:
        cfg = ProsodyConfig()
    else:
        modules = getattr(settings, "modules", {}) or {}
        prosody_raw = modules.get("prosody", {}) if isinstance(modules, dict) else {}
        cfg = ProsodyConfig.from_dict(prosody_raw)

    _singleton = ProsodyManager(cfg)
    return _singleton


def reset_prosody_manager() -> None:
    """Setzt den Singleton zurück (für Tests / Reload)."""
    global _singleton
    _singleton = None
