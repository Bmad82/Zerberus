"""
Patch 122 – Tests für den AST-basierten Code-Chunker.

Prüft dass:
- Python-Dateien per AST nach Funktionen/Klassen/Imports gesplittet werden
- JS/TS per Regex gesplittet wird
- HTML nach <script>/<style>/body getrennt wird
- CSS/JSON/YAML/SQL jeweils semantisch gechunkt werden
- Ungültige Inputs (leer, invalide Python) nicht crashen
- Metadaten vollständig sind und context_header vorangestellt wird
- Min/Max-Chunk-Limits eingehalten werden
"""
import pytest

from zerberus.modules.rag.code_chunker import (
    chunk_code,
    chunk_python,
    chunk_js,
    chunk_html,
    chunk_css,
    chunk_json,
    chunk_yaml,
    chunk_sql,
    is_code_file,
    describe_chunker,
    MIN_CHUNK_CHARS,
    MAX_CHUNK_CHARS,
)


class TestIsCodeFile:
    def test_python_is_code(self):
        assert is_code_file("foo.py") is True

    def test_javascript_is_code(self):
        assert is_code_file("foo.js") is True
        assert is_code_file("foo.jsx") is True
        assert is_code_file("foo.ts") is True
        assert is_code_file("foo.tsx") is True

    def test_html_is_code(self):
        assert is_code_file("foo.html") is True

    def test_markdown_is_not_code(self):
        assert is_code_file("foo.md") is False

    def test_pdf_is_not_code(self):
        assert is_code_file("foo.pdf") is False

    def test_txt_is_not_code(self):
        assert is_code_file("foo.txt") is False

    def test_unknown_extension(self):
        assert is_code_file("foo.xyz") is False


class TestPythonChunker:
    def test_function_and_class_become_chunks(self):
        code = '''
def foo():
    return 1

class Bar:
    def baz(self):
        return 2
'''
        chunks = chunk_python(code, "sample.py")
        names = [c["metadata"]["name"] for c in chunks]
        types = [c["metadata"]["chunk_type"] for c in chunks]
        assert "foo" in names
        assert "Bar" in names
        assert "function" in types
        assert "class" in types

    def test_imports_become_single_chunk(self):
        code = "import os\nimport sys\nfrom pathlib import Path\n"
        chunks = chunk_python(code, "sample.py")
        import_chunks = [c for c in chunks if c["metadata"]["chunk_type"] == "imports"]
        assert len(import_chunks) == 1
        assert "import os" in import_chunks[0]["content"]
        assert "pathlib" in import_chunks[0]["content"]

    def test_module_docstring_becomes_chunk(self):
        code = '"""Modul-Beschreibung."""\n\ndef foo():\n    pass\n'
        chunks = chunk_python(code, "sample.py")
        doc_chunks = [c for c in chunks if c["metadata"]["chunk_type"] == "module_docstring"]
        assert len(doc_chunks) == 1

    def test_empty_python_returns_empty_list(self):
        chunks = chunk_python("", "empty.py")
        assert chunks == []

    def test_invalid_python_raises(self):
        with pytest.raises(SyntaxError):
            chunk_python("def foo(:\n  pass\n", "broken.py")

    def test_metadata_has_language(self):
        chunks = chunk_python("def foo(): pass\n", "sample.py")
        assert chunks
        for c in chunks:
            assert c["metadata"]["language"] == "python"
            assert c["metadata"]["file_path"] == "sample.py"

    def test_start_and_end_lines_are_set(self):
        code = "def foo():\n    return 1\n\nclass Bar:\n    pass\n"
        chunks = chunk_python(code, "sample.py")
        block_chunks = [c for c in chunks if c["metadata"]["chunk_type"] in ("function", "class")]
        for c in block_chunks:
            assert c["metadata"]["start_line"] >= 1
            assert c["metadata"]["end_line"] >= c["metadata"]["start_line"]


class TestJSChunker:
    def test_named_function_becomes_chunk(self):
        code = "function foo() { return 1; }\nfunction bar() { return 2; }\n"
        chunks = chunk_js(code, "sample.js")
        names = [c["metadata"]["name"] for c in chunks]
        assert "foo" in names
        assert "bar" in names

    def test_class_becomes_chunk(self):
        code = "class MyClass {\n  constructor() {}\n}\n"
        chunks = chunk_js(code, "sample.js")
        class_chunks = [c for c in chunks if c["metadata"]["chunk_type"] == "class"]
        assert len(class_chunks) == 1
        assert class_chunks[0]["metadata"]["name"] == "MyClass"

    def test_arrow_function_becomes_chunk(self):
        code = "const foo = () => { return 1; };\n"
        chunks = chunk_js(code, "sample.js")
        names = [c["metadata"]["name"] for c in chunks]
        assert "foo" in names

    def test_typescript_language_tag(self):
        code = "function foo(): number { return 1; }\n"
        chunks = chunk_js(code, "sample.ts", language="typescript")
        assert chunks
        assert chunks[-1]["metadata"]["language"] == "typescript"

    def test_empty_js_returns_empty_list(self):
        # Leerer Input ohne Boundaries → keine Chunks (Aufrufer fällt auf Prose zurück)
        chunks = chunk_js("// only comments\n", "sample.js")
        assert chunks == []


class TestHtmlChunker:
    def test_script_and_style_are_separated(self):
        code = """<html>
<head>
<style>body { color: red; }</style>
</head>
<body>
<p>Hallo</p>
<script>console.log('hi');</script>
</body>
</html>"""
        chunks = chunk_html(code, "sample.html")
        types = [c["metadata"]["chunk_type"] for c in chunks]
        assert "script" in types
        assert "style" in types
        assert "body" in types

    def test_body_without_script_or_style(self):
        code = "<html><body><p>Hallo</p></body></html>"
        chunks = chunk_html(code, "sample.html")
        assert chunks
        assert any(c["metadata"]["chunk_type"] == "body" for c in chunks)


class TestCssChunker:
    def test_rules_become_chunks(self):
        code = ".foo { color: red; }\n.bar { background: blue; }\n"
        chunks = chunk_css(code, "sample.css")
        assert len(chunks) == 2
        names = [c["metadata"]["name"] for c in chunks]
        assert ".foo" in names
        assert ".bar" in names

    def test_media_query_is_kept_together(self):
        code = "@media (max-width: 600px) {\n  .foo { color: red; }\n}\n"
        chunks = chunk_css(code, "sample.css")
        assert chunks


class TestJsonChunker:
    def test_top_level_keys_become_chunks(self):
        code = '{"a": 1, "b": {"nested": true}, "c": [1,2,3]}'
        chunks = chunk_json(code, "sample.json")
        names = [c["metadata"]["name"] for c in chunks]
        assert "a" in names
        assert "b" in names
        assert "c" in names

    def test_invalid_json_returns_empty(self):
        chunks = chunk_json("{not-valid", "bad.json")
        assert chunks == []


class TestYamlChunker:
    def test_top_level_keys_become_chunks(self):
        code = "alpha:\n  beta: 1\ngamma: 2\n"
        chunks = chunk_yaml(code, "sample.yaml")
        names = [c["metadata"]["name"] for c in chunks]
        assert "alpha" in names
        assert "gamma" in names


class TestSqlChunker:
    def test_statements_become_chunks(self):
        code = "SELECT * FROM foo; INSERT INTO bar VALUES (1);"
        chunks = chunk_sql(code, "sample.sql")
        assert len(chunks) == 2
        assert all(c["content"].endswith(";") for c in chunks)


class TestDispatcher:
    def test_chunk_code_handles_python(self):
        chunks = chunk_code("def foo(): return 1\n", "foo.py")
        assert chunks
        # Context-Header wird prepended
        assert chunks[0]["content"].startswith("# Datei: foo.py")

    def test_chunk_code_falls_back_on_invalid_python(self):
        chunks = chunk_code("def foo(:\n  pass\n", "broken.py")
        assert chunks == []  # Aufrufer fällt auf Prose zurück

    def test_chunk_code_empty_returns_empty(self):
        assert chunk_code("", "foo.py") == []
        assert chunk_code("   \n\n", "foo.py") == []

    def test_chunk_code_unknown_extension_returns_empty(self):
        assert chunk_code("anything here", "file.xyz") == []

    def test_chunk_code_context_header_contains_file_path(self):
        chunks = chunk_code("def foo(): return 1\n", "deep/path/module.py")
        assert chunks
        assert "deep/path/module.py" in chunks[0]["content"]

    def test_chunk_code_full_metadata(self):
        chunks = chunk_code("def foo():\n    return 1\n", "sample.py")
        assert chunks
        meta = chunks[0]["metadata"]
        # Alle erwarteten Schlüssel vorhanden
        assert "chunk_type" in meta
        assert "name" in meta
        assert "language" in meta
        assert "file_path" in meta


class TestSizeLimits:
    def test_tiny_chunks_get_merged(self):
        # JSON mit vielen winzigen Top-Level-Keys - die sollten zusammengeführt werden.
        code = '{"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}'
        chunks = chunk_code(code, "sample.json")
        # Jeder einzelne Key-Chunk wäre kleiner als MIN_CHUNK_CHARS → Merge greift.
        # Wir prüfen: am Ende gibt es mindestens einen Chunk, und jeder ist >= MIN_CHUNK_CHARS
        # ODER es ist der einzige Chunk (dann darf er klein sein).
        assert chunks
        if len(chunks) > 1:
            for c in chunks:
                assert len(c["content"]) >= MIN_CHUNK_CHARS

    def test_huge_chunk_gets_split(self):
        # Erzeuge eine riesige Python-Funktion
        body = "    x = 1\n" * 500  # ~5000 Zeichen
        code = f"def huge():\n{body}"
        chunks = chunk_code(code, "huge.py")
        assert chunks
        # Nach Split sollte jeder Chunk <= MAX_CHUNK_CHARS sein
        # (Context-Header zählt dazu, etwas Toleranz)
        for c in chunks:
            assert len(c["content"]) <= MAX_CHUNK_CHARS + 200


class TestDescribeChunker:
    def test_python_label(self):
        assert describe_chunker("foo.py") == "Python (AST)"

    def test_javascript_label(self):
        assert "JavaScript" in describe_chunker("foo.js")

    def test_unknown_label(self):
        assert describe_chunker("foo.xyz") == "Prosa"
