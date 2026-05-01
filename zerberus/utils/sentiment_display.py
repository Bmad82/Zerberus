"""
Sentiment-Display – Patch 192

Emoji-Mapping fuer Triptychon-UI (BERT-Text + Prosodie + Konsens).
Logger-Tag: [SENTIMENT-192]

Drei Kanaele:
- BERT  : Text-Sentiment (was geschrieben/gesagt wird)
- Prosodie : Stimm-Analyse (wie es klingt) — nur bei Audio-Input
- Konsens : fusioniertes Gesamtbild (Mehrabian: Stimme > Text bei hoher Confidence)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_BERT_HIGH = 0.7
_BERT_POSITIVE = 0.5
_PROSODY_VALENCE_NEGATIVE = -0.2
_PROSODY_DOMINATES_CONFIDENCE = 0.5

_PROSODY_MOOD_EMOJI = {
    "happy":     "😊",
    "excited":   "🤩",
    "calm":      "😌",
    "sad":       "😢",
    "angry":     "😠",
    "stressed":  "😰",
    "tired":     "😴",
    "anxious":   "😬",
    "sarcastic": "😏",
    "neutral":   "😶",
}


def bert_emoji(label: str, score: float) -> str:
    """BERT-Label + Score → Emoji."""
    label = (label or "neutral").lower()
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    if label == "positive" and s > _BERT_HIGH:
        return "😊"
    if label == "positive":
        return "🙂"
    if label == "negative" and s > _BERT_HIGH:
        return "😟"
    if label == "negative":
        return "😐"
    return "😶"


def prosody_emoji(prosody: Optional[dict]) -> str:
    """Prosodie-Dict → Emoji (Default: neutral)."""
    if not prosody:
        return "😶"
    mood = (prosody.get("mood") or "neutral").lower()
    return _PROSODY_MOOD_EMOJI.get(mood, "😶")


def consensus_emoji(bert_label: str, bert_score: float, prosody: Optional[dict]) -> str:
    """Fusioniertes Emoji — erkennt Inkongruenz (Text positiv, Stimme negativ → 🤔).

    Mehrabian-Regel: bei hoher Prosodie-Confidence dominiert die Stimme,
    sonst faellt der Konsens auf BERT zurueck.
    """
    label = (bert_label or "neutral").lower()
    try:
        s = float(bert_score)
    except (TypeError, ValueError):
        s = 0.0

    if not prosody:
        return bert_emoji(label, s)

    bert_positive = label == "positive" and s > _BERT_POSITIVE
    try:
        valence = float(prosody.get("valence", 0.5))
    except (TypeError, ValueError):
        valence = 0.5
    try:
        confidence = float(prosody.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    prosody_negative = valence < _PROSODY_VALENCE_NEGATIVE

    # Inkongruenz: Text sagt positiv, Stimme sagt negativ → Widerspruch.
    if bert_positive and prosody_negative:
        return "🤔"

    # Mehrabian: hohe Confidence → Stimme dominiert.
    if confidence > _PROSODY_DOMINATES_CONFIDENCE:
        return prosody_emoji(prosody)

    # Fallback auf BERT.
    return bert_emoji(label, s)


def compute_consensus(bert_label: str, bert_score: float, prosody: Optional[dict]) -> dict:
    """Berechnet Konsens-Dict fuer Frontend.

    Rueckgabe:
        {
            "emoji": str,
            "incongruent": bool,
            "source": "bert+prosody" | "bert_only"
        }
    """
    label = (bert_label or "neutral").lower()
    try:
        s = float(bert_score)
    except (TypeError, ValueError):
        s = 0.0

    if prosody:
        try:
            valence = float(prosody.get("valence", 0.5))
        except (TypeError, ValueError):
            valence = 0.5
        bert_positive = label == "positive" and s > _BERT_POSITIVE
        prosody_negative = valence < _PROSODY_VALENCE_NEGATIVE
        incongruent = bool(bert_positive and prosody_negative)
        source = "bert+prosody"
    else:
        incongruent = False
        source = "bert_only"

    return {
        "emoji": consensus_emoji(label, s, prosody),
        "incongruent": incongruent,
        "source": source,
    }


def build_sentiment_payload(
    text: str,
    prosody: Optional[dict] = None,
    bert_result: Optional[dict] = None,
) -> dict:
    """Komplettes Sentiment-Dict fuer eine Bubble (Frontend-Triptychon).

    bert_result wird optional mitgereicht (vermeidet doppelte BERT-Calls);
    fehlt es, wird das Sentiment-Modul direkt befragt.

    Rueckgabe-Form:
        {
            "bert": {"label": str, "score": float, "emoji": str},
            "prosody": dict|None,    # nur bei Audio
            "consensus": dict|None,  # nur wenn BERT vorhanden
        }
    """
    payload: dict = {"bert": None, "prosody": None, "consensus": None}

    if bert_result is None:
        try:
            from zerberus.modules.sentiment.router import analyze_sentiment
            bert_result = analyze_sentiment(text or "")
        except Exception as err:
            logger.warning(f"[SENTIMENT-192] BERT-Analyse fehlgeschlagen: {err}")
            bert_result = {"label": "neutral", "score": 0.5}

    if bert_result:
        label = bert_result.get("label", "neutral")
        score = bert_result.get("score", 0.5)
        payload["bert"] = {
            "label": label,
            "score": score,
            "emoji": bert_emoji(label, score),
        }

        # Prosodie nur uebernehmen wenn nicht-Stub und sinnvolle Daten.
        prosody_clean: Optional[dict] = None
        if prosody and isinstance(prosody, dict) and prosody.get("source") != "stub":
            prosody_clean = {
                "mood": prosody.get("mood"),
                "tempo": prosody.get("tempo"),
                "valence": prosody.get("valence"),
                "arousal": prosody.get("arousal"),
                "confidence": prosody.get("confidence"),
                "source": prosody.get("source"),
                "emoji": prosody_emoji(prosody),
            }
        payload["prosody"] = prosody_clean
        payload["consensus"] = compute_consensus(label, score, prosody_clean)

    return payload
