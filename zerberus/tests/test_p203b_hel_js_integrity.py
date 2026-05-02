"""Patch 203b — Hel-UI-Hotfix: JS-Integrity-Tests.

Bug-Kontext (Chris, 2026-05-02): Hel rendert, aber NICHTS ist anklickbar
(Tabs wechseln nicht, Buttons reagieren nicht). Nala laeuft normal.

Root-Cause: Im ``loadProjectFiles``-Renderer (eingefuehrt mit P196) stand:

    + ',\\'' + _escapeHtml(f.relative_path).replace(/'/g, "\\\\'") + '\\')" '

In Python's triple-quoted Source ergibt das nach Escape:

    + ',''  + _escapeHtml(f.relative_path).replace(/'/g, "\\'") + '')" '

JavaScript parst ``+ ',''`` als zwei adjacent String-Literale ohne Operator
und wirft ``SyntaxError: Unexpected string``. Ein einziger Syntax-Fehler in
einem ``<script>``-Block invalidiert den GANZEN Block — alle Funktionen
(inkl. ``activateTab``) werden nicht definiert. Symptom: keine Klicks.

Warum erst nach P200-P202 sichtbar? Vor P200 hatte der Browser-HTTP-Cache
(oder vor SW-v2-Roll-Out: ein zwischengeschalteter SW-v1) die fehlerfreie
Hel-Variante. P200/P202 + Cache-Wipe haben das durchgereicht.

Fix: Inline ``onclick`` durch ``data-*``-Attribute + Event-Delegation
ersetzt — XSS-sicher und immun gegen Quote-Escape-Probleme.

Tests:
1. **Source-Audit** — die kaputten Quote-Patterns sind weg.
2. **Source-Audit** — die neue Event-Delegation ist da.
3. **JS-Integrity** — alle inline ``<script>``-Bloecke parsen mit
   ``node --check`` (uebersprungen, wenn ``node`` nicht im PATH).
4. **Smoke** — der Endpunkt liefert das HTML mit dem Fix.
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


# ---------------------------------------------------------------------------
# 1) Source-Audit: Bug-Pattern abwesend
# ---------------------------------------------------------------------------

class TestBrokenQuotePatternAbsent:
    """Sicherstellen, dass das kaputte Quote-Pattern nicht zurueckkehrt."""

    def test_kein_inline_onclick_mit_deleteProjectFile(self):
        # Vor P203b: '<button onclick="deleteProjectFile(' + ... — diese Form
        # ist anfaellig fuer Quote-Escape-Bugs. Nach P203b: data-* + delegation.
        assert "onclick=\"deleteProjectFile(" not in hel.ADMIN_HTML, (
            "Inline onclick mit deleteProjectFile ist zurueck — "
            "P203b: data-Attribute + addEventListener verwenden."
        )

    def test_kein_kaputtes_quote_pattern_im_rendered_output(self):
        # Direktes Symptom: ``+ ','' +`` im Rendered-JS (drei adjacent quotes)
        # ist ein JS-Syntax-Error. Erscheint, wenn Python-Source `,\''` statt
        # `,\\\''` schreibt.
        # Wir suchen die exakte Sequenz im rendered HTML.
        assert "+ ',''" not in hel.ADMIN_HTML, (
            "Kaputtes ``+ ',''``-Pattern im rendered HTML — "
            "Python-Quote-Escape ist falsch."
        )

    def test_kein_replace_mit_unescapter_backslash_quote(self):
        # `replace(/'/g, "\\'")` im rendered JS ist ein No-Op (JS interpretiert
        # `"\\'"` als 1-char string `'`). Vor P203b war das ein zusaetzlicher
        # XSS-Vektor. Nach P203b nicht mehr noetig — wir verwenden data-attrs.
        assert 'replace(/\'/g, "\\\'")' not in hel.ADMIN_HTML


# ---------------------------------------------------------------------------
# 2) Source-Audit: Neue Event-Delegation
# ---------------------------------------------------------------------------

class TestEventDelegationPresent:
    """P203b: Event-Delegation auf .proj-file-delete-btn."""

    def test_delete_button_class_exists(self):
        assert "proj-file-delete-btn" in hel.ADMIN_HTML

    def test_data_project_id_attribute(self):
        assert "data-project-id" in hel.ADMIN_HTML

    def test_data_file_id_attribute(self):
        assert "data-file-id" in hel.ADMIN_HTML

    def test_data_relative_path_attribute(self):
        assert "data-relative-path" in hel.ADMIN_HTML

    def test_addEventListener_auf_delete_btn(self):
        # Der Selektor + addEventListener-Pattern muss im Source stehen.
        assert ".proj-file-delete-btn" in hel.ADMIN_HTML
        # Die Delegation soll click events binden.
        # Nicht zu strikt — Source-Form kann variieren — aber beide Begriffe
        # muessen in derselben Funktion vorkommen.
        idx_class = hel.ADMIN_HTML.find(".proj-file-delete-btn")
        nearby = hel.ADMIN_HTML[idx_class : idx_class + 600]
        assert "addEventListener" in nearby
        assert "deleteProjectFile" in nearby


# ---------------------------------------------------------------------------
# 3) JS-Integrity: rendered HTML parst sauber durch node --check
# ---------------------------------------------------------------------------

def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node nicht im PATH")
class TestJsSyntaxIntegrity:
    """Wenn node verfuegbar ist: jeder inline <script>-Block muss parsen.

    Ein einzelner SyntaxError invalidiert den gesamten Block — und damit
    alle Funktionen darin (inkl. ``activateTab``). Genau das war der P203b-
    Bug. Dieser Test schlaegt frueh an, bevor er live wieder auftaucht.
    """

    def test_alle_inline_scripts_parsen(self):
        html = hel.ADMIN_HTML
        # Nur inline scripts (kein src=...).
        scripts = re.findall(
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
            html,
            re.DOTALL,
        )
        assert scripts, "Keine inline <script>-Bloecke gefunden — Test sinnlos"

        with tempfile.TemporaryDirectory() as td:
            for i, body in enumerate(scripts):
                path = Path(td) / f"hel_script_{i}.js"
                path.write_bytes(body.encode("utf-8", errors="surrogatepass"))
                proc = subprocess.run(
                    ["node", "--check", str(path)],
                    capture_output=True,
                    text=True,
                )
                assert proc.returncode == 0, (
                    f"Inline <script>-Block #{i} hat Syntax-Fehler:\n"
                    f"{proc.stderr}"
                )


# ---------------------------------------------------------------------------
# 4) Smoke-Test: Endpunkt liefert HTML mit dem Fix
# ---------------------------------------------------------------------------

class TestHelEndpointSmoke:
    """Hel-Endpunkt rendert das gefixte HTML aus."""

    def test_admin_html_in_endpoint_response(self):
        # Der Endpunkt ruft _sanitize_unicode(ADMIN_HTML) auf — also muss
        # die proj-file-delete-btn-Klasse im Response-Body stehen, sobald
        # ADMIN_HTML sie enthaelt.
        sanitized = hel._sanitize_unicode(hel.ADMIN_HTML)
        assert "proj-file-delete-btn" in sanitized
        assert "data-relative-path" in sanitized
        # Kein inline onclick mit deleteProjectFile mehr
        assert 'onclick="deleteProjectFile(' not in sanitized
