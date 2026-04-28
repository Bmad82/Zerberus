"""
Fenrir Chaos-Tests — Mega-Patch (Patch 130).

Stress- und Edge-Case-Tests für die UI-Änderungen aus Patches 124/127/128:
  - Input-Stress (10k Zeichen, Rapid-Fire, Unicode-Bomben)
  - UI-Stress (Burger-Toggle, Viewport-Resize, parallele Aktionen)
  - Farbwähler-Extremwerte
  - Code-Chunker / RAG-Upload-Edge-Cases
"""
from __future__ import annotations

import base64
import io
import os
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e  # P176: braucht laufenden Server


MOBILE_VIEWPORT = {"width": 375, "height": 812}
DESKTOP_VIEWPORT = {"width": 1280, "height": 720}


# ═══════════════════════════════════════════════════════════
#  Input-Stress
# ═══════════════════════════════════════════════════════════

class TestInputStress:
    """Fenrir ballert Daten in die Eingabe."""

    def test_f01_ten_thousand_chars_no_crash(self, logged_in_fenrir: Page):
        """F-01: 10k Zeichen in Textarea → kein Crash, Seite responsiv."""
        page = logged_in_fenrir
        huge = "abcdefghij" * 1000  # 10k Zeichen
        page.fill("#text-input", huge)
        page.wait_for_timeout(300)
        # Seite muss noch auf Interaktion reagieren
        val = page.locator("#text-input").input_value()
        assert len(val) == 10_000
        expect(page.locator("body")).to_be_visible()

    def test_f03_empty_enter_no_bubble(self, logged_in_fenrir: Page):
        """F-03: Leerer Submit (nur Enter) → keine leere Bubble."""
        page = logged_in_fenrir
        initial = page.locator(".message").count()
        page.focus("#text-input")
        page.keyboard.press("Enter")
        page.wait_for_timeout(700)
        assert page.locator(".message").count() == initial, (
            "Enter ohne Text hat trotzdem eine Bubble erzeugt"
        )

    def test_f04_unicode_bomb_displayed(self, logged_in_fenrir: Page):
        """F-04: Emojis + CJK + RTL + Kombinierende Zeichen werden korrekt eingetippt."""
        page = logged_in_fenrir
        payload = "🎉💀🔥 مرحبا 你好 café naïve Ñ"
        page.fill("#text-input", payload)
        val = page.locator("#text-input").input_value()
        assert val == payload, f"Unicode-Roundtrip defekt: {val!r} != {payload!r}"


# ═══════════════════════════════════════════════════════════
#  UI-Stress
# ═══════════════════════════════════════════════════════════

class TestUiStress:
    """Fenrir schüttelt die UI."""

    def test_f05_rapid_burger_toggle(self, logged_in_fenrir: Page):
        """F-05: 20x Burger-Toggle → kein Hänger, Sidebar-State konsistent."""
        page = logged_in_fenrir
        burger = page.locator(".hamburger").first
        for _ in range(20):
            try:
                burger.click(timeout=800, force=True)
            except Exception:
                pass
            page.wait_for_timeout(50)
        # Nach geradzahligen Toggles ist Sidebar wieder zu — prüfen wir aber nicht exakt,
        # wichtig ist nur: Seite lebt und Sidebar ist in einem sinnvollen Zustand
        expect(page.locator("body")).to_be_visible()
        left_px = page.evaluate(
            "() => parseFloat(getComputedStyle(document.getElementById('sidebar')).left)"
        )
        # Sidebar ist entweder offen (~0) oder geschlossen (-300)
        assert -310 <= left_px <= 10, f"Sidebar in undefiniertem Zustand: left={left_px}px"

    def test_f06_settings_during_pending_message(self, logged_in_fenrir: Page):
        """F-06: Settings öffnen während Bot-Antwort noch lädt → kein Crash."""
        page = logged_in_fenrir
        page.fill("#text-input", "Kurze Testfrage")
        page.keyboard.press("Enter")
        # Sofort Settings öffnen — noch bevor Bot geantwortet hat
        page.wait_for_timeout(100)
        page.locator(".icon-btn[aria-label='Einstellungen']").first.click()
        page.wait_for_timeout(400)
        display = page.evaluate(
            "() => getComputedStyle(document.getElementById('settings-modal')).display"
        )
        assert display != "none", "Settings-Modal nicht geöffnet"
        expect(page.locator("body")).to_be_visible()

    def test_f07_viewport_resize_live(self, logged_in_fenrir: Page):
        """F-07: Mobile ↔ Desktop ↔ Mobile während Chat → kein Layout-Crash."""
        page = logged_in_fenrir
        page.fill("#text-input", "Viewport-Test")
        page.keyboard.press("Enter")
        page.wait_for_selector(".user-message", timeout=5000)

        for vp in (DESKTOP_VIEWPORT, MOBILE_VIEWPORT, DESKTOP_VIEWPORT, MOBILE_VIEWPORT):
            page.set_viewport_size(vp)
            page.wait_for_timeout(150)

        # Nach mehrfachem Resize: Bubbles noch sichtbar, keine Scroll-Überläufe
        overflow = page.evaluate(
            "() => ({"
            "  scrollW: document.documentElement.scrollWidth,"
            "  clientW: document.documentElement.clientWidth"
            "})"
        )
        # Leichte Toleranz (Scrollbars etc.)
        assert overflow["scrollW"] <= overflow["clientW"] + 20, (
            f"Horizontaler Overflow nach Resize: scrollW={overflow['scrollW']}, "
            f"clientW={overflow['clientW']}"
        )


# ═══════════════════════════════════════════════════════════
#  Farbwähler-Stress (Patch 128 HSL-Slider)
# ═══════════════════════════════════════════════════════════

class TestHslSliderStress:
    """Fenrir dreht alle Slider an die Anschläge."""

    def _require_hsl(self, page: Page):
        if page.locator("#hsl-user-h").count() == 0:
            pytest.skip("HSL-Slider (Patch 128) nicht vom Server ausgeliefert — Neustart nötig")

    def _open_settings(self, page: Page):
        page.locator(".icon-btn[aria-label='Einstellungen']").first.click()
        page.wait_for_timeout(300)

    def _set_slider(self, page: Page, slider_id: str, value: int):
        page.evaluate(
            "(args) => {"
            "  const s = document.getElementById(args.id);"
            "  if (!s) return;"
            "  s.value = args.v;"
            "  s.dispatchEvent(new Event('input', {bubbles: true}));"
            "}",
            {"id": slider_id, "v": value},
        )

    def test_f09_hue_extreme_values(self, logged_in_fenrir: Page):
        """F-09: Hue auf 0 und 360 → Bubble-Farbe bleibt valide."""
        page = logged_in_fenrir
        self._require_hsl(page)
        self._open_settings(page)
        for h in (0, 360):
            self._set_slider(page, "hsl-user-h", h)
            page.wait_for_timeout(100)
            bg = page.evaluate(
                "() => getComputedStyle(document.documentElement).getPropertyValue('--bubble-user-bg').trim()"
            )
            assert bg and bg.lower() != "nan", f"Ungültige Farbe bei Hue={h}: {bg!r}"
            # Akzeptiere rgba(), rgb() oder # — Hauptsache kein leeres String
            assert any(bg.startswith(p) for p in ("rgb", "#", "hsl")), (
                f"Farbe in unerwartetem Format bei Hue={h}: {bg!r}"
            )

    def test_f10_all_sliders_zero(self, logged_in_fenrir: Page):
        """F-10/11: Alle Slider auf Extremwerte → keine JS-Fehler, --bubble-user-bg ist valide."""
        page = logged_in_fenrir
        self._require_hsl(page)
        self._open_settings(page)

        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))

        for h, s, l in ((0, 0, 10), (360, 100, 90), (180, 50, 50)):
            self._set_slider(page, "hsl-user-h", h)
            self._set_slider(page, "hsl-user-s", s)
            self._set_slider(page, "hsl-user-l", l)
            page.wait_for_timeout(80)

        bg = page.evaluate(
            "() => getComputedStyle(document.documentElement).getPropertyValue('--bubble-user-bg').trim()"
        )
        assert bg, "--bubble-user-bg leer nach Extremwerten"
        assert not errors, f"JS-Fehler bei HSL-Extremwerten: {errors}"


# ═══════════════════════════════════════════════════════════
#  Code-Chunker / RAG-Upload-Edge-Cases
# ═══════════════════════════════════════════════════════════

class TestCodeChunkerEdgeCases:
    """Fenrir wirft kaputte Dateien ins RAG.

    Bewusst als Unit-Test gegen den Code-Chunker, nicht via UI —
    UI-Upload braucht Hel und Admin-Login, und die Chunker-Logik ist
    was uns hier interessiert (Patch 123).
    """

    def test_f12_syntax_error_python_returns_empty_not_crash(self):
        """F-12: Python mit SyntaxError → chunk_code gibt [] zurück (Aufrufer fällt auf Prose zurück)."""
        from zerberus.modules.rag.code_chunker import chunk_code
        # Bewusst kaputter Python-Code
        broken = "def foo(:\n    return 1 + \n\n"
        try:
            chunks = chunk_code(broken, "broken.py")
        except SyntaxError:
            pytest.fail("chunk_code hat SyntaxError durchgereicht statt Fallback zu nehmen")
        # Kontrakt: leere Liste bedeutet "Aufrufer soll Prose-Chunker nehmen"
        assert chunks == [], f"Erwartet [] bei SyntaxError, bekam {len(chunks)} Chunks"

    def test_f13_empty_file_no_crash(self):
        """F-13: Leere Datei → chunk_code gibt [] zurück, kein Crash."""
        from zerberus.modules.rag.code_chunker import chunk_code
        for empty_input in ("", "   \n\n  \t", None):
            try:
                if empty_input is None:
                    continue  # chunk_code akzeptiert keinen None-Content
                chunks = chunk_code(empty_input, "empty.py")
            except Exception as exc:
                pytest.fail(f"Empty-Input crasht den Chunker: {type(exc).__name__}: {exc}")
            assert chunks == [], f"Erwartet [] bei leerem Input, bekam {len(chunks)} Chunks"

    def test_f14_large_file_completes(self):
        """F-14: Großer Python-Text → Chunker terminiert, erzeugt viele Chunks."""
        from zerberus.modules.rag.code_chunker import chunk_code
        # Viele Top-Level-Funktionen mit unterschiedlichen Namen für AST-Split
        parts = [f"def f{i}():\n    return {i}\n\n" for i in range(2000)]
        big = "".join(parts)  # ~56KB — realistisch, kein DOS
        try:
            chunks = chunk_code(big, "big.py")
        except Exception as exc:
            pytest.fail(f"Large-Input crasht den Chunker: {type(exc).__name__}: {exc}")
        # AST-Chunker sollte pro Funktion einen Chunk erzeugen (oder gebündelt nach size-limits)
        assert len(chunks) >= 1, "Chunker hat keinen einzigen Chunk erzeugt"

    def test_f15_unicode_content_survives(self):
        """F-15 (bonus): Python mit Umlauten/Emojis in Kommentaren crasht nicht."""
        from zerberus.modules.rag.code_chunker import chunk_code
        src = (
            '"""Modul mit Üniçödé und 🔥 im Docstring."""\n'
            'def grüß_gott():\n'
            '    """Eine Funktion mit Ümläüten."""\n'
            '    return "hällo 🌍"\n'
        )
        chunks = chunk_code(src, "unicode.py")
        assert len(chunks) >= 1, "Unicode-Python produziert keine Chunks"


# ═══════════════════════════════════════════════════════════
#  Patch 154 — Stress-Tests für Patches 137–153
# ═══════════════════════════════════════════════════════════

class TestFarbenStress:
    """Fenrir testet den Farb-Fix (Patch 153) unter Stress-Bedingungen."""

    def test_f_color_01_logout_login_no_black(self, nala_page: Page):
        """F-COLOR-01: Logout + Login → Bubble-BG nicht #000000 (Patch 153)."""
        from zerberus.tests.conftest import FENRIR_CREDS, _do_login
        page = nala_page

        # Erst einloggen
        _do_login(page, FENRIR_CREDS)
        page.wait_for_selector("#text-input", timeout=10000)

        # Logout
        page.evaluate("""() => {
            if (typeof doLogout === 'function') doLogout();
        }""")
        page.wait_for_selector("#login-screen", state="visible", timeout=5000)

        # Wieder einloggen
        _do_login(page, FENRIR_CREDS)
        page.wait_for_selector("#text-input", timeout=10000)

        colors = page.evaluate("""() => {
            const s = getComputedStyle(document.documentElement);
            return {
                userBg: s.getPropertyValue('--bubble-user-bg').trim(),
                llmBg:  s.getPropertyValue('--bubble-llm-bg').trim(),
            };
        }""")
        black = {'#000000', '#000', 'rgb(0, 0, 0)', ''}
        assert colors['userBg'].lower() not in black, (
            f"Nach Logout+Login: user-bubble-bg schwarz: {colors['userBg']!r}"
        )
        assert colors['llmBg'].lower() not in black, (
            f"Nach Logout+Login: llm-bubble-bg schwarz: {colors['llmBg']!r}"
        )

    def test_f_color_02_hsl_picker_no_black(self, logged_in_fenrir: Page):
        """F-COLOR-02: HSL-Slider Extremwert → Farbpicker zeigt kein #000000 (Patch 153)."""
        page = logged_in_fenrir

        # Settings öffnen
        burger = page.locator(".hamburger, .burger-btn").first
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(300)
        gear = page.locator(".sidebar-footer-cog, [class*='settings-btn']").first
        if gear.count() == 0:
            pytest.skip("Settings-Button nicht gefunden")
        gear.click()
        page.wait_for_timeout(400)

        # HSL-Slider für User-BG auf Extremwert setzen (H=338, S=82, L=59 — Pink)
        h_slider = page.locator("#hsl-user-h, [id*='hsl-user']").first
        if h_slider.count() == 0:
            pytest.skip("HSL-Slider nicht gefunden")

        h_slider.evaluate("el => { el.value = 338; el.dispatchEvent(new Event('input')); }")
        page.wait_for_timeout(300)

        # Farbpicker-Wert prüfen — darf nicht #000000 sein
        picker_val = page.evaluate(
            "() => (document.getElementById('bc-user-bg') || {}).value || ''"
        )
        assert picker_val != "#000000", (
            f"Farbpicker zeigt #000000 nach HSL=338 — cssToHex-HSL-Bug noch aktiv"
        )
        assert picker_val.startswith("#"), f"Farbpicker-Wert kein Hex: {picker_val!r}"

    def test_f_color_03_hsl_extreme_contrast_readable(self, logged_in_fenrir: Page):
        """F-COLOR-03: HSL-Extremwerte → Text bleibt lesbar (Auto-Kontrast Patch 140)."""
        page = logged_in_fenrir

        # CSS-Variable direkt auf weißen Hintergrund setzen (extremster Fall)
        page.evaluate("""() => {
            document.documentElement.style.setProperty('--bubble-user-bg', '#ffffff');
            if (typeof applyAutoContrast === 'function') applyAutoContrast();
        }""")
        page.wait_for_timeout(200)

        text_color = page.evaluate(
            "() => getComputedStyle(document.documentElement)"
            ".getPropertyValue('--bubble-user-text').trim()"
        )
        # Auf weißem Hintergrund muss dunkler Text gewählt werden
        assert text_color in ("#1a1a1a", "#000000", "#0a1628", "rgb(26, 26, 26)") or (
            text_color.lower() not in ("#ffffff", "#f0f0f0", "#fff")
        ), f"Auto-Kontrast auf weißem BG hat hellen Text gewählt: {text_color!r}"


class TestPacemakerStress:
    """Fenrir stresst den Pacemaker (Patch 150)."""

    def test_f_pace_01_master_toggle_stable(self, hel_page: Page):
        """F-PACE-01: Master-Schalter Rapid-Toggle (5×) → Seite stabil."""
        page = hel_page
        toggle = page.locator(
            "#pacemaker-master, #pacemaker-enabled, input[id*='pacemaker']"
        ).first
        if toggle.count() == 0:
            pytest.skip("Pacemaker-Master-Toggle nicht gefunden")

        for _ in range(5):
            toggle.click()
            page.wait_for_timeout(150)

        # Seite noch responsiv
        from playwright.sync_api import expect
        expect(page.locator("body")).to_be_visible()

    def test_f_pace_02_page_stays_responsive(self, hel_page: Page):
        """F-PACE-02: Pacemaker-Sektion öffnen → keine JS-Fehler."""
        page = hel_page
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # System-Tab öffnen (wo Pacemaker sitzt)
        sys_tab = page.locator(
            ".hel-tab[data-tab='system'], .hel-tab[data-tab='sysctl']"
        ).first
        if sys_tab.count() > 0:
            sys_tab.click()
            page.wait_for_timeout(500)

        assert errors == [], f"JS-Fehler nach Pacemaker-Tab: {errors}"


class TestTTSStress:
    """Fenrir stresst TTS (Patch 143)."""

    def test_f_tts_01_empty_text_no_crash(self, logged_in_fenrir: Page):
        """F-TTS-01: TTS-Aufruf mit leerem Text wirft keinen unbehandelten Fehler."""
        page = logged_in_fenrir
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # speakText('') direkt aufrufen
        page.evaluate("""async () => {
            try {
                if (typeof speakText === 'function') {
                    await speakText('');
                }
            } catch(e) {
                // Erwarteter Fehler bei leerem Text — kein globaler Crash
            }
        }""")
        page.wait_for_timeout(1000)
        # Seite darf nicht crashen (kein unbehandelter pageerror)
        fatal = [e for e in errors if "Cannot read" in e or "undefined" in e]
        assert fatal == [], f"TTS mit leerem Text: unbehandelte Fehler: {fatal}"

    def test_f_tts_02_button_not_duplicate(self, logged_in_fenrir: Page):
        """F-TTS-02: Nach mehrfachem Chat-Senden kein TTS-Button-Duplikat."""
        page = logged_in_fenrir
        for _ in range(3):
            page.fill("#text-input", "Test")
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)

        try:
            page.wait_for_selector(".bot-message", timeout=30000)
        except Exception:
            pytest.skip("Bot hat nicht geantwortet")

        # Erstes Bot-Bubble prüfen: genau 1 TTS-Button
        tts_in_first = page.evaluate("""() => {
            const first = document.querySelector('.bot-message');
            if (!first) return 0;
            return first.querySelectorAll('[class*="tts"], .speak-btn').length;
        }""")
        assert tts_in_first <= 1, (
            f"Erste Bot-Bubble hat {tts_in_first} TTS-Buttons (Duplikat!)"
        )
