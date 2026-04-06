"""
Sentiment-Modul – Patch 57
Deutsches BERT-Modell (oliverguhr/german-sentiment-bert) ersetzt VADER.
Wird beim ersten Import geladen und gecacht – kein Reload pro Request.
"""
import logging

logger = logging.getLogger(__name__)

_pipeline = None
_BERT_OK = False

try:
    import torch
    from transformers import pipeline as _hf_pipeline

    _device = 0 if torch.cuda.is_available() else -1
    _device_label = "cuda" if _device == 0 else "cpu"
    logger.info(f"[Sentiment] Lade german-sentiment-bert (device={_device_label})...")
    _pipeline = _hf_pipeline(
        "text-classification",
        model="oliverguhr/german-sentiment-bert",
        device=_device,
    )
    _BERT_OK = True
    logger.info("[Sentiment] german-sentiment-bert erfolgreich geladen.")
except Exception as _load_err:
    logger.warning(
        f"[Sentiment] BERT-Modell konnte nicht geladen werden (graceful fallback aktiv): {_load_err}"
    )
    _pipeline = None
    _BERT_OK = False

# Normalisierung der Label-Namen des Modells
_LABEL_MAP = {
    "positive": "positive",
    "negative": "negative",
    "neutral": "neutral",
}


def analyze_sentiment(text: str) -> dict:
    """
    Analysiert den Sentiment eines (deutschen) Textes via BERT.

    Rückgabe: {"label": "positive"|"negative"|"neutral", "score": float 0–1}
    Fallback bei fehlendem Modell oder Fehler: {"label": "neutral", "score": 0.5}
    """
    if not _BERT_OK or _pipeline is None:
        return {"label": "neutral", "score": 0.5}
    try:
        # BERT max 512 Tokens – Text vorab kürzen (zeichenbasiert als Näherung)
        result = _pipeline(text[:512])[0]
        label = _LABEL_MAP.get(result["label"].lower(), "neutral")
        return {"label": label, "score": float(result["score"])}
    except Exception as e:
        logger.warning(f"[Sentiment] analyze_sentiment fehlgeschlagen (graceful fallback): {e}")
        return {"label": "neutral", "score": 0.5}
