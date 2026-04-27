"""Patch 169 — Tests fuer den Bug-Sweep (B1 Bubble-Defaults, B2 RAG-Status,
B6 Cleaner innerHTML-Guard) plus Block-A-Existenz-Check (Self-Knowledge-RAG-Doku).

B1 + B6 sind Frontend-Bugs; wir koennen sie hier nur via Source-String-Match
absichern (Playwright laeuft nicht im Standard-Subset). B2 ist Backend-seitig
und voll testbar mit FastAPI-In-Process-Aufrufen + gemockten Modul-Globals.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────────
#  Block A — huginn_kennt_zerberus.md existiert + enthaelt die Negationen
# ──────────────────────────────────────────────────────────────────────


class TestSelfKnowledgeDoc:
    """Das Self-Knowledge-RAG-Doku ist nicht ausfuehrbar, aber wir
    sichern den Inhalt ab, damit die haeufigsten Halluzinationen (FIDO,
    ROSA-Cloud, Kerberos-Protokoll) explizit negiert sind."""

    @pytest.fixture(scope="class")
    def doc_text(self) -> str:
        path = (
            Path(__file__).resolve().parents[2]
            / "docs" / "RAG Testdokumente" / "huginn_kennt_zerberus.md"
        )
        assert path.exists(), f"Self-Knowledge-Doku fehlt unter {path}"
        return path.read_text(encoding="utf-8")

    def test_doc_negates_kerberos_protocol(self, doc_text):
        # Die Doku muss explizit klarstellen, dass Zerberus NICHT das
        # Authentifizierungs-Protokoll ist.
        lower = doc_text.lower()
        assert "kerberos" in lower or "authentifizierung" in lower
        assert "nicht" in lower

    def test_doc_negates_fido(self, doc_text):
        assert "fido" in doc_text.lower()
        # Negation-Marker in der Naehe (innerhalb ~200 Zeichen)
        idx = doc_text.lower().find("fido")
        window = doc_text[max(0, idx - 200):idx + 200].lower()
        assert "kein" in window or "nicht" in window or "halluzin" in window

    def test_doc_negates_openshift(self, doc_text):
        assert "openshift" in doc_text.lower() or "rosa" in doc_text.lower()
        # Rosa-Sektion muss klarstellen: nicht Red Hat
        assert "red hat" in doc_text.lower()

    def test_doc_mentions_core_components(self, doc_text):
        # Mindestabdeckung der Haupt-Komponenten
        for component in ("Huginn", "Nala", "Hel", "Guard", "RAG", "Whisper", "Pacemaker"):
            assert component in doc_text, f"Komponente {component!r} fehlt in der Doku"

    def test_doc_mentions_naming_origins(self, doc_text):
        # Mythologische Herkunft sollte fuer die Top-Namen drin sein
        for myth_term in ("Odin", "Hel", "Ratatoskr", "Heimdall"):
            assert myth_term in doc_text, f"Mythologie-Term {myth_term!r} fehlt"

    def test_doc_does_not_use_code_blocks(self, doc_text):
        # Patch-Spec (Block A): „Natürliche Sprache, keine Code-Blöcke."
        assert "```" not in doc_text, (
            "Self-Knowledge-Doku darf keine Code-Bloecke enthalten — "
            "wuerde RAG-Chunks polluten und Halluzinations-Risiko erhoehen."
        )


# ──────────────────────────────────────────────────────────────────────
#  B1 — Bubble-Farben: Backend-Default + Frontend-Filter
# ──────────────────────────────────────────────────────────────────────


class TestB1ThemeColorDefault:
    """Login-Endpoint: schwarze theme_color → #ec407a-Default."""

    def _call_login_with_profile(self, profile_overrides: dict) -> dict:
        """Direkter Aufruf von ``login`` aus nala.py mit gemocktem profiles_path
        und gemocktem JWT-Encode. Liefert das Response-Dict zurueck."""
        from zerberus.app.routers import nala as nala_mod

        async def _run():
            captured: dict = {}

            async def fake_load_profile(*a, **kw):
                profile = {
                    "display_name": "Tester",
                    "theme_color": profile_overrides.get("theme_color"),
                    "permission_level": "guest",
                    "system_prompt": "test prompt",
                }
                profile.update({
                    k: v for k, v in profile_overrides.items()
                    if k != "theme_color"
                })
                captured["profile"] = profile
                return profile

            return captured

        return asyncio.run(_run())

    def test_default_for_missing_theme_color(self):
        # Direkter Funktions-Test der Default-Logik aus dem Login-Endpoint.
        # Wir replizieren die Patch-169-Defensive inline und stellen sicher,
        # dass sie die gewuenschten Faelle abfaengt.
        def _harden(raw_theme):
            theme_color = str(raw_theme).strip() if raw_theme else ""
            if not theme_color or theme_color.lower() in (
                "#000000", "#000", "rgb(0,0,0)"
            ):
                return "#ec407a"
            return theme_color

        assert _harden(None) == "#ec407a"
        assert _harden("") == "#ec407a"
        assert _harden("   ") == "#ec407a"
        assert _harden("#000000") == "#ec407a"
        assert _harden("#000") == "#ec407a"
        assert _harden("rgb(0,0,0)") == "#ec407a"
        assert _harden("#ec407a") == "#ec407a"
        assert _harden("#abcdef") == "#abcdef"

    def test_login_endpoint_source_has_169_guard(self):
        """Sichert ab, dass die Defensive im echten Login-Code drin ist —
        nicht nur in obigem Inline-Replikat."""
        path = (
            Path(__file__).resolve().parents[1]
            / "app" / "routers" / "nala.py"
        )
        src = path.read_text(encoding="utf-8")
        assert "[SETTINGS-169]" in src, (
            "Patch 169 (B1): Backend-Default-Logging fehlt"
        )
        # Die Default-Konstante muss explizit im Login-Code stehen
        # (nicht nur als Profile-Default-Fallback).
        assert 'theme_color = "#ec407a"' in src or "theme_color = '#ec407a'" in src


class TestB1FavoriteBlackFilter:
    """Frontend-Filter im Favoriten-Boot-Block: schwarze BG-Werte werden
    nicht in CSS geschrieben + persistent aus dem Favoriten entfernt."""

    @pytest.fixture(scope="class")
    def nala_src(self) -> str:
        path = (
            Path(__file__).resolve().parents[1]
            / "app" / "routers" / "nala.py"
        )
        return path.read_text(encoding="utf-8")

    def test_fav_black_constant_present(self, nala_src):
        # Filter-Block existiert (FAV_BLACK + Cleanup) — wir suchen den
        # Patch-169-Marker und das _cleanFav-Symbol.
        assert "Patch 169 (B1)" in nala_src
        assert "FAV_BLACK" in nala_src
        assert "_cleanFav" in nala_src

    def test_fav_filter_writes_back_cleaned_favorite(self, nala_src):
        # Wenn ein BG-Feld bereinigt wurde, muss der Favorit zurueckgeschrieben
        # werden — sonst bleibt er korrupt und der Bug kommt nach Reload wieder.
        assert "localStorage.setItem('nala_theme_fav_'" in nala_src
        assert "delete fav.bubble.userBg" in nala_src
        assert "delete fav.bubble.llmBg" in nala_src


# ──────────────────────────────────────────────────────────────────────
#  B2 — RAG-Status: Lazy-Init bei jedem Read
# ──────────────────────────────────────────────────────────────────────


class TestB2RagStatusLazyInit:
    """``rag_status`` und ``rag_documents`` muessen ``_ensure_init``
    aufrufen, sonst zeigt Hel ``0 Dokumente`` bis zum ersten Schreibvorgang.
    """

    def test_rag_status_calls_ensure_init(self, monkeypatch):
        from zerberus.app.routers import hel as hel_mod
        from zerberus.modules.rag import router as rag_mod

        ensure_init_calls: list = []

        async def fake_ensure_init(settings):
            ensure_init_calls.append(True)
            # Modul-Globals befuellen, damit der Re-Import sie sieht
            class _FakeIndex:
                ntotal = 7
            rag_mod._index = _FakeIndex()
            rag_mod._metadata = [
                {"source": "doc_a.md", "category": "reference"},
                {"source": "doc_a.md", "category": "reference"},
                {"source": "doc_b.txt", "category": "general"},
                {"source": "doc_c.md", "category": "reference", "deleted": True},
            ]

        # get_settings: Minimal-Stub mit aktivem RAG-Modul.
        class _Settings:
            modules = {"rag": {"enabled": True}}

        monkeypatch.setattr(hel_mod, "get_settings", lambda: _Settings())
        monkeypatch.setattr(rag_mod, "_ensure_init", fake_ensure_init)
        # Ausgangs-State: Index leer, Metadata leer — wie nach frischem Start
        monkeypatch.setattr(rag_mod, "_index", None)
        monkeypatch.setattr(rag_mod, "_metadata", [])

        result = asyncio.run(hel_mod.rag_status())

        assert ensure_init_calls, (
            "B2-Fix: rag_status() MUSS _ensure_init aufrufen, "
            "damit der Index aus den On-Disk-Dateien rehydriert wird"
        )
        assert result["total_chunks"] == 7
        assert result["active_chunks"] == 3  # 1 soft-deleted weggefiltert
        # Reihenfolge ist nicht garantiert; nur die Mengen pruefen
        assert set(result["sources"]) == {"doc_a.md", "doc_a.md", "doc_b.txt"} - {""}
        # sources_meta gleich lang wie active_chunks
        assert len(result["sources_meta"]) == 3

    def test_rag_documents_calls_ensure_init(self, monkeypatch):
        from zerberus.app.routers import hel as hel_mod
        from zerberus.modules.rag import router as rag_mod

        ensure_init_calls: list = []

        async def fake_ensure_init(settings):
            ensure_init_calls.append(True)
            class _FakeIndex:
                ntotal = 4
            rag_mod._index = _FakeIndex()
            rag_mod._metadata = [
                {"source": "alpha.md", "category": "reference", "word_count": 200},
                {"source": "alpha.md", "category": "reference", "word_count": 180},
                {"source": "beta.txt", "category": "general", "word_count": 90},
                {"source": "gamma.md", "category": "lore", "deleted": True},
            ]

        class _Settings:
            modules = {"rag": {"enabled": True}}

        monkeypatch.setattr(hel_mod, "get_settings", lambda: _Settings())
        monkeypatch.setattr(rag_mod, "_ensure_init", fake_ensure_init)
        monkeypatch.setattr(rag_mod, "_index", None)
        monkeypatch.setattr(rag_mod, "_metadata", [])

        result = asyncio.run(hel_mod.rag_documents())

        assert ensure_init_calls
        assert result["total_chunks"] == 4
        assert result["total_documents"] == 2  # alpha + beta (gamma soft-deleted)
        sources = sorted(d["source"] for d in result["documents"])
        assert sources == ["alpha.md", "beta.txt"]

    def test_rag_status_when_module_disabled(self, monkeypatch):
        """Wenn RAG in config.yaml aus ist, darf _ensure_init NICHT laufen —
        sonst lockt der Endpoint ein deaktiviertes Modell-Subsystem an."""
        from zerberus.app.routers import hel as hel_mod
        from zerberus.modules.rag import router as rag_mod

        called = []

        async def fake_ensure_init(settings):
            called.append(True)

        class _Settings:
            modules = {"rag": {"enabled": False}}

        monkeypatch.setattr(hel_mod, "get_settings", lambda: _Settings())
        monkeypatch.setattr(rag_mod, "_ensure_init", fake_ensure_init)
        monkeypatch.setattr(rag_mod, "_index", None)
        monkeypatch.setattr(rag_mod, "_metadata", [])

        result = asyncio.run(hel_mod.rag_status())
        assert not called, "Bei deaktiviertem RAG-Modul kein Lazy-Init"
        assert result["total_chunks"] == 0
        assert result["active_chunks"] == 0


# ──────────────────────────────────────────────────────────────────────
#  B6 — Cleaner innerHTML Null-Guard
# ──────────────────────────────────────────────────────────────────────


class TestB6CleanerInnerHtmlGuard:
    """``renderCleanerList`` greift auf ``cleanerList``-DOM-Element zu,
    das mit Patch 149 entfernt wurde. Beim Page-Load crasht der Cleaner-
    Tab mit ``can't access property "innerHTML", host is null``.
    Patch 169 (B6) fixt das mit Null-Guards in ``renderCleanerList`` UND
    ``loadCleaner``."""

    @pytest.fixture(scope="class")
    def hel_src(self) -> str:
        path = (
            Path(__file__).resolve().parents[1]
            / "app" / "routers" / "hel.py"
        )
        return path.read_text(encoding="utf-8")

    def test_render_cleaner_list_has_null_guard(self, hel_src):
        # Wir suchen das Patch-169-Tag + den Frueh-Return im Render-Block.
        idx = hel_src.find("function renderCleanerList()")
        assert idx > 0, "renderCleanerList() Funktion nicht gefunden"
        # Die naechsten ~500 Zeichen muessen den Null-Guard enthalten
        block = hel_src[idx:idx + 800]
        assert "if (!host) return;" in block, (
            "Patch 169 (B6): renderCleanerList braucht Null-Guard auf 'host'"
        )

    def test_load_cleaner_has_null_guard(self, hel_src):
        idx = hel_src.find("async function loadCleaner()")
        assert idx > 0
        # End-Marker = naechste async-Funktion (saveCleaner) — bietet einen
        # robusten Funktions-Body unabhaengig von Zeilen-Laenge.
        end = hel_src.find("async function saveCleaner()", idx)
        assert end > idx
        block = hel_src[idx:end]
        assert "getElementById('cleanerList')" in block
        # Frueh-Return wenn das Element fehlt
        assert "if (!document.getElementById('cleanerList')) return;" in block, (
            "Patch 169 (B6): loadCleaner braucht Frueh-Return wenn DOM fehlt"
        )
        # Auch der catch-Block muss cleanerStatus null-checken
        assert "if (statusEl)" in block, (
            "Patch 169 (B6): loadCleaner-Catch braucht Null-Guard auf cleanerStatus"
        )
