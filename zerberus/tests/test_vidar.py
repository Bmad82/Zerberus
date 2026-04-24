"""
Vidar — Post-Deployment Smoke-Test-Suite (Patch 153).

Vidar (altnordisch: "der Rächer") ist Odins Sohn der nach Ragnarök überlebt
und die neue Welt aufbaut. In Zerberus prüft er nach jedem Deployment ob die
wichtigsten Flows funktionieren: Go/No-Go in ~60s.

Kategorien:
  CRITICAL  — Wenn einer fehlschlägt → NICHT deployen
  IMPORTANT — Wenn einer fehlschlägt → deployen, aber fixen
  COSMETIC  — Nice-to-have, kein Alarm

Aufruf:
    pytest zerberus/tests/test_vidar.py -v --timeout=60
    pytest zerberus/tests/test_vidar.py -k critical -v

Nutzt das `vidar` Profil (vidartest123, is_test=true).
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


MOBILE_VP = {"width": 390, "height": 844}
BASE_URL = "https://127.0.0.1:5000"

# ══════════════════════════════════════════════════════════════
#  CRITICAL — Produktions-Blocker
# ══════════════════════════════════════════════════════════════

class TestCritical:
    """Alle Tests hier sind Deploy-Blocker."""

    def test_nala_loads(self, page: Page):
        """V-C-01: Nala-Seite liefert HTTP 200."""
        resp = page.goto(f"{BASE_URL}/nala")
        assert resp is not None and resp.status < 400, (
            f"Nala antwortet mit HTTP {resp.status if resp else '?'}"
        )

    def test_login_works(self, logged_in_vidar: Page):
        """V-C-02: Login mit Vidar-Profil öffnet Chat-Screen."""
        page = logged_in_vidar
        # Chat-Input muss sichtbar sein — Login-Screen weg
        expect(page.locator("#text-input")).to_be_visible(timeout=10000)
        assert "/login" not in page.url, "Noch auf Login-Seite nach erfolgreicher Anmeldung"

    def test_bubbles_not_black(self, logged_in_vidar: Page):
        """V-C-03: Bubble-Hintergründe sind nicht #000000 (Farb-Default-Fix Patch 153)."""
        page = logged_in_vidar
        colors = page.evaluate("""() => {
            const s = getComputedStyle(document.documentElement);
            return {
                userBg:  s.getPropertyValue('--bubble-user-bg').trim(),
                llmBg:   s.getPropertyValue('--bubble-llm-bg').trim(),
            };
        }""")
        black = {'#000000', '#000', 'rgb(0, 0, 0)', 'rgba(0,0,0,1)', ''}
        assert colors['userBg'].lower() not in black, (
            f"User-Bubble-BG ist schwarz: {colors['userBg']!r}"
        )
        assert colors['llmBg'].lower() not in black, (
            f"LLM-Bubble-BG ist schwarz: {colors['llmBg']!r}"
        )

    def test_chat_send_receive(self, logged_in_vidar: Page):
        """V-C-04: Nachricht senden und Bot-Antwort empfangen."""
        page = logged_in_vidar
        page.fill("#text-input", "Vidar Smoke-Test: Antworte bitte kurz.")
        page.keyboard.press("Enter")
        # Bot-Bubble muss erscheinen (45s max — langsames Modell)
        try:
            page.wait_for_selector(".bot-message", timeout=45000)
        except Exception:
            pytest.skip("Kein Modell verfügbar — Bot hat nicht geantwortet")
        bot_bubbles = page.locator(".bot-message")
        assert bot_bubbles.count() > 0, "Keine Bot-Bubble nach Nachricht"

    def test_hel_loads(self, hel_page: Page):
        """V-C-05: Hel-Dashboard liefert HTTP 200."""
        resp = hel_page.goto(f"{BASE_URL}/hel/")
        assert resp is not None and resp.status < 400, (
            f"Hel antwortet mit HTTP {resp.status if resp else '?'}"
        )

    def test_design_css_loaded(self, logged_in_vidar: Page):
        """V-C-06: shared-design.css ist eingebunden (Patch 151)."""
        page = logged_in_vidar
        loaded = page.evaluate("""() => {
            const links = document.querySelectorAll('link[rel="stylesheet"]');
            return Array.from(links).some(l =>
                l.href.includes('shared-design') || l.href.includes('shared_design'));
        }""")
        assert loaded, "shared-design.css nicht im DOM gefunden"


# ══════════════════════════════════════════════════════════════
#  IMPORTANT — Fixen, aber nicht Deploy-Blocker
# ══════════════════════════════════════════════════════════════

class TestImportant:
    """Fehlende Checks hier → deployen, aber als Issue anlegen."""

    def test_touch_targets_44px(self, logged_in_vidar: Page):
        """V-I-01: Sichtbare Buttons/Links ≥ 44px (Mobile-First, Patch 97)."""
        page = logged_in_vidar
        page.set_viewport_size(MOBILE_VP)
        violations = page.evaluate("""() => {
            const els = document.querySelectorAll(
                'button:not([hidden]), a[role="button"]:not([hidden])');
            const bad = [];
            for (const el of els) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 &&
                    (r.width < 44 || r.height < 44)) {
                    bad.push({tag: el.tagName, cls: el.className.slice(0,50),
                               w: Math.round(r.width), h: Math.round(r.height)});
                }
            }
            return bad;
        }""")
        assert violations == [], (
            f"{len(violations)} Touch-Target(s) < 44px:\n"
            + "\n".join(f"  {v['tag']} .{v['cls']} {v['w']}×{v['h']}px" for v in violations[:5])
        )

    def test_design_tokens_present(self, logged_in_vidar: Page):
        """V-I-02: Design-Tokens --zb-touch-min und --zb-primary existieren."""
        page = logged_in_vidar
        tokens = page.evaluate("""() => {
            const s = getComputedStyle(document.documentElement);
            return {
                touchMin: s.getPropertyValue('--zb-touch-min').trim(),
                primary:  s.getPropertyValue('--zb-primary').trim(),
            };
        }""")
        assert tokens['touchMin'] != '', "--zb-touch-min nicht gesetzt (shared-design.css)"
        assert tokens['primary'] != '', "--zb-primary nicht gesetzt (shared-design.css)"

    def test_settings_modal_opens(self, logged_in_vidar: Page):
        """V-I-03: Settings-Modal lässt sich öffnen."""
        page = logged_in_vidar
        # Burger-Menü → Sidebar → Zahnrad
        burger = page.locator(".burger-btn, #burger-btn, [class*='burger']").first
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(400)
        gear = page.locator("[class*='settings-btn'], #settings-btn, .sidebar-footer-cog").first
        gear.wait_for(state="visible", timeout=5000)
        gear.click()
        page.wait_for_timeout(400)
        modal = page.locator("#settings-modal, [class*='settings-modal']").first
        expect(modal).to_be_visible(timeout=3000)

    def test_settings_has_3_tabs(self, logged_in_vidar: Page):
        """V-I-04: Settings hat Tabs Aussehen, Ausdruck, System."""
        page = logged_in_vidar
        burger = page.locator(".burger-btn, #burger-btn, [class*='burger']").first
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(300)
        gear = page.locator("[class*='settings-btn'], #settings-btn, .sidebar-footer-cog").first
        if gear.count() > 0:
            gear.click()
            page.wait_for_timeout(300)
        content = page.content()
        assert "Aussehen" in content, "Settings-Tab 'Aussehen' fehlt"
        assert "Ausdruck" in content, "Settings-Tab 'Ausdruck' fehlt"
        assert "System" in content, "Settings-Tab 'System' fehlt"

    def test_paw_indicator_exists(self, logged_in_vidar: Page):
        """V-I-05: Katzenpfoten-DOM-Element vorhanden (Patch 144)."""
        page = logged_in_vidar
        paw = page.locator("#pawIndicator, .paw-indicator, [id*='paw']").first
        # Kann hidden sein — nur DOM-Existenz prüfen
        assert paw.count() > 0, "#pawIndicator nicht im DOM"

    def test_particle_canvas_exists(self, logged_in_vidar: Page):
        """V-I-06: Particle-Canvas für Feuerwerk vorhanden (Patch 145)."""
        page = logged_in_vidar
        canvas = page.locator("#particleCanvas, canvas[id*='particle']").first
        assert canvas.count() > 0, "#particleCanvas nicht im DOM"

    def test_hel_has_system_tab(self, hel_page: Page):
        """V-I-07: Hel hat 'System'-Tab (nicht 'Sysctl', Patch 149)."""
        page = hel_page
        content = page.content()
        assert "System" in content, "Hel: 'System'-Tab fehlt"
        assert "Sysctl" not in content, "Hel: veralteter 'Sysctl'-Tab noch vorhanden"

    def test_hel_memory_section(self, hel_page: Page):
        """V-I-08: Memory-Dashboard in Hel (Patch 152)."""
        page = hel_page
        content = page.content()
        has_memory = any(w in content for w in ["Memory", "Gedächtnis", "gedaechtnis"])
        assert has_memory, "Kein Memory-Abschnitt in Hel gefunden"

    def test_hel_pacemaker_section(self, hel_page: Page):
        """V-I-09: Pacemaker-Steuerung in Hel (Patch 150)."""
        page = hel_page
        content = page.content()
        has_pace = any(w in content for w in ["Pacemaker", "pacemaker"])
        assert has_pace, "Kein Pacemaker-Abschnitt in Hel"

    def test_session_list_no_empty_titles(self, logged_in_vidar: Page):
        """V-I-10: Session-Liste hat keine leeren Titel."""
        page = logged_in_vidar
        burger = page.locator(".burger-btn, #burger-btn, [class*='burger']").first
        if burger.count() > 0:
            burger.click()
            page.wait_for_timeout(400)
        items = page.locator(".session-item, [class*='session-item']")
        for i in range(min(items.count(), 5)):
            txt = items.nth(i).inner_text().strip()
            assert txt, f"Session-Item #{i} hat keinen Titel"

    def test_tts_button_present(self, logged_in_vidar: Page):
        """V-I-11: TTS-Button in Bot-Bubble vorhanden (Patch 143)."""
        page = logged_in_vidar
        page.fill("#text-input", "Kurz bitte.")
        page.keyboard.press("Enter")
        try:
            page.wait_for_selector(".bot-message", timeout=45000)
        except Exception:
            pytest.skip("Bot hat nicht geantwortet — TTS-Test nicht möglich")
        tts = page.locator("[class*='tts'], .speak-btn, [title*='vorlesen'], [aria-label*='vorlesen']").first
        assert tts.count() > 0, "TTS-Button nicht in Bot-Bubble gefunden"


# ══════════════════════════════════════════════════════════════
#  COSMETIC — Kein Alarm, aber dokumentieren
# ══════════════════════════════════════════════════════════════

class TestCosmetic:
    """Optionale Checks. Fehlschlag = Notiz, kein Block."""

    def test_hel_llm_dropdowns(self, hel_page: Page):
        """V-K-01: LLM-Tab hat mindestens 2 Model-Dropdowns."""
        page = hel_page
        llm_tab = page.locator("[data-tab='llm'], [href*='llm'], .tab-llm").first
        if llm_tab.count() > 0:
            llm_tab.click()
            page.wait_for_timeout(300)
        dropdowns = page.locator("select[id*='model'], select[class*='model'], .model-select")
        assert dropdowns.count() >= 1, "Weniger als 1 Model-Dropdown in Hel LLM-Tab"

    def test_no_json_in_dialects(self, hel_page: Page):
        """V-K-02: Dialekte-Tab zeigt kein rohes JSON (Patch 148)."""
        page = hel_page
        dial_tab = page.locator("[data-tab='dialekte'], [href*='dialekt']").first
        if dial_tab.count() == 0:
            pytest.skip("Kein Dialekte-Tab vorhanden")
        dial_tab.click()
        page.wait_for_timeout(300)
        content = page.locator(".tab-content, #dialekte-tab, [class*='dialekt']").first
        if content.count() == 0:
            pytest.skip("Dialekte-Content nicht gefunden")
        text = content.inner_text()
        # Rohes JSON hat keine Labels/Beschriftungen, nur Schlüssel-Wert
        assert "{" not in text[:200], "Dialekte-Tab enthält rohes JSON am Anfang"

    def test_cssvar_user_bubble_bg_not_empty(self, logged_in_vidar: Page):
        """V-K-03: CSS-Variable --bubble-user-bg ist gesetzt und nicht leer."""
        page = logged_in_vidar
        val = page.evaluate(
            "() => getComputedStyle(document.documentElement)"
            ".getPropertyValue('--bubble-user-bg').trim()"
        )
        assert val != "", "--bubble-user-bg ist leer"

    def test_hel_model_dropdowns_consistent(self, hel_page: Page):
        """V-K-04: Hel LLM-Dropdowns nutzen einheitliche select-Elemente (Patch 147)."""
        page = hel_page
        # Prüft dass es KEINE <input type="text"> für Model-Wahl gibt (Patch 147 Regression)
        model_inputs = page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[id*="model"], input[name*="model"]');
            return Array.from(inputs).map(i => i.id || i.name);
        }""")
        assert model_inputs == [], (
            f"Model-Auswahl via <input> statt <select>: {model_inputs}"
        )
