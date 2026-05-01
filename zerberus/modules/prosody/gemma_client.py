"""
Patch 189 — Gemma 4 E2B Audio-Client (Dual-Path: CLI + Server).

Pfad A (CLI, funktioniert JETZT):
    `llama-mtmd-cli` als Subprocess. Cold-Load pro Call (~1-2s),
    aber zuverlässig und unabhängig von #21868.

Pfad B (Server, Zukunft):
    `llama-server` mit `--mmproj`, OpenAI-kompatibles
    `input_audio` Content-Block (Issue #21868). Sobald gemergt:
    Server bleibt resident, schneller pro Call.

Die `mode`-Property routet automatisch — Stub wenn nichts konfiguriert.

Logging-Tags:
  [PROSODY-189]   normaler Pfad
  [PROSODY-189-CLI]  CLI-Subprocess
  [PROSODY-189-SRV]  Server-HTTP
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)
_LOG_TAG = "[PROSODY-189]"


class GemmaAudioClient:
    """Spricht mit Gemma 4 E2B — erst via CLI, später via llama-server."""

    def __init__(self, settings: dict | None = None):
        s = settings or {}
        self._model_path = str(s.get("model_path", ""))
        self._mmproj_path = str(s.get("mmproj_path", ""))
        self._server_url = str(s.get("server_url", ""))
        self._llama_cli_path = str(s.get("llama_cli_path", "llama-mtmd-cli"))
        self._device = str(s.get("device", "cuda"))
        self._ngl = int(s.get("n_gpu_layers", 99))
        self._timeout = int(s.get("timeout_seconds", 30))

    # ---------------------------------------------------------------
    # Routing
    # ---------------------------------------------------------------
    @property
    def mode(self) -> str:
        """Welcher Inference-Pfad aktiv ist: 'server' / 'cli' / 'none'."""
        if self._server_url:
            return "server"
        if self._model_path and self._mmproj_path:
            return "cli"
        return "none"

    async def analyze_audio(self, audio_bytes: bytes, prompt: str) -> dict:
        """Audio analysieren — routing nach verfügbarem Backend."""
        m = self.mode
        if m == "server":
            return await self._analyze_via_server(audio_bytes, prompt)
        if m == "cli":
            return await self._analyze_via_cli(audio_bytes, prompt)
        logger.warning(f"{_LOG_TAG} Kein Gemma-Backend konfiguriert")
        return self._stub_result()

    # ---------------------------------------------------------------
    # Pfad B: llama-server (Zukunft, wenn #21868 gemergt ist)
    # ---------------------------------------------------------------
    async def _analyze_via_server(self, audio_bytes: bytes, prompt: str) -> dict:
        audio_b64 = base64.b64encode(audio_bytes).decode()
        payload = {
            "model": "gemma-4-e2b",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "temperature": 0.1,
            "max_tokens": 200,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._server_url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                logger.info(f"{_LOG_TAG}-SRV Server-Analyse OK ({len(text)} Zeichen)")
                return self._parse_gemma_output(text)
        except Exception as e:
            logger.error(f"{_LOG_TAG}-SRV Server-Analyse fehlgeschlagen: {e}")
            return self._stub_result()

    # ---------------------------------------------------------------
    # Pfad A: llama-mtmd-cli (funktioniert JETZT)
    # ---------------------------------------------------------------
    async def _analyze_via_cli(self, audio_bytes: bytes, prompt: str) -> dict:
        # tmp-Datei mit Audio-Bytes — wird im finally gelöscht (Defense-in-Depth)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            cmd = [
                self._llama_cli_path,
                "-m", self._model_path,
                "--mmproj", self._mmproj_path,
                "--audio", tmp_path,
                "-p", prompt,
                "--temp", "0.1",
                "--top-k", "64",
                "--top-p", "0.95",
                "-ngl", str(self._ngl),
                "--jinja",
                "--no-warmup",
                "-n", "200",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout
            )

            if process.returncode != 0:
                err_excerpt = (stderr or b"").decode(errors="replace")[:500]
                logger.error(f"{_LOG_TAG}-CLI rc={process.returncode}: {err_excerpt}")
                return self._stub_result()

            text = (stdout or b"").decode(errors="replace").strip()
            logger.info(f"{_LOG_TAG}-CLI Analyse OK ({len(text)} Zeichen)")
            return self._parse_gemma_output(text)

        except asyncio.TimeoutError:
            logger.error(f"{_LOG_TAG}-CLI Timeout nach {self._timeout}s")
            return self._stub_result()
        except FileNotFoundError:
            logger.error(f"{_LOG_TAG}-CLI Binary nicht gefunden: {self._llama_cli_path}")
            return self._stub_result()
        except Exception as e:
            logger.error(f"{_LOG_TAG}-CLI unerwarteter Fehler: {e}")
            return self._stub_result()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ---------------------------------------------------------------
    # Output-Parsing
    # ---------------------------------------------------------------
    def _parse_gemma_output(self, text: str) -> dict:
        """Versucht JSON aus dem Gemma-Output zu extrahieren.

        Robust gegen:
          - Markdown-Wrapper (```json ... ```)
          - Zusätzlichen Text vor/nach dem JSON
          - Fehlende Pflichtfelder (Defaults)
        """
        if not text:
            return self._stub_result()

        # 1. Markdown-Block ```json ... ``` extrahieren
        md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = md_match.group(1) if md_match else None

        # 2. Fallback: erstes {...}-Stück mit "mood" finden
        if not candidate:
            json_match = re.search(r"\{[^{}]*\"mood\"[^{}]*\}", text, re.DOTALL)
            candidate = json_match.group() if json_match else None

        # 3. Letzter Versuch: irgendein Objekt
        if not candidate:
            obj_match = re.search(r"\{.*\}", text, re.DOTALL)
            candidate = obj_match.group() if obj_match else None

        if not candidate:
            logger.warning(f"{_LOG_TAG} Kein JSON in Gemma-Output, Fallback auf Stub")
            return self._stub_result()

        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"{_LOG_TAG} JSON-Parse fehlgeschlagen: {e}")
            return self._stub_result()

        if not isinstance(data, dict):
            return self._stub_result()

        # Pflichtfelder mit Defaults absichern
        try:
            return {
                "mood": str(data.get("mood", "neutral")),
                "tempo": str(data.get("tempo", "normal")),
                "confidence": float(data.get("confidence", 0.5)),
                "valence": float(data.get("valence", 0.5)),
                "arousal": float(data.get("arousal", 0.5)),
                "dominance": float(data.get("dominance", 0.5)),
                "source": "gemma_e2b",
            }
        except (TypeError, ValueError) as e:
            logger.warning(f"{_LOG_TAG} Feld-Coercion fehlgeschlagen: {e}")
            return self._stub_result()

    @staticmethod
    def _stub_result() -> dict:
        """Neutraler Default — wird genutzt wenn Backend down/disabled.

        Source 'stub' signalisiert dem Konsumenten: das ist Bullshit,
        nicht für Prompt-Injektion verwenden (vgl. ProsodyInjector P190).
        """
        return {
            "mood": "neutral",
            "tempo": "normal",
            "confidence": 0.0,
            "valence": 0.5,
            "arousal": 0.5,
            "dominance": 0.5,
            "source": "stub",
        }
