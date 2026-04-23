"""
Patch 129 - Tests fuer die Migrations-Logik.

Die echten Modelle werden NICHT geladen. Wir testen nur die
Sprache-Kategorisierung und das Dry-Run-Verhalten.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _import_migrate_module():
    """Importiert scripts/migrate_embedder.py als Modul."""
    import importlib.util
    script_path = ROOT / "scripts" / "migrate_embedder.py"
    spec = importlib.util.spec_from_file_location("migrate_embedder", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCategorizeByLanguage:
    def test_empty_list(self):
        mod = _import_migrate_module()
        result = mod.categorize_by_language([])
        assert result == {"de": [], "en": []}

    def test_deleted_chunks_skipped(self):
        mod = _import_migrate_module()
        chunks = [
            {"text": "Der Bunker ist dunkel.", "deleted": True},
            {"text": "Das System laeuft stabil."},
        ]
        result = mod.categorize_by_language(chunks)
        # Deleted chunk nicht im Output
        total = sum(len(v) for v in result.values())
        assert total == 1

    def test_german_and_english_bucket(self):
        mod = _import_migrate_module()
        chunks = [
            {"text": "Der Bunker ist der Ort der Wahrheit und der Rosendornen."},
            {"text": "The system architecture includes a FAISS index and a rerank step."},
            {"text": "Die Rosa-Instanz ist mit Nala verwoben und bleibt liebevoll."},
        ]
        result = mod.categorize_by_language(chunks)
        assert len(result["de"]) >= 1
        assert len(result["en"]) >= 1

    def test_empty_text_skipped(self):
        mod = _import_migrate_module()
        chunks = [{"text": "   "}, {"text": "Der Bunker ist der Ort."}]
        result = mod.categorize_by_language(chunks)
        total = sum(len(v) for v in result.values())
        assert total == 1


class TestEmbedAndBuildDryRun:
    def test_dry_run_returns_summary(self):
        mod = _import_migrate_module()
        buckets = {
            "de": [{"text": "Der Bunker."}],
            "en": [{"text": "The bunker."}, {"text": "More text."}],
        }
        summary = mod.embed_and_build(buckets, dry_run=True)
        assert summary["de"]["count"] == 1
        assert summary["en"]["count"] == 2
        # Dry-Run darf keine echten Paths zurueckgeben
        assert summary["de"]["index_path"] == "(dry-run)"
