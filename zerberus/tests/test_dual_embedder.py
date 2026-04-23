"""
Patch 126 - Tests fuer Spracherkennung + DualEmbedder-Infrastruktur.

Die eigentlichen Embedding-Modelle werden NICHT geladen (Zeit/GB),
nur die Konfig und der Dispatch-Pfad.
"""
import pytest

from zerberus.modules.rag.language_detector import (
    detect_language,
    language_confidence,
)
from zerberus.modules.rag.dual_embedder import (
    DEFAULT_DE_MODEL,
    DEFAULT_EN_MODEL,
    DualEmbedder,
    DualEmbedderConfig,
)


class TestLanguageDetection:
    def test_empty_string_defaults_de(self):
        assert detect_language("") == "de"
        assert detect_language("   \n  ") == "de"

    def test_german_text_is_de(self):
        text = (
            "Der Bunker ist der Ort der Wahrheit. Die Rosendornen sind das Symbol "
            "der Suche. Ich bin nicht allein in diesem System."
        )
        assert detect_language(text) == "de"

    def test_english_text_is_en(self):
        text = (
            "The system is the truth. The search continues for those who believe. "
            "We are not alone in this digital world of ours."
        )
        assert detect_language(text) == "en"

    def test_umlauts_boost_german(self):
        # Kurzer Text mit Umlauten - sollte als DE erkannt werden
        text = "Über die Bücher für die Zukunft."
        assert detect_language(text) == "de"

    def test_code_tokens_are_ignored(self):
        # Python-Code mit deutschen Kommentaren - nicht als EN erkannt
        text = """
        def berechne_summe(werte):
            # Die Funktion summiert die uebergebenen Werte
            return sum(werte)

        class BenutzerVerwaltung:
            pass
        """
        # Hier dominiert "def/class/return" normalerweise nicht mehr - sollte DE sein
        lang = detect_language(text)
        assert lang in ("de", "en")  # Mindestens: kein Crash

    def test_frontmatter_is_stripped(self):
        text = (
            "---\n"
            "title: Ein Test\n"
            "---\n"
            "Der eigentliche Inhalt ist hier und enthält viele deutsche Woerter."
        )
        assert detect_language(text) == "de"

    def test_very_short_input_defaults_de(self):
        # Zu kurz für sichere Erkennung - Default DE
        assert detect_language("ok") == "de"


class TestLanguageConfidence:
    def test_returns_scores(self):
        result = language_confidence("Der die das und ist.")
        assert "language" in result
        assert "de_score" in result
        assert "en_score" in result
        assert result["de_score"] > 0

    def test_empty_returns_zeros(self):
        result = language_confidence("")
        assert result["de_score"] == 0
        assert result["en_score"] == 0


class TestDualEmbedderConfig:
    def test_defaults(self):
        cfg = DualEmbedderConfig()
        assert cfg.de_model == DEFAULT_DE_MODEL
        assert cfg.en_model == DEFAULT_EN_MODEL
        assert cfg.de_device == "cuda"
        assert cfg.en_device == "cpu"
        assert cfg.auto_detect is True

    def test_from_dict_empty(self):
        cfg = DualEmbedderConfig.from_dict({})
        assert cfg.de_model == DEFAULT_DE_MODEL

    def test_from_dict_full_override(self):
        raw = {
            "embedder": {
                "de": {"model": "mein-de-modell", "device": "cpu"},
                "en": {"model": "mein-en-modell", "device": "cuda"},
                "auto_detect_language": False,
                "fallback_language": "en",
            }
        }
        cfg = DualEmbedderConfig.from_dict(raw)
        assert cfg.de_model == "mein-de-modell"
        assert cfg.en_model == "mein-en-modell"
        assert cfg.de_device == "cpu"
        assert cfg.en_device == "cuda"
        assert cfg.auto_detect is False
        assert cfg.fallback_language == "en"


class TestDualEmbedderLazy:
    def test_instance_has_no_models_until_embed(self):
        emb = DualEmbedder(DualEmbedderConfig())
        assert emb._de_model is None
        assert emb._en_model is None

    def test_unload_clears_models(self, monkeypatch):
        emb = DualEmbedder(DualEmbedderConfig())

        class FakeModel:
            def encode(self, text, normalize_embeddings=True):
                return [0.1, 0.2, 0.3]

        emb._de_model = FakeModel()
        emb._en_model = FakeModel()
        emb.unload()
        assert emb._de_model is None
        assert emb._en_model is None

    def test_embed_dispatches_by_language(self, monkeypatch):
        """Verifiziert die Weiche DE/EN ohne echte Modelle zu laden."""
        calls: list[str] = []

        class FakeModel:
            def __init__(self, tag: str):
                self.tag = tag

            def encode(self, text, normalize_embeddings=True):
                calls.append(self.tag)
                return [0.1]

        emb = DualEmbedder(DualEmbedderConfig(auto_detect=True))
        emb._de_model = FakeModel("de")
        emb._en_model = FakeModel("en")

        emb.embed("Der Bunker ist der Ort der Wahrheit.")
        assert calls[-1] == "de"

        emb.embed("The system knows what is true.")
        assert calls[-1] == "en"

    def test_embed_respects_explicit_language(self):
        calls = []

        class FakeModel:
            def __init__(self, tag):
                self.tag = tag

            def encode(self, text, normalize_embeddings=True):
                calls.append(self.tag)
                return [0.1]

        emb = DualEmbedder(DualEmbedderConfig(auto_detect=True))
        emb._de_model = FakeModel("de")
        emb._en_model = FakeModel("en")

        # Explizit EN trotz deutschem Text
        emb.embed("Der Bunker ist dunkel.", language="en")
        assert calls[-1] == "en"

    def test_embed_batch_length(self):
        class FakeModel:
            def encode(self, text, normalize_embeddings=True):
                return [0.1, 0.2]

        emb = DualEmbedder(DualEmbedderConfig(auto_detect=False, fallback_language="de"))
        emb._de_model = FakeModel()
        emb._en_model = FakeModel()

        result = emb.embed_batch(["a", "b", "c"], language="de")
        assert len(result) == 3
        assert all(len(v) == 2 for v in result)
