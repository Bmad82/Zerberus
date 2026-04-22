"""
Whisper-Cleaner: Füllwörter, Korrekturen.
Patch 102 (B-01): Phrasen-Repetition-Filter ergänzt.
"""
import json
import re
import logging
from pathlib import Path
from difflib import get_close_matches

logger = logging.getLogger(__name__)

CLEANER_PATH = Path("whisper_cleaner.json")

# Patch 102 (B-01): Defaults für Phrasen-Repetition-Filter.
# Können via config.yaml → whisper_cleaner.repetition_filter überschrieben werden,
# fallen sonst auf diese Konstanten zurück (Fail-Safe wenn settings nicht erreichbar).
PHRASE_REP_ENABLED_DEFAULT = True
PHRASE_REP_MIN_LEN = 2
PHRASE_REP_MAX_LEN = 6
PHRASE_REP_MAX_REPEATS = 2

def load_cleaner_config():
    if CLEANER_PATH.exists():
        with open(CLEANER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_repetition_filter_settings():
    """Liest repetition_filter aus config.yaml; Fallback auf Konstanten."""
    try:
        from zerberus.core.config import get_settings
        wc = getattr(get_settings(), "whisper_cleaner", None)
        cfg = getattr(wc, "repetition_filter", None) if wc else None
        if cfg is None:
            return PHRASE_REP_ENABLED_DEFAULT, PHRASE_REP_MIN_LEN, PHRASE_REP_MAX_LEN, PHRASE_REP_MAX_REPEATS
        # cfg kann ein dict (extra=allow) oder ein Modell sein
        if isinstance(cfg, dict):
            return (
                cfg.get("enabled", PHRASE_REP_ENABLED_DEFAULT),
                cfg.get("min_phrase_len", PHRASE_REP_MIN_LEN),
                cfg.get("max_phrase_len", PHRASE_REP_MAX_LEN),
                cfg.get("max_repeats", PHRASE_REP_MAX_REPEATS),
            )
        return (
            getattr(cfg, "enabled", PHRASE_REP_ENABLED_DEFAULT),
            getattr(cfg, "min_phrase_len", PHRASE_REP_MIN_LEN),
            getattr(cfg, "max_phrase_len", PHRASE_REP_MAX_LEN),
            getattr(cfg, "max_repeats", PHRASE_REP_MAX_REPEATS),
        )
    except Exception:
        return PHRASE_REP_ENABLED_DEFAULT, PHRASE_REP_MIN_LEN, PHRASE_REP_MAX_LEN, PHRASE_REP_MAX_REPEATS


def detect_phrase_repetition(text: str,
                              min_phrase_len: int = PHRASE_REP_MIN_LEN,
                              max_phrase_len: int = PHRASE_REP_MAX_LEN,
                              max_repeats: int = PHRASE_REP_MAX_REPEATS) -> str:
    """
    Patch 102 (B-01): Erkennt wiederholte Phrasen (N-Gramme) und kürzt auf eine Wiederholung.
    Beispiel: 'ein bisschen so ein bisschen so ein bisschen so' → 'ein bisschen so'.
    Längere Phrasen werden zuerst geprüft, damit das größtmögliche Pattern greift.
    """
    if not text:
        return text
    words = text.split()
    if len(words) < min_phrase_len * max_repeats:
        return text
    upper = min(max_phrase_len, len(words) // max_repeats)
    for phrase_len in range(upper, min_phrase_len - 1, -1):
        i = 0
        result = []
        changed = False
        while i < len(words):
            phrase = words[i:i + phrase_len]
            if len(phrase) < phrase_len:
                result.extend(words[i:])
                break
            count = 1
            j = i + phrase_len
            while j + phrase_len <= len(words) and words[j:j + phrase_len] == phrase:
                count += 1
                j += phrase_len
            if count >= max_repeats:
                logger.warning(f"[WHISPER-REP-102] Phrasen-Repetition: '{' '.join(phrase)}' x{count} → 1x")
                result.extend(phrase)
                i = j
                changed = True
            else:
                result.append(words[i])
                i += 1
        if changed:
            words = result
    return ' '.join(words)

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
    # Patch 102 (B-01): Phrasen-Repetition-Filter NACH Regex-Replacements, VOR fuzzy_correct.
    # Läuft für BEIDE Config-Formate (Liste + dict), weil Whisper-Endlosschleifen wie
    # "ein bisschen so ein bisschen so ..." vom Wort-Level-max_repeat nicht gefasst werden.
    enabled, min_len, max_len, max_reps = _get_repetition_filter_settings()
    if enabled:
        text = detect_phrase_repetition(text, min_len, max_len, max_reps)
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
