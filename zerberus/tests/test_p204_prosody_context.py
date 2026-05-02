"""Patch 204 (Phase 5a #17) — Tests fuer Prosodie-Kontext im LLM.

Die alte Pipeline-Brücke (P190 ``inject_prosody_context``) wurde zu einem
markierten ``[PROSODIE]...[/PROSODIE]``-Block ausgebaut, der zusaetzlich
BERT-Sentiment + Konsens-Label aufnimmt. Worker-Protection (P191): keine
Zahlenwerte im Block, nur qualitative Labels.

Coverage:
  - ``build_prosody_block`` Pure-Function: Format, BERT-Integration, Konsens
  - ``inject_prosody_context`` mit BERT-Parametern: Layout, Stub/Conf-Gating
  - ``_consensus_label`` Mehrabian-Logik (BERT-Fallback, Stimme dominiert,
    Inkongruenz)
  - Worker-Protection: keine numerischen Werte im Block
  - Voice-only-Garantie ueber Source-Audit (legacy.py liest
    ``X-Prosody-Context`` + ``X-Prosody-Consent``, ruft ``inject_prosody_context``
    mit BERT-Parametern auf)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from zerberus.modules.prosody.injector import (
    PROSODY_BLOCK_CLOSE,
    PROSODY_BLOCK_MARKER,
    _bert_qualitative,
    _consensus_label,
    build_prosody_block,
    inject_prosody_context,
)


ROOT = Path(__file__).resolve().parents[2]


# ====================================================================
# build_prosody_block — Pure-Function-Format
# ====================================================================


class TestBuildProsodyBlock:
    def test_block_has_marker_and_close(self):
        block = build_prosody_block({
            "mood": "calm", "tempo": "normal", "confidence": 0.7,
            "valence": 0.4, "source": "gemma_e2b",
        })
        assert PROSODY_BLOCK_MARKER in block
        assert PROSODY_BLOCK_CLOSE in block
        # Marker steht VOR dem Close (Block ist sauber geklammert).
        assert block.index(PROSODY_BLOCK_MARKER) < block.index(PROSODY_BLOCK_CLOSE)

    def test_block_has_stimme_and_tempo_labels(self):
        block = build_prosody_block({
            "mood": "tired", "tempo": "slow", "confidence": 0.8,
            "valence": 0.0, "source": "gemma_e2b",
        })
        assert "Stimme: muede" in block
        assert "Tempo: langsam" in block

    def test_block_has_consensus_line_with_bert(self):
        block = build_prosody_block(
            {
                "mood": "calm", "tempo": "normal", "confidence": 0.7,
                "valence": 0.3, "source": "gemma_e2b",
            },
            bert_label="positive",
            bert_score=0.6,
        )
        assert "Sentiment-Text:" in block
        assert "Sentiment-Stimme:" in block
        assert "Konsens:" in block
        # Mit BERT 'positive' + Score 0.6 → leicht positiv (kein "deutlich")
        assert "leicht positiv" in block
        assert "deutlich" not in block

    def test_block_without_bert_has_no_text_sentiment_line(self):
        """Ohne BERT-Label nur Stimm-Seite — Sentiment-Text-Zeile fehlt."""
        block = build_prosody_block({
            "mood": "happy", "tempo": "fast", "confidence": 0.7,
            "valence": 0.5, "source": "gemma_e2b",
        })
        assert "Sentiment-Stimme:" in block
        assert "Sentiment-Text:" not in block
        assert "Konsens:" in block

    def test_block_empty_for_stub_source(self):
        assert build_prosody_block({
            "mood": "neutral", "tempo": "normal", "confidence": 0.0,
            "valence": 0.5, "source": "stub",
        }) == ""

    def test_block_empty_for_low_confidence(self):
        assert build_prosody_block({
            "mood": "happy", "tempo": "fast", "confidence": 0.2,
            "valence": 0.5, "source": "gemma_e2b",
        }) == ""

    def test_block_empty_for_none_or_wrong_type(self):
        assert build_prosody_block(None) == ""
        assert build_prosody_block("nicht-dict") == ""
        assert build_prosody_block([1, 2, 3]) == ""

    def test_block_empty_for_non_numeric_confidence(self):
        assert build_prosody_block({
            "mood": "happy", "tempo": "fast", "confidence": "high",
            "valence": 0.5, "source": "gemma_e2b",
        }) == ""

    def test_block_unknown_mood_falls_back_to_raw(self):
        """Unbekanntes Mood-Label wird durchgereicht (keine Lookup-Treffer)."""
        block = build_prosody_block({
            "mood": "ekstatisch", "tempo": "fast", "confidence": 0.7,
            "valence": 0.5, "source": "gemma_e2b",
        })
        assert "Stimme: ekstatisch" in block


# ====================================================================
# Worker-Protection (P191) — keine Zahlen im Block
# ====================================================================


class TestWorkerProtectionNoNumbers:
    """Defense gegen Performance-Bewertungen aus Stimmungsdaten.

    Der Block darf KEINE numerischen Werte enthalten — kein Score, keine
    Confidence-Prozente, keine Valence/Arousal-Floats. Nur qualitative
    Labels gehen ans LLM. Damit kann das Modell die Daten nicht in
    quantitative Verhaltens-Ableitungen verkocken.
    """

    @pytest.mark.parametrize("prosody,bert", [
        # Volle Pipeline mit BERT
        (
            {"mood": "happy", "tempo": "fast", "confidence": 0.85,
             "valence": 0.7, "arousal": 0.8, "source": "gemma_e2b"},
            ("positive", 0.92),
        ),
        # Negative Werte
        (
            {"mood": "sad", "tempo": "slow", "confidence": 0.65,
             "valence": -0.5, "arousal": 0.2, "source": "gemma_e2b"},
            ("negative", 0.71),
        ),
        # Edge: extrem hohe Confidence
        (
            {"mood": "calm", "tempo": "normal", "confidence": 0.99,
             "valence": 0.4, "arousal": 0.3, "source": "gemma_e2b"},
            None,
        ),
    ])
    def test_block_contains_no_numbers(self, prosody, bert):
        bert_label = bert[0] if bert else None
        bert_score = bert[1] if bert else None
        block = build_prosody_block(
            prosody, bert_label=bert_label, bert_score=bert_score,
        )
        assert block, "Block sollte gebaut werden"
        # Keine Confidence-Prozente
        assert "%" not in block
        # Keine Decimal-Floats (z.B. "0.85", "-0.5")
        assert not re.search(r"-?\d+\.\d+", block)
        # Keine Standalone-Integer als Werte (Marker enthaelt die "204"/Tags
        # nicht — der Block ist menschenlesbar). Wir checken nur, dass keine
        # Zahl als eigenes Token vorkommt.
        for line in block.splitlines():
            # Erlaubte Zeilen: leer, Marker (eckige Klammern), "Stimme: ...", etc.
            if line.startswith("[") or not line.strip():
                continue
            assert not re.search(r"\b\d+\b", line), f"Zahl in Zeile: {line!r}"


# ====================================================================
# Konsens-Label — Mehrabian-Logik
# ====================================================================


class TestConsensusLabel:
    def test_consensus_inkongruent_when_text_positive_voice_negative(self):
        prosody = {"mood": "sad", "valence": -0.5, "confidence": 0.7}
        out = _consensus_label("positive", 0.8, prosody)
        assert "inkongruent" in out.lower()

    def test_consensus_voice_dominates_at_high_confidence(self):
        """Confidence > 0.5 + Stimme positiv → Stimme dominiert."""
        prosody = {"mood": "happy", "valence": 0.5, "confidence": 0.8}
        out = _consensus_label("neutral", 0.5, prosody)
        # Mit "happy"-Mood + hohe Confidence → Stimm-Mood gewinnt
        assert out == "froehlich"

    def test_consensus_falls_back_to_bert_at_low_confidence(self):
        """Confidence < 0.5 → BERT-Fallback."""
        prosody = {"mood": "happy", "valence": 0.4, "confidence": 0.2}
        out = _consensus_label("negative", 0.8, prosody)
        # BERT-Fallback: deutlich negativ
        assert out == "deutlich negativ"

    def test_consensus_neutral_bert_low_voice_confidence(self):
        prosody = {"mood": "neutral", "valence": 0.5, "confidence": 0.0}
        out = _consensus_label("neutral", 0.5, prosody)
        assert out == "neutral"

    def test_consensus_without_bert_falls_back_to_voice(self):
        """Kein BERT → Stimm-Mood ist Konsens (egal welche Confidence)."""
        prosody = {"mood": "tired", "valence": 0.0, "confidence": 0.4}
        out = _consensus_label(None, None, prosody)
        assert out == "muede"

    def test_consensus_handles_invalid_numeric_inputs(self):
        prosody = {"mood": "calm", "valence": "huge", "confidence": "high"}
        out = _consensus_label("positive", "great", prosody)
        # Defaults greifen: Valence=0.5, Confidence=0.0 → BERT-Fallback
        assert isinstance(out, str)
        assert out  # non-empty


class TestBertQualitative:
    def test_bert_qualitative_positive_high(self):
        assert _bert_qualitative("positive", 0.85) == "deutlich positiv"

    def test_bert_qualitative_positive_low(self):
        assert _bert_qualitative("positive", 0.55) == "leicht positiv"

    def test_bert_qualitative_negative_high(self):
        assert _bert_qualitative("negative", 0.9) == "deutlich negativ"

    def test_bert_qualitative_negative_low(self):
        assert _bert_qualitative("negative", 0.4) == "leicht negativ"

    def test_bert_qualitative_neutral(self):
        # Neutral hat kein "leicht/deutlich"-Praefix
        assert _bert_qualitative("neutral", 0.99) == "neutral"

    def test_bert_qualitative_handles_invalid_score(self):
        assert _bert_qualitative("positive", "high") == "leicht positiv"


# ====================================================================
# inject_prosody_context — Backward-Compat + BERT-Erweiterung
# ====================================================================


class TestInjectWithBert:
    def test_inject_block_appended_with_bert_labels(self):
        sys = "Du bist Nala."
        out = inject_prosody_context(
            sys,
            {
                "mood": "calm", "tempo": "normal", "confidence": 0.7,
                "valence": 0.4, "source": "gemma_e2b",
            },
            bert_label="positive",
            bert_score=0.6,
        )
        assert sys in out
        assert PROSODY_BLOCK_MARKER in out
        assert "Sentiment-Text: leicht positiv (BERT)" in out
        assert "Sentiment-Stimme: ruhig (Gemma)" in out
        assert "Konsens:" in out

    def test_inject_with_bert_skips_when_stub(self):
        sys = "Du bist Nala."
        out = inject_prosody_context(
            sys,
            {"source": "stub", "mood": "neutral", "confidence": 0.0,
             "valence": 0.5},
            bert_label="positive",
            bert_score=0.9,
        )
        # Stub gewinnt — kein Block, auch wenn BERT da ist
        assert out == sys

    def test_inject_with_bert_skips_low_confidence(self):
        sys = "Du bist Nala."
        out = inject_prosody_context(
            sys,
            {"mood": "happy", "tempo": "fast", "confidence": 0.1,
             "valence": 0.5, "source": "gemma_e2b"},
            bert_label="positive",
            bert_score=0.8,
        )
        assert out == sys

    def test_inject_into_empty_system_prompt(self):
        """Leerer Base-Prompt → Block beginnt direkt (ohne Leerzeilen-Praefix)."""
        out = inject_prosody_context(
            "",
            {"mood": "happy", "tempo": "fast", "confidence": 0.7,
             "valence": 0.5, "source": "gemma_e2b"},
            bert_label="positive",
            bert_score=0.8,
        )
        assert out.startswith(PROSODY_BLOCK_MARKER)
        assert PROSODY_BLOCK_CLOSE in out

    def test_inject_idempotent_with_marker_already_present(self):
        sys = f"Du bist Nala.\n\n{PROSODY_BLOCK_MARKER}\nSchon da.\n{PROSODY_BLOCK_CLOSE}"
        out = inject_prosody_context(
            sys,
            {"mood": "happy", "tempo": "fast", "confidence": 0.7,
             "valence": 0.5, "source": "gemma_e2b"},
            bert_label="positive",
            bert_score=0.8,
        )
        # Kein zweiter Marker
        assert out == sys
        assert out.count(PROSODY_BLOCK_MARKER) == 1


# ====================================================================
# Source-Audit fuer P204-Verdrahtung in legacy.py
# ====================================================================


class TestP204LegacyVerdrahtung:
    def test_legacy_imports_inject_prosody_context(self):
        legacy_src = (
            ROOT / "zerberus" / "app" / "routers" / "legacy.py"
        ).read_text(encoding="utf-8")
        assert "inject_prosody_context" in legacy_src

    def test_legacy_passes_bert_label_and_score(self):
        """legacy.py reicht bert_label + bert_score an inject_prosody_context."""
        legacy_src = (
            ROOT / "zerberus" / "app" / "routers" / "legacy.py"
        ).read_text(encoding="utf-8")
        # Quick-check: bert_label kommt im Aufruf-Kontext vor
        assert "bert_label=" in legacy_src
        assert "bert_score=" in legacy_src

    def test_legacy_uses_p204_log_tag(self):
        legacy_src = (
            ROOT / "zerberus" / "app" / "routers" / "legacy.py"
        ).read_text(encoding="utf-8")
        assert "[PROSODY-204]" in legacy_src

    def test_legacy_voice_only_via_x_prosody_context_header(self):
        """Voice-only-Garantie: Block wird nur gebaut wenn der Header da ist.

        Source-Audit-Schwelle: legacy.py liest ``X-Prosody-Context`` UND
        ``X-Prosody-Consent``, baut den Block nur unter beiden Bedingungen.
        """
        legacy_src = (
            ROOT / "zerberus" / "app" / "routers" / "legacy.py"
        ).read_text(encoding="utf-8")
        assert 'request.headers.get("X-Prosody-Context"' in legacy_src
        assert 'request.headers.get("X-Prosody-Consent"' in legacy_src

    def test_legacy_calls_inject_with_keyword_args(self):
        """Defense-in-Depth: BERT-Parameter sind keyword-only (additiv)."""
        legacy_src = (
            ROOT / "zerberus" / "app" / "routers" / "legacy.py"
        ).read_text(encoding="utf-8")
        # Keyword-Aufruf-Pattern
        assert "inject_prosody_context(" in legacy_src
        # bert_label= als Keyword (nicht als positional)
        assert re.search(
            r"inject_prosody_context\([^)]*bert_label\s*=",
            legacy_src,
            re.DOTALL,
        ), "inject_prosody_context muss bert_label als Keyword bekommen"

    def test_legacy_bert_call_is_failopen(self):
        """BERT-Aufruf ist in try/except gewickelt (fail-open)."""
        legacy_src = (
            ROOT / "zerberus" / "app" / "routers" / "legacy.py"
        ).read_text(encoding="utf-8")
        # Suche: analyze_sentiment-Call innerhalb eines try-Blocks mit
        # PROSODY-204-Logger im except-Pfad
        assert "analyze_sentiment(last_user_msg" in legacy_src
        # except-Pfad mit PROSODY-204-Tag (fail-open)
        assert "[PROSODY-204] BERT-Analyse" in legacy_src


# ====================================================================
# Marker-Eindeutigkeit (analog [PROJEKT-RAG] aus P199)
# ====================================================================


class TestMarkerUniqueness:
    def test_marker_format_starts_with_prosodie(self):
        assert PROSODY_BLOCK_MARKER.startswith("[PROSODIE")

    def test_close_marker_format(self):
        assert PROSODY_BLOCK_CLOSE == "[/PROSODIE]"

    def test_marker_distinct_from_other_markers(self):
        """Sicherstellen, dass der Marker nicht mit P190/P192/P197/P199 kollidiert."""
        from zerberus.core.persona_merge import PROJECT_BLOCK_MARKER
        from zerberus.core.projects_rag import PROJECT_RAG_BLOCK_MARKER

        assert PROSODY_BLOCK_MARKER != PROJECT_BLOCK_MARKER
        assert PROSODY_BLOCK_MARKER != PROJECT_RAG_BLOCK_MARKER
        # Auch substring-disjoint, damit ein Match auf einen Marker nicht
        # versehentlich beim anderen anschlaegt.
        assert PROSODY_BLOCK_MARKER not in PROJECT_BLOCK_MARKER
        assert PROSODY_BLOCK_MARKER not in PROJECT_RAG_BLOCK_MARKER
