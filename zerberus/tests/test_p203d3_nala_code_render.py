"""Patch 203d-3 — UI-Render im Nala-Frontend fuer Sandbox-Code-Execution.

Schliesst Phase-5a-Ziel #5 endgueltig: nach P203d-1 (Backend-Code-Detection +
Sandbox-Roundtrip) und P203d-2 (Output-Synthese) reicht der Endpunkt das
``code_execution``-Feld in der Chat-Response durch. P203d-3 rendert dieses
Feld im Frontend als Code-Card + Output-Card unter dem Bot-Bubble.

Test-Schichten:

1. **Source-Audit Renderer** — ``renderCodeExecution`` existiert, liest die
   richtigen Felder (language/code/exit_code/stdout/stderr/truncated/error/
   execution_time_ms), haengt unter dem Bot-Bubble (vor Triptychon) ein.
2. **Source-Audit Verdrahtung** — ``sendMessage`` ruft den Renderer mit
   ``data.code_execution`` auf, ``addMessage`` liefert das Wrapper-Element
   zurueck, der Aufruf passiert NACH ``addMessage``.
3. **XSS-Audit** — ``escapeHtml(``-Aufrufe im Renderer-Body min. 4 (lang,
   code, stdout, stderr — error+exit_code zusaetzlich). Helper definiert.
4. **CSS-Audit** — Klassen ``.code-card``/``.output-card`` definiert,
   ``.code-toggle`` hat Mobile-first 44px Touch-Target, Code-Block hat
   ``overflow-x: auto`` (kein horizontal-Overflow-Bruch auf Mobile).
5. **JS-Integrity** — alle inline ``<script>``-Bloecke parsen mit
   ``node --check`` (uebersprungen wenn ``node`` nicht im PATH).
6. **Smoke** — ``GET /nala/`` liefert 200 und enthaelt den neuen Renderer.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
NALA_PY = ROOT / "zerberus" / "app" / "routers" / "nala.py"


@pytest.fixture(scope="module")
def nala_src() -> str:
    return NALA_PY.read_text(encoding="utf-8")


def _renderer_body(src: str) -> str:
    """Body von ``function renderCodeExecution`` als String zurueckgeben."""
    idx = src.find("function renderCodeExecution")
    assert idx > 0, "renderCodeExecution fehlt in nala.py"
    end = src.find("\n    function ", idx + 30)
    assert end > idx, "Funktions-Ende von renderCodeExecution nicht gefunden"
    return src[idx:end]


# ---------------------------------------------------------------------------
# 1) Source-Audit Renderer
# ---------------------------------------------------------------------------


class TestRendererExists:
    def test_function_definiert(self, nala_src):
        assert "function renderCodeExecution" in nala_src

    def test_signatur_zwei_parameter(self, nala_src):
        # Caller (sendMessage) reicht (botWrapper, data.code_execution) durch.
        assert (
            "function renderCodeExecution(wrapperEl, codeExec)" in nala_src
        )


class TestRendererLiestSchemaFelder:
    """P203d-1-Schema: {language, code, exit_code, stdout, stderr,
    execution_time_ms, truncated, error}. Renderer muss alle Felder lesen."""

    def test_liest_code(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.code" in body

    def test_liest_language(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.language" in body

    def test_liest_exit_code(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.exit_code" in body

    def test_liest_stdout(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.stdout" in body

    def test_liest_stderr(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.stderr" in body

    def test_liest_truncated(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.truncated" in body

    def test_liest_error(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.error" in body

    def test_liest_execution_time_ms(self, nala_src):
        body = _renderer_body(nala_src)
        assert "codeExec.execution_time_ms" in body


class TestRendererFallbacks:
    """Renderer skippt sauber bei null/leer/Non-Object-Input und alten
    Backends ohne ``code_execution``-Feld (Backwards-Compat)."""

    def test_null_check_im_eingang(self, nala_src):
        body = _renderer_body(nala_src)
        # !codeExec im Guard-Pfad
        assert "!codeExec" in body

    def test_skip_bei_leerem_code(self, nala_src):
        body = _renderer_body(nala_src)
        # Leerer Code-Block → nichts rendern (sonst leere Code-Card)
        assert "codeStr.trim()" in body and "return" in body


class TestRendererInsertionPoint:
    """Visual-Order: bubble → toolbar → code-card → output-card → triptych."""

    def test_insertbefore_triptych(self, nala_src):
        body = _renderer_body(nala_src)
        # Renderer muss vor dem Triptychon einhaengen, damit die
        # Sentiment-Chips weiter ganz unten sitzen.
        assert "querySelector('.sentiment-triptych')" in body
        assert "insertBefore(codeCard" in body


# ---------------------------------------------------------------------------
# 2) Source-Audit Verdrahtung in sendMessage
# ---------------------------------------------------------------------------


class TestSendMessageVerdrahtung:
    def test_addMessage_returns_wrapper(self, nala_src):
        # addMessage liefert den Wrapper zurueck, sonst kann der Renderer
        # nicht eingehaengt werden. Caller-Pattern: const w = addMessage(...).
        idx = nala_src.find("function addMessage(text, sender, tsOverride)")
        assert idx > 0
        end = nala_src.find("\n    function ", idx + 30)
        body = nala_src[idx:end]
        assert "return wrapper" in body

    def test_caller_uebernimmt_wrapper(self, nala_src):
        # In sendMessage muss der Bot-addMessage-Call den Wrapper an einen
        # Bezeichner binden, damit renderCodeExecution ihn bekommt.
        assert "= addMessage(reply, 'bot')" in nala_src

    def test_renderer_call_in_sendMessage(self, nala_src):
        idx = nala_src.find("async function sendMessage(text)")
        assert idx > 0
        end = nala_src.find("\n    async function ", idx + 30)
        if end < 0:
            end = nala_src.find("\n    function ", idx + 30)
        body = nala_src[idx:end] if end > idx else nala_src[idx:idx + 8000]
        assert "data.code_execution" in body
        assert "renderCodeExecution(" in body

    def test_render_nach_addMessage(self, nala_src):
        # Reihenfolge: addMessage(reply, 'bot') VOR renderCodeExecution(...)
        # — sonst gibt es keinen Wrapper, in den der Renderer einhaengen kann.
        add_idx = nala_src.find("= addMessage(reply, 'bot')")
        render_idx = nala_src.find("renderCodeExecution(botWrapper")
        assert add_idx > 0 and render_idx > 0
        assert add_idx < render_idx, (
            "renderCodeExecution muss NACH addMessage(reply, 'bot') aufgerufen werden"
        )

    def test_renderer_aufruf_failopen(self, nala_src):
        # Renderer-Crash darf den Chat-Loop nicht unterbrechen — try/catch.
        idx = nala_src.find("renderCodeExecution(botWrapper")
        assert idx > 0
        # Suche rueckwaerts ein "try {" auf den letzten Zeilen davor
        window = nala_src[max(0, idx - 200):idx + 200]
        assert "try { renderCodeExecution(" in window
        assert "catch" in window


# ---------------------------------------------------------------------------
# 3) XSS-Audit
# ---------------------------------------------------------------------------


class TestXssEscape:
    def test_escapeHtml_helper_definiert(self, nala_src):
        # Eigener Helper (delegiert an escapeProjectText), damit der
        # Audit-Test ``escapeHtml(``-Aufrufe im Renderer zaehlen kann.
        assert "function escapeHtml(s)" in nala_src

    def test_escapeProjectText_NICHT_geloescht(self, nala_src):
        # P201-Audit zaehlt escapeProjectText — wir haben den Helper nicht
        # ersetzt sondern eine Alias-Funktion daneben.
        assert "function escapeProjectText" in nala_src

    def test_min_count_escapeHtml_im_renderer(self, nala_src):
        body = _renderer_body(nala_src)
        count = body.count("escapeHtml(")
        # lang + code + stdout + stderr + (optional error/exit/exec_time):
        # min. 4 fuer den Happy-Path.
        assert count >= 4, (
            f"escapeHtml(...) nur {count}x in renderCodeExecution — XSS-Risiko"
        )

    def test_keine_innerHTML_ohne_escape(self, nala_src):
        """Heuristik: jeder ``.innerHTML = ...``-Assign im Renderer-Body
        muss entweder ein statisch-konstanter String sein oder einen
        ``escapeHtml(``-Call enthalten."""
        body = _renderer_body(nala_src)
        # Suche alle innerHTML-Statements (vereinfachtes Pattern fuer eine Zeile).
        statements = re.findall(
            r"\.innerHTML\s*=\s*([^;]+);", body, re.DOTALL
        )
        assert statements, "Renderer enthaelt keine innerHTML-Assigns — Test sinnlos"
        for s in statements:
            assert "escapeHtml(" in s, (
                f"innerHTML ohne escapeHtml: {s.strip()[:120]}"
            )


# ---------------------------------------------------------------------------
# 4) CSS-Audit
# ---------------------------------------------------------------------------


class TestCss:
    def test_code_card_klasse(self, nala_src):
        assert ".code-card {" in nala_src or ".code-card{" in nala_src

    def test_output_card_klasse(self, nala_src):
        assert ".output-card {" in nala_src or ".output-card{" in nala_src

    def test_code_toggle_44px_touch(self, nala_src):
        # Mobile-first: Toggle-Button muss min. 44px hoch UND breit sein.
        idx = nala_src.find(".code-toggle {")
        if idx < 0:
            idx = nala_src.find(".code-toggle{")
        assert idx > 0
        end = nala_src.find("}", idx)
        body = nala_src[idx:end]
        assert "min-height: 44px" in body or "min-height:44px" in body
        assert "min-width: 44px" in body or "min-width:44px" in body

    def test_code_content_horizontal_scroll(self, nala_src):
        # Code-Block darf Mobile-Layout nicht sprengen — overflow-x: auto.
        idx = nala_src.find(".code-content {")
        if idx < 0:
            idx = nala_src.find(".code-content{")
        assert idx > 0
        end = nala_src.find("}", idx)
        body = nala_src[idx:end]
        assert "overflow-x: auto" in body or "overflow-x:auto" in body

    def test_collapsed_state_default(self, nala_src):
        # Output-Card startet eingeklappt, damit lange Outputs den Chat
        # nicht ueberschwemmen. Der Toggle expandiert auf Klick.
        assert ".output-card.collapsed .output-card-body" in nala_src

    def test_exit_badge_farbcodes(self, nala_src):
        # exit-ok = gruen, exit-fail = rot — semantischer Status auf Anhieb.
        assert ".exit-badge.exit-ok" in nala_src
        assert ".exit-badge.exit-fail" in nala_src


# ---------------------------------------------------------------------------
# 5) JS-Integrity (node --check) — analog P203b
# ---------------------------------------------------------------------------


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node nicht im PATH")
class TestJsSyntaxIntegrity:
    """Lesson aus P203b: ein einziger SyntaxError invalidiert den ganzen
    inline ``<script>``-Block. Wir lassen ``node --check`` ueber alle Bloecke
    von NALA_HTML laufen, damit der P203d-3-Renderer sauber parst."""

    def test_alle_inline_scripts_parsen(self):
        from zerberus.app.routers.nala import NALA_HTML

        scripts = re.findall(
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
            NALA_HTML,
            re.DOTALL,
        )
        assert scripts, "Keine inline <script>-Bloecke gefunden — Test sinnlos"
        with tempfile.TemporaryDirectory() as td:
            for i, body in enumerate(scripts):
                p = Path(td) / f"nala_script_{i}.js"
                p.write_bytes(body.encode("utf-8", errors="surrogatepass"))
                proc = subprocess.run(
                    ["node", "--check", str(p)],
                    capture_output=True,
                    text=True,
                )
                assert proc.returncode == 0, (
                    f"Inline <script>-Block #{i} hat Syntax-Fehler:\n"
                    f"{proc.stderr}"
                )


# ---------------------------------------------------------------------------
# 6) Smoke: /nala/-Endpoint enthaelt den Renderer
# ---------------------------------------------------------------------------


class TestNalaEndpointSmoke:
    def test_renderer_im_endpoint_response(self):
        import asyncio
        from zerberus.app.routers.nala import nala_interface

        body = asyncio.run(nala_interface())
        assert "function renderCodeExecution" in body
        assert "data.code_execution" in body
        assert ".code-card" in body
        assert ".output-card" in body
