"""Patch 165 — Doku-Konsistenz-Checker.

Pruefungen (jeweils mit Exit-Code-Beitrag):

1. **README-Footer == SUPERVISOR-Header**: Beide nennen die aktuelle
   Patch-Nummer; Drift fuehrt zu Konfusion beim Supervisor.
2. **CLAUDE_ZERBERUS.md referenzierte Dateien existieren**: Bibel-Fibel
   linkt mit Markdown-Links; tote Verweise wandern unbemerkt durch.
3. **Log-Tags `[XYZ-NNN]` referenzieren existierende Patches**: Tags wie
   `[INTENT-164]` oder `[HITL-123]` muessen zu einer Patch-Nummer
   gehoeren, die <= aktueller Patch ist.
4. **Imports in `zerberus/`** sind im aktiven venv installiert
   (Top-Level-Module). `python -c "import X"` schlaegt sonst fehl.
5. **`config.yaml`-Keys, die der Code referenziert, existieren auch in
   der YAML**: Heuristik via `settings.legacy.models.cloud_model`-artige
   Pfade. Findet Drift zwischen Config-Schema und tatsaechlicher Datei.

Ausfuehrung:

    venv\\Scripts\\python.exe scripts/check_docs_consistency.py

Exit-Code:
    0 — alle Checks gruen
    1 — mindestens ein Check rot

Das Script ist additiv zu pytest — nichts hier ueberschneidet sich mit
den Unit-Tests.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Maximale Patch-Nummer, die wir akzeptieren. Fuehrt der README-Footer eine
# hoehere Nummer als wir kennen, wird das ggf. trotzdem akzeptiert (siehe
# Pruefung 1). Hier setzen wir die untere Schranke fuer Tag-Validierung.
MIN_PATCH = 1


# ----- Hilfen ---------------------------------------------------------------


class CheckResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = True
        self.messages: list[str] = []

    def fail(self, msg: str) -> None:
        self.passed = False
        self.messages.append(f"  - FAIL: {msg}")

    def info(self, msg: str) -> None:
        self.messages.append(f"  - {msg}")

    def render(self) -> str:
        head = f"[{'OK' if self.passed else 'FAIL'}] {self.name}"
        if not self.messages:
            return head
        return head + "\n" + "\n".join(self.messages)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _highest_patch_in_supervisor(text: str) -> int | None:
    """Sucht die hoechste **Patch NNN** im Header von SUPERVISOR_ZERBERUS.md."""
    matches = re.findall(r"\*\*Patch\s+(\d+)", text)
    if not matches:
        return None
    return max(int(m) for m in matches)


def _patch_in_readme_footer(text: str) -> int | None:
    """Sucht im Footer ``Patch NNN`` (letzte 1500 Zeichen)."""
    tail = text[-1500:]
    matches = re.findall(r"Patch\s+(\d+)", tail)
    if not matches:
        return None
    return max(int(m) for m in matches)


# ----- Pruefung 1 -----------------------------------------------------------


def check_patch_number_consistency() -> CheckResult:
    r = CheckResult("README-Footer-Patch == SUPERVISOR-Header-Patch")
    readme = _read(ROOT / "README.md")
    supervisor = _read(ROOT / "SUPERVISOR_ZERBERUS.md")

    readme_n = _patch_in_readme_footer(readme)
    sup_n = _highest_patch_in_supervisor(supervisor)

    r.info(f"README-Footer: Patch {readme_n}")
    r.info(f"SUPERVISOR-Header: Patch {sup_n}")

    if readme_n is None:
        r.fail("README-Footer enthaelt keine Patch-Nummer")
    if sup_n is None:
        r.fail("SUPERVISOR-Header enthaelt keine Patch-Nummer")
    if readme_n is not None and sup_n is not None and readme_n != sup_n:
        r.fail(
            f"Drift: README zeigt Patch {readme_n}, SUPERVISOR Patch {sup_n} "
            "— beide muessen synchron sein (CLAUDE_ZERBERUS-Regel: nach jedem "
            "Patch beide updaten)"
        )
    return r


# ----- Pruefung 2 -----------------------------------------------------------


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def check_claudemd_referenced_files_exist() -> CheckResult:
    r = CheckResult("Referenzierte Dateien aus CLAUDE_ZERBERUS.md existieren")
    text = _read(ROOT / "CLAUDE_ZERBERUS.md")

    bad: list[tuple[str, str]] = []
    seen = 0
    for label, target in _MD_LINK_RE.findall(text):
        # Externe URLs ueberspringen.
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # Relative Pfade — nur lokale .md/.py/.yaml/...
        rel = target.split("#", 1)[0].strip()
        if not rel:
            continue
        # Patch 169: URL-encoded Spaces (%20) zu echten Leerzeichen aufloesen,
        # damit Pfade wie "docs/RAG Testdokumente/..." korrekt gefunden werden.
        from urllib.parse import unquote
        rel = unquote(rel)
        full = (ROOT / rel).resolve()
        seen += 1
        if not full.exists():
            bad.append((label, rel))

    r.info(f"{seen} interne Links geprueft")
    for label, rel in bad:
        r.fail(f"toter Link: [{label}]({rel}) — Datei fehlt")
    return r


# ----- Pruefung 3 -----------------------------------------------------------


_LOG_TAG_RE = re.compile(r"\[([A-Z][A-Z0-9-]+)-(\d{2,3}[a-z]?)\]")


def check_log_tags_reference_known_patches(max_patch: int) -> CheckResult:
    r = CheckResult("Log-Tags referenzieren existierende Patch-Nummern")
    py_files = [
        p
        for p in ROOT.glob("zerberus/**/*.py")
        if "__pycache__" not in p.parts and "/tests/" not in p.as_posix()
    ]

    # Hotfixes wie 162a/162b sind erlaubt; wir extrahieren die numerische Basis.
    bad: dict[str, set[str]] = {}
    seen = 0
    for path in py_files:
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # pragma: no cover
            continue
        for prefix, num in _LOG_TAG_RE.findall(txt):
            seen += 1
            base = re.match(r"^(\d+)", num)
            if not base:
                continue
            n = int(base.group(1))
            if n < MIN_PATCH or n > max_patch:
                bad.setdefault(f"[{prefix}-{num}]", set()).add(
                    str(path.relative_to(ROOT))
                )

    r.info(f"{seen} Tag-Vorkommen, max akzeptierte Patch-Nummer: {max_patch}")
    for tag, files in sorted(bad.items()):
        r.fail(f"{tag} referenziert unbekannten Patch — gefunden in {sorted(files)}")
    return r


# ----- Pruefung 4 -----------------------------------------------------------


_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([\w\.]+)", re.MULTILINE)
# Top-Level-Module die zum Repo selbst gehoeren (intern, immer ok).
_INTERNAL_PREFIXES = ("zerberus", "scripts")
# stdlib-Allowlist: einige stdlib-Module geben bei find_spec ungewohnten
# Output, hier explizit als ok markieren (heuristisch).
_STDLIB_ALLOWLIST = {
    "__future__", "abc", "asyncio", "base64", "collections", "contextlib",
    "csv", "ctypes", "dataclasses", "datetime", "enum", "errno", "functools",
    "gc", "glob", "hashlib", "hmac", "html", "http", "importlib", "inspect",
    "io", "ipaddress", "itertools", "json", "logging", "math", "mimetypes",
    "multiprocessing", "operator", "os", "pathlib", "pickle", "pkgutil",
    "platform", "queue", "random", "re", "secrets", "shlex", "shutil",
    "signal", "socket", "sqlite3", "ssl", "stat", "statistics", "string",
    "struct", "subprocess", "sys", "tempfile", "textwrap", "threading",
    "time", "tomllib", "traceback", "types", "typing", "unicodedata",
    "urllib", "uuid", "warnings", "weakref", "xml", "zipfile", "zoneinfo",
}


def check_imports_resolvable() -> CheckResult:
    r = CheckResult("Imports aus zerberus/ sind im venv aufloesbar")
    py_files = [
        p
        for p in ROOT.glob("zerberus/**/*.py")
        if "__pycache__" not in p.parts
    ]

    seen: set[str] = set()
    for path in py_files:
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # pragma: no cover
            continue
        for full in _IMPORT_RE.findall(txt):
            top = full.split(".", 1)[0]
            if not top:
                # ``from . import X`` — relativer Import, nicht extern.
                continue
            if top.startswith(_INTERNAL_PREFIXES):
                continue
            if top in _STDLIB_ALLOWLIST:
                continue
            seen.add(top)

    bad: list[str] = []
    for module in sorted(seen):
        try:
            spec = importlib.util.find_spec(module)
        except (ValueError, ModuleNotFoundError, ImportError):
            spec = None
        if spec is None:
            bad.append(module)

    r.info(f"{len(seen)} externe Top-Level-Imports geprueft")
    for module in bad:
        r.fail(f"Import nicht aufloesbar: '{module}' (pip install? Tippfehler?)")
    return r


# ----- Pruefung 5 -----------------------------------------------------------


_SETTINGS_KEY_RE = re.compile(
    r"settings\.([a-z_]+(?:\.[a-z_]+){1,4})", re.IGNORECASE
)
# Letzte Segmente die ein dict-Method-Aufruf sind, nicht ein Settings-Key.
_DICT_METHODS = {
    "get", "keys", "values", "items", "pop", "setdefault", "update", "copy",
    "clear",
}
# Schluessel die per Pydantic-Default ohne YAML-Eintrag funktionieren
# (siehe lessons.md: config.yaml gitignored → Defaults im Code).
_OPTIONAL_KEYS = {
    "settings.features",                    # P118a — Dict-Default im Code
    "settings.features.decision_boxes",
    "settings.features.whisper_watchdog",   # whisper_watchdog.py — default True
    "settings.features.hallucination_guard",
    "settings.modules.rag.min_chunk_words",  # P88 — optional override
    "settings.modules.rag.rerank_min_score",
}


def _yaml_has_path(data: object, parts: list[str]) -> bool:
    cur: object = data
    for p in parts:
        if not isinstance(cur, dict):
            return False
        if p not in cur:
            return False
        cur = cur[p]
    return True


def check_yaml_keys_match_code() -> CheckResult:
    r = CheckResult("config.yaml enthaelt alle vom Code referenzierten Keys")
    yaml_path = ROOT / "config.yaml"
    if not yaml_path.exists():
        r.info("config.yaml fehlt — Check uebersprungen (gitignored?)")
        return r

    data = yaml.safe_load(_read(yaml_path)) or {}
    py_files = [
        p
        for p in ROOT.glob("zerberus/**/*.py")
        if "__pycache__" not in p.parts and "/tests/" not in p.as_posix()
    ]

    found_keys: set[str] = set()
    for path in py_files:
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # pragma: no cover
            continue
        for match in _SETTINGS_KEY_RE.findall(txt):
            # Trailing dict-Methode → kein Settings-Key, sondern dict-Access.
            tail = match.rsplit(".", 1)[-1]
            if tail in _DICT_METHODS:
                continue
            found_keys.add(f"settings.{match}")

    missing: list[str] = []
    for key in sorted(found_keys):
        if key in _OPTIONAL_KEYS:
            continue
        # ``settings.X`` → top-level Key X. Y.Z → ``[X][Y][Z]``.
        parts = key.split(".")[1:]
        if not _yaml_has_path(data, parts):
            missing.append(key)

    r.info(f"{len(found_keys)} Settings-Pfade im Code gefunden")
    for key in missing:
        r.fail(
            f"Code referenziert '{key}', YAML hat ihn nicht — "
            "Config-Drift oder Default-only?"
        )
    return r


# ----- Main -----------------------------------------------------------------


def main() -> int:
    # Windows-Konsolen sind oft cp1252 — wir wollen aber sauber drucken.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover — stdlib-Best-Effort
        pass

    print(f"Doku-Konsistenz-Check fuer {ROOT}")
    print("=" * 70)

    # Hoechste bekannte Patch-Nummer fuer Tag-Validierung dynamisch holen.
    sup_text = _read(ROOT / "SUPERVISOR_ZERBERUS.md")
    max_patch = _highest_patch_in_supervisor(sup_text) or 999

    checks = [
        check_patch_number_consistency(),
        check_claudemd_referenced_files_exist(),
        check_log_tags_reference_known_patches(max_patch),
        check_imports_resolvable(),
        check_yaml_keys_match_code(),
    ]

    failed = [c for c in checks if not c.passed]
    for c in checks:
        print(c.render())
    print("=" * 70)
    print(f"Ergebnis: {len(checks) - len(failed)}/{len(checks)} Checks gruen")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
