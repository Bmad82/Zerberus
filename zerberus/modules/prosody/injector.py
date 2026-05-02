"""
Patch 190 + Patch 204 — Prosodie-Bruecke zum LLM.

P190 hat einen kompakten Hinweis hinter den System-Prompt gepackt, damit
DeepSeek die Stimme "hoeren" kann. P204 (Phase 5a #17) erweitert das zu
einem markierten ``[PROSODIE]``-Block analog ``[PROJEKT-RAG]`` (P199):

    [PROSODIE — Stimmungs-Kontext aus Voice-Input]
    Stimme: ruhig
    Tempo: langsam
    Sentiment-Text: leicht positiv (BERT)
    Sentiment-Stimme: muede (Gemma)
    Konsens: ruhig-leicht-gedrueckt
    [/PROSODIE]

Worker-Protection (P191):
  Der Block enthaelt **ausschliesslich qualitative Labels**, keine
  Zahlenwerte. Confidence/Valence/Arousal/BERT-Score werden im
  Konsens-Label verkocht, das LLM bekommt nur die menschenlesbare
  Beschreibung. Damit kann es die Daten nicht zu Performance-
  Bewertungen aus Stimmungsdaten missbrauchen.

Gating (Fail-open):
  - ``prosody=None`` oder Stub-Source                     → kein Block
  - ``confidence < 0.3``                                  → kein Block
  - kein BERT mitgegeben                                  → Block ohne
    Sentiment-Text-Zeile (Voice-only-Pfad)
  - Idempotenz: ``PROSODY_BLOCK_MARKER`` schon im Prompt  → kein zweiter

Voice-only-Garantie:
  Diese Pure-Function entscheidet nicht, ob der Input Voice oder Tipp
  war — das ist Aufgabe des Endpoints. Der ``X-Prosody-Context``-Header
  wird im Frontend nur nach einem Whisper-Roundtrip gesetzt; bei
  getipptem Text kommt er gar nicht erst an. Defense-in-depth: das
  Stub-Source-Check filtert ausserdem alle Faelle, in denen das Frontend
  versehentlich einen Pseudo-Context mitsendet.

Logging-Tags:
  [PROSODY-190]  Legacy-Aufrufe (Backward-Compat)
  [PROSODY-204]  Erweiterte Bruecke mit BERT + Konsens
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)
_LOG_TAG_190 = "[PROSODY-190]"
_LOG_TAG_204 = "[PROSODY-204]"


# ---------------------------------------------------------------------------
# Marker — analog [PROJEKT-RAG ...] aus P199
# ---------------------------------------------------------------------------

PROSODY_BLOCK_MARKER = "[PROSODIE — Stimmungs-Kontext aus Voice-Input]"
PROSODY_BLOCK_CLOSE = "[/PROSODIE]"


# ---------------------------------------------------------------------------
# Lookup-Tabellen — qualitative Labels, keine Zahlen
# ---------------------------------------------------------------------------

_BERT_LABEL_DE = {
    "positive": "positiv",
    "negative": "negativ",
    "neutral": "neutral",
}

_PROSODY_MOOD_DE = {
    "happy": "froehlich",
    "excited": "begeistert",
    "calm": "ruhig",
    "sad": "traurig",
    "angry": "veraergert",
    "stressed": "gestresst",
    "tired": "muede",
    "anxious": "angespannt",
    "sarcastic": "sarkastisch",
    "neutral": "neutral",
}

_PROSODY_TEMPO_DE = {
    "slow": "langsam",
    "normal": "normal",
    "fast": "schnell",
}

# Schwellen aus P192/sentiment_display abgeleitet — gleiche Mehrabian-
# Heuristik, damit UI-Konsens und LLM-Konsens nicht voneinander abweichen.
_BERT_HIGH = 0.7
_BERT_POSITIVE = 0.5
_PROSODY_VALENCE_NEGATIVE = -0.2
_PROSODY_DOMINATES_CONFIDENCE = 0.5
_MIN_CONFIDENCE_FOR_BLOCK = 0.3


# ---------------------------------------------------------------------------
# Pure-Function-Helpers
# ---------------------------------------------------------------------------


def _bert_qualitative(label: Optional[str], score: Optional[float]) -> str:
    """BERT-Label + Score → qualitatives Adjektiv ohne Zahl."""
    label_lc = (label or "neutral").lower()
    try:
        s = float(score) if score is not None else 0.5
    except (TypeError, ValueError):
        s = 0.5
    base = _BERT_LABEL_DE.get(label_lc, label_lc)
    if label_lc == "neutral":
        return base
    return f"deutlich {base}" if s > _BERT_HIGH else f"leicht {base}"


def _consensus_label(
    bert_label: Optional[str],
    bert_score: Optional[float],
    prosody: dict,
) -> str:
    """Sprachliches Konsens-Label, ohne Zahlen.

    Mehrabian-Logik (analog ``utils.sentiment_display.consensus_emoji``):
      - Inkongruent (BERT positiv + Prosody-Valenz negativ)  → Warnung
      - Hohe Prosody-Confidence (> 0.5)                      → Stimme
      - Sonst                                                → BERT
    """
    bert_lc = (bert_label or "").lower() if bert_label else ""
    try:
        bert_s = float(bert_score) if bert_score is not None else 0.5
    except (TypeError, ValueError):
        bert_s = 0.5

    try:
        valence = float(prosody.get("valence", 0.5))
        confidence = float(prosody.get("confidence", 0.0))
    except (TypeError, ValueError):
        valence = 0.5
        confidence = 0.0

    bert_positive = bert_lc == "positive" and bert_s > _BERT_POSITIVE
    prosody_negative = valence < _PROSODY_VALENCE_NEGATIVE

    if bert_positive and prosody_negative:
        return "inkongruent — Text positiv, Stimme negativ (moegliche Ironie oder Stress)"

    if confidence > _PROSODY_DOMINATES_CONFIDENCE:
        mood = (prosody.get("mood") or "neutral").lower()
        return _PROSODY_MOOD_DE.get(mood, mood)

    if not bert_lc:
        # Kein BERT-Input → fallback auf Stimm-Mood (auch wenn Confidence niedrig)
        mood = (prosody.get("mood") or "neutral").lower()
        return _PROSODY_MOOD_DE.get(mood, mood)

    return _bert_qualitative(bert_lc, bert_s)


# ---------------------------------------------------------------------------
# Pure-Function: Block bauen (testet ohne I/O)
# ---------------------------------------------------------------------------


def build_prosody_block(
    prosody: Optional[dict],
    *,
    bert_label: Optional[str] = None,
    bert_score: Optional[float] = None,
) -> str:
    """Baut den ``[PROSODIE]``-Block; ``""`` wenn Gating greift.

    Args:
        prosody: Output von ``ProsodyManager.analyze()``.
        bert_label: Optional, ``"positive"``/``"negative"``/``"neutral"``.
        bert_score: Optional, BERT-Score 0..1.

    Worker-Protection: keine Zahl im Output. Confidence/Score/Valence
    werden im Konsens-Label verkocht.
    """
    if not prosody or not isinstance(prosody, dict):
        return ""
    if prosody.get("source") == "stub":
        return ""

    try:
        confidence = float(prosody.get("confidence", 0.0))
    except (TypeError, ValueError):
        return ""
    if confidence < _MIN_CONFIDENCE_FOR_BLOCK:
        return ""

    mood_raw = (prosody.get("mood") or "neutral").lower()
    tempo_raw = (prosody.get("tempo") or "normal").lower()
    mood_de = _PROSODY_MOOD_DE.get(mood_raw, mood_raw)
    tempo_de = _PROSODY_TEMPO_DE.get(tempo_raw, tempo_raw)

    consensus = _consensus_label(bert_label, bert_score, prosody)

    parts: list[str] = [
        "",
        "",
        PROSODY_BLOCK_MARKER,
        f"Stimme: {mood_de}",
        f"Tempo: {tempo_de}",
    ]
    if bert_label:
        parts.append(f"Sentiment-Text: {_bert_qualitative(bert_label, bert_score)} (BERT)")
    parts.append(f"Sentiment-Stimme: {mood_de} (Gemma)")
    parts.append(f"Konsens: {consensus}")
    parts.append(PROSODY_BLOCK_CLOSE)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API — am System-Prompt anhaengen
# ---------------------------------------------------------------------------


def inject_prosody_context(
    system_prompt: str,
    prosody_result: Optional[dict],
    *,
    bert_label: Optional[str] = None,
    bert_score: Optional[float] = None,
) -> str:
    """Haengt den ``[PROSODIE]``-Block hinten an den System-Prompt.

    Args:
        system_prompt: Bestehender System-Prompt.
        prosody_result: Output von ``ProsodyManager.analyze()`` oder None.
        bert_label: Optional, BERT-Sentiment-Label fuer Konsens.
        bert_score: Optional, BERT-Score 0..1 fuer Konsens.

    Returns:
        System-Prompt mit angefuegtem Block (wenn Gating es zulaesst),
        sonst unveraendert.

    P204: optionale ``bert_*``-Parameter sind keyword-only und additiv —
    bestehende P190-Aufrufer ohne BERT bleiben funktionsfaehig (der Block
    wird dann ohne Sentiment-Text-Zeile gebaut). Idempotent: ein Prompt
    mit bereits eingehaengtem Marker bekommt keinen zweiten Block.
    """
    if not prosody_result:
        return system_prompt

    if system_prompt and PROSODY_BLOCK_MARKER in system_prompt:
        return system_prompt

    block = build_prosody_block(
        prosody_result,
        bert_label=bert_label,
        bert_score=bert_score,
    )
    if not block:
        confidence_logged = prosody_result.get("confidence", 0.0)
        logger.debug(
            f"{_LOG_TAG_190} kein Block — source={prosody_result.get('source')!r} "
            f"confidence={confidence_logged}"
        )
        return system_prompt

    mood = prosody_result.get("mood")
    if bert_label:
        logger.info(
            f"{_LOG_TAG_204} block_added bert_label={bert_label!r} mood={mood!r}"
        )
    else:
        logger.info(f"{_LOG_TAG_190} block_added mood={mood!r} (no bert)")

    if not system_prompt:
        # Block beginnt mit zwei Leerzeilen; bei leerem Prompt strippen.
        return block.lstrip("\n")
    return system_prompt + block
