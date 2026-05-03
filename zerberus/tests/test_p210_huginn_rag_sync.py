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


class TestResolveBaseUrl:
    def test_default(self) -> None:
        from tools.sync_huginn_rag import resolve_base_url
        assert resolve_base_url(env={}) == "http://localhost:5000"

    def test_from_env(self) -> None:
        from tools.sync_huginn_rag import resolve_base_url
        result = resolve_base_url(env={"ZERBERUS_URL": "https://example.com"})
        assert result == "https://example.com"

    def test_strips_trailing_slash(self) -> None:
        from tools.sync_huginn_rag import resolve_base_url
        result = resolve_base_url(env={"ZERBERUS_URL": "http://x/"})
        assert result == "http://x"


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
        assert callable(mod.main)

    def test_constants_match_protocol(self) -> None:
        from tools import sync_huginn_rag as mod
        assert mod.SYNC_DEFAULT_SOURCE_NAME == "huginn_kennt_zerberus.md"
        assert mod.SYNC_DEFAULT_CATEGORY == "system"
        assert mod.SYNC_DEFAULT_BASE_URL == "http://localhost:5000"
        assert mod.SYNC_AUTH_ENV_VAR == "HUGINN_RAG_AUTH"
        assert mod.SYNC_BASE_URL_ENV_VAR == "ZERBERUS_URL"

    def test_powershell_wrapper_exists(self) -> None:
        path = ROOT / "scripts" / "sync_huginn_rag.ps1"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "tools.sync_huginn_rag" in text
        assert "[SYNC-210]" in text
