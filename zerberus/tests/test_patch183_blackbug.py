"""Patch 183 - Black-Bug Forensik (vierter Anlauf).

Vorgaenger-Patches P109/P153/P169 hatten unvollstaendige BLACK_VALUES-Listen
(nur exakte String-Matches fuer 4 Formate). Plus: bubblePreview / bubbleTextPreview
/ applyHsl / loadFav setzten Bubble-CSS-Vars KOMPLETT OHNE Guard.

Fix:
- regex-basiertes _isBubbleBlack() (HSL-Format, RGB-ohne-Spaces, RGBA-Float-Alpha)
- zentraler sanitizeBubbleColor() durch JEDE setProperty('--bubble-*')-Stelle
- cleanBlackFromStorage() Sweep beim Pre-Render UND in showChatScreen
- Favoriten-Sweep auch fuer userText/llmText, nicht nur BG

Tests sind ueberwiegend Source-Match-Tests, weil das JS nicht im pytest-Prozess
laeuft. Plus ein Python-Replikat der _isBubbleBlack-Logik, das sicherstellt,
dass die Erkennung auch Edge-Cases trifft.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


NALA_PATH = Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"


@pytest.fixture(scope="module")
def nala_src() -> str:
    return NALA_PATH.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────
#  Python-Replikat der _isBubbleBlack-Logik
#  (1:1 zur JS-Variante, damit wir die Detection-Regeln in Python testen)
# ──────────────────────────────────────────────────────────────────────


def _py_is_bubble_black(v):
    """Python-Spiegel der JS-Funktion _isBubbleBlack aus nala.py.

    Wenn diese Spiegel-Funktion und das JS-Original auseinanderlaufen, ist
    EINER der beiden falsch — die Tests fangen das, weil ein zweiter Source-
    Test sicherstellt dass beide Listen synchron sind.
    """
    if v is None:
        return True
    s = str(v).strip().lower()
    s = re.sub(r"\s+", "", s)
    if s in ("", "transparent", "none"):
        return True
    if s in ("#000", "#000f", "#000000", "#000000ff"):
        return True
    if re.match(r"^#0{6}[1-9a-f][0-9a-f]$", s):
        return True
    if re.match(r"^#0{3}[1-9a-f]$", s):
        return True
    if re.match(r"^rgb\(0,0,0\)$", s):
        return True
    if re.match(r"^rgba\(0,0,0,(?:0?\.0*[1-9]\d*|1(?:\.0+)?)\)$", s):
        return True
    if re.match(
        r"^hsla?\(\d+(?:\.\d+)?,\d+(?:\.\d+)?%?,0(?:\.0+)?%(?:,[\d.]+)?\)$", s
    ):
        return True
    m = re.match(r"^hsla?\([^,]+,[^,]+,([\d.]+)%", s)
    if m and float(m.group(1)) < 5:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────
#  Block 1 - sanitizeBubbleColor blockt schwarze Varianten
# ──────────────────────────────────────────────────────────────────────


class TestSanitizeBubbleColorBlocksBlack:
    """Alle bekannten Schwarz-Varianten muessen erkannt und durch Fallback
    ersetzt werden — das ist der Kern des P183-Fixes."""

    BLACK_INPUTS = [
        # Hex
        "#000",
        "#000000",
        "#000000ff",
        "#000F",
        "#000f",
        # RGB
        "rgb(0,0,0)",
        "rgb(0, 0, 0)",
        "RGB(0, 0, 0)",
        # RGBA mit alpha != 0
        "rgba(0,0,0,1)",
        "rgba(0,0,0,1.0)",
        "rgba(0, 0, 0, 1)",
        "rgba(0, 0, 0, 0.5)",
        # HSL mit L=0
        "hsl(0,0%,0%)",
        "hsl(0, 0%, 0%)",
        "hsl(220,40%,0%)",
        "hsla(0, 0%, 0%, 1)",
        # HSL mit L<5%
        "hsl(0,0%,4%)",
        "hsl(220, 40%, 1%)",
        "hsl(0, 0%, 0.5%)",
        # leer/null/transparent
        "",
        None,
        "transparent",
        "  ",
    ]

    @pytest.mark.parametrize("value", BLACK_INPUTS)
    def test_python_mirror_blocks(self, value):
        assert _py_is_bubble_black(value) is True, (
            f"Python-Mirror sollte {value!r} als schwarz erkennen"
        )


class TestSanitizeBubbleColorPassesValid:
    """Gueltige (nicht-schwarze) Bubble-Farben muessen durchgereicht werden,
    sonst frisst der Sanitizer normale Themes auf."""

    VALID_INPUTS = [
        "#ec407a",
        "#1a2f4e",
        "#abcdef",
        "rgb(236,64,122)",
        "rgb(26, 47, 78)",
        "rgba(236, 64, 122, 0.88)",
        "rgba(26, 47, 78, 0.85)",
        # HSL >= 5% Lightness
        "hsl(0, 0%, 5%)",
        "hsl(220, 40%, 28%)",
        "hsl(45, 80%, 55%)",
        "hsla(220, 40%, 28%, 0.85)",
    ]

    @pytest.mark.parametrize("value", VALID_INPUTS)
    def test_python_mirror_passes(self, value):
        assert _py_is_bubble_black(value) is False, (
            f"Python-Mirror sollte {value!r} durchlassen"
        )


# ──────────────────────────────────────────────────────────────────────
#  Block 2 - JS-Source enthaelt die zentralen Funktionen
# ──────────────────────────────────────────────────────────────────────


class TestSanitizerExistsInSource:
    def test_is_bubble_black_function_defined(self, nala_src):
        assert "function _isBubbleBlack(" in nala_src, (
            "P183 Kern: _isBubbleBlack-Funktion fehlt"
        )

    def test_sanitize_bubble_color_function_defined(self, nala_src):
        assert "function sanitizeBubbleColor(" in nala_src, (
            "P183 Kern: sanitizeBubbleColor-Funktion fehlt"
        )

    def test_clean_black_from_storage_defined(self, nala_src):
        assert "function cleanBlackFromStorage(" in nala_src, (
            "P183 Block 3: cleanBlackFromStorage-Sweep fehlt"
        )

    def test_warning_log_tag_present(self, nala_src):
        # Mindestens drei [BLACK-183] Warnings — Pre-Render-Sweep, IIFE-Guard,
        # Sanitizer-Trigger. Erleichtert Diagnose im Browser-Konsolen-Log.
        assert nala_src.count("[BLACK-183]") >= 3

    def test_python_mirror_lists_match_js_source(self, nala_src):
        """Stellt sicher, dass die JS-Regex-Patterns die wir hier in Python
        spiegeln auch im echten JS-Source vorhanden sind. Schuetzt vor
        Drift zwischen Test und Code."""
        # Hex-schwarz Set — explizite Strings in JS muessen drin sein
        assert "'#000'" in nala_src and "'#000000'" in nala_src
        assert "'transparent'" in nala_src
        # HSL L=0 Regex-Marker (Teil-String reicht)
        assert ",0(?:\\.0+)?%" in nala_src or ",0(\\.0+)?%" in nala_src
        # HSL L<5 Fallback-Branch
        assert "parseFloat(hslMatch[1]) < 5" in nala_src


# ──────────────────────────────────────────────────────────────────────
#  Block 3 - Source-Audit: Jede setProperty('--bubble-*) lauft durch Sanitizer
#                          oder durch _isBubbleBlack-Filter
# ──────────────────────────────────────────────────────────────────────


class TestEverySetPropertyHasGuard:
    """Block 4 Punkt 5 aus dem Patch-Spec: An JEDER setProperty('--bubble-*')-
    Stelle MUSS sanitizeBubbleColor() oder _isBubbleBlack() im umliegenden
    Funktionsbereich auftauchen.

    Der Test isst die Funktionen einzeln aus, weil applyAutoContrast und der
    IIFE-Guard verschiedene Strategien nutzen (sanitize vs. isBlack-Skip).
    """

    BUBBLE_SETTERS = [
        # (Funktionsname / Marker, erwartetes Guard-Symbol)
        ("function bubblePreview()", "sanitizeBubbleColor"),
        ("function bubbleTextPreview(", "sanitizeBubbleColor"),
        ("function applyHsl(", "sanitizeBubbleColor"),
        ("function applyAutoContrast()", "sanitizeBubbleColor"),
        # loadFav nutzt _isBubbleBlack() im bMap-Loop (Skip-Strategie)
        ("function loadFav(", "_isBubbleBlack"),
    ]

    @pytest.mark.parametrize("marker,guard", BUBBLE_SETTERS)
    def test_setter_uses_guard(self, nala_src, marker, guard):
        idx = nala_src.find(marker)
        assert idx > 0, f"Marker {marker!r} nicht in nala.py gefunden"
        # Suche das Funktions-Ende ueber die naechste 'function ' auf der
        # gleichen Einrueck-Ebene oder einen begrenzten Lookahead.
        end = nala_src.find("\n    function ", idx + len(marker))
        if end < 0:
            end = idx + 4000
        body = nala_src[idx:end]
        # Die Funktion muss ein setProperty machen (egal ob literal '--bubble-*'
        # oder variabel ueber cfg.css / cssVar) UND den Guard im Body haben.
        assert "setProperty(" in body, (
            f"{marker} hat keinen setProperty-Aufruf — Test-Marker stale?"
        )
        assert guard in body, (
            f"{marker} setzt CSS-Var OHNE {guard} — P183-Anti-Invariante verletzt"
        )

    def test_iife_uses_is_bubble_black(self, nala_src):
        """Die IIFE im <head> setzt Bubble-Vars aus localStorage. Sie muss
        _isBubbleBlack() im Body haben (Pre-IIFE-Sweep allein reicht nicht
        als defense-in-depth)."""
        # IIFE-Anfang: nach dem cleanBlackFromStorage()-Aufruf
        idx = nala_src.find("cleanBlackFromStorage();\n        (function()")
        assert idx > 0, "P183: Pre-IIFE-Sweep fehlt"
        end = nala_src.find("</script>", idx)
        assert end > idx
        iife_body = nala_src[idx:end]
        assert "_isBubbleBlack" in iife_body, (
            "P183: IIFE muss _isBubbleBlack als defense-in-depth haben"
        )

    def test_no_legacy_black_string_lists(self, nala_src):
        """Die alten BLACK_VALUES / FAV_BLACK Listen mit nur 4 Strings haben
        den Bug verursacht. Sie duerfen nicht mehr als Funktion stehen.

        Erlaubt: Erwaehnung in Kommentaren oder Tests-Dokumentation.
        Verboten: var FAV_BLACK = [...] / var BLACK_VALUES = [...]"""
        # Wir matchen die alte Struktur als Anti-Pattern
        assert "var FAV_BLACK = ['#000000', '#000', 'rgb(0, 0, 0)', 'rgba(0,0,0,1)']" not in nala_src, (
            "P183: alte FAV_BLACK-Liste muss durch _isBubbleBlack ersetzt sein"
        )
        assert "var BLACK_VALUES = ['#000000', '#000', 'rgb(0, 0, 0)', 'rgba(0,0,0,1)']" not in nala_src, (
            "P183: alte BLACK_VALUES-Liste muss durch _isBubbleBlack ersetzt sein"
        )


# ──────────────────────────────────────────────────────────────────────
#  Block 4 - Login + Favoriten Sweep-Hooks
# ──────────────────────────────────────────────────────────────────────


class TestSweepHooks:
    def test_show_chat_screen_calls_sweep(self, nala_src):
        """showChatScreen() ist die Login-Aftermath-Funktion — sie muss
        cleanBlackFromStorage() rufen, damit zwischen IIFE und Login
        eingeschleuste schwarze Werte nicht durchrutschen."""
        idx = nala_src.find("function showChatScreen()")
        assert idx > 0
        end = nala_src.find("\n    function ", idx + 30)
        body = nala_src[idx:end]
        assert "cleanBlackFromStorage" in body, (
            "P183 Block 3: showChatScreen muss cleanBlackFromStorage rufen"
        )

    def test_pre_iife_sweep_runs(self, nala_src):
        """Ganz frueh im <head>-Script muss cleanBlackFromStorage() laufen,
        VOR der IIFE. Sonst kommt der Bug bei IIFE-Read durch."""
        # Reihenfolge: Definition von cleanBlackFromStorage, dann der Aufruf,
        # dann die IIFE.
        defn_idx = nala_src.find("function cleanBlackFromStorage(")
        call_idx = nala_src.find("cleanBlackFromStorage();")
        iife_idx = nala_src.find("(function() {\n            var r = document.documentElement.style;\n            var favApplied")
        assert defn_idx > 0 and call_idx > 0 and iife_idx > 0
        assert defn_idx < call_idx < iife_idx, (
            f"P183 Reihenfolge falsch: defn={defn_idx} call={call_idx} iife={iife_idx}"
        )

    def test_favorites_sweep_covers_text_fields(self, nala_src):
        """P169 hat nur userBg/llmBg im Favoriten gesweeped. P183 macht das
        fuer alle vier Felder, weil schwarzer Text auf dunklem Bubble auch
        unlesbar ist (Auto-Kontrast greift nicht wenn _manual gesetzt ist)."""
        idx = nala_src.find("function cleanBlackFromStorage(")
        end = nala_src.find("\n        }\n", idx + 200)
        if end < 0:
            end = idx + 2000
        body = nala_src[idx:end]
        for field in ("userBg", "llmBg", "userText", "llmText"):
            assert f"'{field}'" in body, (
                f"P183: cleanBlackFromStorage muss {field} aus Favoriten saeubern"
            )
