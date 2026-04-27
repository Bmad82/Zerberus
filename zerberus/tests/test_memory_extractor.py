"""Unit-Tests für Patch 115: Background Memory Extraction.
Laufen ohne echten LLM-Call und ohne FAISS-Setup — alle externen Aufrufe werden gemockt.
"""
from __future__ import annotations

import asyncio
import types

import numpy as np
import pytest

from zerberus.modules.memory import extractor as mem


def test_parse_facts_valid_json():
    raw = '[{"fact": "Chris mag Tee", "category": "preference"}, {"fact": "Jojo lebt in Ulm", "category": "personal"}]'
    facts = mem._parse_facts(raw)
    assert len(facts) == 2
    assert facts[0]["fact"] == "Chris mag Tee"
    assert facts[0]["category"] == "preference"
    assert facts[1]["category"] == "personal"


def test_parse_facts_with_code_fence():
    raw = "Hier sind die Fakten:\n```json\n[{\"fact\": \"X\", \"category\": \"event\"}]\n```"
    facts = mem._parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["category"] == "event"


def test_parse_facts_unknown_category_fallback():
    raw = '[{"fact": "X", "category": "garbage"}]'
    facts = mem._parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["category"] == "personal"


def test_parse_facts_empty_and_malformed():
    assert mem._parse_facts("") == []
    assert mem._parse_facts("kein json hier") == []
    assert mem._parse_facts("{\"not\": \"array\"}") == []
    assert mem._parse_facts("[{\"nofact\": true}]") == []


def test_parse_facts_missing_fact_field():
    raw = '[{"fact": "", "category": "event"}, {"fact": "echt", "category": "event"}]'
    facts = mem._parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["fact"] == "echt"


def test_batch_messages_single_batch():
    rows = [("2026-04-23 10:00:00", "Hallo Welt"), ("2026-04-23 10:01:00", "zweite Nachricht")]
    batches = mem._batch_messages(rows, max_words=1000)
    assert len(batches) == 1
    assert "Hallo Welt" in batches[0]
    assert "zweite Nachricht" in batches[0]


def test_batch_messages_splits_when_exceeds_limit():
    long = " ".join(["wort"] * 500)
    rows = [
        ("2026-04-23 10:00:00", long),
        ("2026-04-23 10:01:00", long),
        ("2026-04-23 10:02:00", long),
    ]
    batches = mem._batch_messages(rows, max_words=600)
    assert len(batches) >= 2


def test_batch_messages_empty_content_skipped():
    rows = [("2026-04-23 10:00:00", ""), ("2026-04-23 10:00:01", None), ("2026-04-23 10:00:02", "echt")]
    batches = mem._batch_messages(rows, max_words=1000)
    assert len(batches) == 1
    assert "echt" in batches[0]


def test_batch_messages_empty_rows():
    assert mem._batch_messages([], max_words=1000) == []


def test_is_duplicate_no_index(monkeypatch):
    """Wenn der Index leer ist, gibt _is_duplicate False zurück."""
    fake_router = types.SimpleNamespace(_index=None)
    monkeypatch.setitem(mem.__dict__, "_is_duplicate_test", True)
    import sys
    # Patch 169 (Test-Isolation): via monkeypatch.setitem statt direkter
    # Zuweisung — sonst bleibt SimpleNamespace nach Test in sys.modules und
    # spaetere Tests (z. B. test_patch169_bugsweep) brechen mit
    # "cannot import name '_ensure_init' from '<unknown module name>'".
    monkeypatch.setitem(sys.modules, "zerberus.modules.rag.router", fake_router)
    vec = np.zeros((1, 384), dtype="float32")
    assert mem._is_duplicate(vec, 0.9) is False


def test_is_duplicate_below_threshold(monkeypatch):
    """Ein Treffer mit L2=1.0 (cos=0.5) unter threshold=0.9 → nicht Duplikat."""
    import sys

    class FakeIndex:
        ntotal = 5
        def search(self, vec, k):
            return np.array([[1.0]]), np.array([[0]])

    fake_router = types.SimpleNamespace(_index=FakeIndex())
    # Patch 169 (Test-Isolation): via monkeypatch.setitem statt direkter
    # Zuweisung — sonst bleibt SimpleNamespace nach Test in sys.modules und
    # spaetere Tests (z. B. test_patch169_bugsweep) brechen mit
    # "cannot import name '_ensure_init' from '<unknown module name>'".
    monkeypatch.setitem(sys.modules, "zerberus.modules.rag.router", fake_router)
    vec = np.zeros((1, 384), dtype="float32")
    assert mem._is_duplicate(vec, 0.9) is False


def test_is_duplicate_above_threshold(monkeypatch):
    """Ein Treffer mit L2=0.2 (cos=0.98) über threshold=0.9 → Duplikat."""
    import sys

    class FakeIndex:
        ntotal = 5
        def search(self, vec, k):
            return np.array([[0.2]]), np.array([[0]])

    fake_router = types.SimpleNamespace(_index=FakeIndex())
    # Patch 169 (Test-Isolation): via monkeypatch.setitem statt direkter
    # Zuweisung — sonst bleibt SimpleNamespace nach Test in sys.modules und
    # spaetere Tests (z. B. test_patch169_bugsweep) brechen mit
    # "cannot import name '_ensure_init' from '<unknown module name>'".
    monkeypatch.setitem(sys.modules, "zerberus.modules.rag.router", fake_router)
    vec = np.zeros((1, 384), dtype="float32")
    assert mem._is_duplicate(vec, 0.9) is True


def test_call_extraction_llm_no_api_key(monkeypatch):
    """Ohne OPENROUTER_API_KEY → leeres Fakten-Array."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = asyncio.run(mem._call_extraction_llm("irgendein Text", {}))
    assert result == []


def test_extract_memories_disabled():
    """extraction_enabled=false → früher Exit mit error-Flag."""
    result = asyncio.run(mem.extract_memories({"extraction_enabled": False}))
    assert result["extracted"] == 0
    assert result["indexed"] == 0
    assert "disabled" in result["errors"]
