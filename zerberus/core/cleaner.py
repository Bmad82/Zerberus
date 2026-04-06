"""
Whisper-Cleaner: Füllwörter, Korrekturen.
"""
import json
import re
import logging
from pathlib import Path
from difflib import get_close_matches

logger = logging.getLogger(__name__)

CLEANER_PATH = Path("whisper_cleaner.json")

def load_cleaner_config():
    if CLEANER_PATH.exists():
        with open(CLEANER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def clean_transcript(text: str) -> str:
    """Wendet alle Cleaner-Regeln an."""
    if not text:
        return text
    config = load_cleaner_config()
    # Unterstützt zwei Formate: Liste von {"pattern": "...", "replacement": "..."}
    # oder dict mit "corrections", "fillers", etc.
    if isinstance(config, list):
        for rule in config:
            if "pattern" not in rule:
                continue
            pattern = rule["pattern"]
            replacement = rule["replacement"]
            replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)
            text = re.sub(pattern, replacement, text)
    elif isinstance(config, dict):
        corrections = config.get("corrections", [])
        for corr in corrections:
            old = corr.get("old", "")
            new = corr.get("new", "")
            if old:
                text = text.replace(old, new)
        fillers = config.get("fillers", [])
        if fillers:
            pattern = r'(' + '|'.join(map(re.escape, fillers)) + r')'
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
            text = re.sub(r'\s+', ' ', text).strip()
        max_repeat = config.get("max_repetitions", 3)
        if max_repeat > 0:
            words = text.split()
            new_words = []
            i = 0
            while i < len(words):
                word = words[i]
                count = 1
                while i + count < len(words) and words[i + count] == word:
                    count += 1
                new_words.extend([word] * min(count, max_repeat))
                i += count
            text = ' '.join(new_words)
    text = fuzzy_correct(text)
    return text.strip()


def fuzzy_correct(text: str) -> str:
    """Korrigiert Whisper-Fehler via Fuzzy-Matching gegen Projekt-Dictionary."""
    dict_path = Path("fuzzy_dictionary.json")
    if not dict_path.exists():
        return text
    try:
        with open(dict_path, "r", encoding="utf-8") as f:
            fuzz_cfg = json.load(f)
    except Exception:
        return text

    terms = fuzz_cfg.get("terms", [])
    cutoff = fuzz_cfg.get("cutoff", 0.82)
    min_len = fuzz_cfg.get("min_word_length", 4)

    if not terms:
        return text

    words = text.split()
    result = []
    for word in words:
        # Kurze Wörter und Wörter mit Sonderzeichen überspringen
        core = word.strip(".,!?;:\"'")
        if len(core) < min_len:
            result.append(word)
            continue
        matches = get_close_matches(core, terms, n=1, cutoff=cutoff)
        if matches and matches[0] != core:
            logger.info(f"🔤 Fuzzy-Korrektur: '{core}' → '{matches[0]}'")
            result.append(word.replace(core, matches[0]))
        else:
            result.append(word)
    return " ".join(result)
