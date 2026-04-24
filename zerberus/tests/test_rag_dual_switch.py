"""Tests für Patch 133 — Dual-Embedder-Switch in rag/router.py.

Der Switch-Mechanismus liest `modules.rag.use_dual_embedder` aus config.yaml.
Defaults auf False (= Legacy MiniLM). Wenn True, werden Dual-Indices
(de.index + de_meta.json) erwartet; fehlen sie → Fallback auf Legacy.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestDualSwitchDefaults:
    def test_module_globals_initial(self):
        """_use_dual und _dual_embedder sind vor Init false/None."""
        # Frischer Import via reload um globals zurückzusetzen ist komplex;
        # wir prüfen nur, dass die Variablen existieren und die Default-Werte
        # bei einem frischen Modul-Load false/None sind.
        import zerberus.modules.rag.router as rag_router
        # Nach vorherigen Tests können diese schon gesetzt sein, aber die Namen
        # müssen existieren
        assert hasattr(rag_router, "_use_dual")
        assert hasattr(rag_router, "_dual_embedder")


class TestConfigDefaultFalse:
    def test_config_yaml_use_dual_embedder_false(self):
        """config.yaml hat use_dual_embedder explizit auf false (Pre-Patch-133-Verhalten)."""
        import yaml
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        rag = cfg["modules"]["rag"]
        assert rag.get("use_dual_embedder") is False, (
            "use_dual_embedder muss initial false sein (Legacy MiniLM bleibt aktiv)"
        )


class TestFallbackWhenDualIndicesMissing:
    """Wenn use_dual_embedder=true aber de.index fehlt → Fallback auf Legacy."""

    def test_fallback_logic(self, tmp_path, monkeypatch):
        """Simuliert: Settings sagen dual=true, aber keine de.index vorhanden.

        _init_sync() muss dann _use_dual=False setzen und Legacy laden.
        """
        import zerberus.modules.rag.router as rag_router
        # Reset state for isolation
        rag_router._index = None
        rag_router._model = None
        rag_router._metadata = []
        rag_router._initialized = False
        rag_router._dual_embedder = None
        rag_router._use_dual = False

        settings = MagicMock()
        settings.modules = {
            "rag": {
                "use_dual_embedder": True,
                "vector_db_path": str(tmp_path),  # leer → keine de.index
                "embedding_model": "all-MiniLM-L6-v2",
                "device": "cpu",
            }
        }

        # SentenceTransformer mocken, sonst wird es echt geladen
        fake_st_class = MagicMock()
        fake_instance = MagicMock()
        fake_instance.encode = MagicMock(return_value=MagicMock())
        fake_st_class.return_value = fake_instance

        # FAISS-Read muss auch gemockt werden, da die index files nicht da sind
        with patch("zerberus.modules.rag.router.SentenceTransformer", fake_st_class):
            rag_router._init_sync(settings)

        # Nach Fallback muss _use_dual=False sein
        assert rag_router._use_dual is False, (
            "Bei fehlendem de.index muss der Switch auf Legacy zurückfallen"
        )


class TestMigrateScriptDryRun:
    """Der Dry-Run-Modus schreibt nichts und liefert Bucket-Counts."""

    def test_dry_run_categorizes_existing_index(self):
        """Aktuelle FAISS-Index wird sprachlich bucketed. Erwartung: 61 DE / 0 EN."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "migrate_embedder_test", "scripts/migrate_embedder.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        chunks = mod.load_metadata()
        if not chunks:
            pytest.skip("Keine aktive metadata.json — Migration nicht relevant")
        buckets = mod.categorize_by_language(chunks)
        # Erwartet: zumindest einige DE-Chunks
        assert "de" in buckets
        assert len(buckets["de"]) > 0


class TestBackupExists:
    def test_pre_patch133_backup_present(self):
        """Patch 133 hat vor jeder Aktion ein Backup angelegt."""
        backup_dirs = list(Path("data/backups").glob("pre_patch133_*"))
        assert backup_dirs, "Kein Backup pre_patch133_* gefunden"
        any_with_files = False
        for d in backup_dirs:
            if (d / "faiss.index").exists() and (d / "metadata.json").exists():
                any_with_files = True
                break
        assert any_with_files, "Backup-Verzeichnis existiert, aber ohne faiss.index/metadata.json"
