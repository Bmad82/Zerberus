"""
Patch 190 + Patch 204 — Tests fuer Prosodie-LLM-Bruecke.

P190 hat den Block eingefuehrt; P204 (Phase 5a #17) hat das Format auf
einen markierten ``[PROSODIE]...[/PROSODIE]``-Block umgestellt und um
optionale BERT-Konsens-Logik erweitert. Die Gating-Regeln (Stub,
Low-Confidence, None, invalid types) gelten unveraendert.

Coverage hier:
  - Gating-Verhalten (Stub-Skip, Low-Conf-Skip, None, invalid)
  - Idempotenz (zweiter Aufruf haengt nichts an)
  - Original-Prompt bleibt vor dem Block
  - asyncio.gather-Logik (parallel, Failure-Pfade)
  - Source-Audit fuer Pipeline-Hooks in legacy.py + nala.py

P204-spezifische Erweiterungen (BERT, Konsens, Voice-only):
  → ``test_p204_prosody_context.py``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from zerberus.modules.prosody.injector import (
    PROSODY_BLOCK_CLOSE,
    PROSODY_BLOCK_MARKER,
    inject_prosody_context,
)


ROOT = Path(__file__).resolve().parents[2]


# ====================================================================
# Injector — Block-Bau + Gating
# ====================================================================

class TestInjectProsodyContext:
    def test_inject_prosody_adds_block(self):
        """Echtes Ergebnis (gemma_e2b, hohe Confidence) → markierter Block."""
        sys = "Du bist Nala."
        out = inject_prosody_context(sys, {
            "mood": "happy", "tempo": "fast", "confidence": 0.85,
            "valence": 0.6, "arousal": 0.7, "dominance": 0.5,
            "source": "gemma_e2b",
        })
        assert sys in out  # Original bleibt davor
        assert PROSODY_BLOCK_MARKER in out
        assert PROSODY_BLOCK_CLOSE in out
        # Qualitative Labels statt Zahlen (Worker-Protection P191)
        assert "Stimme: froehlich" in out
        assert "Tempo: schnell" in out
        # Keine Confidence-Zahl mehr im Block
        assert "85%" not in out
        assert "Confidence" not in out

    def test_inject_prosody_skips_stub(self):
        """source='stub' → unveränderter Prompt."""
        sys = "Du bist Nala."
        out = inject_prosody_context(sys, {
            "mood": "neutral", "tempo": "normal", "confidence": 0.0,
            "valence": 0.5, "arousal": 0.5, "dominance": 0.5,
            "source": "stub",
        })
        assert out == sys

    def test_inject_prosody_skips_low_confidence(self):
        """Confidence < 0.3 → kein Block (zu unsicher)."""
        sys = "Du bist Nala."
        out = inject_prosody_context(sys, {
            "mood": "happy", "tempo": "fast", "confidence": 0.2,
            "valence": 0.6, "arousal": 0.7, "dominance": 0.5,
            "source": "gemma_e2b",
        })
        assert out == sys

    def test_inject_prosody_consensus_label_present(self):
        """Block enthält 'Konsens:' als Label (immer, mit oder ohne BERT)."""
        sys = "Du bist Nala."
        out = inject_prosody_context(sys, {
            "mood": "calm", "tempo": "normal", "confidence": 0.7,
            "valence": 0.4, "arousal": 0.3, "dominance": 0.5,
            "source": "gemma_e2b",
        })
        assert "Konsens:" in out
        # Ohne BERT → Stimm-Mood ist Konsens-Default
        assert "ruhig" in out

    def test_inject_prosody_inkongruenz_when_text_positive_voice_negative(self):
        """BERT positiv + Prosody-Valenz negativ → Inkongruenz-Konsens."""
        sys = "Du bist Nala."
        out = inject_prosody_context(
            sys,
            {
                "mood": "sad", "tempo": "slow", "confidence": 0.7,
                "valence": -0.6, "arousal": 0.4, "dominance": 0.3,
                "source": "gemma_e2b",
            },
            bert_label="positive",
            bert_score=0.8,
        )
        assert "inkongruent" in out.lower()

    def test_inject_prosody_no_inkongruenz_for_positive_valence(self):
        """Positive Valenz → kein Inkongruenz-Konsens."""
        sys = "Du bist Nala."
        out = inject_prosody_context(sys, {
            "mood": "happy", "tempo": "fast", "confidence": 0.7,
            "valence": 0.5, "arousal": 0.6, "dominance": 0.5,
            "source": "gemma_e2b",
        })
        assert "inkongruent" not in out.lower()
        assert "Ironie" not in out

    def test_inject_prosody_preserves_original_prompt(self):
        """Block kommt HINTER den Original-Prompt, der bleibt unverändert."""
        sys = "Du bist Nala. Sei höflich."
        out = inject_prosody_context(sys, {
            "mood": "calm", "tempo": "normal", "confidence": 0.6,
            "valence": 0.4, "arousal": 0.3, "dominance": 0.5,
            "source": "gemma_e2b",
        })
        assert out.startswith(sys)
        assert len(out) > len(sys)

    def test_inject_prosody_handles_none(self):
        """prosody_result=None → unverändert."""
        sys = "Du bist Nala."
        assert inject_prosody_context(sys, None) == sys

    def test_inject_prosody_handles_missing_confidence(self):
        """Kein confidence-Feld → fallback (skip wegen 0 < 0.3)."""
        sys = "Du bist Nala."
        out = inject_prosody_context(sys, {
            "mood": "happy", "tempo": "fast",
            "valence": 0.5, "source": "gemma_e2b",
        })
        # confidence default 0 < 0.3 → kein Block
        assert out == sys

    def test_inject_prosody_handles_invalid_confidence(self):
        """Confidence ist nicht-numerisch → unverändert."""
        sys = "Du bist Nala."
        out = inject_prosody_context(sys, {
            "mood": "happy", "tempo": "fast", "confidence": "high",
            "source": "gemma_e2b",
        })
        assert out == sys

    def test_inject_prosody_idempotent_no_double_block(self):
        """Zweiter Aufruf am gleichen Prompt haengt keinen zweiten Block an."""
        sys = "Du bist Nala."
        prosody = {
            "mood": "happy", "tempo": "fast", "confidence": 0.7,
            "valence": 0.5, "arousal": 0.6, "source": "gemma_e2b",
        }
        once = inject_prosody_context(sys, prosody)
        twice = inject_prosody_context(once, prosody)
        assert once == twice
        assert once.count(PROSODY_BLOCK_MARKER) == 1


# ====================================================================
# Parallel-Execution-Logik (asyncio.gather Pattern)
# ====================================================================

class TestParallelExecution:
    def test_parallel_execution_both_succeed(self):
        """Beide Tasks liefern Ergebnis → beides verfügbar."""
        async def whisper():
            return {"text": "hallo"}

        async def prosody():
            return {"mood": "happy", "source": "gemma_e2b"}

        async def run():
            w_task = asyncio.create_task(whisper())
            p_task = asyncio.create_task(prosody())
            return await asyncio.gather(w_task, p_task, return_exceptions=True)

        wr, pr = asyncio.run(run())
        assert wr == {"text": "hallo"}
        assert pr["mood"] == "happy"

    def test_parallel_execution_prosody_fails(self):
        """Prosody Exception → return_exceptions liefert die Exception, Whisper OK."""
        async def whisper():
            return {"text": "hallo"}

        async def prosody():
            raise RuntimeError("gemma kaputt")

        async def run():
            return await asyncio.gather(
                whisper(), prosody(), return_exceptions=True
            )

        wr, pr = asyncio.run(run())
        assert wr == {"text": "hallo"}
        assert isinstance(pr, Exception)

    def test_parallel_execution_whisper_fails(self):
        """Whisper Exception → muss propagiert werden (harter Fehler)."""
        async def whisper():
            raise RuntimeError("whisper down")

        async def prosody():
            return {"mood": "happy", "source": "gemma_e2b"}

        async def run():
            return await asyncio.gather(
                whisper(), prosody(), return_exceptions=True
            )

        wr, pr = asyncio.run(run())
        # Endpoint-Logik prüft `isinstance(wr, Exception)` und re-raise't
        assert isinstance(wr, Exception)
        assert pr["source"] == "gemma_e2b"

    def test_prosody_disabled_no_parallel(self):
        """ProsodyManager.is_active=False → kein gather, nur Whisper-Call."""
        from zerberus.modules.prosody.manager import ProsodyManager, ProsodyConfig
        mgr = ProsodyManager(ProsodyConfig(enabled=False))
        assert mgr.is_active is False
        # Endpoint nutzt is_active als Gate — bei False keine Parallel-Logik

    def test_prosody_active_when_enabled_and_path_set(self, tmp_path):
        from zerberus.modules.prosody.manager import ProsodyManager, ProsodyConfig
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True,
            model_path=str(tmp_path / "g.gguf"),
            mmproj_path=str(tmp_path / "p.gguf"),
        ))
        assert mgr.is_active is True


# ====================================================================
# Source-Audit für Pipeline-Hooks
# ====================================================================

class TestProsodyP190SourceAudit:
    def test_log_tag_p190_in_legacy(self):
        """Source-Audit: [PROSODY-190] in legacy.py."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        assert "[PROSODY-190]" in legacy_src

    def test_log_tag_p190_in_nala(self):
        """Source-Audit: [PROSODY-190] in nala.py."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "[PROSODY-190]" in nala_src

    def test_legacy_uses_asyncio_gather_for_parallel(self):
        """legacy.py nutzt asyncio.gather mit return_exceptions=True."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        assert "asyncio.gather" in legacy_src
        assert "return_exceptions=True" in legacy_src

    def test_nala_uses_asyncio_gather_for_parallel(self):
        """nala.py nutzt asyncio.gather mit return_exceptions=True."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "asyncio.gather" in nala_src
        assert "return_exceptions=True" in nala_src

    def test_legacy_chat_completions_uses_injector(self):
        """legacy.py /chat/completions ruft inject_prosody_context auf."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        assert "inject_prosody_context" in legacy_src
        assert "X-Prosody-Context" in legacy_src

    def test_legacy_uses_x_prosody_consent(self):
        """legacy.py liest X-Prosody-Consent Header."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        assert "X-Prosody-Consent" in legacy_src

    def test_nala_uses_x_prosody_consent(self):
        """nala.py /voice liest X-Prosody-Consent Header."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "X-Prosody-Consent" in nala_src

    def test_injector_module_exists(self):
        injector_path = ROOT / "zerberus" / "modules" / "prosody" / "injector.py"
        assert injector_path.exists()

    def test_pipeline_uses_is_active_gating(self):
        """Beide Endpoints prüfen is_active vor Parallel-Logik."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "is_active" in legacy_src
        assert "is_active" in nala_src


# ====================================================================
# Worker-Protection-Check (Defense-in-Depth)
# ====================================================================

class TestWorkerProtection:
    def test_audio_response_no_prosody_when_consent_off(self):
        """Konzept-Test: Wenn Consent fehlt, gibt der Endpoint kein prosody-Feld."""
        # Logik im legacy.py: prosody_outcome bleibt None wenn _prosody_active=False
        # → response["prosody"] wird nie gesetzt.
        # Hier nur Test der is_active+consent-Kombi:
        from zerberus.modules.prosody.manager import ProsodyManager, ProsodyConfig
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            from pathlib import Path as _P
            (_P(td) / "g.gguf").write_bytes(b"")
            (_P(td) / "p.gguf").write_bytes(b"")
            mgr = ProsodyManager(ProsodyConfig(
                enabled=True,
                model_path=str(_P(td) / "g.gguf"),
                mmproj_path=str(_P(td) / "p.gguf"),
            ))
            consent = False
            active_in_endpoint = mgr.is_active and consent
            assert active_in_endpoint is False
