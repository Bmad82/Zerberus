"""Patch 205 — RAG-Toast in Hel-UI (Phase 5a Schuld aus P199).

Kontext: ``POST /hel/admin/projects/{id}/files`` retourniert seit P199 ein
``rag``-Dict im Response-Body (``{chunks, skipped, reason}``). Das Hel-
Frontend hat das Feld bisher ignoriert — der User sah nicht, ob die Datei
indiziert wurde, wieviel Chunks gebaut wurden oder warum sie geskippt wurde.

P205 baut einen Toast unten rechts, der nach jedem File-Upload kurz
auftaucht. Mobile-first 44px Touch-Target, fail-quiet wenn `body.rag`
fehlt (Backwards-Compat zu Backends, die das Feld nicht haben).

Tests:
- Renderer-Definition (`function _showRagToast(rag)`)
- Reason-Mapping (`too_large` → "zu gross" etc.)
- CSS (`.rag-toast` mit `min-height: 44px`, `position: fixed`)
- DOM (`<div id="ragToast" ...>`)
- Verdrahtung im Upload-onload (NACH dem "fertig"-Render)
- XSS-Sanity (`textContent` oder `_escapeHtml` im Renderer-Body)
- JS-Integrity (`node --check` ueber alle inline scripts)
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from zerberus.app.routers import hel


ROOT = Path(__file__).resolve().parents[2]
HEL_PY = ROOT / "zerberus" / "app" / "routers" / "hel.py"


def _src() -> str:
    return HEL_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Renderer-Definition
# ---------------------------------------------------------------------------

class TestToastFunctionExists:
    def test_show_rag_toast_defined(self):
        assert "function _showRagToast" in _src()

    def test_takes_rag_param(self):
        assert re.search(r"function\s+_showRagToast\s*\(\s*rag\s*\)", _src())


# ---------------------------------------------------------------------------
# Reason-Mapping (de-DE Strings fuer die Codes aus index_project_file)
# ---------------------------------------------------------------------------

class TestReasonMapping:
    REASON_CODES = [
        "too_large",
        "binary",
        "empty",
        "no_chunks",
        "embed_failed",
    ]

    @pytest.mark.parametrize("code", REASON_CODES)
    def test_reason_code_present(self, code):
        # Code-Literal taucht im Mapping auf (Quoting beliebig)
        src = _src()
        assert (
            f"'{code}'" in src
            or f'"{code}"' in src
        ), f"Reason-Code {code!r} fehlt im Toast-Mapping"

    def test_too_large_label_de(self):
        # 'too_large' → "zu gross" (Hauptbeispiel aus HANDOVER)
        m = re.search(
            r"['\"]too_large['\"]\s*:\s*['\"]zu gross['\"]",
            _src(),
        )
        assert m, "too_large muss auf 'zu gross' gemappt sein"


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

class TestRagToastCss:
    def test_class_defined(self):
        assert ".rag-toast" in _src()

    def test_min_height_44px(self):
        # Mobile-first: 44px Touch-Target zum Dismiss
        m = re.search(
            r"\.rag-toast\b[^}]*\{[^}]*min-height:\s*44px",
            _src(),
            re.DOTALL,
        )
        assert m, "rag-toast braucht min-height: 44px (Mobile-first)"

    def test_position_fixed(self):
        m = re.search(
            r"\.rag-toast\b[^}]*\{[^}]*position:\s*fixed",
            _src(),
            re.DOTALL,
        )
        assert m, "rag-toast braucht position: fixed"

    def test_has_visible_state(self):
        # Toggle-Klasse, damit show/hide nicht durch innerHTML-Wegnehmen
        # gemacht wird (CSS-Transition + leiser Renderer)
        src = _src()
        assert (
            ".rag-toast.visible" in src
            or ".rag-toast.show" in src
            or re.search(r"\.rag-toast\.\w+", src) is not None
        )


# ---------------------------------------------------------------------------
# Toast-DOM-Element
# ---------------------------------------------------------------------------

class TestToastDom:
    def test_toast_div_exists(self):
        assert re.search(r'id="ragToast"', _src())


# ---------------------------------------------------------------------------
# Verdrahtung im Upload-Pfad
# ---------------------------------------------------------------------------

class TestUploadWiring:
    def _upload_block(self) -> str:
        # Greife den _uploadProjectFiles-Body anhand der expliziten Patch-196-
        # Block-Marker. Klammern-Counten in einem Regex ist heikel, weil die
        # Funktion verschachtelte Closures/Promises enthaelt — die Marker sind
        # robuster und exakt scoped.
        src = _src()
        start = src.find("function _uploadProjectFiles")
        end = src.find("/Patch 196", start)
        assert start != -1, "function _uploadProjectFiles nicht gefunden"
        assert end != -1, "/Patch 196 End-Marker nicht gefunden"
        return src[start:end]

    def test_body_rag_referenced(self):
        # Im Upload-Block wird `body.rag` aus der geparsten XHR-Response gelesen
        block = self._upload_block()
        assert "body.rag" in block, "Upload muss body.rag aus xhr.responseText lesen"

    def test_show_rag_toast_called_in_upload(self):
        block = self._upload_block()
        assert re.search(r"_showRagToast\s*\(", block), (
            "_showRagToast muss im Upload-Block aufgerufen werden"
        )

    def test_toast_after_fertig_render(self):
        # Reihenfolge: erst Status-Zeile aktualisieren ('fertig'), dann Toast.
        # Sonst flackert der Toast bevor die Progress-Liste den Erfolg zeigt.
        block = self._upload_block()
        idx_fertig = block.find("fertig")
        idx_toast = block.find("_showRagToast")
        assert idx_fertig != -1, "Erwartet 'fertig'-Marker im Upload-Block"
        assert idx_toast != -1, "Erwartet _showRagToast-Aufruf im Upload-Block"
        assert idx_toast > idx_fertig, (
            "_showRagToast muss NACH dem 'fertig'-Render aufgerufen werden"
        )


# ---------------------------------------------------------------------------
# XSS-Sanity im Toast-Renderer
# ---------------------------------------------------------------------------

class TestToastXss:
    def _renderer_body(self) -> str:
        # _showRagToast lebt im Patch-196-Block und endet mit der naechsten
        # Top-Level-Funktion (`function _uploadProjectFiles`). Greife den
        # Bereich dazwischen — verlaesslich gegen Verschachtelungen.
        src = _src()
        start = src.find("function _showRagToast")
        end = src.find("function _uploadProjectFiles", start)
        assert start != -1, "function _showRagToast nicht gefunden"
        assert end != -1, "function _uploadProjectFiles nicht gefunden"
        return src[start:end]

    def test_no_unsafe_inner_html(self):
        # Reason kommt vom Server — wir vertrauen ihm zwar, aber der Toast
        # MUSS entweder textContent nutzen ODER _escapeHtml fuer den Reason-
        # String aufrufen, sonst ist der DOM-Zweig XSS-anfaellig.
        body = self._renderer_body()
        used_safe = ("textContent" in body) or ("_escapeHtml" in body)
        assert used_safe, (
            "_showRagToast muss textContent oder _escapeHtml verwenden — "
            "kein nackes innerHTML mit Reason-String"
        )


# ---------------------------------------------------------------------------
# JS-Integrity: Alle inline <script>-Bloecke parsen
# ---------------------------------------------------------------------------

class TestJsSyntaxIntegrity:
    @pytest.mark.skipif(
        shutil.which("node") is None,
        reason="node nicht im PATH — JS-Integrity-Check uebersprungen",
    )
    def test_all_inline_scripts_parse(self):
        # Lesson aus P203b/P203d-3: ein einzelner SyntaxError invalidiert
        # den gesamten <script>-Block. Daher pruefen wir jeden Block einzeln.
        src = _src()
        scripts = re.findall(r"<script>([\s\S]*?)</script>", src)
        assert scripts, "Mindestens ein <script>-Block muss existieren"
        for i, body in enumerate(scripts):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8"
            ) as f:
                f.write(body)
                path = f.name
            try:
                r = subprocess.run(
                    ["node", "--check", path],
                    capture_output=True, text=True, timeout=15,
                )
                assert r.returncode == 0, (
                    f"Inline-Script #{i} parse-fail:\n{r.stderr}\n"
                    f"--- erste 200 Zeichen ---\n{body[:200]}"
                )
            finally:
                Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Smoke gegen ADMIN_HTML
# ---------------------------------------------------------------------------

class TestHelHtmlSmoke:
    def test_admin_html_contains_toast_pieces(self):
        html = hel.ADMIN_HTML
        assert ".rag-toast" in html
        assert "function _showRagToast" in html
        assert 'id="ragToast"' in html
        assert "body.rag" in html

    def test_admin_html_no_double_toast_div(self):
        # Falls jemand spaeter den Toast-DIV doppelt einfuegt, faellt der
        # Test sofort auf — mehrere `id="ragToast"` ist invalides HTML.
        count = hel.ADMIN_HTML.count('id="ragToast"')
        assert count == 1, f"Erwartet genau ein <div id=\"ragToast\">, gefunden {count}"
