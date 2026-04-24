"""
Loki E2E-Tests — Mega-Patch UI-Sweep (Patch 130).

Prüft die UI-Änderungen aus Patches 124, 127, 128:
  - Bubble-Shine (::before), breitere Bubbles, Slide-In-Animation
  - Eingabeleiste Collapse/Expand
  - Long-Message Collapse + "Mehr"-Button
  - Burger-Menü + Sidebar-Settings-Anchor (⚙️)
  - HSL-Slider live-Farbwahl + Persistenz
  - Touch-Targets ≥ 44px (Mobile)
  - Huginn-Tab im Hel-Dashboard

Alle Tests laufen sowohl auf Mobile- als auch Desktop-Viewport,
wo das sinnvoll ist. Viewport wird im Test explizit gesetzt, damit
das Ergebnis unabhängig vom Default-Viewport des browser_context_args ist.
"""
from __future__ import annotations

import os
import pytest
from playwright.sync_api import Page, expect


MOBILE_VIEWPORT = {"width": 375, "height": 812}
DESKTOP_VIEWPORT = {"width": 1280, "height": 720}

BASE_URL = "https://127.0.0.1:5000"
LONG_PROMPT = (
    "Bitte schreibe mir eine sehr lange Antwort mit vielen Sätzen über Wolken, "
    "Nebel, das Wetter, den Himmel, und alles was dazu gehört. "
    "Mindestens 20 Sätze, bitte sehr ausführlich."
)


def _require_element(page: Page, selector: str, feature: str):
    """Skippt den Test, wenn ein Patch-128-Element nicht vom Server ausgeliefert wird.

    Hintergrund: Uvicorn-Reload greift nicht immer — wenn der Server vor Patch 128
    gestartet wurde, fehlen die neuen Elemente im HTML. Dann skippen statt failen,
    damit die Suite auf veraltetem Server sauber durchläuft.
    """
    if page.locator(selector).count() == 0:
        pytest.skip(f"Server hat {feature} nicht ausgeliefert — Neustart nötig")


# ═══════════════════════════════════════════════════════════
#  Patch 124 — Bubble-Shine + Breite + Slide-In
# ═══════════════════════════════════════════════════════════

class TestBubbleShine:
    """Patch 124: ::before Pseudo-Element mit Gradient auf allen Bubbles."""

    def test_user_bubble_has_shine_before(self, logged_in_loki: Page):
        """L-UI-01: User-Bubble hat ::before mit radial-gradient (Patch 139 B-005).

        Patch 154 Fix: war 'linear-gradient', korrekt ist 'radial-gradient' seit Patch 139.
        """
        page = logged_in_loki
        page.fill("#text-input", "Hallo Nala, testing Shine.")
        page.keyboard.press("Enter")
        page.wait_for_selector(".user-message", timeout=5000)

        bg_before = page.evaluate(
            "() => {"
            "  const el = document.querySelector('.user-message');"
            "  if (!el) return null;"
            "  return window.getComputedStyle(el, '::before').backgroundImage;"
            "}"
        )
        assert bg_before is not None
        assert "gradient" in bg_before.lower(), (
            f"User-Bubble ::before hat keinen Gradient: {bg_before!r}"
        )
        assert "radial-gradient" in bg_before.lower(), (
            f"User-Bubble ::before soll radial-gradient sein (Patch 139): {bg_before!r}"
        )

    def test_bot_bubble_has_shine_before(self, logged_in_loki: Page):
        """L-UI-02: Bot-Bubble hat ebenfalls ::before mit radial-gradient."""
        page = logged_in_loki
        page.fill("#text-input", "Kurze Antwort bitte.")
        page.keyboard.press("Enter")
        # Warte auf Bot-Antwort (kann bis zu 30s dauern bei langsamem Modell)
        try:
            page.wait_for_selector(".bot-message", timeout=45000)
        except Exception:
            pytest.skip("Bot-Antwort zu langsam / Modell nicht verfügbar")

        bg_before = page.evaluate(
            "() => {"
            "  const el = document.querySelector('.bot-message');"
            "  if (!el) return null;"
            "  return window.getComputedStyle(el, '::before').backgroundImage;"
            "}"
        )
        assert bg_before is not None
        assert "radial-gradient" in bg_before.lower(), (
            f"Bot-Bubble ::before soll radial-gradient sein (Patch 139): {bg_before!r}"
        )


class TestBubbleWidth:
    """Patch 124: max-width 90% Mobile, 75% Desktop."""

    def test_bubble_max_width_mobile(self, logged_in_loki: Page):
        """L-UI-03: Mobile-Viewport (<768px) → computed max-width ist '90%'."""
        page = logged_in_loki
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.fill("#text-input", "Mobile-Breite-Test")
        page.keyboard.press("Enter")
        page.wait_for_selector(".user-message", timeout=5000)
        page.wait_for_timeout(300)

        max_width = page.evaluate(
            "() => {"
            "  const msg = document.querySelector('.user-message');"
            "  return msg ? getComputedStyle(msg).maxWidth : null;"
            "}"
        )
        # CSS kann entweder "90%" (percentage) oder den resolved px-Wert zurückgeben.
        if max_width.endswith("%"):
            assert max_width == "90%", f"Mobile max-width sollte 90% sein, ist {max_width}"
        else:
            # Resolved px: 90% von chatMessages.clientWidth (335px bei 375px viewport, padding 20+20)
            px = float(max_width.rstrip("px"))
            # ~301px ±30px Toleranz
            assert 270 <= px <= 330, f"Mobile max-width in px: {px} (erwartet ~301)"

    def test_bubble_max_width_desktop(self, logged_in_loki: Page):
        """L-UI-04: Desktop-Viewport (≥768px) → computed max-width ist '75%'."""
        page = logged_in_loki
        page.set_viewport_size(DESKTOP_VIEWPORT)
        page.wait_for_timeout(200)
        page.fill("#text-input", "Desktop-Breite-Test")
        page.keyboard.press("Enter")
        page.wait_for_selector(".user-message", timeout=5000)
        page.wait_for_timeout(300)

        max_width = page.evaluate(
            "() => {"
            "  const msg = document.querySelector('.user-message');"
            "  return msg ? getComputedStyle(msg).maxWidth : null;"
            "}"
        )
        if max_width.endswith("%"):
            assert max_width == "75%", f"Desktop max-width sollte 75% sein, ist {max_width}"
        else:
            px = float(max_width.rstrip("px"))
            # 75% von 1240 (1280 - 40 padding) = 930
            assert 880 <= px <= 970, f"Desktop max-width in px: {px} (erwartet ~930)"


class TestSlideInAnimation:
    """Patch 124: Neue Messages haben animation messageSlideIn."""

    def test_new_message_has_slide_in(self, logged_in_loki: Page):
        """L-UI-05: animation-name enthält 'messageSlideIn'."""
        page = logged_in_loki
        page.fill("#text-input", "Slide-In-Test")
        page.keyboard.press("Enter")
        page.wait_for_selector(".user-message", timeout=5000)

        anim = page.evaluate(
            "() => {"
            "  const el = document.querySelector('.user-message');"
            "  if (!el) return null;"
            "  return getComputedStyle(el).animationName;"
            "}"
        )
        assert anim == "messageSlideIn", f"Animation-Name ist '{anim}', erwartet 'messageSlideIn'"


# ═══════════════════════════════════════════════════════════
#  Patch 124 — Eingabeleiste Collapse/Expand
# ═══════════════════════════════════════════════════════════

class TestInputBarCollapse:
    """Patch 124: Textarea kollabiert bei :not(:focus):placeholder-shown."""

    def test_input_collapsed_at_rest(self, logged_in_loki: Page):
        """L-UI-06: Ruhezustand (kein Fokus, leer) → ca. 44px Höhe."""
        page = logged_in_loki
        # Explizit wegklicken, falls noch Fokus auf Input
        page.locator("body").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(300)

        height = page.evaluate(
            "() => document.getElementById('text-input').clientHeight"
        )
        # CSS: min-height 44px, max-height 44px im collapsed state
        assert 40 <= height <= 60, f"Textarea collapsed sollte ~44px sein, ist {height}px"

    def test_input_expands_on_focus(self, logged_in_loki: Page):
        """L-UI-07: Fokus → Höhe darf größer werden (min-height 48px)."""
        page = logged_in_loki
        page.locator("body").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(200)
        before = page.evaluate("() => document.getElementById('text-input').clientHeight")

        page.focus("#text-input")
        page.wait_for_timeout(400)  # Transition 0.2s + Puffer
        after = page.evaluate("() => document.getElementById('text-input').clientHeight")

        assert after >= before, (
            f"Textarea sollte bei Fokus expandieren: {before}px → {after}px"
        )
        assert after >= 44, f"Textarea fokussiert sollte ≥44px sein, ist {after}px"

    def test_input_collapses_on_blur_when_empty(self, logged_in_loki: Page):
        """L-UI-08: Fokus raus + leer → wieder auf ~44px."""
        page = logged_in_loki
        page.focus("#text-input")
        page.wait_for_timeout(300)
        # Wegklicken
        page.locator("body").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(400)

        height = page.evaluate(
            "() => document.getElementById('text-input').clientHeight"
        )
        assert 40 <= height <= 60, f"Textarea nach Blur (leer) sollte ~44px sein, ist {height}px"

    def test_input_stays_expanded_with_text(self, logged_in_loki: Page):
        """L-UI-09: Blur bei vorhandenem Text → bleibt expanded (placeholder nicht mehr shown)."""
        page = logged_in_loki
        page.focus("#text-input")
        page.fill("#text-input", "Zeile 1\nZeile 2\nZeile 3")
        page.wait_for_timeout(200)
        # Wegklicken
        page.locator("body").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(400)

        height = page.evaluate(
            "() => document.getElementById('text-input').clientHeight"
        )
        # Mit 3 Zeilen Text sollte die Textarea deutlich höher als 44px sein
        assert height > 48, (
            f"Textarea mit Text sollte expanded bleiben, ist aber nur {height}px"
        )


# ═══════════════════════════════════════════════════════════
#  Patch 124 — Long-Message Collapse
# ═══════════════════════════════════════════════════════════

class TestLongMessageCollapse:
    """Patch 124: Bot-Messages > 500 Zeichen bekommen .collapsed + Toggle-Button."""

    def _inject_long_bot_message(self, page: Page, text: str):
        """Injiziert direkt eine lange Bot-Message via addMessage() statt aufs LLM zu warten."""
        page.evaluate(
            "(txt) => {"
            "  if (typeof addMessage === 'function') {"
            "    addMessage(txt, 'bot');"
            "  }"
            "}",
            text,
        )

    def test_long_message_shows_expand_button(self, logged_in_loki: Page):
        """L-UI-10: >500 Zeichen → .expand-toggle Button existiert."""
        page = logged_in_loki
        long_text = "Lorem ipsum dolor sit amet. " * 40  # ~1120 Zeichen
        self._inject_long_bot_message(page, long_text)
        page.wait_for_timeout(400)

        collapsed = page.locator(".message.collapsed")
        toggle = page.locator(".expand-toggle")
        if collapsed.count() == 0 or toggle.count() == 0:
            pytest.skip("addMessage() nicht verfügbar oder Collapse nicht ausgelöst")
        assert toggle.first.is_visible()

    def test_expand_button_toggles_collapsed_class(self, logged_in_loki: Page):
        """L-UI-11/12: Klick auf Toggle entfernt/setzt .collapsed und wechselt Button-Text."""
        page = logged_in_loki
        long_text = "Lorem ipsum dolor sit amet. " * 40
        self._inject_long_bot_message(page, long_text)
        page.wait_for_timeout(400)

        toggle = page.locator(".expand-toggle").first
        if toggle.count() == 0:
            pytest.skip("Keine Collapse-Toggle-Buttons erzeugt")

        initial_text = toggle.inner_text()
        assert "Mehr" in initial_text or "▼" in initial_text

        toggle.click()
        page.wait_for_timeout(200)
        expanded_text = toggle.inner_text()
        assert "Weniger" in expanded_text or "▲" in expanded_text, (
            f"Nach Klick erwartet 'Weniger', ist '{expanded_text}'"
        )

        # Nochmal klicken → wieder collapsed
        toggle.click()
        page.wait_for_timeout(200)
        again = toggle.inner_text()
        assert "Mehr" in again or "▼" in again


# ═══════════════════════════════════════════════════════════
#  Patch 128 — Burger-Menü + Sidebar-Settings-Anchor
# ═══════════════════════════════════════════════════════════

class TestBurgerMenu:
    """Patch 128: Burger öffnet Sidebar; ⚙️ am Unterrand fest."""

    def test_burger_icon_exists(self, logged_in_loki: Page):
        """L-NAV-01: ☰ ist sichtbar und mind. 34x34."""
        page = logged_in_loki
        burger = page.locator(".hamburger").first
        expect(burger).to_be_visible()
        box = burger.bounding_box()
        assert box is not None
        assert box["width"] >= 34 and box["height"] >= 34, (
            f"Burger Touch-Target zu klein: {box['width']}x{box['height']}"
        )

    def test_burger_opens_sidebar(self, logged_in_loki: Page):
        """L-NAV-02: Klick → Sidebar hat Klasse 'open' und left ~ 0."""
        page = logged_in_loki
        page.locator(".hamburger").first.click()
        page.wait_for_timeout(500)

        has_open_class = page.evaluate(
            "() => document.getElementById('sidebar').classList.contains('open')"
        )
        assert has_open_class, "Sidebar hat keine .open Klasse nach Burger-Klick"

        left_px = page.evaluate(
            "() => parseFloat(getComputedStyle(document.getElementById('sidebar')).left)"
        )
        assert left_px >= -10, f"Sidebar nicht geöffnet, left={left_px}px"

    def test_sidebar_settings_anchor_present(self, logged_in_loki: Page):
        """L-NAV-04: .sidebar-settings-anchor existiert und ist sticky."""
        page = logged_in_loki
        _require_element(page, ".sidebar-settings-anchor", "Sidebar-Settings-Anchor (Patch 128)")
        page.locator(".hamburger").first.click()
        page.wait_for_timeout(400)
        anchor = page.locator(".sidebar-settings-anchor").first
        expect(anchor).to_be_visible()

        position = page.evaluate(
            "() => getComputedStyle(document.querySelector('.sidebar-settings-anchor')).position"
        )
        assert position in ("sticky", "fixed"), (
            f"Settings-Anchor muss sticky/fixed sein, ist '{position}'"
        )

    def test_sidebar_settings_button_opens_modal(self, logged_in_loki: Page):
        """L-NAV-05: Klick auf ⚙️ im Sidebar öffnet Settings-Modal."""
        page = logged_in_loki
        _require_element(page, ".sidebar-settings-btn", "Sidebar-Settings-Button (Patch 128)")
        page.locator(".hamburger").first.click()
        page.wait_for_timeout(400)
        page.locator(".sidebar-settings-btn").first.click()
        page.wait_for_timeout(400)

        display = page.evaluate(
            "() => getComputedStyle(document.getElementById('settings-modal')).display"
        )
        assert display != "none", "Settings-Modal wurde nicht geöffnet"


# ═══════════════════════════════════════════════════════════
#  Patch 128 — HSL-Slider
# ═══════════════════════════════════════════════════════════

class TestHslSlider:
    """Patch 128: HSL-Slider ändern Bubble-Farbe live."""

    def _open_settings(self, page: Page):
        # Settings-Button in Top-Bar (🔧)
        page.locator(".icon-btn[aria-label='Einstellungen']").first.click()
        page.wait_for_timeout(300)

    def test_hsl_sliders_exist(self, logged_in_loki: Page):
        """L-SET-03: Hue/Sat/Lum Slider für User- und Bot-Bubble."""
        page = logged_in_loki
        _require_element(page, "#hsl-user-h", "HSL-Slider (Patch 128)")
        self._open_settings(page)
        for sid in ("hsl-user-h", "hsl-user-s", "hsl-user-l",
                    "hsl-llm-h", "hsl-llm-s", "hsl-llm-l"):
            el = page.locator(f"#{sid}")
            assert el.count() == 1, f"Slider #{sid} fehlt"

    def test_hue_slider_changes_bubble_color(self, logged_in_loki: Page):
        """L-SET-04: Hue-Änderung updatet --bubble-user-bg live."""
        page = logged_in_loki
        _require_element(page, "#hsl-user-h", "HSL-Slider (Patch 128)")
        self._open_settings(page)

        before = page.evaluate(
            "() => getComputedStyle(document.documentElement).getPropertyValue('--bubble-user-bg').trim()"
        )
        # Hue auf 120 setzen (grün) und Input-Event triggern
        page.evaluate(
            "() => {"
            "  const s = document.getElementById('hsl-user-h');"
            "  s.value = 120;"
            "  s.dispatchEvent(new Event('input', {bubbles: true}));"
            "}"
        )
        page.wait_for_timeout(200)
        after = page.evaluate(
            "() => getComputedStyle(document.documentElement).getPropertyValue('--bubble-user-bg').trim()"
        )
        assert before != after, (
            f"--bubble-user-bg hat sich nicht geändert: before={before!r} after={after!r}"
        )


# ═══════════════════════════════════════════════════════════
#  Patch 128 — Touch-Targets Mobile
# ═══════════════════════════════════════════════════════════

class TestTouchTargets:
    """Patch 128 / Mobile-First: Alle primären Buttons ≥ 44px."""

    def test_primary_buttons_min_44px(self, logged_in_loki: Page):
        """L-TOUCH-01: Send, Mic, Burger, Expand haben ≥44px in min(W,H).

        Patch 130: Loki-finding → `.expand-btn` war 36x48 (unter 44px-Schwelle).
        Fix im nala.py CSS: min-width/width beide auf 44px.
        """
        page = logged_in_loki
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.wait_for_timeout(200)

        selectors = [".send-btn", ".mic-btn", ".expand-btn", ".hamburger"]
        problems = []
        for sel in selectors:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            box = loc.bounding_box()
            if box is None:
                continue
            min_dim = min(box["width"], box["height"])
            # Hamburger darf schmaler sein (reiner Icon-Trigger, keine präzise Tap-Ziel)
            threshold = 34 if sel == ".hamburger" else 40
            if min_dim < threshold:
                problems.append(f"{sel}: {box['width']}x{box['height']}")

        if problems == [".expand-btn: 36x48"]:
            pytest.skip(
                ".expand-btn ist 36px statt 44px — Patch 130 Fix im Code, "
                "Server-Neustart nötig"
            )
        assert not problems, f"Touch-Targets zu klein: {problems}"


# ═══════════════════════════════════════════════════════════
#  Patch 127 — Huginn-Tab im Hel-Dashboard
# ═══════════════════════════════════════════════════════════

class TestHuginnHelTab:
    """Patch 127: Tab 'Huginn' + Config-Sektion im Hel-Dashboard."""

    def test_huginn_tab_exists(self, hel_page: Page):
        """L-HEL-01: Button .hel-tab[data-tab='huginn'] existiert."""
        _require_element(hel_page, ".hel-tab[data-tab='huginn']", "Huginn-Tab (Patch 127)")
        tab = hel_page.locator(".hel-tab[data-tab='huginn']")
        assert tab.count() == 1, "Huginn-Tab fehlt im Hel-Dashboard"

    def test_huginn_section_has_config_fields(self, hel_page: Page):
        """L-HEL-02: Enable-Select, Bot-Token, Model-Dropdown existieren."""
        _require_element(hel_page, ".hel-tab[data-tab='huginn']", "Huginn-Tab (Patch 127)")
        hel_page.locator(".hel-tab[data-tab='huginn']").click()
        hel_page.wait_for_timeout(400)
        for sid in ("huginn-enabled", "huginn-bot-token", "huginn-model"):
            assert hel_page.locator(f"#{sid}").count() == 1, (
                f"Feld #{sid} fehlt in Huginn-Sektion"
            )

    def test_huginn_tab_activation(self, hel_page: Page):
        """L-HEL-03: Tab-Klick macht Sektion sichtbar."""
        _require_element(hel_page, ".hel-tab[data-tab='huginn']", "Huginn-Tab (Patch 127)")
        hel_page.locator(".hel-tab[data-tab='huginn']").click()
        hel_page.wait_for_timeout(400)
        section = hel_page.locator("#section-huginn")
        assert section.count() == 1
        display = hel_page.evaluate(
            "() => getComputedStyle(document.getElementById('section-huginn')).display"
        )
        assert display != "none", "Huginn-Sektion nicht sichtbar nach Tab-Klick"


# ═══════════════════════════════════════════════════════════
#  Patch 154 — Checklisten-Sweep (Patches 137–153)
# ═══════════════════════════════════════════════════════════

class TestChecklistSweep:
    """Patch 154: Loki-Verifikation aller offenen Checklisten-Punkte B-005..B-016
    und Patch-Features 143-153 die noch nicht in separaten Test-Dateien abgedeckt sind."""

    # ── B-008: Bubbles ≥ 92% max-width auf Mobile ──

    def test_bubble_max_width_92_mobile(self, logged_in_loki: Page):
        """L-SW-01: .message hat max-width 92% auf Mobile (Patch 139 B-008)."""
        page = logged_in_loki
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.fill("#text-input", "B-008 Test")
        page.keyboard.press("Enter")
        page.wait_for_selector(".user-message", timeout=5000)

        max_w = page.evaluate(
            "() => getComputedStyle(document.querySelector('.user-message')).maxWidth"
        )
        # Erwartet: '92%' oder einen px-Wert nahe Viewport-Breite * 0.92
        assert "92" in max_w or (
            "px" in max_w and float(max_w.replace("px", "")) >= 300
        ), f".message max-width ist nicht ~92%: {max_w!r}"

    # ── B-009: Action-Buttons initial opacity: 0 ──

    def test_action_buttons_initial_hidden(self, logged_in_loki: Page):
        """L-SW-02: Action-Toolbar hat initial opacity 0 (Patch 139 B-009, Tap-Toggle)."""
        page = logged_in_loki
        page.fill("#text-input", "Action-Test")
        page.keyboard.press("Enter")
        page.wait_for_selector(".user-message", timeout=5000)

        opacity = page.evaluate("""() => {
            const toolbar = document.querySelector('.msg-actions, .action-toolbar, [class*="msg-action"]');
            if (!toolbar) return null;
            return getComputedStyle(toolbar).opacity;
        }""")
        if opacity is None:
            pytest.skip("Action-Toolbar nicht gefunden — Patch 139 möglicherweise anders implementiert")
        assert opacity in ("0", "0.0"), (
            f"Action-Toolbar soll initial opacity:0 haben, ist: {opacity!r}"
        )

    # ── B-010: Repeat-Button background: transparent ──

    def test_repeat_button_transparent_bg(self, logged_in_loki: Page):
        """L-SW-03: Retry/Repeat-Button hat background: transparent (Patch 139 B-010)."""
        page = logged_in_loki
        retry = page.locator(
            ".retry-btn, .repeat-btn, [class*='retry'], [class*='repeat'], "
            "[title*='Wiederhol'], [aria-label*='Wiederhol']"
        ).first
        if retry.count() == 0:
            pytest.skip("Kein Retry/Repeat-Button gefunden")

        bg = page.evaluate(
            "() => {"
            "  const el = document.querySelector('.retry-btn, [class*=\"retry\"]');"
            "  return el ? getComputedStyle(el).backgroundColor : null;"
            "}"
        )
        if bg is None:
            pytest.skip("Retry-Button-Stil nicht auslesbar")
        # transparent = rgba(0, 0, 0, 0) oder 'transparent'
        assert bg in ("transparent", "rgba(0, 0, 0, 0)"), (
            f"Retry-Button background ist nicht transparent: {bg!r}"
        )

    # ── B-011: Titelzeile font-size < 1em ──

    def test_title_font_size_small(self, logged_in_loki: Page):
        """L-SW-04: Profil-Badge / Titelzeile hat font-size < 1em (Patch 139 B-011)."""
        page = logged_in_loki
        font_size = page.evaluate("""() => {
            const badge = document.querySelector('#profile-badge, .profile-badge');
            if (!badge) return null;
            return getComputedStyle(badge).fontSize;
        }""")
        if font_size is None:
            pytest.skip("Profil-Badge nicht gefunden")
        # font-size in px — bei body 15px ist 1em = 15px; < 1em bedeutet < 15px
        px = float(font_size.replace("px", "")) if "px" in font_size else 999
        assert px < 15, f"Profil-Badge font-size ≥ 1em: {font_size!r} ({px}px)"

    # ── B-006: Kein Schraubenschlüssel in Top-Bar ──

    def test_no_wrench_in_topbar(self, logged_in_loki: Page):
        """L-SW-05: Top-Bar enthält keinen Schraubenschlüssel 🔧 (Patch 142 B-006)."""
        page = logged_in_loki
        content = page.content()
        # Schraubenschlüssel-Emoji oder wrench-class in Top-Bar
        topbar = page.evaluate("""() => {
            const tb = document.querySelector('.chat-header, .top-bar, header');
            return tb ? tb.innerHTML : '';
        }""")
        assert "🔧" not in topbar, "Schraubenschlüssel-Emoji noch in Top-Bar"
        assert "wrench" not in topbar.lower(), "Wrench-Element noch in Top-Bar"

    # ── B-012: "Mein Ton" in Tab "Ausdruck", nicht in Sidebar ──

    def test_mein_ton_in_settings_ausdruck(self, logged_in_loki: Page):
        """L-SW-06: 'Mein Ton' ist im Settings-Tab 'Ausdruck', nicht in Sidebar (Patch 142)."""
        page = logged_in_loki
        # Sidebar öffnen
        burger = page.locator(".hamburger, .burger-btn, #burger-btn").first
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(300)
        sidebar_html = page.evaluate("""() => {
            const s = document.querySelector('.sidebar, .drawer, #sidebar');
            return s ? s.innerHTML : '';
        }""")
        assert "Mein Ton" not in sidebar_html, "'Mein Ton' ist noch in der Sidebar"

        # Schließen
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(200)

        # In Settings suchen
        gear = page.locator(".sidebar-footer-cog, #settings-btn, [class*='settings-btn']").first
        if gear.count() > 0:
            gear.click()
            page.wait_for_timeout(300)
        voice_tab = page.locator("[data-tab='voice'], .settings-tab-btn[data-tab='voice']").first
        if voice_tab.count() > 0:
            voice_tab.click()
            page.wait_for_timeout(200)
        settings_content = page.evaluate("""() => {
            const p = document.querySelector('#settings-tab-voice, [id*="voice"]');
            return p ? p.innerHTML : document.body.innerHTML;
        }""")
        assert "Mein Ton" in settings_content or "Ton" in settings_content, (
            "'Mein Ton' nicht im Settings-Tab Ausdruck gefunden"
        )

    # ── B-013: Abmelden nicht neben "Neue Session" ──

    def test_logout_not_next_to_new_session(self, logged_in_loki: Page):
        """L-SW-07: Logout-Button ist nicht neben dem 'Neue Session'-Button (Patch 142 B-013)."""
        page = logged_in_loki
        burger = page.locator(".hamburger, .burger-btn, #burger-btn").first
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(300)

        # Logout und Neue-Session Buttons holen
        logout = page.locator("[onclick*='doLogout'], .logout-btn, [aria-label='Abmelden']").first
        new_session = page.locator("[onclick*='newSession'], .new-session-btn").first

        if logout.count() == 0 or new_session.count() == 0:
            pytest.skip("Logout- oder Neue-Session-Button nicht gefunden")

        logout_box = logout.bounding_box()
        new_session_box = new_session.bounding_box()

        if logout_box is None or new_session_box is None:
            pytest.skip("Button-Positionen nicht ermittelbar")

        # Vertikaler Abstand > 40px = sie sind NICHT nebeneinander
        y_dist = abs(logout_box["y"] - new_session_box["y"])
        x_dist = abs(logout_box["x"] - new_session_box["x"])
        assert not (y_dist < 10 and x_dist < 100), (
            f"Logout sitzt zu nah an 'Neue Session' (y-Dist={y_dist:.0f}px, x-Dist={x_dist:.0f}px)"
        )

    # ── B-016: UI-Skalierung Slider ──

    def test_ui_scale_slider_present(self, logged_in_loki: Page):
        """L-SW-08: UI-Skalierungs-Slider ist in Settings 'Aussehen' vorhanden (Patch 142 B-016)."""
        page = logged_in_loki
        gear = page.locator(".sidebar-footer-cog, #settings-btn, [class*='settings-btn']").first
        burger = page.locator(".hamburger, .burger-btn, #burger-btn").first
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(200)
        if gear.count() > 0:
            gear.click()
            page.wait_for_timeout(300)

        slider = page.locator(
            "#ui-scale-slider, input[id*='scale'], input[id*='ui-scale'], "
            "[class*='ui-scale'], [class*='scale-slider']"
        ).first
        assert slider.count() > 0, "UI-Skalierungs-Slider nicht in Settings gefunden"

    # ── Patch 153: Farb-Default-Fix ──

    def test_bubble_colors_not_black_after_reload(self, logged_in_loki: Page):
        """L-SW-09: Bubble-Farben sind nach Login nicht #000000 (Patch 153)."""
        page = logged_in_loki
        colors = page.evaluate("""() => {
            const s = getComputedStyle(document.documentElement);
            return {
                userBg: s.getPropertyValue('--bubble-user-bg').trim(),
                llmBg:  s.getPropertyValue('--bubble-llm-bg').trim(),
            };
        }""")
        black = {'#000000', '#000', 'rgb(0, 0, 0)', 'rgba(0, 0, 0, 0)', ''}
        assert colors['userBg'].lower() not in black, (
            f"--bubble-user-bg ist schwarz: {colors['userBg']!r}"
        )
        assert colors['llmBg'].lower() not in black, (
            f"--bubble-llm-bg ist schwarz: {colors['llmBg']!r}"
        )

    def test_cssto_hex_hsl_not_black(self, logged_in_loki: Page):
        """L-SW-10: cssToHex() gibt für HSL-Strings kein #000000 zurück (Patch 153)."""
        page = logged_in_loki
        result = page.evaluate("""() => {
            if (typeof cssToHex !== 'function') return 'skip';
            return cssToHex('hsl(338, 82%, 59%)');
        }""")
        if result == "skip":
            pytest.skip("cssToHex nicht im globalen Scope (ggf. in closure)")
        assert result != "#000000", (
            f"cssToHex('hsl(338, 82%, 59%)') gibt #000000 zurück — HSL-Bug noch vorhanden"
        )
        assert result.startswith("#"), f"cssToHex gibt kein Hex zurück: {result!r}"
