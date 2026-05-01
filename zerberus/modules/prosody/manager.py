"""
Prosody-Manager — Patch 188 (Foundation) + Patch 189 (Gemma-Backend) +
Patch 190 (`is_active` für Pipeline-Routing).

Der Manager kapselt den Backend-Pfad zu Gemma 4 E2B. `analyze()` routet
über `GemmaAudioClient` — Stub wenn nichts konfiguriert, CLI wenn
Modellpfade gesetzt, Server wenn `server_url` konfiguriert.

Logging-Tags:
  [PROSODY-188]      Startup, Healthcheck (legacy)
  [PROSODY-189]      Gemma-Client-Aufrufe
  [PROSODY-STUB-188] Stub-Antwort (kein Modell + nicht enabled)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from zerberus.modules.prosody.gemma_client import GemmaAudioClient
from zerberus.modules.prosody.prompts import PROSODY_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class ProsodyConfig:
    """Konfiguration für die Prosodie-Pipeline (Gemma 4 E2B).

    Defaults werden im Code gehalten (statt nur in config.yaml), weil
    config.yaml gitignored ist — sonst würden die Werte nach `git clone`
    fehlen.

    P189: erweitert um Backend-Felder (mmproj_path, server_url, llama_cli_path,
    n_gpu_layers, timeout_seconds). Bestehende Felder (enabled, model_path,
    device, vram_threshold_gb, output_format) bleiben unverändert.
    """
    enabled: bool = False
    model_path: str = ""           # Pfad zur GGUF-Datei (leer = nicht geladen)
    mmproj_path: str = ""          # P189: Pfad zur mmproj-Datei (Audio+Vision-Projector)
    server_url: str = ""           # P189: llama-server URL für Pfad B
    llama_cli_path: str = "llama-mtmd-cli"  # P189: CLI-Binary für Pfad A
    device: str = "cuda"           # "cuda" / "cpu"
    n_gpu_layers: int = 99         # P189: -ngl Parameter (99 = alles auf GPU)
    timeout_seconds: int = 30      # P189: CLI/HTTP Timeout
    vram_threshold_gb: float = 2.0 # min freier VRAM zum Laden
    output_format: str = "json"    # "json" / "text"

    @classmethod
    def from_dict(cls, raw: dict) -> "ProsodyConfig":
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            model_path=str(raw.get("model_path", "")),
            mmproj_path=str(raw.get("mmproj_path", "")),
            server_url=str(raw.get("server_url", "")),
            llama_cli_path=str(raw.get("llama_cli_path", "llama-mtmd-cli")),
            device=str(raw.get("device", "cuda")),
            n_gpu_layers=int(raw.get("n_gpu_layers", 99)),
            timeout_seconds=int(raw.get("timeout_seconds", 30)),
            vram_threshold_gb=float(raw.get("vram_threshold_gb", 2.0)),
            output_format=str(raw.get("output_format", "json")),
        )

    def to_client_settings(self) -> dict:
        """Mappt auf das Settings-Dict, das `GemmaAudioClient` erwartet."""
        return {
            "model_path": self.model_path,
            "mmproj_path": self.mmproj_path,
            "server_url": self.server_url,
            "llama_cli_path": self.llama_cli_path,
            "device": self.device,
            "n_gpu_layers": self.n_gpu_layers,
            "timeout_seconds": self.timeout_seconds,
        }


_STUB_FIELDS = ("mood", "tempo", "confidence", "valence", "arousal", "dominance", "source")


class ProsodyManager:
    """Verwaltet das Gemma-4-E2B-Modell für Prosodie-Analyse.

    Patch 188: Foundation + Healthcheck.
    Patch 189: GemmaAudioClient für echten Inferenz-Pfad (CLI/Server).
    Patch 190: `is_active` Property für Pipeline-Gating.
    """

    def __init__(self, config: ProsodyConfig | None = None):
        self.config = config or ProsodyConfig()
        self._model: Any = None  # Reserviert für Pfad B (residenter Server-Client)
        self._client: GemmaAudioClient = GemmaAudioClient(self.config.to_client_settings())
        # P191: Audit-Counter für Hel-Admin (Worker-Protection: keine Inhalte!)
        self._success_count: int = 0
        self._error_count: int = 0
        self._last_success_ts: Optional[float] = None

    # ---------------------------------------------------------------
    # Status / Routing (P190)
    # ---------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        """True wenn Prosodie konfiguriert UND Backend bereit.

        Gating für die Audio-Pipeline (P190). Wenn False:
        Whisper läuft wie bisher, kein gather() mit Gemma.
        """
        return self.config.enabled and self._client.mode != "none"

    @property
    def client_mode(self) -> str:
        """Backend-Modus für Healthcheck/Hel-Admin (cli/server/none)."""
        return self._client.mode

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
            "client_mode": self._client.mode,  # P189
        }

    # ---------------------------------------------------------------
    # Analyse
    # ---------------------------------------------------------------
    async def analyze(self, audio_bytes: bytes) -> dict:
        """Analysiert Audio-Bytes und gibt Prosodie-Metadaten zurück.

        Returns:
            dict mit den Feldern:
              mood, tempo, confidence, valence, arousal, dominance, source

        Routing (P189):
            - enabled=False → Stub (P188-Verhalten)
            - mode=none → Stub (P189-Verhalten, kein Backend konfiguriert)
            - mode=cli  → llama-mtmd-cli Subprocess
            - mode=server → llama-server HTTP
        """
        if not self.config.enabled:
            logger.debug("[PROSODY-STUB-188] analyze() disabled — Stub-Antwort")
            return self._stub_result()

        if self._client.mode == "none":
            logger.debug("[PROSODY-STUB-188] analyze() mode=none — Stub-Antwort")
            return self._stub_result()

        try:
            result = await self._client.analyze_audio(audio_bytes, PROSODY_ANALYSIS_PROMPT)
            if result.get("source") == "gemma_e2b":
                self._success_count += 1
                import time
                self._last_success_ts = time.time()
            else:
                self._error_count += 1
            return result
        except Exception as e:
            self._error_count += 1
            logger.error(f"[PROSODY-189] analyze() Fehler: {e}")
            return self._stub_result()

    # ---------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------
    def _load_model(self) -> None:
        """Lazy-Load Gemma 4 E2B.

        P189: Bei CLI-Modus wird KEIN persistentes Modell geladen — jeder
        Call ist ein eigener Subprocess (Cold-Load). `_model` bleibt None.
        Bei Server-Modus könnte hier eine Health-Probe gegen den Server
        gemacht werden — aktuell nicht implementiert (Server-Pfad wartet
        auf Issue #21868).
        """
        if self._model is not None:
            return
        logger.info(
            f"[PROSODY-189] _load_model() — mode={self._client.mode}, "
            "kein persistentes Modell (CLI lädt pro Call)"
        )

    @staticmethod
    def _stub_result() -> dict:
        return {
            "mood": "neutral",
            "tempo": "normal",
            "confidence": 0.0,
            "valence": 0.5,
            "arousal": 0.5,
            "dominance": 0.5,
            "source": "stub",
        }

    # ---------------------------------------------------------------
    # P191: Admin-Status (für Hel-Admin-Endpoint)
    # ---------------------------------------------------------------
    def admin_status(self) -> dict:
        """Status-Snapshot für Hel-Admin — KEINE individuellen Inhalte.

        Worker-Protection: Mood/Valence/Arousal-Werte werden nicht
        herausgegeben. Nur Aggregate (Counter, Modus, letzter
        Erfolg-Timestamp) sind zugänglich.
        """
        return {
            "enabled": self.config.enabled,
            "mode": self._client.mode,
            "is_active": self.is_active,
            "success_count": self._success_count,
            "error_count": self._error_count,
            "last_success_ts": self._last_success_ts,
            "model_path_set": bool(self.config.model_path),
            "mmproj_path_set": bool(self.config.mmproj_path),
            "server_url_set": bool(self.config.server_url),
        }


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
