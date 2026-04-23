"""
Patch 122 — AST-basierter Code-Chunker für RAG.

Teilt Code-Dateien nach semantischen Einheiten statt nach festen Zeichenlängen:
- .py        → Python AST (Funktionen, Klassen, Imports, Modul-Docstring)
- .js/.ts    → Regex-basiert (function/class/const-arrow/export default)
- .html      → Tag-basiert (<script>, <style>, Body)
- .css/.scss → Regel-basiert (Top-Level-Selektoren, Media-Queries)
- .json      → Top-Level-Keys
- .yaml/.yml → Top-Level-Keys
- .sql       → Statement-basiert (nach ';')
- Rest       → Fallback auf den klassischen Prosa-Chunker

Jeder Chunk bekommt einen `context_header` (Dateipfad + Position) prepended,
sodass der Retriever auch bei kurzen Funktionen genug Signal hat.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Dateiendungen die als Code erkannt werden. Rest → Prosa-Chunker.
_CODE_EXTENSIONS: set[str] = {
    ".py",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".html", ".htm",
    ".css", ".scss", ".sass",
    ".json",
    ".yaml", ".yml",
    ".sql",
}

MIN_CHUNK_CHARS = 50
MAX_CHUNK_CHARS = 2000


def is_code_file(file_path: str) -> bool:
    """True wenn die Dateiendung vom Code-Chunker behandelt wird."""
    suffix = Path(file_path).suffix.lower()
    return suffix in _CODE_EXTENSIONS


def _language_for_suffix(suffix: str) -> str:
    mapping = {
        ".py": "python",
        ".js": "javascript", ".jsx": "javascript",
        ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript",
        ".html": "html", ".htm": "html",
        ".css": "css", ".scss": "scss", ".sass": "scss",
        ".json": "json",
        ".yaml": "yaml", ".yml": "yaml",
        ".sql": "sql",
    }
    return mapping.get(suffix, "text")


def _build_header(file_path: str, chunk_type: str, name: str,
                  start_line: int | None = None, end_line: int | None = None) -> str:
    """Erzeugt den Kontext-Header der VOR jeden Chunk geschrieben wird."""
    lines = [f"# Datei: {file_path}"]
    if start_line is not None and end_line is not None:
        lines.append(f"# {chunk_type}: {name} (Zeile {start_line}-{end_line})")
    else:
        lines.append(f"# {chunk_type}: {name}")
    return "\n".join(lines) + "\n"


def _enforce_size_limits(chunks: list[dict]) -> list[dict]:
    """Führt kleine Chunks zusammen und teilt zu große an sinnvollen Breakpoints."""
    if not chunks:
        return chunks

    # 1. Kleine Chunks mit dem nächsten/vorherigen mergen
    merged: list[dict] = []
    for chunk in chunks:
        content = chunk.get("content", "")
        if len(content) < MIN_CHUNK_CHARS and merged:
            prev = merged[-1]
            prev["content"] = prev["content"] + "\n\n" + content
            prev_meta = prev.get("metadata", {})
            new_meta = chunk.get("metadata", {})
            prev_meta["end_line"] = new_meta.get("end_line", prev_meta.get("end_line"))
            if prev_meta.get("name") and new_meta.get("name"):
                prev_meta["name"] = f"{prev_meta['name']}+{new_meta['name']}"
        else:
            merged.append(dict(chunk))

    if merged and len(merged[0].get("content", "")) < MIN_CHUNK_CHARS and len(merged) > 1:
        merged[1]["content"] = merged[0]["content"] + "\n\n" + merged[1]["content"]
        merged.pop(0)

    # 2. Zu große Chunks an Zeilen-Grenzen teilen
    final: list[dict] = []
    for chunk in merged:
        content = chunk.get("content", "")
        if len(content) <= MAX_CHUNK_CHARS:
            final.append(chunk)
            continue

        lines = content.split("\n")
        buffer: list[str] = []
        part_idx = 0
        base_meta = dict(chunk.get("metadata", {}))
        for line in lines:
            prospective = ("\n".join(buffer + [line])) if buffer else line
            if len(prospective) > MAX_CHUNK_CHARS and buffer:
                part_meta = dict(base_meta)
                part_meta["name"] = f"{base_meta.get('name', 'part')}#part{part_idx}"
                final.append({"content": "\n".join(buffer), "metadata": part_meta})
                buffer = [line]
                part_idx += 1
            else:
                buffer.append(line)
        if buffer:
            part_meta = dict(base_meta)
            if part_idx > 0:
                part_meta["name"] = f"{base_meta.get('name', 'part')}#part{part_idx}"
            final.append({"content": "\n".join(buffer), "metadata": part_meta})

    return final


def _add_headers(chunks: list[dict], file_path: str) -> list[dict]:
    """Schreibt den context_header an den Content jedes Chunks."""
    out: list[dict] = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        header = _build_header(
            file_path=file_path,
            chunk_type=meta.get("chunk_type", "chunk"),
            name=meta.get("name", "unbekannt"),
            start_line=meta.get("start_line"),
            end_line=meta.get("end_line"),
        )
        out.append({
            "content": header + chunk.get("content", ""),
            "metadata": meta,
        })
    return out


# ---------------------------------------------------------------------------
# Python AST-Chunker
# ---------------------------------------------------------------------------

def chunk_python(source_code: str, file_path: str) -> list[dict]:
    """Zerlegt Python-Code in semantische Einheiten via ast.parse.

    Erzeugt einen Chunk pro:
    - Modul-Docstring (falls vorhanden)
    - Import-Block (alle Top-Level-Imports zusammen)
    - Jede Funktion / Klasse auf Top-Level
    - Übrig gebliebener Modul-Level-Code

    Bei Syntax-Fehlern fällt die Funktion NICHT auf Prose zurück — das muss
    der Aufrufer tun (siehe chunk_code).
    """
    tree = ast.parse(source_code)  # darf raisen
    source_lines = source_code.splitlines()

    chunks: list[dict] = []
    import_nodes: list[ast.AST] = []
    block_nodes: list[tuple[str, str, ast.AST]] = []  # (type, name, node)
    module_code_nodes: list[ast.AST] = []

    module_docstring = ast.get_docstring(tree, clean=False)
    docstring_end_line: int | None = None
    if module_docstring is not None and tree.body:
        first = tree.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            docstring_end_line = getattr(first, "end_lineno", first.lineno)

    for node in tree.body:
        if module_docstring is not None and isinstance(node, ast.Expr) \
                and isinstance(node.value, ast.Constant) \
                and node.value.value == module_docstring:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_nodes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            block_nodes.append(("function", node.name, node))
        elif isinstance(node, ast.ClassDef):
            block_nodes.append(("class", node.name, node))
        else:
            module_code_nodes.append(node)

    def _slice(lineno: int, end_lineno: int) -> str:
        return "\n".join(source_lines[lineno - 1:end_lineno])

    if module_docstring is not None:
        end = docstring_end_line or 1
        chunks.append({
            "content": _slice(1, end),
            "metadata": {
                "file_path": file_path,
                "chunk_type": "module_docstring",
                "name": "module",
                "start_line": 1,
                "end_line": end,
                "language": "python",
            },
        })

    if import_nodes:
        start = import_nodes[0].lineno
        end = max(getattr(n, "end_lineno", n.lineno) for n in import_nodes)
        chunks.append({
            "content": _slice(start, end),
            "metadata": {
                "file_path": file_path,
                "chunk_type": "imports",
                "name": "imports",
                "start_line": start,
                "end_line": end,
                "language": "python",
            },
        })

    for ctype, name, node in block_nodes:
        start = node.lineno
        # Decorator-Lines mitnehmen
        decorator_list = getattr(node, "decorator_list", [])
        if decorator_list:
            start = min(start, min(d.lineno for d in decorator_list))
        end = getattr(node, "end_lineno", start)
        chunks.append({
            "content": _slice(start, end),
            "metadata": {
                "file_path": file_path,
                "chunk_type": ctype,
                "name": name,
                "start_line": start,
                "end_line": end,
                "language": "python",
            },
        })

    if module_code_nodes:
        start = min(n.lineno for n in module_code_nodes)
        end = max(getattr(n, "end_lineno", n.lineno) for n in module_code_nodes)
        chunks.append({
            "content": _slice(start, end),
            "metadata": {
                "file_path": file_path,
                "chunk_type": "module_code",
                "name": "module",
                "start_line": start,
                "end_line": end,
                "language": "python",
            },
        })

    return chunks


# ---------------------------------------------------------------------------
# JS/TS Regex-Chunker
# ---------------------------------------------------------------------------

_JS_PATTERNS = [
    # Named function/class declarations und exports
    (re.compile(r'^\s*(?:export\s+(?:default\s+)?)?(async\s+)?function\s+(\w+)', re.MULTILINE), "function"),
    (re.compile(r'^\s*(?:export\s+(?:default\s+)?)?class\s+(\w+)', re.MULTILINE), "class"),
    # const foo = () => { ... }  /  const foo = function()
    (re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)\s*=>|function)', re.MULTILINE), "function"),
    # export default function/class without name
    (re.compile(r'^\s*export\s+default\s+(?:async\s+)?(function|class)\b', re.MULTILINE), "export_default"),
]


def chunk_js(source_code: str, file_path: str, language: str = "javascript") -> list[dict]:
    """Regex-basierte Chunking-Strategie für JS/TS. Kein vollständiger AST nötig."""
    boundaries: list[tuple[int, str, str]] = []  # (byte-offset, type, name)
    for pattern, ctype in _JS_PATTERNS:
        for m in pattern.finditer(source_code):
            offset = m.start()
            if ctype == "export_default":
                name = f"default_{m.group(1)}"
                ctype_real = m.group(1)
            else:
                # Die benannte Gruppe ist Gruppe 2 beim function-Pattern (wegen async),
                # sonst Gruppe 1. Wir nehmen die letzte Gruppe mit Content.
                groups = [g for g in m.groups() if g]
                name = groups[-1] if groups else "anonymous"
                ctype_real = ctype
            boundaries.append((offset, ctype_real, name))

    boundaries.sort(key=lambda b: b[0])

    if not boundaries:
        return []

    chunks: list[dict] = []
    source_lines = source_code.splitlines()

    # Alles vor dem ersten Boundary ist "header" (Imports / Module-Docstring)
    first_offset = boundaries[0][0]
    if first_offset > 0 and source_code[:first_offset].strip():
        head_lines_count = source_code[:first_offset].count("\n")
        chunks.append({
            "content": source_code[:first_offset].rstrip(),
            "metadata": {
                "file_path": file_path,
                "chunk_type": "module_header",
                "name": "header",
                "start_line": 1,
                "end_line": max(1, head_lines_count),
                "language": language,
            },
        })

    for i, (offset, ctype, name) in enumerate(boundaries):
        next_offset = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(source_code)
        block = source_code[offset:next_offset].rstrip()
        if not block.strip():
            continue
        start_line = source_code[:offset].count("\n") + 1
        end_line = start_line + block.count("\n")
        chunks.append({
            "content": block,
            "metadata": {
                "file_path": file_path,
                "chunk_type": ctype,
                "name": name,
                "start_line": start_line,
                "end_line": end_line,
                "language": language,
            },
        })

    return chunks


# ---------------------------------------------------------------------------
# HTML Splitter
# ---------------------------------------------------------------------------

_HTML_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_HTML_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)


def chunk_html(source_code: str, file_path: str) -> list[dict]:
    """Extrahiert <script>- und <style>-Inhalte als eigene Chunks, Rest als body."""
    chunks: list[dict] = []

    scripts = list(_HTML_SCRIPT_RE.finditer(source_code))
    styles = list(_HTML_STYLE_RE.finditer(source_code))

    for i, m in enumerate(scripts):
        content = m.group(1).strip()
        if not content:
            continue
        start_line = source_code[:m.start()].count("\n") + 1
        end_line = start_line + m.group(0).count("\n")
        chunks.append({
            "content": content,
            "metadata": {
                "file_path": file_path,
                "chunk_type": "script",
                "name": f"script_{i}",
                "start_line": start_line,
                "end_line": end_line,
                "language": "html",
            },
        })

    for i, m in enumerate(styles):
        content = m.group(1).strip()
        if not content:
            continue
        start_line = source_code[:m.start()].count("\n") + 1
        end_line = start_line + m.group(0).count("\n")
        chunks.append({
            "content": content,
            "metadata": {
                "file_path": file_path,
                "chunk_type": "style",
                "name": f"style_{i}",
                "start_line": start_line,
                "end_line": end_line,
                "language": "html",
            },
        })

    # Body = alles ohne <script>/<style>-Inhalte
    stripped = _HTML_SCRIPT_RE.sub("", source_code)
    stripped = _HTML_STYLE_RE.sub("", stripped)
    body_text = stripped.strip()
    if body_text:
        chunks.append({
            "content": body_text,
            "metadata": {
                "file_path": file_path,
                "chunk_type": "body",
                "name": "body",
                "start_line": 1,
                "end_line": source_code.count("\n") + 1,
                "language": "html",
            },
        })

    return chunks


# ---------------------------------------------------------------------------
# CSS Splitter
# ---------------------------------------------------------------------------

def chunk_css(source_code: str, file_path: str, language: str = "css") -> list[dict]:
    """Splittet CSS an Top-Level-Regeln ('}' auf Einrückungsebene 0)."""
    chunks: list[dict] = []
    depth = 0
    start = 0
    current_line = 1
    rule_start_line = 1
    for i, ch in enumerate(source_code):
        if ch == "{":
            if depth == 0:
                # Regel-Start
                rule_start_line = current_line
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                block = source_code[start:i + 1].strip()
                if block:
                    # Selektor-Name extrahieren (alles vor dem ersten "{")
                    selector_end = block.find("{")
                    selector = block[:selector_end].strip() if selector_end > 0 else "rule"
                    selector = selector.replace("\n", " ")[:60]
                    end_line = current_line
                    chunks.append({
                        "content": block,
                        "metadata": {
                            "file_path": file_path,
                            "chunk_type": "rule",
                            "name": selector or "rule",
                            "start_line": rule_start_line,
                            "end_line": end_line,
                            "language": language,
                        },
                    })
                start = i + 1
        if ch == "\n":
            current_line += 1

    tail = source_code[start:].strip()
    if tail:
        chunks.append({
            "content": tail,
            "metadata": {
                "file_path": file_path,
                "chunk_type": "rule",
                "name": "tail",
                "start_line": rule_start_line,
                "end_line": current_line,
                "language": language,
            },
        })

    return chunks


# ---------------------------------------------------------------------------
# JSON Splitter
# ---------------------------------------------------------------------------

def chunk_json(source_code: str, file_path: str) -> list[dict]:
    """Top-Level-Keys als einzelne Chunks (max Tiefe 2)."""
    try:
        data = json.loads(source_code)
    except (json.JSONDecodeError, ValueError):
        return []

    chunks: list[dict] = []
    if isinstance(data, dict):
        for key, value in data.items():
            content = json.dumps({key: value}, indent=2, ensure_ascii=False)
            chunks.append({
                "content": content,
                "metadata": {
                    "file_path": file_path,
                    "chunk_type": "json_key",
                    "name": str(key),
                    "start_line": None,
                    "end_line": None,
                    "language": "json",
                },
            })
    elif isinstance(data, list):
        # Liste: jedes Top-Level-Element ein Chunk (max 100)
        for i, item in enumerate(data[:100]):
            content = json.dumps(item, indent=2, ensure_ascii=False)
            chunks.append({
                "content": content,
                "metadata": {
                    "file_path": file_path,
                    "chunk_type": "json_item",
                    "name": f"item_{i}",
                    "start_line": None,
                    "end_line": None,
                    "language": "json",
                },
            })
    else:
        chunks.append({
            "content": source_code.strip(),
            "metadata": {
                "file_path": file_path,
                "chunk_type": "json_scalar",
                "name": "value",
                "start_line": None,
                "end_line": None,
                "language": "json",
            },
        })

    return chunks


# ---------------------------------------------------------------------------
# YAML Splitter
# ---------------------------------------------------------------------------

_YAML_TOP_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_\-]+)\s*:", re.MULTILINE)


def chunk_yaml(source_code: str, file_path: str) -> list[dict]:
    """Splittet YAML an Top-Level-Keys. Kein PyYAML nötig (simple Regex)."""
    chunks: list[dict] = []
    matches = list(_YAML_TOP_KEY_RE.finditer(source_code))
    if not matches:
        return []

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source_code)
        block = source_code[start:end].rstrip()
        if not block.strip():
            continue
        start_line = source_code[:start].count("\n") + 1
        end_line = start_line + block.count("\n")
        chunks.append({
            "content": block,
            "metadata": {
                "file_path": file_path,
                "chunk_type": "yaml_key",
                "name": m.group("key"),
                "start_line": start_line,
                "end_line": end_line,
                "language": "yaml",
            },
        })

    return chunks


# ---------------------------------------------------------------------------
# SQL Splitter
# ---------------------------------------------------------------------------

def chunk_sql(source_code: str, file_path: str) -> list[dict]:
    """Trennt SQL-Statements an ';'-Grenzen."""
    chunks: list[dict] = []
    statements = [s.strip() for s in source_code.split(";") if s.strip()]
    current_line = 1
    offset = 0
    for i, stmt in enumerate(statements):
        # Ersten Befehl (SELECT/INSERT/CREATE/…) als Name nehmen
        first_word_match = re.match(r"\s*(\w+)", stmt)
        name = (first_word_match.group(1).lower() if first_word_match else f"stmt_{i}")
        name = f"{name}_{i}"
        start_line = source_code[:offset].count("\n") + 1
        end_line = start_line + stmt.count("\n")
        chunks.append({
            "content": stmt + ";",
            "metadata": {
                "file_path": file_path,
                "chunk_type": "sql_statement",
                "name": name,
                "start_line": start_line,
                "end_line": end_line,
                "language": "sql",
            },
        })
        offset = source_code.find(stmt, offset) + len(stmt) + 1
    return chunks


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def chunk_code(content: str, file_path: str) -> list[dict]:
    """Hauptfunktion: erkennt Dateityp und wählt passenden Chunker.

    Returns:
        Liste von Chunks: [{content, metadata}, ...]
        Bei leerem/kaputtem Input → leere Liste.
        Bei unbekannter Extension → leere Liste (Aufrufer fällt auf Prose zurück).
    """
    if not content or not content.strip():
        return []

    suffix = Path(file_path).suffix.lower()
    language = _language_for_suffix(suffix)

    try:
        if suffix == ".py":
            try:
                chunks = chunk_python(content, file_path)
            except SyntaxError as e:
                logger.warning(f"[CHUNK-122] Python SyntaxError in {file_path}: {e} — Fallback zu Prose")
                return []
        elif suffix in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
            chunks = chunk_js(content, file_path, language=language)
        elif suffix in (".html", ".htm"):
            chunks = chunk_html(content, file_path)
        elif suffix in (".css", ".scss", ".sass"):
            chunks = chunk_css(content, file_path, language=language)
        elif suffix == ".json":
            chunks = chunk_json(content, file_path)
        elif suffix in (".yaml", ".yml"):
            chunks = chunk_yaml(content, file_path)
        elif suffix == ".sql":
            chunks = chunk_sql(content, file_path)
        else:
            return []
    except Exception as e:
        logger.exception(f"[CHUNK-122] Chunker-Fehler für {file_path}: {e} — Fallback zu Prose")
        return []

    if not chunks:
        return []

    chunks = _enforce_size_limits(chunks)
    chunks = _add_headers(chunks, file_path)
    return chunks


def describe_chunker(file_path: str) -> str:
    """Gibt einen Menschen-lesbaren Namen der Chunker-Strategie zurück."""
    suffix = Path(file_path).suffix.lower()
    mapping = {
        ".py": "Python (AST)",
        ".js": "JavaScript (Regex)", ".jsx": "JavaScript (Regex)",
        ".mjs": "JavaScript (Regex)", ".cjs": "JavaScript (Regex)",
        ".ts": "TypeScript (Regex)", ".tsx": "TypeScript (Regex)",
        ".html": "HTML (Tag)", ".htm": "HTML (Tag)",
        ".css": "CSS (Regel)", ".scss": "SCSS (Regel)", ".sass": "Sass (Regel)",
        ".json": "JSON (Key)",
        ".yaml": "YAML (Key)", ".yml": "YAML (Key)",
        ".sql": "SQL (Statement)",
    }
    return mapping.get(suffix, "Prosa")
