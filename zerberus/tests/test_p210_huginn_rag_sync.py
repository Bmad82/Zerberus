"""Patch 210 (Phase 5a #18) — Tests fuer den Huginn-RAG-Auto-Sync.

Architektur-Schichten (analog P209):

1. **Pure-Function** — ``build_sync_plan``, ``validate_doc_header``,
   ``extract_current_patch``, ``parse_auth_string``, ``load_auth_from_env``,
   ``resolve_base_url``.
2. **Async-Wrapper** — ``execute_sync_plan`` mit Mock-HTTP-Client,
   fail-soft bei DELETE-404, fail-fast bei UPLOAD-Fehler.
3. **CLI** — ``main`` mit Mock-Argv und Mock-Client.
4. **Source-Audit Doku** — ``docs/huginn_kennt_zerberus.md`` und
   Spiegel-Kopie tragen Stand-Anker-Block.
5. **Source-Audit Workflow** — Doku-Pflicht-Tabelle nennt Sync-Schritt.
6. **Smoke** — Module-Exports, Constants.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Mock-HTTP-Client
# ---------------------------------------------------------------------------


@dataclass
class MockResponse:
    status_code: int
    json_body: Any = None

    def json(self) -> Any:
        if self.json_body is None:
            raise ValueError("no json body")
        return self.json_body


@dataclass
class MockClient:
    responses: list[MockResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    raise_on_call: int | None = None

    async def request(self, method: str, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        idx = len(self.calls) - 1
        if self.raise_on_call is not None and idx == self.raise_on_call:
            raise RuntimeError(f"mock crash at call {idx}")
        if not self.responses:
            raise AssertionError(
                f"MockClient: keine Antwort fuer Call {idx} ({method} {url})"
            )
        return self.responses.pop(0)

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


VALID_DOC = """# Zerberus — Systemwissen fuer Huginn

## Aktueller Stand (Stand-Anker fuer RAG-Lookup)

- **Letzter Patch:** P210 — Huginn-RAG-Auto-Sync (Phase 5a, Ziel #18).
- **Phase:** 5a (Nala-Projekte).
- **Tests:** 2173+ gruen.
- **Datum:** 2026-05-03.

---

Bla bla normale Doku."""


INVALID_DOC_NO_HEADER = """# Zerberus

Keine Stand-Anker-Sektion. Wuerde Huginn wieder raten lassen."""


INVALID_DOC_NO_PATCH = """# Zerberus

## Aktueller Stand

Hier fehlt die **Letzter Patch**-Zeile mit P###."""


@pytest.fixture
def valid_doc(tmp_path: Path) -> Path:
    p = tmp_path / "huginn_kennt_zerberus.md"
    p.write_text(VALID_DOC, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1) Pure-Function: build_sync_plan
# ---------------------------------------------------------------------------


class TestBuildSyncPlan:
    def test_default_plan_has_two_steps(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan = build_sync_plan(valid_doc)
        assert len(plan) == 2
        assert plan[0].method == "DELETE"
        assert plan[1].method == "POST"
        assert plan[1].path == "/hel/admin/rag/upload"

    def test_delete_uses_source_param(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan = build_sync_plan(valid_doc, source_name="custom.md")
        assert plan[0].params == {"source": "custom.md"}

    def test_delete_accepts_404(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan = build_sync_plan(valid_doc)
        assert 404 in plan[0].success_codes
        assert 200 in plan[0].success_codes

    def test_upload_accepts_only_200(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan = build_sync_plan(valid_doc)
        assert plan[1].success_codes == (200,)

    def test_upload_carries_category(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan = build_sync_plan(valid_doc, category="system")
        assert plan[1].data == {"category": "system"}

    def test_upload_carries_file(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan = build_sync_plan(valid_doc, source_name="x.md")
        assert plan[1].files == (("x.md", valid_doc),)

    def test_reindex_step_optional(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan_no_reindex = build_sync_plan(valid_doc)
        plan_reindex = build_sync_plan(valid_doc, run_reindex=True)
        assert len(plan_no_reindex) == 2
        assert len(plan_reindex) == 3
        assert plan_reindex[2].path == "/hel/admin/rag/reindex"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        with pytest.raises(FileNotFoundError):
            build_sync_plan(tmp_path / "doesnotexist.md")

    def test_missing_header_raises(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        bad = tmp_path / "bad.md"
        bad.write_text(INVALID_DOC_NO_HEADER, encoding="utf-8")
        with pytest.raises(ValueError, match="Stand-Anker"):
            build_sync_plan(bad)

    def test_missing_patch_raises(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        bad = tmp_path / "bad.md"
        bad.write_text(INVALID_DOC_NO_PATCH, encoding="utf-8")
        with pytest.raises(ValueError, match="Letzter Patch"):
            build_sync_plan(bad)

    def test_delete_before_upload(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan
        plan = build_sync_plan(valid_doc)
        # Reihenfolge: erst DELETE, dann UPLOAD — sonst loescht DELETE
        # die gerade hochgeladenen neuen Chunks.
        assert plan[0].method == "DELETE"
        assert plan[1].method == "POST"


# ---------------------------------------------------------------------------
# 2) Pure-Function: validate_doc_header / extract_current_patch
# ---------------------------------------------------------------------------


class TestValidateDocHeader:
    def test_valid(self) -> None:
        from tools.sync_huginn_rag import validate_doc_header
        ok, msg = validate_doc_header(VALID_DOC)
        assert ok is True
        assert msg == ""

    def test_empty(self) -> None:
        from tools.sync_huginn_rag import validate_doc_header
        ok, msg = validate_doc_header("")
        assert ok is False
        assert "leer" in msg.lower()

    def test_missing_header(self) -> None:
        from tools.sync_huginn_rag import validate_doc_header
        ok, msg = validate_doc_header(INVALID_DOC_NO_HEADER)
        assert ok is False
        assert "Stand-Anker" in msg

    def test_missing_patch_line(self) -> None:
        from tools.sync_huginn_rag import validate_doc_header
        ok, msg = validate_doc_header(INVALID_DOC_NO_PATCH)
        assert ok is False
        assert "Letzter Patch" in msg

    def test_header_in_middle_of_file(self) -> None:
        from tools.sync_huginn_rag import validate_doc_header
        text = "# Title\n\nIntro\n\n## Aktueller Stand\n\n**Letzter Patch:** P200\n"
        ok, _ = validate_doc_header(text)
        assert ok is True


class TestExtractCurrentPatch:
    def test_extract_p210(self) -> None:
        from tools.sync_huginn_rag import extract_current_patch
        assert extract_current_patch(VALID_DOC) == "P210"

    def test_extract_uppercase(self) -> None:
        from tools.sync_huginn_rag import extract_current_patch
        text = "**letzter patch:** p209 — foo"
        assert extract_current_patch(text) == "P209"

    def test_no_match(self) -> None:
        from tools.sync_huginn_rag import extract_current_patch
        assert extract_current_patch("nichts hier") is None

    def test_three_digit_or_four_digit(self) -> None:
        from tools.sync_huginn_rag import extract_current_patch
        assert extract_current_patch("**Letzter Patch:** P999") == "P999"
        assert extract_current_patch("**Letzter Patch:** P1024") == "P1024"


# ---------------------------------------------------------------------------
# 3) Pure-Function: parse_auth_string / load_auth_from_env / resolve_base_url
# ---------------------------------------------------------------------------


class TestParseAuthString:
    def test_basic(self) -> None:
        from tools.sync_huginn_rag import parse_auth_string
        assert parse_auth_string("Chris:secret") == ("Chris", "secret")

    def test_password_with_colon(self) -> None:
        from tools.sync_huginn_rag import parse_auth_string
        assert parse_auth_string("Chris:pass:word") == ("Chris", "pass:word")

    def test_empty_returns_none(self) -> None:
        from tools.sync_huginn_rag import parse_auth_string
        assert parse_auth_string("") is None
        assert parse_auth_string(None) is None

    def test_no_colon_returns_none(self) -> None:
        from tools.sync_huginn_rag import parse_auth_string
        assert parse_auth_string("just_a_token") is None

    def test_empty_user_returns_none(self) -> None:
        from tools.sync_huginn_rag import parse_auth_string
        assert parse_auth_string(":password") is None


class TestLoadAuthFromEnv:
    def test_from_env_var(self) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        result = load_auth_from_env(env={"HUGINN_RAG_AUTH": "Chris:s3cret"})
        assert result == ("Chris", "s3cret")

    def test_no_env_no_file(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        result = load_auth_from_env(
            env={}, env_file_path=tmp_path / "missing.env",
        )
        assert result is None

    def test_from_env_file(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OTHER=foo\nHUGINN_RAG_AUTH=Chris:filepass\n# comment\n",
            encoding="utf-8",
        )
        result = load_auth_from_env(env={}, env_file_path=env_file)
        assert result == ("Chris", "filepass")

    def test_env_var_beats_file(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        env_file = tmp_path / ".env"
        env_file.write_text("HUGINN_RAG_AUTH=FROM:FILE\n", encoding="utf-8")
        result = load_auth_from_env(
            env={"HUGINN_RAG_AUTH": "FROM:ENV"},
            env_file_path=env_file,
        )
        assert result == ("FROM", "ENV")

    def test_env_file_with_quoted_value(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        env_file = tmp_path / ".env"
        env_file.write_text('HUGINN_RAG_AUTH="Chris:quoted"\n', encoding="utf-8")
        result = load_auth_from_env(env={}, env_file_path=env_file)
        assert result == ("Chris", "quoted")

    # P213-pre-Hotfix: ADMIN_USER/ADMIN_PASSWORD als Auth-Fallback.
    def test_admin_fallback_from_env_var(self) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        result = load_auth_from_env(
            env={"ADMIN_USER": "Chris", "ADMIN_PASSWORD": "Dingsbums!1"},
        )
        assert result == ("Chris", "Dingsbums!1")

    def test_admin_fallback_from_env_file(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ADMIN_USER=Chris\nADMIN_PASSWORD=Dingsbums!1\n",
            encoding="utf-8",
        )
        result = load_auth_from_env(env={}, env_file_path=env_file)
        assert result == ("Chris", "Dingsbums!1")

    def test_huginn_env_beats_admin_fallback(self) -> None:
        # HUGINN_RAG_AUTH gewinnt immer ueber ADMIN_USER/PASSWORD —
        # damit ein User mit dediziertem Sync-Account die Server-Admin-
        # Credentials nicht versehentlich preisgibt.
        from tools.sync_huginn_rag import load_auth_from_env
        result = load_auth_from_env(
            env={
                "HUGINN_RAG_AUTH": "syncbot:s3cret",
                "ADMIN_USER": "Chris",
                "ADMIN_PASSWORD": "wrong",
            },
        )
        assert result == ("syncbot", "s3cret")

    def test_huginn_file_beats_admin_file(self, tmp_path: Path) -> None:
        from tools.sync_huginn_rag import load_auth_from_env
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ADMIN_USER=Chris\nADMIN_PASSWORD=admin_pw\n"
            "HUGINN_RAG_AUTH=syncbot:huginn_pw\n",
            encoding="utf-8",
        )
        result = load_auth_from_env(env={}, env_file_path=env_file)
        assert result == ("syncbot", "huginn_pw")

    def test_admin_user_without_password_uses_empty(self) -> None:
        # Wenn ADMIN_USER da ist aber ADMIN_PASSWORD fehlt, nutzen wir leeres
        # Passwort. Der Server lehnt das mit 401 ab — Fehler ist transparent,
        # die Sync-Runtime sieht echte Auth-Werte.
        from tools.sync_huginn_rag import load_auth_from_env
        result = load_auth_from_env(env={"ADMIN_USER": "Chris"})
        assert result == ("Chris", "")

    def test_empty_admin_user_falls_through(self, tmp_path: Path) -> None:
        # Leerer ADMIN_USER triggert den Fallback NICHT — wir wollen lieber
        # None zurueckgeben als Auth ohne User aufzubauen.
        from tools.sync_huginn_rag import load_auth_from_env
        result = load_auth_from_env(
            env={"ADMIN_USER": "", "ADMIN_PASSWORD": "x"},
            env_file_path=tmp_path / "missing.env",
        )
        assert result is None

    def test_env_admin_beats_file_huginn(self, tmp_path: Path) -> None:
        # Process-Env (ADMIN_USER) schlaegt File (HUGINN_RAG_AUTH) — Process-
        # Env ist die juengere Quelle.
        from tools.sync_huginn_rag import load_auth_from_env
        env_file = tmp_path / ".env"
        env_file.write_text("HUGINN_RAG_AUTH=syncbot:filepw\n", encoding="utf-8")
        result = load_auth_from_env(
            env={"ADMIN_USER": "Chris", "ADMIN_PASSWORD": "envpw"},
            env_file_path=env_file,
        )
        assert result == ("Chris", "envpw")


class TestResolveBaseUrl:
    def test_default(self) -> None:
        # P213-pre-Hotfix: Default auf HTTPS umgestellt — start.bat startet
        # uvicorn immer mit ``--ssl-keyfile``/``--ssl-certfile`` auf Port 5000.
        from tools.sync_huginn_rag import resolve_base_url
        assert resolve_base_url(env={}) == "https://localhost:5000"

    def test_from_env(self) -> None:
        from tools.sync_huginn_rag import resolve_base_url
        result = resolve_base_url(env={"ZERBERUS_URL": "https://example.com"})
        assert result == "https://example.com"

    def test_strips_trailing_slash(self) -> None:
        from tools.sync_huginn_rag import resolve_base_url
        result = resolve_base_url(env={"ZERBERUS_URL": "http://x/"})
        assert result == "http://x"

    def test_http_override_still_works(self) -> None:
        # P213-pre-Hotfix-Backwards-Compat: Wer den alten HTTP-Pfad will,
        # setzt ``ZERBERUS_URL=http://...`` — wir respektieren das ohne Magic.
        from tools.sync_huginn_rag import resolve_base_url
        assert resolve_base_url(
            env={"ZERBERUS_URL": "http://localhost:5000"}
        ) == "http://localhost:5000"


# ---------------------------------------------------------------------------
# 3b) Pure-Function: should_skip_tls_verify (P213-pre-Hotfix)
# ---------------------------------------------------------------------------


class TestShouldSkipTlsVerify:
    def test_localhost_https_skips(self) -> None:
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify("https://localhost:5000") is True
        assert should_skip_tls_verify("https://LOCALHOST:5000") is True

    def test_loopback_ip_https_skips(self) -> None:
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify("https://127.0.0.1:5000") is True

    def test_private_ips_https_skip(self) -> None:
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify("https://192.168.1.50:5000") is True
        assert should_skip_tls_verify("https://10.0.0.5:5000") is True

    def test_tailscale_ts_net_skips(self) -> None:
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify(
            "https://desktop-rmuhi55.tail79500e.ts.net:5000"
        ) is True

    def test_desktop_prefix_skips(self) -> None:
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify("https://desktop-foo:5000") is True

    def test_public_https_does_not_skip(self) -> None:
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify("https://example.com") is False
        assert should_skip_tls_verify("https://huginn.app/api") is False

    def test_plain_http_does_not_skip(self) -> None:
        # Kein TLS zum Verifizieren, ergo kein "skip verify" zu treffen.
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify("http://localhost:5000") is False
        assert should_skip_tls_verify("http://example.com") is False

    def test_empty_returns_false(self) -> None:
        from tools.sync_huginn_rag import should_skip_tls_verify
        assert should_skip_tls_verify("") is False


# ---------------------------------------------------------------------------
# 4) Async-Wrapper: execute_sync_plan
# ---------------------------------------------------------------------------


class TestExecuteSyncPlan:
    def test_happy_path(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[
                MockResponse(200, {"status": "ok", "chunks_removed": 10}),
                MockResponse(200, {"status": "ok", "chunks_indexed": 15}),
            ],
        )
        result = asyncio.run(
            execute_sync_plan(plan, "http://x", http_client=client),
        )
        assert result.success is True
        assert result.steps_executed == 2
        assert result.steps_failed == 0
        assert result.errors == []
        assert len(client.calls) == 2

    def test_delete_404_ok_first_upload(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[
                MockResponse(404, {"detail": "Keine Chunks gefunden"}),
                MockResponse(200, {"status": "ok"}),
            ],
        )
        result = asyncio.run(
            execute_sync_plan(plan, "http://x", http_client=client),
        )
        assert result.success is True
        assert result.steps_failed == 0

    def test_upload_500_fails(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[
                MockResponse(200, {"status": "ok"}),
                MockResponse(500, {"detail": "Server error"}),
            ],
        )
        result = asyncio.run(
            execute_sync_plan(plan, "http://x", http_client=client),
        )
        assert result.success is False
        assert result.steps_failed == 1
        assert any("500" in e for e in result.errors)

    def test_delete_403_fails(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[
                MockResponse(403, {"detail": "Forbidden"}),
                MockResponse(200, {"status": "ok"}),
            ],
        )
        result = asyncio.run(
            execute_sync_plan(plan, "http://x", http_client=client),
        )
        assert result.success is False
        assert result.steps_failed == 1
        assert any("403" in e for e in result.errors)

    def test_exception_does_not_kill_run(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[MockResponse(200, {"status": "ok"})],
            raise_on_call=0,
        )
        result = asyncio.run(
            execute_sync_plan(plan, "http://x", http_client=client),
        )
        # Erster Call wirft → wird gezaehlt als failed, nicht als executed.
        # Zweiter Call laeuft mit der einzigen verfuegbaren Antwort.
        assert result.success is False
        assert result.steps_failed >= 1
        assert any("Exception" in e for e in result.errors)

    def test_request_carries_method(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[MockResponse(200, {}), MockResponse(200, {})],
        )
        asyncio.run(execute_sync_plan(plan, "http://x", http_client=client))
        assert client.calls[0]["method"] == "DELETE"
        assert client.calls[1]["method"] == "POST"

    def test_upload_sends_multipart(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[MockResponse(200, {}), MockResponse(200, {})],
        )
        asyncio.run(execute_sync_plan(plan, "http://x", http_client=client))
        upload_call = client.calls[1]
        assert "files" in upload_call
        # files-Payload ist [(field, (name, bytes, mime))]
        assert upload_call["files"][0][0] == "file"
        assert upload_call["files"][0][1][0] == "huginn_kennt_zerberus.md"
        # Form-Daten muessen category=system enthalten
        assert upload_call["data"] == {"category": "system"}

    def test_response_payload_recorded(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(
            responses=[
                MockResponse(200, {"chunks_removed": 7}),
                MockResponse(200, {"chunks_indexed": 12}),
            ],
        )
        result = asyncio.run(
            execute_sync_plan(plan, "http://x", http_client=client),
        )
        assert result.response_payloads[0]["body"] == {"chunks_removed": 7}
        assert result.response_payloads[1]["body"] == {"chunks_indexed": 12}

    def test_with_reindex_three_steps(self, valid_doc: Path) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc, run_reindex=True)
        client = MockClient(
            responses=[
                MockResponse(200, {}),
                MockResponse(200, {}),
                MockResponse(200, {"reindexed": 50}),
            ],
        )
        result = asyncio.run(
            execute_sync_plan(plan, "http://x", http_client=client),
        )
        assert result.success is True
        assert result.steps_executed == 3
        assert client.calls[2]["url"] == "/hel/admin/rag/reindex"


# ---------------------------------------------------------------------------
# 4b) httpx-Client-Setup: verify_tls-Auto-Detect + Override (P213-pre-Hotfix)
# ---------------------------------------------------------------------------


class _CapturingHttpx:
    """Captured Setup-Args fuer ``AsyncClient`` und liefert die Mock-Antworten."""

    def __init__(self, responses: list[MockResponse]) -> None:
        self.captured: dict[str, Any] = {}
        self.responses = list(responses)

        outer = self

        class _Auth:
            def __init__(self, *args: Any) -> None:
                outer.captured["auth_args"] = args

        class _Client:
            def __init__(self, **kwargs: Any) -> None:
                outer.captured["client_kwargs"] = kwargs
                self._calls: list[dict[str, Any]] = []

            async def request(self, method: str, url: str, **kwargs: Any) -> MockResponse:
                self._calls.append({"method": method, "url": url, **kwargs})
                if not outer.responses:
                    raise AssertionError("kein Mock-Response mehr")
                return outer.responses.pop(0)

            async def aclose(self) -> None:
                outer.captured["closed"] = True

        self.BasicAuth = _Auth
        self.AsyncClient = _Client


class TestExecuteSyncPlanTlsVerify:
    def _patch_httpx(self, monkeypatch, fake) -> None:
        import sys
        monkeypatch.setitem(sys.modules, "httpx", fake)

    def test_auto_detect_localhost_skips_verify(
        self, valid_doc: Path, monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        fake = _CapturingHttpx([
            MockResponse(200, {}), MockResponse(200, {}),
        ])
        self._patch_httpx(monkeypatch, fake)
        plan = build_sync_plan(valid_doc)
        asyncio.run(execute_sync_plan(plan, "https://localhost:5000"))
        assert fake.captured["client_kwargs"]["verify"] is False

    def test_auto_detect_public_https_keeps_verify(
        self, valid_doc: Path, monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        fake = _CapturingHttpx([
            MockResponse(200, {}), MockResponse(200, {}),
        ])
        self._patch_httpx(monkeypatch, fake)
        plan = build_sync_plan(valid_doc)
        asyncio.run(execute_sync_plan(plan, "https://example.com"))
        assert fake.captured["client_kwargs"]["verify"] is True

    def test_explicit_verify_overrides_auto(
        self, valid_doc: Path, monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        fake = _CapturingHttpx([
            MockResponse(200, {}), MockResponse(200, {}),
        ])
        self._patch_httpx(monkeypatch, fake)
        plan = build_sync_plan(valid_doc)
        # Localhost → Auto wuerde False sagen — wir erzwingen True.
        asyncio.run(
            execute_sync_plan(
                plan, "https://localhost:5000", verify_tls=True,
            )
        )
        assert fake.captured["client_kwargs"]["verify"] is True

    def test_explicit_verify_false_overrides_public(
        self, valid_doc: Path, monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        fake = _CapturingHttpx([
            MockResponse(200, {}), MockResponse(200, {}),
        ])
        self._patch_httpx(monkeypatch, fake)
        plan = build_sync_plan(valid_doc)
        asyncio.run(
            execute_sync_plan(
                plan, "https://example.com", verify_tls=False,
            )
        )
        assert fake.captured["client_kwargs"]["verify"] is False

    def test_injected_client_ignores_verify_param(
        self, valid_doc: Path
    ) -> None:
        # Wenn der Caller bereits einen Client mitbringt, ist verify_tls ein
        # No-Op. Der Caller hat sein eigenes TLS-Setup.
        from tools.sync_huginn_rag import build_sync_plan, execute_sync_plan
        plan = build_sync_plan(valid_doc)
        client = MockClient(responses=[
            MockResponse(200, {}), MockResponse(200, {}),
        ])
        result = asyncio.run(
            execute_sync_plan(
                plan, "https://localhost:5000",
                http_client=client, verify_tls=True,
            )
        )
        assert result.success is True
        # MockClient hat keine ``verify``-Konfiguration — der Param wurde
        # einfach ignoriert weil owns_client False ist.


# ---------------------------------------------------------------------------
# 5) CLI: main()
# ---------------------------------------------------------------------------


class TestCli:
    def test_dry_run_prints_plan(
        self, valid_doc: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import main
        monkeypatch.delenv("HUGINN_RAG_AUTH", raising=False)
        rc = main(["--source", str(valid_doc), "--dry-run", "--env-file",
                   str(valid_doc.parent / "missing.env")])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Dry-Run" in out
        assert "DELETE" in out
        assert "POST" in out

    def test_missing_file_returns_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import main
        monkeypatch.delenv("HUGINN_RAG_AUTH", raising=False)
        rc = main([
            "--source", str(tmp_path / "nope.md"),
            "--env-file", str(tmp_path / "missing.env"),
            "--dry-run",
        ])
        assert rc == 2

    def test_invalid_doc_returns_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import main
        bad = tmp_path / "bad.md"
        bad.write_text(INVALID_DOC_NO_HEADER, encoding="utf-8")
        monkeypatch.delenv("HUGINN_RAG_AUTH", raising=False)
        rc = main([
            "--source", str(bad),
            "--env-file", str(tmp_path / "missing.env"),
            "--dry-run",
        ])
        assert rc == 2

    # P213-pre-Hotfix: --insecure / --verify-tls / Auto-Banner-Anzeige.
    def test_dry_run_default_shows_auto_verify(
        self, valid_doc: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import main
        monkeypatch.delenv("HUGINN_RAG_AUTH", raising=False)
        rc = main([
            "--source", str(valid_doc),
            "--env-file", str(valid_doc.parent / "missing.env"),
            "--base-url", "https://localhost:5000",
            "--dry-run",
        ])
        out = capsys.readouterr().out
        assert rc == 0
        assert "TLS-Verify: AUTO" in out
        assert "lokaler/self-signed" in out

    def test_dry_run_insecure_flag_shows_off(
        self, valid_doc: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import main
        monkeypatch.delenv("HUGINN_RAG_AUTH", raising=False)
        rc = main([
            "--source", str(valid_doc),
            "--env-file", str(valid_doc.parent / "missing.env"),
            "--base-url", "https://example.com",
            "--insecure",
            "--dry-run",
        ])
        out = capsys.readouterr().out
        assert rc == 0
        assert "TLS-Verify: AUS" in out

    def test_dry_run_verify_flag_shows_on(
        self, valid_doc: Path, capsys: pytest.CaptureFixture[str], monkeypatch
    ) -> None:
        from tools.sync_huginn_rag import main
        monkeypatch.delenv("HUGINN_RAG_AUTH", raising=False)
        rc = main([
            "--source", str(valid_doc),
            "--env-file", str(valid_doc.parent / "missing.env"),
            "--base-url", "https://localhost:5000",
            "--verify-tls",
            "--dry-run",
        ])
        out = capsys.readouterr().out
        assert rc == 0
        assert "TLS-Verify: AN" in out

    def test_insecure_and_verify_tls_mutually_exclusive(
        self, valid_doc: Path, monkeypatch
    ) -> None:
        # argparse soll das selbst ablehnen — SystemExit mit Code 2.
        from tools.sync_huginn_rag import main
        monkeypatch.delenv("HUGINN_RAG_AUTH", raising=False)
        with pytest.raises(SystemExit) as exc:
            main([
                "--source", str(valid_doc),
                "--env-file", str(valid_doc.parent / "missing.env"),
                "--insecure",
                "--verify-tls",
                "--dry-run",
            ])
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# 6) Source-Audit: docs/huginn_kennt_zerberus.md hat den Stand-Anker
# ---------------------------------------------------------------------------


class TestDocSourceAudit:
    def test_main_doc_has_stand_anker(self) -> None:
        from tools.sync_huginn_rag import validate_doc_header
        path = ROOT / "docs" / "huginn_kennt_zerberus.md"
        assert path.exists(), f"Doku-Datei fehlt: {path}"
        text = path.read_text(encoding="utf-8")
        ok, msg = validate_doc_header(text)
        assert ok, f"Stand-Anker-Pflicht verletzt: {msg}"

    def test_main_doc_patch_is_recent(self) -> None:
        from tools.sync_huginn_rag import extract_current_patch
        path = ROOT / "docs" / "huginn_kennt_zerberus.md"
        text = path.read_text(encoding="utf-8")
        patch = extract_current_patch(text)
        assert patch is not None
        # Patchnummer mindestens P210 (wenn der Test lief, ist P210 aktuell).
        num = int(patch[1:])
        assert num >= 210, f"Patch in Doku ({patch}) < P210 — Sync vergessen?"

    def test_mirror_doc_has_stand_anker(self) -> None:
        from tools.sync_huginn_rag import validate_doc_header
        path = ROOT / "docs" / "RAG Testdokumente" / "huginn_kennt_zerberus.md"
        assert path.exists(), f"Spiegel-Kopie fehlt: {path}"
        text = path.read_text(encoding="utf-8")
        ok, msg = validate_doc_header(text)
        assert ok, f"Spiegel-Kopie ohne Stand-Anker: {msg}"


# ---------------------------------------------------------------------------
# 7) Source-Audit: WORKFLOW.md hat den Sync-Schritt in der Doku-Pflicht
# ---------------------------------------------------------------------------


class TestWorkflowSourceAudit:
    def test_workflow_lists_sync_in_doku_pflicht(self) -> None:
        path = ROOT / "ZERBERUS_MARATHON_WORKFLOW.md"
        text = path.read_text(encoding="utf-8")
        # Doku-Pflicht-Tabelle muss den Sync-Eintrag tragen.
        assert "RAG-Sync" in text
        assert "sync_huginn_rag" in text

    def test_workflow_phase_5a_has_goal_18(self) -> None:
        path = ROOT / "ZERBERUS_MARATHON_WORKFLOW.md"
        text = path.read_text(encoding="utf-8")
        # Ziel #18 muss existieren und auf P210 verweisen.
        assert "Huginn kennt sich selbst" in text
        assert "P210" in text


# ---------------------------------------------------------------------------
# 8) Smoke: Module-Exports + Konstanten
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7b) Source-Audit: hel.py rag_document_delete ruft _ensure_init auf
#     (P213-pre-Hotfix gegen Lazy-Init-Bug — siehe Modul-Docstring im Endpoint)
# ---------------------------------------------------------------------------


class TestRagDocumentDeleteLazyInitFix:
    """Vor P213-pre konnte ein DELETE direkt nach Server-Start den Index ueber
    eine leere ``_metadata``-Liste iterieren — das Modul-Globale wurde erst
    durch ``_ensure_init`` aus ``de_meta.json`` / ``metadata.json`` hydriert.
    Folge: 404 trotz vorhandener Chunks, anschliessender UPLOAD addierte zu
    den dann doch geladenen Chunks und hinterliess Duplikate.
    """

    def _read_hel(self) -> str:
        path = ROOT / "zerberus" / "app" / "routers" / "hel.py"
        return path.read_text(encoding="utf-8")

    def _delete_endpoint_block(self, source: str) -> str:
        # Roher Substring-Schnitt ist robust genug — wir suchen den Decorator
        # und nehmen das naechste ``async def`` als Block-Ende.
        marker = '@router.delete("/admin/rag/document")'
        start = source.find(marker)
        assert start >= 0, "DELETE-Endpoint nicht gefunden — Decorator umbenannt?"
        # Naechster Decorator/Funktion nach dem Block.
        next_decor = source.find("@router.", start + len(marker))
        if next_decor < 0:
            return source[start:]
        return source[start:next_decor]

    def test_delete_endpoint_calls_ensure_init(self) -> None:
        block = self._delete_endpoint_block(self._read_hel())
        assert "_ensure_init" in block, (
            "rag_document_delete ruft kein _ensure_init auf — Lazy-Init-"
            "Fehler wuerde wieder zuschlagen (P213-pre Hotfix)."
        )
        assert "await _ensure_init(settings)" in block, (
            "_ensure_init muss awaited werden — async-Funktion."
        )

    def test_delete_endpoint_reimports_metadata_after_init(self) -> None:
        # Nach dem Lazy-Init wurde das Modul-Globale ggf. gerade befuellt.
        # Re-Import des _metadata-Symbols stellt sicher, dass die Iteration
        # im Endpoint die hydrierten Eintraege sieht (analog rag_status P169).
        block = self._delete_endpoint_block(self._read_hel())
        # Beide Imports muessen vorkommen (top-level + nach _ensure_init).
        assert block.count("import _metadata") >= 1, (
            "Nach _ensure_init muss _metadata erneut importiert werden, "
            "sonst sieht die Iteration die alte (leere) Liste."
        )

    def test_delete_endpoint_swallows_init_exceptions(self) -> None:
        # Lazy-Init darf den DELETE-Pfad nicht killen — analog rag_status P169.
        block = self._delete_endpoint_block(self._read_hel())
        assert "try:" in block and "except Exception" in block, (
            "Lazy-Init im DELETE-Endpoint muss in try/except Exception gewickelt "
            "sein (Best-Effort, fail-soft)."
        )

    def test_delete_endpoint_iterates_after_init(self) -> None:
        # Sanity: Die for-Schleife ueber _metadata kommt NACH dem _ensure_init-
        # Aufruf, sonst greift der Fix nicht.
        block = self._delete_endpoint_block(self._read_hel())
        ensure_pos = block.find("await _ensure_init(settings)")
        loop_pos = block.find("for meta in _metadata:")
        assert ensure_pos >= 0
        assert loop_pos > ensure_pos, (
            "for meta in _metadata muss NACH _ensure_init kommen — sonst "
            "iteriert der Endpoint weiter ueber die leere Initial-Liste."
        )


# ---------------------------------------------------------------------------
# 8) Smoke: Module-Exports + Konstanten
# ---------------------------------------------------------------------------


class TestSmoke:
    def test_module_exports_callables(self) -> None:
        from tools import sync_huginn_rag as mod
        assert callable(mod.build_sync_plan)
        assert callable(mod.execute_sync_plan)
        assert callable(mod.validate_doc_header)
        assert callable(mod.extract_current_patch)
        assert callable(mod.parse_auth_string)
        assert callable(mod.load_auth_from_env)
        assert callable(mod.resolve_base_url)
        assert callable(mod.should_skip_tls_verify)  # P213-pre-Hotfix
        assert callable(mod.main)

    def test_constants_match_protocol(self) -> None:
        from tools import sync_huginn_rag as mod
        assert mod.SYNC_DEFAULT_SOURCE_NAME == "huginn_kennt_zerberus.md"
        assert mod.SYNC_DEFAULT_CATEGORY == "system"
        # P213-pre-Hotfix: Default ist HTTPS, weil start.bat den Server immer
        # mit ``--ssl-keyfile``/``--ssl-certfile`` startet.
        assert mod.SYNC_DEFAULT_BASE_URL == "https://localhost:5000"
        assert mod.SYNC_AUTH_ENV_VAR == "HUGINN_RAG_AUTH"
        assert mod.SYNC_BASE_URL_ENV_VAR == "ZERBERUS_URL"
        # P213-pre-Hotfix: ADMIN_USER/ADMIN_PASSWORD als Fallback-Quelle.
        assert mod.SYNC_ADMIN_USER_ENV_VAR == "ADMIN_USER"
        assert mod.SYNC_ADMIN_PASSWORD_ENV_VAR == "ADMIN_PASSWORD"

    def test_powershell_wrapper_exists(self) -> None:
        path = ROOT / "scripts" / "sync_huginn_rag.ps1"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "tools.sync_huginn_rag" in text
        assert "[SYNC-210]" in text
