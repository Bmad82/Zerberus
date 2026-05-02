"""Patch 200 (Phase 5a #16) — Tests fuer PWA-Verdrahtung (Nala + Hel).

Drei Schichten:

1. **Pure-Function-Tests** fuer ``render_service_worker`` und die Manifest-
   Dicts. Kein Server-Setup noetig.
2. **Direct-call-Tests** fuer die vier Endpoint-Coroutines (``nala_manifest``,
   ``hel_manifest``, ``nala_service_worker``, ``hel_service_worker``).
   Pruefen Status-Code/Content-Type/Body. Auth-Bypass faellt automatisch ab —
   diese Endpoints leben auf ``pwa.router`` (keine Dependencies), nicht auf
   ``hel.router`` (Basic-Auth).
3. **Source-Audit-Tests** fuer NALA_HTML und ADMIN_HTML — Manifest-Link,
   Theme-Color, Apple-Meta, Service-Worker-Registrierung.

Plus: Icon-Dateien existieren, main.py haengt pwa.router VOR hel.router ein.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from zerberus.app.routers import pwa


ROOT = Path(__file__).resolve().parents[2]
ICONS_DIR = ROOT / "zerberus" / "static" / "pwa"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def nala_src() -> str:
    return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def hel_src() -> str:
    return (ROOT / "zerberus" / "app" / "routers" / "hel.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_src() -> str:
    return (ROOT / "zerberus" / "main.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Pure-Function-Tests
# ---------------------------------------------------------------------------

class TestServiceWorkerRender:
    def test_enthaelt_cache_namen(self):
        body = pwa.render_service_worker("foo-cache-v1", ["/a", "/b"])
        assert "'foo-cache-v1'" in body or '"foo-cache-v1"' in body

    def test_enthaelt_shell_urls(self):
        body = pwa.render_service_worker("c1", ["/x/", "/static/y.css"])
        assert "/x/" in body
        assert "/static/y.css" in body

    def test_install_activate_fetch_handler(self):
        body = pwa.render_service_worker("c1", ["/a"])
        assert "addEventListener('install'" in body
        assert "addEventListener('activate'" in body
        assert "addEventListener('fetch'" in body

    def test_skip_waiting_und_clients_claim(self):
        body = pwa.render_service_worker("c1", ["/a"])
        assert "skipWaiting()" in body
        assert "clients.claim()" in body

    def test_get_only_caching(self):
        body = pwa.render_service_worker("c1", ["/a"])
        # Non-GET-Requests muessen vom Caching ausgenommen sein
        assert "method !== 'GET'" in body


class TestManifestDicts:
    @pytest.mark.parametrize("manifest,prefix,short", [
        (pwa.NALA_MANIFEST, "/nala/", "Nala"),
        (pwa.HEL_MANIFEST, "/hel/", "Hel"),
    ])
    def test_pflichtfelder(self, manifest, prefix, short):
        assert manifest["name"]
        assert manifest["short_name"] == short
        assert manifest["start_url"] == prefix
        assert manifest["scope"] == prefix
        assert manifest["display"] == "standalone"
        assert manifest["theme_color"].startswith("#")
        assert manifest["background_color"].startswith("#")

    @pytest.mark.parametrize("manifest", [pwa.NALA_MANIFEST, pwa.HEL_MANIFEST])
    def test_icons_192_und_512(self, manifest):
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        assert "192x192" in sizes
        assert "512x512" in sizes
        for icon in manifest["icons"]:
            assert icon["type"] == "image/png"
            assert icon["src"].startswith("/static/pwa/")

    def test_themes_unterscheiden_sich(self):
        # Beide Apps muessen visuell unterscheidbare PWAs ergeben
        assert pwa.NALA_MANIFEST["theme_color"] != pwa.HEL_MANIFEST["theme_color"]
        assert pwa.NALA_MANIFEST["short_name"] != pwa.HEL_MANIFEST["short_name"]

    def test_serialisierbar_als_json(self):
        # Wenn das durchlaeuft, kommt der Browser auch durch
        json.dumps(pwa.NALA_MANIFEST)
        json.dumps(pwa.HEL_MANIFEST)


# ---------------------------------------------------------------------------
# 2) Endpoint-Direct-Call-Tests
# ---------------------------------------------------------------------------

class TestManifestEndpoints:
    def test_nala_manifest_endpoint(self):
        from fastapi.responses import JSONResponse

        res = asyncio.run(pwa.nala_manifest())
        assert isinstance(res, JSONResponse)
        assert res.status_code == 200
        assert res.media_type == "application/manifest+json"
        body = json.loads(res.body)
        assert body["short_name"] == "Nala"
        assert body["start_url"] == "/nala/"

    def test_hel_manifest_endpoint(self):
        from fastapi.responses import JSONResponse

        res = asyncio.run(pwa.hel_manifest())
        assert isinstance(res, JSONResponse)
        assert res.status_code == 200
        assert res.media_type == "application/manifest+json"
        body = json.loads(res.body)
        assert body["short_name"] == "Hel"
        assert body["start_url"] == "/hel/"


class TestServiceWorkerEndpoints:
    def test_nala_sw_endpoint(self):
        from fastapi import Response

        res = asyncio.run(pwa.nala_service_worker())
        assert isinstance(res, Response)
        assert res.status_code == 200
        assert res.media_type == "application/javascript"
        body = res.body.decode("utf-8")
        assert "nala-shell-v1" in body
        assert "/nala/" in body
        assert "Cache-Control" in res.headers
        assert res.headers["Cache-Control"] == "no-cache"

    def test_hel_sw_endpoint(self):
        from fastapi import Response

        res = asyncio.run(pwa.hel_service_worker())
        assert isinstance(res, Response)
        assert res.status_code == 200
        assert res.media_type == "application/javascript"
        body = res.body.decode("utf-8")
        assert "hel-shell-v1" in body
        assert "/hel/" in body


# ---------------------------------------------------------------------------
# 3) Source-Audit-Tests fuer NALA_HTML
# ---------------------------------------------------------------------------

class TestNalaHtmlPwaTags:
    def test_manifest_link(self, nala_src):
        assert '<link rel="manifest" href="/nala/manifest.json">' in nala_src

    def test_theme_color(self, nala_src):
        assert '<meta name="theme-color" content="#0a1628">' in nala_src

    def test_apple_capable(self, nala_src):
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in nala_src

    def test_apple_status_bar(self, nala_src):
        assert 'apple-mobile-web-app-status-bar-style' in nala_src

    def test_apple_title(self, nala_src):
        assert '<meta name="apple-mobile-web-app-title" content="Nala">' in nala_src

    def test_apple_touch_icon_192(self, nala_src):
        assert 'apple-touch-icon' in nala_src
        assert '/static/pwa/nala-192.png' in nala_src

    def test_apple_touch_icon_512(self, nala_src):
        assert '/static/pwa/nala-512.png' in nala_src

    def test_sw_registration(self, nala_src):
        assert "navigator.serviceWorker.register('/nala/sw.js'" in nala_src
        assert "scope: '/nala/'" in nala_src


# ---------------------------------------------------------------------------
# 3b) Source-Audit-Tests fuer ADMIN_HTML
# ---------------------------------------------------------------------------

class TestHelHtmlPwaTags:
    def test_manifest_link(self, hel_src):
        assert '<link rel="manifest" href="/hel/manifest.json">' in hel_src

    def test_theme_color(self, hel_src):
        assert '<meta name="theme-color" content="#1a1a1a">' in hel_src

    def test_apple_capable(self, hel_src):
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in hel_src

    def test_apple_title(self, hel_src):
        assert '<meta name="apple-mobile-web-app-title" content="Hel">' in hel_src

    def test_apple_touch_icon_192(self, hel_src):
        assert '/static/pwa/hel-192.png' in hel_src

    def test_apple_touch_icon_512(self, hel_src):
        assert '/static/pwa/hel-512.png' in hel_src

    def test_sw_registration(self, hel_src):
        assert "navigator.serviceWorker.register('/hel/sw.js'" in hel_src
        assert "scope: '/hel/'" in hel_src


# ---------------------------------------------------------------------------
# 4) Asset-Existenz
# ---------------------------------------------------------------------------

class TestPwaIconsExist:
    @pytest.mark.parametrize("filename", [
        "nala-192.png", "nala-512.png",
        "hel-192.png", "hel-512.png",
    ])
    def test_icon_existiert_und_nicht_leer(self, filename):
        path = ICONS_DIR / filename
        assert path.exists(), f"Erwartet: {path}"
        # Sanity: PNG-Magic-Bytes
        head = path.read_bytes()[:8]
        assert head == b"\x89PNG\r\n\x1a\n"
        assert path.stat().st_size > 100, "Icon verdaechtig klein"


# ---------------------------------------------------------------------------
# 5) Routing-Order in main.py — pwa.router VOR hel.router
# ---------------------------------------------------------------------------

class TestRouterOrderInMain:
    def test_pwa_router_importiert(self, main_src):
        assert "from zerberus.app.routers import" in main_src
        assert ", pwa" in main_src or "pwa," in main_src or " pwa\n" in main_src

    def test_pwa_router_eingebunden(self, main_src):
        assert "include_router(pwa.router)" in main_src

    def test_pwa_vor_hel(self, main_src):
        """Sonst greift verify_admin auf /hel/manifest.json + /hel/sw.js."""
        pwa_pos = main_src.find("include_router(pwa.router)")
        hel_pos = main_src.find("include_router(hel.router)")
        assert pwa_pos > 0, "pwa.router nicht eingebunden"
        assert hel_pos > 0, "hel.router nicht eingebunden"
        assert pwa_pos < hel_pos, (
            "pwa.router muss VOR hel.router include_router'ed werden, "
            "sonst auth-gated der Hel-Router /hel/manifest.json + /hel/sw.js."
        )


# ---------------------------------------------------------------------------
# 6) Generator-Skript existiert (fuer Re-Generierung der Icons)
# ---------------------------------------------------------------------------

class TestIconGeneratorScript:
    def test_skript_existiert(self):
        script = ROOT / "scripts" / "generate_pwa_icons.py"
        assert script.exists()

    def test_skript_referenziert_themes(self):
        script = ROOT / "scripts" / "generate_pwa_icons.py"
        text = script.read_text(encoding="utf-8")
        assert "NALA_THEME" in text
        assert "HEL_THEME" in text
        assert "192" in text and "512" in text
