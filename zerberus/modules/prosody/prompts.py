"""
Patch 189 — Prompts für Gemma 4 E2B Audio-Analyse.

Der Prompt ist bewusst auf JSON-only-Output getunt, weil llama.cpp
manchmal Markdown-Wrapper produziert. `_parse_gemma_output()` im Client
muss damit klarkommen — siehe gemma_client.py.
"""

PROSODY_ANALYSIS_PROMPT = """Analyze the emotional prosody of this audio clip.
Respond ONLY with a JSON object, no other text:
{
  "mood": "<one of: neutral, happy, sad, angry, stressed, excited, tired, anxious, sarcastic, calm>",
  "tempo": "<one of: slow, normal, fast, rushed, hesitant>",
  "confidence": <0.0-1.0 how confident you are>,
  "valence": <-1.0 to 1.0, negative=unpleasant, positive=pleasant>,
  "arousal": <0.0 to 1.0, low=calm, high=excited/agitated>,
  "dominance": <0.0 to 1.0, low=submissive, high=dominant>
}
Focus on HOW it sounds (pitch, speed, energy, pauses), not WHAT is said."""
