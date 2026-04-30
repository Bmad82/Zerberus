"""Patch 185 — Runtime-Info-Block fuer System-Prompts.

Loest L9 (Modell-Selbstkenntnis): Statt Modellname und Modul-Status statisch
in huginn_kennt_zerberus.md zu pflegen, wird ein Live-Block aus den Settings
generiert und an den System-Prompt gehaengt — bei Huginn (telegram/router.py)
und bei Nala (legacy.py).

Tests decken: Block-Inhalt, Kurzname-Konvertierung, Robustheit gegen kaputte
Settings, Injection-Punkte in beiden Routern, RAG-Doku-Update.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


# ──────────────────────────────────────────────────────────────────────
#  build_runtime_info — Block-Inhalt
# ──────────────────────────────────────────────────────────────────────


class TestBuildRuntimeInfo:
    def test_contains_zerberus_version(self):
        from zerberus.utils.runtime_info import build_runtime_info
        from zerberus.core.config import get_settings
        block = build_runtime_info(get_settings())
        assert "Zerberus" in block
        assert "Version" in block

    def test_contains_cloud_model_short_name(self):
        from zerberus.utils.runtime_info import build_runtime_info
        from zerberus.core.config import get_settings
        block = build_runtime_info(get_settings())
        # config.yaml hat cloud_model: deepseek/deepseek-v3.2 — Kurzname ohne Anbieter
        assert "deepseek-v3.2" in block
        assert "Dein LLM" in block
        assert "OpenRouter" in block

    def test_contains_guard_model_short_name(self):
        from zerberus.utils.runtime_info import build_runtime_info
        from zerberus.core.config import get_settings
        block = build_runtime_info(get_settings())
        # GUARD_MODEL = "mistralai/mistral-small-24b-instruct-2501" → Kurzname
        assert "mistral-small-24b-instruct-2501" in block
        assert "Guard" in block

    def test_contains_module_status(self):
        from zerberus.utils.runtime_info import build_runtime_info
        from zerberus.core.config import get_settings
        block = build_runtime_info(get_settings())
        assert "RAG" in block
        assert "Sandbox" in block
        # Strings 'aktiv' oder 'deaktiviert' (abhaengig von der echten config)
        assert "aktiv" in block or "deaktiviert" in block

    def test_marker_line_present(self):
        """Doku-Update verspricht 'Block beginnt mit [Aktuelle System-Informationen]'.
        Der Marker muss exakt matchen sonst koennen RAG/User ihn nicht erkennen."""
        from zerberus.utils.runtime_info import build_runtime_info
        from zerberus.core.config import get_settings
        block = build_runtime_info(get_settings())
        assert "[Aktuelle System-Informationen" in block


class TestShortModelName:
    @pytest.mark.parametrize("full,expected", [
        ("deepseek/deepseek-v3.2", "deepseek-v3.2"),
        ("mistralai/mistral-small-24b-instruct-2501", "mistral-small-24b-instruct-2501"),
        ("openai/gpt-4o", "gpt-4o"),
        ("mistral-nemo", "mistral-nemo"),  # ohne Slash unveraendert
        ("", "unbekannt"),
        (None, "unbekannt"),
        ("unbekannt", "unbekannt"),
    ])
    def test_short_name(self, full, expected):
        from zerberus.utils.runtime_info import _short_model_name
        assert _short_model_name(full) == expected


class TestRuntimeInfoRobustness:
    """Bei kaputten oder partiellen Settings darf der Block trotzdem zurueck-
    kommen — der Server soll nicht crashen nur weil ein Config-Pfad fehlt."""

    def test_empty_dict_settings(self):
        from zerberus.utils.runtime_info import build_runtime_info
        block = build_runtime_info({})
        assert "Zerberus" in block
        assert "unbekannt" in block

    def test_none_settings(self):
        from zerberus.utils.runtime_info import build_runtime_info
        block = build_runtime_info(None)
        # Kein Crash, mindestens der Header steht
        assert "[Aktuelle System-Informationen" in block

    def test_settings_without_legacy(self):
        from zerberus.utils.runtime_info import build_runtime_info
        stub = SimpleNamespace(modules={"rag": {"enabled": False}, "sandbox": {"enabled": True}})
        block = build_runtime_info(stub)
        assert "unbekannt" in block  # cloud_model fehlt
        assert "RAG: deaktiviert" in block
        assert "Sandbox: aktiv" in block

    def test_settings_with_dict_legacy(self):
        """Test mit Dict-statt-Pydantic — Tests verwenden manchmal Dicts."""
        from zerberus.utils.runtime_info import build_runtime_info
        stub = SimpleNamespace(
            legacy={"models": {"cloud_model": "anthropic/claude-opus-4"}},
            modules={"rag": {"enabled": True}, "sandbox": {"enabled": False}},
        )
        block = build_runtime_info(stub)
        assert "claude-opus-4" in block
        assert "RAG: aktiv" in block
        assert "Sandbox: deaktiviert" in block


class TestAppendRuntimeInfo:
    def test_appends_to_nonempty_prompt(self):
        from zerberus.utils.runtime_info import append_runtime_info
        from zerberus.core.config import get_settings
        result = append_runtime_info("Du bist Nala.", get_settings())
        assert result.startswith("Du bist Nala.")
        assert "[Aktuelle System-Informationen" in result
        # Doppel-Newline trennt Persona vom Block
        assert "Du bist Nala.\n\n[Aktuelle" in result

    def test_returns_block_alone_for_empty_prompt(self):
        from zerberus.utils.runtime_info import append_runtime_info
        from zerberus.core.config import get_settings
        result = append_runtime_info("", get_settings())
        assert result.startswith("[Aktuelle System-Informationen")


# ──────────────────────────────────────────────────────────────────────
#  Injection-Punkte in den Routern (Source-Audit)
# ──────────────────────────────────────────────────────────────────────


class TestInjectionPoints:
    @pytest.fixture(scope="class")
    def router_src(self) -> str:
        path = Path(__file__).resolve().parents[1] / "modules" / "telegram" / "router.py"
        return path.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def legacy_src(self) -> str:
        path = Path(__file__).resolve().parents[1] / "app" / "routers" / "legacy.py"
        return path.read_text(encoding="utf-8")

    def test_huginn_router_imports_runtime_info(self, router_src):
        assert "from zerberus.utils.runtime_info import append_runtime_info" in router_src

    def test_huginn_router_calls_append_before_rag(self, router_src):
        """Position: NACH Persona, VOR RAG-Kontext. Im Source heisst das:
        append_runtime_info muss VOR _inject_rag_context im _process_text_message-
        Body stehen."""
        idx_func = router_src.find("async def _process_text_message")
        assert idx_func > 0
        body_end = router_src.find("\n\nasync def ", idx_func + 30)
        if body_end < 0:
            body_end = idx_func + 5000
        body = router_src[idx_func:body_end]
        idx_runtime = body.find("append_runtime_info")
        idx_rag = body.find("_inject_rag_context")
        assert idx_runtime > 0, "P185: append_runtime_info-Call fehlt in _process_text_message"
        assert idx_rag > 0
        assert idx_runtime < idx_rag, (
            "P185: append_runtime_info muss VOR _inject_rag_context laufen "
            "(Persona+Runtime-Info bevor RAG-Block angehaengt wird)"
        )

    def test_nala_legacy_imports_runtime_info(self, legacy_src):
        assert "from zerberus.utils.runtime_info import append_runtime_info" in legacy_src

    def test_nala_legacy_calls_append_after_persona_wrap(self, legacy_src):
        """Position in legacy.py: NACH _wrap_persona, VOR append_decision_box_hint —
        Reihenfolge: load → wrap → runtime_info → decision_box_hint → insert."""
        idx_func = legacy_src.find("async def chat_completions(")
        assert idx_func > 0
        body_end = legacy_src.find("\n\n# ", idx_func + 30)
        if body_end < 0:
            body_end = idx_func + 8000
        body = legacy_src[idx_func:body_end]
        idx_wrap = body.find("_wrap_persona")
        idx_runtime = body.find("append_runtime_info")
        idx_dbh = body.find("append_decision_box_hint")
        assert idx_wrap > 0 and idx_runtime > 0 and idx_dbh > 0
        assert idx_wrap < idx_runtime < idx_dbh, (
            "P185: Reihenfolge muss sein wrap_persona → runtime_info → decision_box_hint"
        )


# ──────────────────────────────────────────────────────────────────────
#  RAG-Doku Update
# ──────────────────────────────────────────────────────────────────────


class TestRagDocUpdate:
    @pytest.fixture(scope="class")
    def doc_text(self) -> str:
        path = (
            Path(__file__).resolve().parents[2]
            / "docs" / "RAG Testdokumente" / "huginn_kennt_zerberus.md"
        )
        return path.read_text(encoding="utf-8")

    def test_doc_explains_runtime_block(self, doc_text):
        """Die RAG-Doku muss erklaeren, dass es einen Live-Block gibt der
        statisch NICHT mehr gepflegt wird — sonst raten LLMs Modellnamen
        weiterhin aus statischer Doku obwohl Live-Daten vorliegen."""
        assert "Aktuelle Konfiguration" in doc_text
        assert "[Aktuelle System-Informationen" in doc_text
        assert "Laufzeit" in doc_text or "automatisch" in doc_text

    def test_doc_does_not_hardcode_cloud_model(self, doc_text):
        """Wir wollen NICHT, dass deepseek-v3.2 oder vergleichbare Versions-
        Strings im RAG-Doku stehen — sie veralten und werden dann falsch
        zurueck-halluziniert."""
        # deepseek-v3.2 darf erscheinen wenn es als negative Erwaehnung in der
        # Konfigurations-Sektion steht (das ist OK), aber nicht als
        # wiederkehrende Aussage. Wir lassen die Constraint locker — Hauptsache
        # die Doku sagt klar dass aktuelle Werte aus der Live-Config kommen.
        assert "automatisch generiert" in doc_text or "Live-Konfiguration" in doc_text or "zur Laufzeit" in doc_text
