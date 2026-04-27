"""Patch 170 — Tests fuer Hel-UI Kosmetik-Sweep (B3, B4, B5).

Source-Inspection-Tests fuer die UI-Aenderungen + ein funktionaler Test
fuer den neuen Einzel-Report-Endpoint.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────
# B3 — Provider-Blacklist: Dropdown statt Freitext
# ──────────────────────────────────────────────────────────────────────


class TestB3ProviderDropdown:
    def test_known_providers_konstante_existiert(self, hel_src):
        assert "KNOWN_PROVIDERS" in hel_src
        # Mindestens ein paar bekannte Provider muessen drin sein
        assert "'targon'" in hel_src
        assert "'chutes'" in hel_src
        assert "'deepinfra'" in hel_src

    def test_dropdown_statt_freitext(self, hel_src):
        assert 'id="newProviderSelect"' in hel_src
        assert 'class="zb-select"' in hel_src

    def test_custom_option_fuer_neue_provider(self, hel_src):
        assert "__custom__" in hel_src
        assert "Benutzerdefiniert" in hel_src

    def test_buildProviderSelect_funktion_existiert(self, hel_src):
        assert "function buildProviderSelect" in hel_src
        assert "function onProviderSelectChange" in hel_src

    def test_chip_layout_fuer_eintraege(self, hel_src):
        # Chips sind inline-flex, max 32px hoch, wrappen auf Mobile
        assert "provider-chip" in hel_src
        assert "max-height:32px" in hel_src
        assert "flex-wrap:wrap" in hel_src


# ──────────────────────────────────────────────────────────────────────
# B4 — Dialekte: Gruppe loeschen weniger dominant
# ──────────────────────────────────────────────────────────────────────


class TestB4DialectDeleteButton:
    def test_kein_grosser_roter_button_mehr(self, hel_src):
        # Die alte Variante hatte 'Gruppe löschen' als Text + dunkelrot Background
        assert "'🗑 Gruppe löschen'" not in hel_src
        # Die neue Variante: nur 🗑️ als Icon
        assert "delGroup.textContent = '🗑️'" in hel_src

    def test_button_ist_28px_icon(self, hel_src):
        block = hel_src.split("delGroup.textContent = '🗑️'")[1][:1500]
        assert "width:28px" in block
        assert "height:28px" in block

    def test_tooltip_via_title(self, hel_src):
        block = hel_src.split("delGroup.textContent = '🗑️'")[1][:1500]
        assert "Gruppe löschen" in block

    def test_confirm_dialog_vorhanden(self, hel_src):
        block = hel_src.split("delGroup.textContent = '🗑️'")[1][:1500]
        assert "confirm(" in block

    def test_opacity_gedaempft_im_default(self, hel_src):
        block = hel_src.split("delGroup.textContent = '🗑️'")[1][:1500]
        assert "opacity:0.5" in block


# ──────────────────────────────────────────────────────────────────────
# B5 — Test-Reports einzeln verlinkbar
# ──────────────────────────────────────────────────────────────────────


class TestB5TestReports:
    def test_endpoint_fuer_einzelreports_existiert(self, hel_src):
        assert '/tests/report/{name}' in hel_src
        assert "tests_report_named" in hel_src

    def test_whitelist_verhindert_path_traversal(self, hel_src):
        assert "_ALLOWED_REPORT_NAMES" in hel_src
        assert "fenrir_report" in hel_src
        assert "loki_report" in hel_src

    def test_kryptische_meldung_entfernt(self, hel_src):
        # Die alte Meldung "(nur full_report verlinkbar)" darf nicht mehr aktiv
        # gerendert werden — alle bekannten Reports kriegen jetzt einen Link.
        # Der Fallback fuer unbekannte Files ist freundlicher formuliert.
        assert "(Teil des Gesamtreports)" in hel_src

    def test_frontend_baut_einzel_links(self, hel_src):
        # Der JS-Block referenziert /hel/tests/report/<stem>
        assert "/hel/tests/report/' + stem" in hel_src


class TestB5EndpointFunctional:
    """Direkter Aufruf des neuen Endpoints (kein TestClient noetig)."""

    def test_unbekannter_report_liefert_404(self):
        from zerberus.app.routers.hel import tests_report_named

        resp = asyncio.run(tests_report_named("evil_../../etc/passwd"))
        assert resp.status_code == 404

    def test_bekannter_aber_fehlender_report_liefert_404(self, tmp_path, monkeypatch):
        from zerberus.app.routers import hel as hel_mod

        monkeypatch.setattr(hel_mod, "_REPORT_DIR", tmp_path)
        resp = asyncio.run(hel_mod.tests_report_named("fenrir_report"))
        assert resp.status_code == 404

    def test_bekannter_report_wird_ausgeliefert(self, tmp_path, monkeypatch):
        from zerberus.app.routers import hel as hel_mod

        (tmp_path / "fenrir_report.html").write_text(
            "<html><body>Fenrir OK</body></html>", encoding="utf-8"
        )
        monkeypatch.setattr(hel_mod, "_REPORT_DIR", tmp_path)
        resp = asyncio.run(hel_mod.tests_report_named("fenrir_report"))
        assert resp.status_code == 200
        body = resp.body.decode("utf-8")
        assert "Fenrir OK" in body

    def test_loki_report_wird_ausgeliefert(self, tmp_path, monkeypatch):
        from zerberus.app.routers import hel as hel_mod

        (tmp_path / "loki_report.html").write_text(
            "<html><body>Loki OK</body></html>", encoding="utf-8"
        )
        monkeypatch.setattr(hel_mod, "_REPORT_DIR", tmp_path)
        resp = asyncio.run(hel_mod.tests_report_named("loki_report"))
        assert resp.status_code == 200
        assert "Loki OK" in resp.body.decode("utf-8")
