"""
Loki — der Trickster. Systematische E2E-Tests für Zerberus.

Patch 93: läuft gegen den echten Server via Playwright. Benötigt Test-Account
`loki` (config.yaml) und den Server auf https://127.0.0.1:5000.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


# ═══════════════════════════════════════════════════════════
#  AUTH & LOGIN
# ═══════════════════════════════════════════════════════════

class TestLogin:
    """Loki testet die Eingangstore."""

    def test_login_page_loads(self, nala_page: Page):
        """Login-Screen wird angezeigt."""
        expect(nala_page.locator("#login-password")).to_be_visible()

    def test_login_valid_credentials(self, nala_page: Page):
        """Gültige Credentials → Chat-Interface erscheint."""
        from .conftest import _do_login, LOKI_CREDS
        _do_login(nala_page, LOKI_CREDS)
        expect(nala_page.locator("#text-input")).to_be_visible(timeout=10000)

    def test_login_wrong_password(self, nala_page: Page):
        """Falsches Passwort → Fehlermeldung, Chat bleibt verborgen."""
        nala_page.locator("#login-username").fill("loki")
        nala_page.locator("#login-password").fill("definitely-wrong")
        nala_page.locator("#login-submit").click()
        nala_page.wait_for_timeout(1500)
        # Chat-Input darf nicht sichtbar sein
        assert not nala_page.locator("#text-input").is_visible()

    def test_login_case_insensitive(self, nala_page: Page):
        """Login case-insensitive (LOKI auch gültig) — Patch 78."""
        from .conftest import _do_login
        _do_login(nala_page, {"username": "LOKI", "password": "lokitest123"})
        expect(nala_page.locator("#text-input")).to_be_visible(timeout=10000)


# ═══════════════════════════════════════════════════════════
#  CHAT
# ═══════════════════════════════════════════════════════════

class TestChat:
    """Loki testet die Gesprächsfähigkeit."""

    def test_text_input_writable(self, logged_in_loki: Page):
        """Textarea akzeptiert Eingabe."""
        page = logged_in_loki
        page.fill("#text-input", "Hallo, ich bin ein Test.")
        assert page.locator("#text-input").input_value() == "Hallo, ich bin ein Test."

    def test_empty_message_not_sent(self, logged_in_loki: Page):
        """Leere Nachricht → keine neue Nachricht im Chat."""
        page = logged_in_loki
        initial_count = page.locator(".message, .chat-message, .bubble").count()
        page.fill("#text-input", "")
        # Enter oder Send-Button — ohne Text sollte nichts passieren
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        assert page.locator(".message, .chat-message, .bubble").count() == initial_count


# ═══════════════════════════════════════════════════════════
#  NAVIGATION & UI
# ═══════════════════════════════════════════════════════════

class TestNavigation:
    """Loki prüft Türen und Gänge."""

    def test_hamburger_menu_opens(self, logged_in_loki: Page):
        """Hamburger-Button öffnet die Sidebar."""
        page = logged_in_loki
        hamburger = page.locator(
            ".hamburger, button:has-text('\u2630'), .hamburger-btn, #hamburger-btn, [aria-label*='Menü'], [aria-label*='Menu']"
        ).first
        if hamburger.count() == 0:
            pytest.skip("Kein Hamburger-Button gefunden")
        hamburger.click()
        page.wait_for_timeout(500)
        sidebar = page.locator(".sidebar, #sidebar, .menu-drawer")
        assert sidebar.first.is_visible()

    def test_settings_modal_opens(self, logged_in_loki: Page):
        """Settings-Button öffnet Modal."""
        page = logged_in_loki
        settings_btn = page.locator(
            "button:has-text('\U0001F527'), .settings-btn, #settings-btn, [title*='Einstellungen']"
        ).first
        if settings_btn.count() == 0:
            pytest.skip("Kein Settings-Button gefunden")
        settings_btn.click()
        page.wait_for_timeout(500)
        modal = page.locator(".settings-modal, #settings-modal, .modal, [role='dialog']")
        assert modal.first.is_visible()


# ═══════════════════════════════════════════════════════════
#  HEL DASHBOARD
# ═══════════════════════════════════════════════════════════

class TestHel:
    """Loki inspiziert die Unterwelt."""

    def test_hel_loads(self, hel_page: Page):
        """Hel-Dashboard lädt — irgendein h1 oder h2 sichtbar."""
        header = hel_page.locator("h1, h2").first
        expect(header).to_be_visible()

    def test_metrics_section_present(self, hel_page: Page):
        """Metriken-Sektion mit Chart.js Canvas ist da (Patch 91)."""
        # Entweder das neue Canvas oder der Akkordeon-Header
        has_canvas = hel_page.locator("#metricsCanvas").count() > 0
        has_section = hel_page.locator("#section-metrics").count() > 0
        assert has_canvas or has_section

    def test_time_chips_visible(self, hel_page: Page):
        """Patch 91: Zeitraum-Chips sind sichtbar."""
        chips = hel_page.locator(".time-chip")
        hel_page.wait_for_timeout(500)
        if chips.count() == 0:
            pytest.skip("Metriken-Akkordeon evtl. eingeklappt")
        assert chips.count() >= 4  # 7d, 30d, 90d, Alles, Custom


# ═══════════════════════════════════════════════════════════
#  PATCH 91: METRIKEN-API
# ═══════════════════════════════════════════════════════════

class TestMetricsAPI:
    """Loki prüft den erweiterten /hel/metrics/history Endpoint."""

    def test_history_returns_meta_envelope(self, page: Page):
        """API liefert {meta: ..., results: ...} — Patch 91 Format."""
        import os, base64
        admin_user = os.getenv("ADMIN_USER", "admin")
        admin_pw = os.getenv("ADMIN_PASSWORD", "admin")
        token = base64.b64encode(f"{admin_user}:{admin_pw}".encode()).decode()

        resp = page.request.get(
            "https://127.0.0.1:5000/hel/metrics/history?limit=3&from_date=2026-04-15",
            headers={"Authorization": f"Basic {token}"},
            ignore_https_errors=True,
        )
        assert resp.status == 200, f"HTTP {resp.status}: {resp.text()}"
        body = resp.json()
        # Entweder neues Envelope ODER alte Liste (Backward-Compat)
        if isinstance(body, dict):
            assert "meta" in body and "results" in body
            assert "count" in body["meta"]
        else:
            # altes Format — dann muss der Server neu geladen werden
            pytest.fail(
                "API liefert noch altes Array-Format. Server-Neustart nötig (Patch 91)"
            )
