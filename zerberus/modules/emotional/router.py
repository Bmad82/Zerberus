"""
Emotional Modul – Der Stimmungsmacher (Sentiment-Analyse).
"""
import logging
import random
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Emotional"])

analyzer = SentimentIntensityAnalyzer()

class SentimentRequest(BaseModel):
    text: str

class SentimentResponse(BaseModel):
    sentiment: str
    scores: dict
    mood_boost_suggestion: str = None

@router.post("/analyze")
async def analyze_sentiment(
    req: SentimentRequest,
    settings: Settings = Depends(get_settings)
):
    mod_cfg = settings.modules.get("emotional", {})
    if not mod_cfg.get("enabled", False):
        return {"message": "Emotional Modul deaktiviert"}
    
    scores = analyzer.polarity_scores(req.text)
    compound = scores['compound']
    
    if compound >= 0.05:
        sentiment = "positive"
    elif compound <= mod_cfg.get("sentiment_threshold", -0.5):
        sentiment = "negative"
    else:
        sentiment = "neutral"
    
    suggestion = None
    if sentiment == "negative":
        suggestions = mod_cfg.get("mood_boost_suggestions", [])
        if suggestions:
            suggestion = random.choice(suggestions)
    
    bus = get_event_bus()
    await bus.publish(Event(type="user_sentiment", data={"sentiment": sentiment, "compound": compound}))
    if suggestion:
        await bus.publish(Event(type="mood_boost_suggested", data={"suggestion": suggestion}))
    
    return SentimentResponse(sentiment=sentiment, scores=scores, mood_boost_suggestion=suggestion)

@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "emotional"}
