"""
Patch 110 – Chunking-Weiche pro Category.
Unit-Tests für `_chunk_text` aus [hel.py](zerberus/app/routers/hel.py) — rein
regex/wortbasierte Logik, kein Server-Abhängigkeit.
"""
import pytest

from zerberus.app.routers.hel import _chunk_text, CHUNK_CONFIGS


def _wc(s: str) -> int:
    return len(s.split())


class TestCategoryProfiles:
    """Das Category-Profil muss aus CHUNK_CONFIGS gezogen werden."""

    def test_known_categories_have_required_keys(self):
        required = {"chunk_size", "overlap", "min_chunk_words", "split"}
        for cat, cfg in CHUNK_CONFIGS.items():
            assert required.issubset(cfg.keys()), f"{cat} fehlt Keys"

    def test_narrative_matches_patch75_defaults(self):
        cfg = CHUNK_CONFIGS["narrative"]
        assert cfg["chunk_size"] == 800
        assert cfg["overlap"] == 160
        assert cfg["min_chunk_words"] == 120
        assert cfg["split"] == "chapter"

    def test_technical_uses_markdown_split(self):
        assert CHUNK_CONFIGS["technical"]["split"] == "markdown"

    def test_reference_uses_smaller_chunks(self):
        ref = CHUNK_CONFIGS["reference"]
        nar = CHUNK_CONFIGS["narrative"]
        assert ref["chunk_size"] < nar["chunk_size"]
        assert ref["min_chunk_words"] < nar["min_chunk_words"]


class TestChapterSplit:
    """`narrative`/`lore`/`general` splitten an Prolog/Akt/Epilog/Glossar."""

    def test_chapter_boundary_is_hard_split(self):
        text = (
            "Prolog\n" + ("wort " * 200) +
            "\nAkt I\n" + ("wort " * 200) +
            "\nEpilog\n" + ("wort " * 200)
        )
        chunks, _ = _chunk_text(text, category="narrative")
        # 3 Sections je ~200 Wörter, kein Overlap zwischen Sections
        assert len(chunks) >= 3
        # Prolog darf nicht mit Akt-Wörtern vermischt sein (harter Split)
        for c in chunks:
            has_prolog = "Prolog" in c.split()[0:3]
            has_akt = "Akt" in c
            has_epilog = "Epilog" in c
            markers = sum([has_prolog, has_akt, has_epilog])
            assert markers <= 1, f"Chunk enthält mehrere Section-Marker: {c[:80]!r}"

    def test_lore_uses_chapter_split(self):
        # Gleiche Split-Strategie wie narrative
        assert CHUNK_CONFIGS["lore"]["split"] == "chapter"


class TestMarkdownSplit:
    """`technical` splittet an Markdown-Headern."""

    def test_markdown_headers_are_boundaries(self):
        text = (
            "# Section A\n" + ("word " * 200) +
            "\n## Subsection\n" + ("word " * 200) +
            "\n# Section B\n" + ("word " * 200)
        )
        chunks, _ = _chunk_text(text, category="technical")
        assert len(chunks) >= 3
        # Jeder Chunk beginnt entweder mit einem Header oder ist Fortsetzung eines
        # geteilten Abschnitts. Hier: 3 Sections je ~200 Wörter, technical hat
        # chunk_size=500 → jede Section passt in genau einen Chunk.
        header_chunks = [c for c in chunks if c.startswith("#")]
        assert len(header_chunks) >= 3

    def test_markdown_split_not_used_for_narrative(self):
        text = "# Header\n" + ("word " * 200)
        chunks_tech, _ = _chunk_text(text, category="technical")
        chunks_nar, _ = _chunk_text(text, category="narrative")
        # technical splittet am Header (Section zählt), narrative nicht
        assert chunks_tech[0].startswith("#")
        # Bei narrative bleibt alles in einem Chunk
        assert len(chunks_nar) == 1


class TestReferenceNoSplit:
    """`reference` macht keinen strukturellen Split — nur Wortfenster."""

    def test_reference_ignores_headers(self):
        text = "# Header\n" + ("word " * 500)
        chunks, _ = _chunk_text(text, category="reference")
        # reference: chunk_size=300, overlap=60, keine harten Splits
        # Der "#"-Header gehört einfach zum ersten Chunk
        assert len(chunks) >= 2
        # Chunks sollten alle max ~300 Wörter haben
        for c in chunks:
            assert _wc(c) <= 310


class TestMinChunkWordsMerge:
    """Patch 88 Residual-Merge bleibt funktional."""

    def test_short_tail_merges_into_previous(self):
        # 260 Wörter, chunk_size=200, overlap=50 → step=150.
        #  i=0:   words[0:200]   = 200w  (Chunk 1)
        #  i=150: words[150:350] = 110w  (Chunk 2 = Tail)
        # Mit min=50: 110 >= 50 → beide behalten, merged=0.
        # Mit min=150: 110 < 150 → Chunk 2 merges in Chunk 1 → 1 Chunk, merged=1.
        text = "w " * 260
        chunks_a, merged_a = _chunk_text(
            text, category="general", chunk_size=200, overlap=50, min_chunk_words=50
        )
        chunks_b, merged_b = _chunk_text(
            text, category="general", chunk_size=200, overlap=50, min_chunk_words=150
        )
        assert len(chunks_a) == 2
        assert merged_a == 0
        assert len(chunks_b) == 1
        assert merged_b == 1

    def test_override_parameters_produce_expected_chunk_count(self):
        # 500 Wörter, chunk_size=100, overlap=20, min_chunk_words=10:
        # step=80 → i=0,80,160,240,320,400,480 → 7 Chunks
        # Letzter Chunk [480:580] = 20w → >= 10w → kein Merge
        text = "w " * 500
        chunks, _ = _chunk_text(
            text, category="general", chunk_size=100, overlap=20, min_chunk_words=10
        )
        assert len(chunks) == 7
        # Alle Chunks außer dem letzten haben volle chunk_size
        for c in chunks[:-1]:
            assert _wc(c) == 100
        assert _wc(chunks[-1]) == 20


class TestUnknownCategoryFallback:
    """Unbekannte Category fällt auf 'general' zurück."""

    def test_unknown_category_uses_general_profile(self):
        text = "word " * 200
        chunks_unk, _ = _chunk_text(text, category="blablabla")
        chunks_gen, _ = _chunk_text(text, category="general")
        assert chunks_unk == chunks_gen


class TestEmptyAndEdgeCases:
    def test_empty_text(self):
        chunks, merged = _chunk_text("", category="narrative")
        assert chunks == []
        assert merged == 0

    def test_single_word(self):
        chunks, _ = _chunk_text("hallo", category="general")
        assert chunks == ["hallo"]

    def test_whitespace_only(self):
        chunks, _ = _chunk_text("   \n\n   \t  ", category="narrative")
        assert chunks == []
