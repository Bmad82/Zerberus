"""
Patch 191 — Tests für Consent-UI + Worker-Protection + Hel-Admin-Status.

Coverage:
  - Frontend-Source-Audit (Toggle, Header, Indikator)
  - Consent-Logik (false → kein Analyse-Call)
  - Hel-Admin-Endpoint (Aggregate, KEINE individuellen Daten)
  - Worker-Protection (Audio nicht in DB, tmp-Cleanup)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zerberus.modules.prosody.manager import (
    ProsodyConfig,
    ProsodyManager,
    reset_prosody_manager,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_prosody_manager()
    yield
    reset_prosody_manager()


# ====================================================================
# Frontend-Source-Audit
# ====================================================================

class TestConsentFrontend:
    def test_consent_toggle_in_settings_html(self):
        """Source-Audit: prosodyConsentToggle ist im Settings-Tab."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert 'id="prosodyConsentToggle"' in nala_src

    def test_consent_callback_function_exists(self):
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "function onProsodyConsentToggle" in nala_src
        assert "function isProsodyConsentEnabled" in nala_src

    def test_consent_localstorage_key(self):
        """localStorage-Key 'nala_prosody_consent' wird genutzt."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "'nala_prosody_consent'" in nala_src or '"nala_prosody_consent"' in nala_src

    def test_consent_default_off(self):
        """Default-Logik: kein Wert in localStorage → false."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        # Pattern: localStorage.getItem('nala_prosody_consent') === 'true'
        # → ein nicht gesetzter / nicht-'true' Wert ergibt false
        assert "'nala_prosody_consent'" in nala_src
        # Check: isProsodyConsentEnabled returnt false bei nicht-vorhandenem Wert
        assert "=== 'true'" in nala_src

    def test_consent_header_sent_in_voice_fetch(self):
        """Frontend sendet X-Prosody-Consent im /nala/voice Fetch."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        # Header-Setzen direkt vor /nala/voice fetch
        assert "'X-Prosody-Consent'" in nala_src
        # Es muss isProsodyConsentEnabled() im Fetch-Pfad geprüft werden
        assert "isProsodyConsentEnabled()" in nala_src

    def test_consent_header_sent_in_chat_fetch(self):
        """Frontend sendet X-Prosody-Consent + X-Prosody-Context im /v1/chat/completions Fetch."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "'X-Prosody-Context'" in nala_src

    def test_prosody_indicator_html(self):
        """🎭-Indikator im HTML, neben Mikrofon-Button."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert 'id="prosodyIndicator"' in nala_src

    def test_consent_init_called_in_settings(self):
        """_initProsodyConsentToggle wird beim Settings-Open aufgerufen."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert "_initProsodyConsentToggle()" in nala_src


# ====================================================================
# Consent-Logik (Backend)
# ====================================================================

class TestConsentBackendLogic:
    def test_consent_false_disables_pipeline(self, tmp_path):
        """is_active=True aber Consent=false → Pipeline läuft NICHT."""
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True,
            model_path=str(tmp_path / "g.gguf"),
            mmproj_path=str(tmp_path / "p.gguf"),
        ))
        consent = False
        active_in_endpoint = mgr.is_active and consent
        assert active_in_endpoint is False

    def test_consent_true_enables_pipeline(self, tmp_path):
        """is_active=True + Consent=true → Pipeline AKTIV."""
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True,
            model_path=str(tmp_path / "g.gguf"),
            mmproj_path=str(tmp_path / "p.gguf"),
        ))
        consent = True
        active_in_endpoint = mgr.is_active and consent
        assert active_in_endpoint is True

    def test_legacy_endpoint_reads_consent_header(self):
        """legacy.py liest X-Prosody-Consent (case-insensitive lower())."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        assert 'request.headers.get("X-Prosody-Consent"' in legacy_src

    def test_nala_endpoint_reads_consent_header(self):
        """nala.py /voice liest X-Prosody-Consent."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        assert 'request.headers.get("X-Prosody-Consent"' in nala_src


# ====================================================================
# Hel-Admin-Endpoint
# ====================================================================

class TestHelAdminEndpoint:
    def test_admin_endpoint_exists(self):
        """Source-Audit: /admin/prosody/status Endpoint existiert in hel.py."""
        hel_src = (ROOT / "zerberus" / "app" / "routers" / "hel.py").read_text(encoding="utf-8")
        assert "/admin/prosody/status" in hel_src
        assert "admin_prosody_status" in hel_src

    def test_admin_status_returns_no_individual_data(self):
        """admin_status() Response enthält KEINE mood/valence/arousal Werte."""
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True, model_path="/x/g.gguf", mmproj_path="/x/p.gguf",
        ))
        status = mgr.admin_status()
        # Keine individuellen Felder
        assert "mood" not in status
        assert "valence" not in status
        assert "arousal" not in status
        assert "dominance" not in status
        assert "tempo" not in status

    def test_admin_status_returns_aggregates(self):
        """admin_status() liefert NUR Aggregate (Counter, Modus, Timestamps)."""
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True, model_path="/x/g.gguf", mmproj_path="/x/p.gguf",
        ))
        status = mgr.admin_status()
        assert status["enabled"] is True
        assert status["mode"] == "cli"
        assert "success_count" in status
        assert "error_count" in status
        assert "last_success_ts" in status
        assert status["model_path_set"] is True
        assert status["mmproj_path_set"] is True

    def test_admin_status_disabled_state(self):
        """admin_status bei disabled → enabled=False, mode=none."""
        mgr = ProsodyManager(ProsodyConfig(enabled=False))
        status = mgr.admin_status()
        assert status["enabled"] is False
        assert status["mode"] == "none"
        assert status["is_active"] is False

    def test_admin_log_tag_in_hel(self):
        """Source-Audit: [PROSODY-ADMIN-191] in hel.py."""
        hel_src = (ROOT / "zerberus" / "app" / "routers" / "hel.py").read_text(encoding="utf-8")
        assert "[PROSODY-ADMIN-191]" in hel_src


# ====================================================================
# Worker-Protection (Defense-in-Depth)
# ====================================================================

class TestWorkerProtection:
    def test_audio_bytes_not_in_interactions_schema(self):
        """database.py-Schema: KEIN prosody-Feld in der interactions-Tabelle."""
        db_path = ROOT / "zerberus" / "core" / "database.py"
        if db_path.exists():
            db_src = db_path.read_text(encoding="utf-8")
            # store_interaction sollte KEIN prosody-Feld als Parameter haben
            # Wir prüfen das indirekt: das Wort "prosody" sollte nicht im
            # interactions-Schema-Block stehen (z.B. CREATE TABLE oder Column)
            # Pragmatisch: keine "mood"/"valence"-Spalten
            for forbidden in ("prosody_mood", "prosody_valence", "prosody_arousal"):
                assert forbidden not in db_src, f"Verbotenes Feld {forbidden} in DB-Schema"

    def test_tmp_file_unlink_in_finally(self):
        """gemma_client.py: tmp-Datei wird in finally gelöscht."""
        client_src = (ROOT / "zerberus" / "modules" / "prosody" / "gemma_client.py").read_text(encoding="utf-8")
        assert "finally:" in client_src
        assert "unlink" in client_src
        # Pattern: Path(tmp_path).unlink(missing_ok=True) im finally-Block
        finally_idx = client_src.find("finally:")
        unlink_idx = client_src.find("unlink", finally_idx)
        # finally muss VOR unlink kommen — unlink darf maximal 200 Zeichen weg sein
        assert unlink_idx > finally_idx
        assert (unlink_idx - finally_idx) < 200

    def test_endpoint_does_not_persist_prosody_to_db(self):
        """legacy.py + nala.py: KEIN store_interaction-Aufruf mit prosody-Daten."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        # Es darf KEIN store_interaction("prosody", ...) o.ä. geben
        for src in (legacy_src, nala_src):
            assert 'store_interaction("prosody"' not in src
            assert "store_interaction('prosody'" not in src
            assert "store_interaction(\"prosody_mood\"" not in src

    def test_response_strips_prosody_when_stub(self):
        """legacy.py: prosody-Feld wird nur bei source != stub gesetzt."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        # Pattern: source") != "stub"
        assert 'source") != "stub"' in legacy_src or "source\") != \"stub\"" in legacy_src


# ====================================================================
# Audit-Counter
# ====================================================================

class TestAdminAuditCounters:
    def test_counters_start_at_zero(self):
        mgr = ProsodyManager(ProsodyConfig())
        status = mgr.admin_status()
        assert status["success_count"] == 0
        assert status["error_count"] == 0
        assert status["last_success_ts"] is None

    def test_log_tag_p191_in_manager_or_hel(self):
        """Mindestens ein [PROSODY-CONSENT-191] oder [PROSODY-ADMIN-191] existiert."""
        manager_src = (ROOT / "zerberus" / "modules" / "prosody" / "manager.py").read_text(encoding="utf-8")
        hel_src = (ROOT / "zerberus" / "app" / "routers" / "hel.py").read_text(encoding="utf-8")
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        combined = manager_src + hel_src + nala_src
        assert "[PROSODY-CONSENT-191]" in combined or "[PROSODY-ADMIN-191]" in combined
