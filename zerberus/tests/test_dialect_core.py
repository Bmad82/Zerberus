"""Patch 165 — Tests fuer ``zerberus.core.dialect``.

Deckt die bisher untestete Kern-Logik der Dialekt-Weiche ab:
- Marker-Erkennung (5er-Pattern fuer Berlin/Schwaebisch/Emojis)
- ``apply_dialect`` mit Wortgrenzen-Matching (P103-Lesson: ``ich``
  darf nicht in ``nich`` matchen)
- Multi-Wort-Keys werden vor Einzel-Worten gematcht
- Graceful-Behavior bei fehlender ``dialect.json`` / unbekanntem Dialekt
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from zerberus.core import dialect as dialect_mod


@pytest.fixture
def tmp_dialect_file(tmp_path, monkeypatch):
    """Lenkt ``DIALECT_PATH`` auf eine tmp-JSON-Datei um."""
    target = tmp_path / "dialect.json"
    monkeypatch.setattr(dialect_mod, "DIALECT_PATH", target)
    return target


# ----- detect_dialect_marker ------------------------------------------------


class TestDetectDialectMarker:
    def test_berlin_marker_recognized(self):
        text = "🐻🐻🐻🐻🐻 wie geht's denn so"
        dialect, rest = dialect_mod.detect_dialect_marker(text)
        assert dialect == "berlin"
        assert rest == "wie geht's denn so"

    def test_schwaebisch_marker_recognized(self):
        text = "🥨🥨🥨🥨🥨 wo bisch denn"
        dialect, rest = dialect_mod.detect_dialect_marker(text)
        assert dialect == "schwaebisch"
        assert rest == "wo bisch denn"

    def test_emoji_marker_recognized(self):
        text = "✨✨✨✨✨ hallo"
        dialect, rest = dialect_mod.detect_dialect_marker(text)
        assert dialect == "emojis"
        assert rest == "hallo"

    def test_no_marker_returns_none(self):
        text = "ganz normaler Text ohne Marker"
        dialect, rest = dialect_mod.detect_dialect_marker(text)
        assert dialect is None
        assert rest == text

    def test_partial_marker_x4_does_not_trigger(self):
        # P103-Lesson: Marker-Laenge x5 — x2/x4 darf nicht greifen.
        text = "🐻🐻🐻🐻 hallo"
        dialect, rest = dialect_mod.detect_dialect_marker(text)
        assert dialect is None

    def test_marker_with_leading_whitespace(self):
        text = "   🐻🐻🐻🐻🐻 nach Spaces"
        dialect, rest = dialect_mod.detect_dialect_marker(text)
        assert dialect == "berlin"
        assert rest == "nach Spaces"

    def test_marker_in_middle_does_not_trigger(self):
        text = "Hi 🐻🐻🐻🐻🐻 ja"
        dialect, rest = dialect_mod.detect_dialect_marker(text)
        assert dialect is None


# ----- apply_dialect (Wortgrenzen) ------------------------------------------


class TestApplyDialectFlat:
    def test_word_boundary_protects_substring(self, tmp_dialect_file):
        # P103-Lesson: 'ich' darf nicht 'nich' kapern.
        tmp_dialect_file.write_text(
            json.dumps({"berlin": {"ich": "icke"}}),
            encoding="utf-8",
        )
        result = dialect_mod.apply_dialect("ich kann nich", "berlin")
        assert result == "icke kann nich"

    def test_multi_word_key_replaced_first(self, tmp_dialect_file):
        # Laengere Keys zuerst — 'haben wir' muss vor 'wir' greifen.
        tmp_dialect_file.write_text(
            json.dumps({
                "berlin": {
                    "wir": "wa",
                    "haben wir": "hamm wa",
                }
            }),
            encoding="utf-8",
        )
        result = dialect_mod.apply_dialect("das haben wir gemacht", "berlin")
        assert result == "das hamm wa gemacht"

    def test_unknown_dialect_returns_original(self, tmp_dialect_file):
        tmp_dialect_file.write_text(
            json.dumps({"berlin": {"ich": "icke"}}),
            encoding="utf-8",
        )
        result = dialect_mod.apply_dialect("ich bin", "saxon")
        assert result == "ich bin"

    def test_missing_file_returns_original(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dialect_mod, "DIALECT_PATH", tmp_path / "does-not-exist.json"
        )
        result = dialect_mod.apply_dialect("ich bin", "berlin")
        assert result == "ich bin"

    def test_empty_dialect_data_returns_original(self, tmp_dialect_file):
        tmp_dialect_file.write_text(json.dumps({"berlin": {}}), encoding="utf-8")
        result = dialect_mod.apply_dialect("ich bin", "berlin")
        assert result == "ich bin"

    def test_umlaut_key_preserves_word_boundary(self, tmp_dialect_file):
        tmp_dialect_file.write_text(
            json.dumps({"schwaebisch": {"über": "iber"}}),
            encoding="utf-8",
        )
        # 'überall' soll NICHT zu 'iberall' werden, 'über' alleine schon.
        result = dialect_mod.apply_dialect("über alle, überall", "schwaebisch")
        assert result == "iber alle, überall"


class TestApplyDialectLegacyPatterns:
    def test_legacy_patterns_apply(self, tmp_dialect_file):
        tmp_dialect_file.write_text(
            json.dumps({
                "berlin": {
                    "patterns": [
                        {"trigger": "Hallo", "response": "Tach"},
                    ]
                }
            }),
            encoding="utf-8",
        )
        result = dialect_mod.apply_dialect("Hallo Welt", "berlin")
        assert result == "Tach Welt"

    def test_legacy_pattern_without_trigger_skipped(self, tmp_dialect_file):
        tmp_dialect_file.write_text(
            json.dumps({
                "berlin": {
                    "patterns": [
                        {"trigger": "", "response": "X"},
                    ]
                }
            }),
            encoding="utf-8",
        )
        result = dialect_mod.apply_dialect("Hallo Welt", "berlin")
        assert result == "Hallo Welt"


# ----- load_dialects --------------------------------------------------------


def test_load_dialects_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dialect_mod, "DIALECT_PATH", tmp_path / "nope.json")
    assert dialect_mod.load_dialects() == {}


def test_load_dialects_returns_parsed_json(tmp_dialect_file):
    payload = {"berlin": {"ich": "icke"}, "schwaebisch": {"ist": "isch"}}
    tmp_dialect_file.write_text(json.dumps(payload), encoding="utf-8")
    assert dialect_mod.load_dialects() == payload
