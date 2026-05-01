"""
Patch 190 — Prosodie-Injektor für System-Prompts.

Der Injektor packt einen kompakten Prosodie-Hinweis hinter den
System-Prompt, damit das LLM beim Antworten weiß: "die Stimme
klingt gestresst" oder "Tempo ist hektisch". Inkongruenz-Warnung
wenn Valenz negativ ist (Hinweis auf verdeckte Ironie/Stress).

Gating:
  - Stub-Quelle (`source == "stub"`)  → kein Block
  - Confidence < 0.3                  → kein Block (zu unsicher)
  - Sonst: Block hinten an System-Prompt

Logging-Tags:
  [PROSODY-190]  Injektor-Aufrufe (nur INFO, kein Inhalt!)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)
_LOG_TAG = "[PROSODY-190]"


def inject_prosody_context(system_prompt: str, prosody_result: Optional[dict]) -> str:
    """Fügt einen Prosodie-Hinweis hinter den System-Prompt.

    Args:
        system_prompt: Der bestehende System-Prompt.
        prosody_result: Output von `ProsodyManager.analyze()` oder None.

    Returns:
        System-Prompt mit angefügtem Block (wenn Confidence ausreicht),
        sonst unverändert.
    """
    if not prosody_result:
        return system_prompt

    if prosody_result.get("source") == "stub":
        return system_prompt

    try:
        confidence = float(prosody_result.get("confidence", 0))
    except (TypeError, ValueError):
        return system_prompt

    if confidence < 0.3:
        logger.debug(f"{_LOG_TAG} Confidence {confidence:.2f} < 0.3 — kein Block")
        return system_prompt

    mood = str(prosody_result.get("mood", "neutral"))
    tempo = str(prosody_result.get("tempo", "normal"))
    try:
        valence = float(prosody_result.get("valence", 0.5))
        arousal = float(prosody_result.get("arousal", 0.5))
    except (TypeError, ValueError):
        valence = 0.5
        arousal = 0.5

    block = (
        f"\n\n[Prosodie-Hinweis (Confidence: {confidence:.0%}): "
        f"Stimmung={mood}, Tempo={tempo}, "
        f"Valenz={valence:+.1f}, Arousal={arousal:.1f}]"
    )

    # Inkongruenz-Heuristik: Negative Valenz im Audio = Hinweis auf
    # verdeckte Ironie / Stress / Sarkasmus, auch wenn der Text-Sentiment
    # neutral oder positiv ist.
    if valence < -0.3:
        block += (
            "\n[Hinweis: Stimme klingt anders als Text vermuten lässt — "
            "mögliche Ironie oder verdeckter Stress]"
        )

    logger.info(f"{_LOG_TAG} mood={mood} tempo={tempo} confidence={confidence:.2f}")
    return system_prompt + block
