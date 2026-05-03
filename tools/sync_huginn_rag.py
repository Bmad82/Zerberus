"""Patch 210 (Phase 5a #18) — Auto-Sync fuer ``docs/huginn_kennt_zerberus.md``.

Hintergrund:
    Huginn antwortete konsistent "Patch 178" weil der RAG-Index nicht synchron
    zur Doku gepflegt wurde. Coda hat die Datei aktualisiert, Chris musste sie
    manuell hochladen — und vergass es regelmaessig. Mit P210 macht Coda das
    automatisch am Session-Ende als Teil des Marathon-Push-Zyklus.

Architektur (analog P209/P208):
    1. **Pure-Function-Schicht** — ``build_sync_plan``, ``validate_doc_header``,
       ``extract_current_patch``. Kein IO, kein Netzwerk, voll testbar.
    2. **Async-Wrapper** — ``execute_sync_plan`` mit ``httpx.AsyncClient``,
       fail-soft bei DELETE-404 (Idempotenz), fail-fast bei UPLOAD-Fehler.
    3. **CLI** — ``python -m tools.sync_huginn_rag`` mit ``--source``,
       ``--base-url``, ``--reindex``. Liest Auth aus ``HUGINN_RAG_AUTH``-
       Env-Var oder ``.env``-Datei.

Endpoints (vom Hel-Admin-Router):
    - ``DELETE /hel/admin/rag/document?source=<name>`` — Soft-Delete (P116).
      404 wenn keine Chunks zur Source existieren — fuer den Sync OK
      (Erst-Upload-Fall).
    - ``POST /hel/admin/rag/upload`` — Form ``file``, ``category=system``.
      Chunks werden via Prose-Chunker mit ``CHUNK_CONFIGS["system"]`` indexiert.
    - ``POST /hel/admin/rag/reindex`` — Optional. Physische Bereinigung
      soft-deleted Chunks. Default OFF — Soft-Delete reicht fuer Lookup.

Aufruf:
    Python:    ``python -m tools.sync_huginn_rag``
    PowerShell: ``scripts/sync_huginn_rag.ps1`` (Wrapper, gleicher Effekt)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Pure-Function-Schicht (kein IO, kein Netzwerk)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncStep:
    """Ein einzelner geplanter HTTP-Call.

    ``success_codes`` ist die Menge der HTTP-Status-Codes, die als Erfolg
    gewertet werden. Fuer DELETE ist ``404`` zusaetzlich erlaubt — beim
    Erst-Upload existieren noch keine Chunks zum Loeschen.
    """

    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)
    files: tuple[tuple[str, Path], ...] = ()
    data: dict[str, str] = field(default_factory=dict)
    success_codes: tuple[int, ...] = (200,)
    description: str = ""


@dataclass
class SyncResult:
    """Ergebnis eines Sync-Laufs.

    ``success`` ist ``True``, wenn alle Steps innerhalb ihrer
    ``success_codes`` lagen. ``errors`` ist eine Liste von Klartext-
    Beschreibungen fuer fehlgeschlagene Steps; leer bei Erfolg.
    """

    success: bool
    steps_executed: int
    steps_failed: int
    errors: list[str] = field(default_factory=list)
    response_payloads: list[dict[str, Any]] = field(default_factory=list)


SYNC_DEFAULT_SOURCE_NAME = "huginn_kennt_zerberus.md"
SYNC_DEFAULT_CATEGORY = "system"
SYNC_DEFAULT_BASE_URL = "http://localhost:5000"
SYNC_AUTH_ENV_VAR = "HUGINN_RAG_AUTH"
SYNC_BASE_URL_ENV_VAR = "ZERBERUS_URL"

_STAND_ANKER_HEADER_RE = re.compile(
    r"^##\s+Aktueller Stand\b", re.MULTILINE
)
_PATCH_LINE_RE = re.compile(
    r"\*\*Letzter Patch:\*\*\s+(P\d{3,4})\b", re.IGNORECASE
)


def build_sync_plan(
    source_path: Path,
    *,
    source_name: str = SYNC_DEFAULT_SOURCE_NAME,
    category: str = SYNC_DEFAULT_CATEGORY,
    run_reindex: bool = False,
) -> list[SyncStep]:
    """Plant die HTTP-Schritte fuer den RAG-Sync. Pure Function.

    Reihenfolge: erst DELETE (alte Chunks raus), dann UPLOAD (neue rein),
    optional REINDEX (physische Bereinigung). Kehrt man UPLOAD und DELETE
    um, wuerde DELETE die gerade hochgeladenen neuen Chunks loeschen — der
    klassische Off-by-One-Fail.

    Args:
        source_path: Pfad zur Doku-Datei. Muss existieren UND den
            ``## Aktueller Stand``-Header tragen — sonst ``ValueError``.
        source_name: Name unter dem die Datei im RAG-Index liegt. Default
            ``huginn_kennt_zerberus.md`` (matcht den Filename in der Doku).
        category: RAG-Kategorie. Default ``system`` weil Huginn nur diese
            Kategorie sehen darf (P178 ``rag_allowed_categories``).
        run_reindex: Wenn ``True`` wird nach dem Upload ein Reindex-Call
            gemacht. Default ``False``, weil Soft-Delete fuer Lookup reicht.

    Raises:
        FileNotFoundError: Source-Pfad existiert nicht.
        ValueError: Datei hat keinen Stand-Anker-Header (P210 Pflicht).
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Doku-Datei nicht gefunden: {source_path}")
    text = source_path.read_text(encoding="utf-8", errors="replace")
    ok, msg = validate_doc_header(text)
    if not ok:
        raise ValueError(f"{source_path}: {msg}")

    plan: list[SyncStep] = [
        SyncStep(
            method="DELETE",
            path="/hel/admin/rag/document",
            params={"source": source_name},
            success_codes=(200, 404),  # 404 = Erst-Upload, Idempotenz
            description=f"Soft-Delete alter Chunks fuer {source_name}",
        ),
        SyncStep(
            method="POST",
            path="/hel/admin/rag/upload",
            files=((source_name, source_path),),
            data={"category": category},
            success_codes=(200,),
            description=f"Upload neue Version von {source_name} ({category})",
        ),
    ]
    if run_reindex:
        plan.append(
            SyncStep(
                method="POST",
                path="/hel/admin/rag/reindex",
                success_codes=(200,),
                description="Reindex (physische Bereinigung soft-deleted Chunks)",
            )
        )
    return plan


def validate_doc_header(text: str) -> tuple[bool, str]:
    """Prueft ob die Doku einen ``## Aktueller Stand``-Block hat.

    Diagnose-Helper: ohne diesen Block wuerde Huginn beim "welcher Patch?"-
    Frage wieder auf prominente Logging-Tags raten (Symptom des
    Original-Bugs aus 2026-05-03).

    Returns:
        ``(True, "")`` bei Erfolg, ``(False, "<Grund>")`` bei Fehler.
    """
    if not text.strip():
        return False, "Datei ist leer"
    if not _STAND_ANKER_HEADER_RE.search(text):
        return False, (
            "Stand-Anker-Block '## Aktueller Stand' fehlt. "
            "Pflicht seit P210, sonst raet Huginn bei Stand-Fragen."
        )
    patch = extract_current_patch(text)
    if patch is None:
        return False, (
            "Stand-Anker-Block enthaelt keine '**Letzter Patch:** P###'-Zeile. "
            "Pflicht seit P210."
        )
    return True, ""


def extract_current_patch(text: str) -> Optional[str]:
    """Liest die Patchnummer aus dem Stand-Anker-Block.

    Sucht ``**Letzter Patch:** P###`` im ersten Treffer (case-insensitive).
    Returns ``None`` wenn nichts gefunden — Diagnose-Helper.
    """
    m = _PATCH_LINE_RE.search(text)
    if m is None:
        return None
    return m.group(1).upper()


def parse_auth_string(raw: Optional[str]) -> Optional[tuple[str, str]]:
    """Parst ``User:Pass``-Format zu ``(user, pass)`` oder ``None``.

    Akzeptiert Doppelpunkte im Passwort (rsplit auf erstem). Leere Strings
    oder fehlende Doppelpunkte → ``None`` (HTTP-Call laeuft dann ohne Auth).
    """
    if not raw:
        return None
    raw = raw.strip()
    if ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    user = user.strip()
    if not user:
        return None
    return user, password


def load_auth_from_env(
    env: Optional[dict[str, str]] = None,
    env_file_path: Optional[Path] = None,
) -> Optional[tuple[str, str]]:
    """Auth aus Env-Var ODER ``.env``-Datei lesen.

    Reihenfolge: ``HUGINN_RAG_AUTH`` aus ``env`` (Default ``os.environ``)
    schlaegt jede ``.env``-Datei. Wenn beides fehlt → ``None``.

    Pure-Function: ``env`` und ``env_file_path`` injectable fuer Tests.
    """
    env = env if env is not None else dict(os.environ)
    raw = env.get(SYNC_AUTH_ENV_VAR)
    auth = parse_auth_string(raw)
    if auth is not None:
        return auth
    if env_file_path is None or not env_file_path.exists():
        return None
    for raw_line in env_file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == SYNC_AUTH_ENV_VAR:
            value = value.strip().strip('"').strip("'")
            return parse_auth_string(value)
    return None


def resolve_base_url(env: Optional[dict[str, str]] = None) -> str:
    """Server-URL aus ``ZERBERUS_URL``-Env-Var oder Default."""
    env = env if env is not None else dict(os.environ)
    raw = env.get(SYNC_BASE_URL_ENV_VAR, SYNC_DEFAULT_BASE_URL)
    return (raw or SYNC_DEFAULT_BASE_URL).rstrip("/")


# ---------------------------------------------------------------------------
# Async-Wrapper (httpx-IO)
# ---------------------------------------------------------------------------


async def execute_sync_plan(
    plan: Iterable[SyncStep],
    base_url: str,
    *,
    auth: Optional[tuple[str, str]] = None,
    http_client: Any = None,
    timeout: float = 30.0,
) -> SyncResult:
    """Fuehrt einen Sync-Plan aus. Async, fail-soft bei erlaubten Codes.

    Args:
        plan: Liste von ``SyncStep``-Objekten.
        base_url: Server-Wurzel (z. B. ``http://localhost:5000``).
        auth: ``(user, pass)``-Tupel fuer Basic-Auth oder ``None``.
        http_client: Injectable HTTP-Client (httpx.AsyncClient-kompatibel).
            Wenn ``None``, wird ein eigener Client erzeugt und geschlossen.
        timeout: Pro-Request-Timeout in Sekunden.

    Returns:
        ``SyncResult`` mit Erfolg/Fehler-Metriken und Server-Antworten.
    """
    plan_list = list(plan)
    errors: list[str] = []
    payloads: list[dict[str, Any]] = []
    executed = 0
    failed = 0

    owns_client = http_client is None
    if owns_client:
        try:
            import httpx
        except ImportError:
            return SyncResult(
                success=False,
                steps_executed=0,
                steps_failed=len(plan_list),
                errors=["httpx nicht installiert — pip install httpx"],
            )
        basic_auth = httpx.BasicAuth(*auth) if auth else None
        http_client = httpx.AsyncClient(
            base_url=base_url, auth=basic_auth, timeout=timeout,
        )

    try:
        for step in plan_list:
            try:
                response = await _run_step(http_client, step)
            except Exception as exc:
                failed += 1
                errors.append(
                    f"[{step.method} {step.path}] Exception: {type(exc).__name__}: {exc}"
                )
                continue

            executed += 1
            status = getattr(response, "status_code", 0)
            payload: dict[str, Any] = {
                "method": step.method,
                "path": step.path,
                "status": status,
                "description": step.description,
            }
            try:
                body = response.json()
                payload["body"] = body
            except Exception:
                payload["body"] = None

            if status not in step.success_codes:
                failed += 1
                errors.append(
                    f"[{step.method} {step.path}] HTTP {status} "
                    f"(erwartet {step.success_codes}): {payload.get('body')!r}"
                )
            payloads.append(payload)
    finally:
        if owns_client and hasattr(http_client, "aclose"):
            await http_client.aclose()

    return SyncResult(
        success=failed == 0,
        steps_executed=executed,
        steps_failed=failed,
        errors=errors,
        response_payloads=payloads,
    )


async def _run_step(client: Any, step: SyncStep) -> Any:
    """Fuehrt einen einzelnen Step aus. Multipart bei Files, sonst plain."""
    method = step.method.upper()
    if step.files:
        files_payload = []
        for field_name, file_path in step.files:
            files_payload.append(
                ("file", (file_path.name, file_path.read_bytes(),
                          "text/markdown"))
            )
        return await client.request(
            method=method,
            url=step.path,
            params=step.params or None,
            files=files_payload,
            data=step.data or None,
        )
    return await client.request(
        method=method,
        url=step.path,
        params=step.params or None,
        data=step.data or None,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_cli_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sync_huginn_rag",
        description=(
            "Synct docs/huginn_kennt_zerberus.md mit dem Hel-RAG-Index "
            "(P210). Lockt alte Chunks per Soft-Delete, laedt neue hoch, "
            "optional Reindex."
        ),
    )
    default_source = (
        Path(__file__).resolve().parents[1] / "docs" / SYNC_DEFAULT_SOURCE_NAME
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=default_source,
        help=f"Pfad zur Doku-Datei (Default: {default_source})",
    )
    parser.add_argument(
        "--source-name",
        default=SYNC_DEFAULT_SOURCE_NAME,
        help="Name unter dem die Datei im RAG-Index liegt",
    )
    parser.add_argument(
        "--category",
        default=SYNC_DEFAULT_CATEGORY,
        help="RAG-Kategorie (Default: system, fuer Huginn-Filter)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            f"Server-Wurzel (Default: ${SYNC_BASE_URL_ENV_VAR} oder "
            f"{SYNC_DEFAULT_BASE_URL})"
        ),
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Nach Upload Reindex-Call schicken (physische Bereinigung)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="Pfad zur .env-Datei fuer Auth-Lookup",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plant nur, fuehrt nichts aus. Validiert Header.",
    )
    return parser.parse_args(argv)


def _print_result(result: SyncResult) -> None:
    print("")
    print(f"  Steps ausgefuehrt: {result.steps_executed}")
    print(f"  Steps fehlgeschlagen: {result.steps_failed}")
    for payload in result.response_payloads:
        marker = "[OK]" if payload["status"] in (200, 404) else "[FAIL]"
        print(
            f"  {marker} {payload['method']} {payload['path']} "
            f"-> HTTP {payload['status']}"
        )
        if payload.get("body"):
            body_preview = str(payload["body"])
            if len(body_preview) > 240:
                body_preview = body_preview[:237] + "..."
            print(f"        body: {body_preview}")
    if result.errors:
        print("")
        print("  FEHLER:")
        for err in result.errors:
            print(f"    - {err}")


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_cli_args(argv)
    base_url = args.base_url or resolve_base_url()
    auth = load_auth_from_env(env_file_path=args.env_file)

    print(f"[SYNC-210] Quelle: {args.source}")
    print(f"[SYNC-210] Server: {base_url}")
    print(f"[SYNC-210] Source-Name im Index: {args.source_name}")
    print(f"[SYNC-210] Kategorie: {args.category}")
    print(f"[SYNC-210] Reindex nach Upload: {args.reindex}")
    print(f"[SYNC-210] Auth: {'gesetzt' if auth else 'NICHT gesetzt'}")

    try:
        plan = build_sync_plan(
            args.source,
            source_name=args.source_name,
            category=args.category,
            run_reindex=args.reindex,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[SYNC-210] Plan-Fehler: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[SYNC-210] Dry-Run — {len(plan)} geplante Schritte:")
        for step in plan:
            print(f"  - {step.method:6} {step.path}  ({step.description})")
        return 0

    result = asyncio.run(
        execute_sync_plan(plan, base_url, auth=auth)
    )
    _print_result(result)
    if result.success:
        print("\n[SYNC-210] Sync erfolgreich.")
        return 0
    print(
        f"\n[SYNC-210] Sync fehlgeschlagen ({result.steps_failed} Step(s)).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
