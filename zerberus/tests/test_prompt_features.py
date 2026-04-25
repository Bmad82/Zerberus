"""Patch 165 — Tests fuer ``zerberus.core.prompt_features``.

Deckt den Decision-Box-Hint-Injector ab (P118a). Wichtig: das Feature
ist Opt-In via ``settings.features.decision_boxes`` — der Hint darf
NICHT bei jedem Prompt erscheinen, weil das den System-Prompt sonst
mit Marker-Anweisungen flutet.
"""
from __future__ import annotations

from types import SimpleNamespace

from zerberus.core.prompt_features import (
    DECISION_BOX_HINT,
    append_decision_box_hint,
)


def _settings(features):
    return SimpleNamespace(features=features)


class TestAppendDecisionBoxHint:
    def test_appends_when_feature_enabled(self):
        result = append_decision_box_hint("Original", _settings({"decision_boxes": True}))
        assert result.startswith("Original")
        assert "[DECISION]" in result
        assert "[OPTION:wert1]" in result

    def test_no_append_when_feature_disabled(self):
        result = append_decision_box_hint("Original", _settings({"decision_boxes": False}))
        assert result == "Original"

    def test_no_append_when_features_missing(self):
        # settings ohne ``features``-Attribut → kein Crash, kein Append.
        result = append_decision_box_hint("Original", SimpleNamespace())
        assert result == "Original"

    def test_no_append_when_features_none(self):
        result = append_decision_box_hint("Original", _settings(None))
        assert result == "Original"

    def test_empty_prompt_returns_empty(self):
        result = append_decision_box_hint("", _settings({"decision_boxes": True}))
        assert result == ""

    def test_no_double_injection(self):
        already = "Some prompt\n[DECISION]\n[OPTION:x] foo\n[/DECISION]"
        result = append_decision_box_hint(already, _settings({"decision_boxes": True}))
        assert result == already

    def test_silent_fail_on_attribute_error(self):
        # Settings ist ``object()`` — getattr-Default greift.
        result = append_decision_box_hint("Original", object())
        assert result == "Original"

    def test_hint_constant_contains_required_markers(self):
        # Sanity: die Konstante enthaelt das Marker-Vokabular,
        # auf das das Frontend (P118a) parst.
        assert "[DECISION]" in DECISION_BOX_HINT
        assert "[/DECISION]" in DECISION_BOX_HINT
        assert "[OPTION:" in DECISION_BOX_HINT
