"""
Patch 93: Shared Playwright-Fixtures für Loki (E2E) und Fenrir (Chaos).
Läuft gegen den echten Server (HTTPS, Tailscale-Cert self-signed).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page


def _load_env_file():
    """Lädt .env im Projekt-Root, falls ADMIN_USER/ADMIN_PASSWORD nicht schon gesetzt sind.

    Simpler KEY=VALUE-Parser — keine Quotes, keine Exports. Reicht für die Test-Suite,
    vermeidet python-dotenv als Zusatz-Dependency.
    """
    if os.getenv("ADMIN_USER") and os.getenv("ADMIN_PASSWORD"):
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file()


def pytest_configure(config):
    """Patch 171/172 — registriert Custom-Marker fuer Live-Tests."""
    config.addinivalue_line(
        "markers",
        "docker: Tests, die einen erreichbaren Docker-Daemon brauchen (Sandbox).",
    )
    config.addinivalue_line(
        "markers",
        "guard_live: Tests, die einen erreichbaren OpenRouter-Guard "
        "brauchen (Mistral Small via OPENROUTER_API_KEY).",
    )


BASE_URL = "https://127.0.0.1:5000"

LOKI_CREDS = {"username": "loki", "password": "lokitest123"}
FENRIR_CREDS = {"username": "fenrir", "password": "fenrirtest123"}
VIDAR_CREDS = {"username": "vidar", "password": "vidartest123"}  # Patch 153: Smoke-Test


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Self-signed-Cert tolerieren + Viewport auf iPhone-Portrait als Default."""
    return {
        **browser_context_args,
        "ignore_https_errors": True,
        "viewport": {"width": 390, "height": 844},
    }


@pytest.fixture
def nala_page(page: Page) -> Page:
    """Öffnet Nala. Wartet bis das Login-Interface geladen ist."""
    page.goto(f"{BASE_URL}/nala")
    page.wait_for_load_state("networkidle")
    return page


@pytest.fixture
def logged_in_loki(nala_page: Page) -> Page:
    _do_login(nala_page, LOKI_CREDS)
    return nala_page


@pytest.fixture
def logged_in_fenrir(nala_page: Page) -> Page:
    _do_login(nala_page, FENRIR_CREDS)
    return nala_page


@pytest.fixture
def logged_in_vidar(nala_page: Page) -> Page:
    """Patch 153: Vidar Smoke-Test-Profil."""
    _do_login(nala_page, VIDAR_CREDS)
    return nala_page


@pytest.fixture
def hel_page(browser):
    """Öffnet Hel in einem eigenen Context mit http_credentials (Basic Auth).

    Credentials kommen aus ADMIN_USER/ADMIN_PASSWORD (.env). Fällt auf admin/admin zurück.
    Eigener Context, weil der Default-Context keine Basic-Auth-Credentials hat.
    """
    admin_user = os.getenv("ADMIN_USER", "admin")
    admin_pw = os.getenv("ADMIN_PASSWORD", "admin")
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 390, "height": 844},
        http_credentials={"username": admin_user, "password": admin_pw},
    )
    page = context.new_page()
    try:
        page.goto(f"{BASE_URL}/hel/")
        page.wait_for_load_state("networkidle")
        yield page
    finally:
        context.close()


def _do_login(page: Page, creds: dict):
    """Login-Helper für Nala.

    Tatsächliche IDs (nala.py):
      - Profil-Feld: #login-username
      - Passwort:    #login-password
      - Submit-Btn:  #login-submit
      - Wrapper:     #login-screen
    """
    username_input = page.locator("#login-username").first
    username_input.wait_for(state="visible", timeout=10000)
    username_input.fill(creds["username"])

    pw_input = page.locator("#login-password").first
    pw_input.fill(creds["password"])

    page.locator("#login-submit").first.click()

    # Warten bis Login-Screen weg ist oder Chat-Input erscheint
    page.wait_for_selector("#text-input", timeout=10000, state="visible")
