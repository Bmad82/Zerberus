"""Prompt-Feature-Inject (Patch 118a).

Kleiner Helper, der je nach `settings.features` Hinweise an den System-Prompt
anhängt. Zentral, damit legacy.py und orchestrator.py dieselbe Logik teilen.
"""
from __future__ import annotations

from typing import Any

DECISION_BOX_HINT = (
    "\n\n---\n"
    "Wenn du dem User eine Entscheidungsfrage stellst (Ja/Nein oder eine "
    "kleine Auswahl), formatiere die Optionen als klickbare Entscheidungsbox:\n"
    "[DECISION]\n"
    "[OPTION:wert1] Beschreibung Option 1\n"
    "[OPTION:wert2] Beschreibung Option 2\n"
    "[OPTION:auto] Soll ich das f\u00fcr dich \u00fcbernehmen?\n"
    "[/DECISION]\n"
    "Der User kann so per Tap antworten statt zu tippen. Nur einsetzen, wenn "
    "eine echte bin\u00e4re/ternäre Entscheidung ansteht \u2014 nicht f\u00fcr offene "
    "Fragen."
)


def append_decision_box_hint(prompt: str, settings: Any) -> str:
    """Häängt den Decision-Box-Hinweis an, wenn `features.decision_boxes` aktiv.

    Fail-Safe: Wenn `settings` kein `features`-Attribut hat oder das Feature
    nicht explizit aktiviert ist, bleibt der Prompt unverändert.
    """
    if not prompt:
        return prompt
    try:
        features = getattr(settings, "features", None) or {}
        if not features.get("decision_boxes", False):
            return prompt
    except Exception:
        return prompt
    if "[DECISION]" in prompt:  # Vermeidet Doppel-Injection
        return prompt
    return prompt + DECISION_BOX_HINT
