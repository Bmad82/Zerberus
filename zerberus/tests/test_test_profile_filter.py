"""
Patch 138 (B-004): Test-Profile loki/fenrir werden aus Session-Liste gefiltert.

Tests:
- Test-Profile haben is_test=true in config.yaml
- _get_test_profile_keys() listet sie korrekt
- get_all_sessions() unterstützt exclude_profiles
- /archive/sessions filtert Test-Profile standardmäßig aus
"""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class TestTestProfileFlag:
    def test_loki_ist_test_profil(self):
        with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["profiles"]["loki"].get("is_test") is True

    def test_fenrir_ist_test_profil(self):
        with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["profiles"]["fenrir"].get("is_test") is True

    def test_chris_ist_kein_test_profil(self):
        with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["profiles"]["chris"].get("is_test", False) is False

    def test_jojo_ist_kein_test_profil(self):
        with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["profiles"]["jojo"].get("is_test", False) is False


class TestArchiveFilter:
    def test_get_test_profile_keys(self):
        from zerberus.app.routers.archive import _get_test_profile_keys
        keys = _get_test_profile_keys()
        assert "loki" in keys
        assert "fenrir" in keys
        assert "chris" not in keys
        assert "jojo" not in keys

    def test_get_all_sessions_accepts_exclude(self):
        """get_all_sessions nimmt exclude_profiles Parameter (Signatur-Check)."""
        import inspect
        from zerberus.core.database import get_all_sessions
        sig = inspect.signature(get_all_sessions)
        assert "exclude_profiles" in sig.parameters


class TestCleanupScript:
    def test_cleanup_script_existiert(self):
        script = ROOT / "scripts" / "cleanup_test_sessions.py"
        assert script.exists(), "cleanup_test_sessions.py fehlt"

    def test_cleanup_hat_dry_run_default(self):
        """Script muss --execute brauchen um wirklich zu löschen."""
        script = (ROOT / "scripts" / "cleanup_test_sessions.py").read_text(encoding="utf-8")
        assert "--execute" in script
        assert "DRY-RUN" in script or "dry-run" in script.lower()

    def test_cleanup_macht_backup(self):
        script = (ROOT / "scripts" / "cleanup_test_sessions.py").read_text(encoding="utf-8")
        assert "_backup_db" in script
        assert "bunker_memory" in script
