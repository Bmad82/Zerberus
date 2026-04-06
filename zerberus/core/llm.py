"""
LLM Service – zentraler Aufruf von OpenRouter/Cloud-Modellen.
Patch 34: Split-Brain behoben – liest nur noch aus config.yaml via get_settings()
"""
import os
import json
import logging
import httpx
from pathlib import Path
from typing import Optional, Tuple

from zerberus.core.config import get_settings
from zerberus.core.event_bus import get_event_bus, Event
from zerberus.core.database import save_cost

logger = logging.getLogger(__name__)

class LLMService:
    HISTORY_LIMIT = 20

    def __init__(self):
        self.system_prompt_path = Path("system_prompt.json")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            with open(self.system_prompt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("prompt", "")
        return ""

    async def call(self, messages: list, session_id: Optional[str] = None, model_override: Optional[str] = None) -> Tuple[str, str, int, int, float]:
        """
        Führt einen LLM-Call durch.
        Gibt zurück: (antwort, modell, prompt_tokens, completion_tokens, kosten_in_usd)
        Konfiguration kommt ausschliesslich aus config.yaml (Single Source of Truth).
        model_override: wenn gesetzt, wird dieses Modell statt dem globalen cloud_model verwendet.
        """
        settings = get_settings()
        model = model_override or settings.legacy.models.cloud_model
        temperature = settings.legacy.settings.ai_temperature

        # System-Prompt einfügen, falls nicht vorhanden
        sys_prompt = self._load_system_prompt()
        if sys_prompt and not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": sys_prompt})

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            logger.error("Kein OPENROUTER_API_KEY gesetzt")
            return ("Fehler: Kein API-Key", model, 0, 0, 0.0)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "provider": {
                "data_collection": "deny",
                "order": ["EU"],
                "allow_fallbacks": True
            }
        }
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    settings.legacy.urls.cloud_api_url,
                    json=payload,
                    headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                answer = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                cost = float(resp.headers.get("X-Credits-Used", 0.0))

                if session_id:
                    await save_cost(session_id, model, prompt_tokens, completion_tokens, cost)

                bus = get_event_bus()
                await bus.publish(Event(
                    type="llm_response",
                    data={"model": model, "message": messages[-1]["content"][:100]}
                ))

                return answer, model, prompt_tokens, completion_tokens, cost

        except Exception as e:
            logger.exception("LLM-Aufruf fehlgeschlagen")
            return (f"Fehler: {str(e)}", model, 0, 0, 0.0)