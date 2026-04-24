"""
Patch 143 (B-014): TTS-Integration via edge-tts.

Tests:
- zerberus/utils/tts.py existiert mit den Kern-Funktionen
- Invalide Eingaben erzeugen sinnvolle Exceptions (ValueError/RuntimeError)
- Router-Endpoints sind registriert
- TTS-Controls und 🔊-Button im Frontend
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def nala_src() -> str:
    return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")


class TestTtsUtil:
    def test_modul_existiert(self):
        assert (ROOT / "zerberus" / "utils" / "tts.py").exists()

    def test_text_to_speech_funktion_existiert(self):
        from zerberus.utils import tts
        assert hasattr(tts, "text_to_speech")
        assert hasattr(tts, "list_voices")
        assert hasattr(tts, "is_available")

    def test_is_available_gibt_bool(self):
        from zerberus.utils import tts
        assert isinstance(tts.is_available(), bool)

    def test_leerer_text_raises_value_error(self):
        import asyncio
        from zerberus.utils import tts
        async def _run():
            await tts.text_to_speech("")
        with pytest.raises(ValueError):
            asyncio.run(_run())

    def test_invalides_rate_format_raises(self):
        import asyncio
        from zerberus.utils import tts
        if not tts.is_available():
            pytest.skip("edge-tts nicht installiert")
        async def _run():
            await tts.text_to_speech("hallo", rate="schnell")
        with pytest.raises(ValueError):
            asyncio.run(_run())


class TestTtsRouterEndpoints:
    def test_voices_endpoint_registriert(self, nala_src):
        assert '@router.get("/tts/voices")' in nala_src
        assert "async def tts_voices" in nala_src

    def test_speak_endpoint_registriert(self, nala_src):
        assert '@router.post("/tts/speak")' in nala_src
        assert "async def tts_speak" in nala_src

    def test_audio_mpeg_content_type(self, nala_src):
        speak_block = nala_src.split("async def tts_speak")[1][:2500]
        assert "audio/mpeg" in speak_block

    def test_503_wenn_edge_tts_fehlt(self, nala_src):
        """Wenn tts.is_available() == False, kommt 503 statt 500."""
        voices_block = nala_src.split("async def tts_voices")[1][:2000]
        speak_block = nala_src.split("async def tts_speak")[1][:2500]
        assert "503" in voices_block
        assert "503" in speak_block


class TestTtsFrontend:
    def test_tts_settings_section_existiert(self, nala_src):
        # Das "Ausdruck"-Tab hat TTS-Controls (voice + rate + preview)
        voice_start = nala_src.find('id="settings-tab-voice"')
        voice_block = nala_src[voice_start:voice_start + 5000]
        assert 'id="tts-voice-select"' in voice_block
        assert 'id="tts-rate-slider"' in voice_block
        assert "previewTts" in voice_block

    def test_tts_voice_dropdown(self, nala_src):
        assert 'id="tts-voice-select"' in nala_src
        assert "initTtsControls" in nala_src

    def test_rate_slider_range(self, nala_src):
        slider_block = nala_src.split('id="tts-rate-slider"')[1][:500]
        assert 'min="-50"' in slider_block
        assert 'max="100"' in slider_block

    def test_speak_text_js_funktion(self, nala_src):
        assert "async function speakText" in nala_src
        block = nala_src.split("async function speakText")[1][:2000]
        assert "/nala/tts/speak" in block

    def test_tts_button_in_bot_bubble(self, nala_src):
        """Jede Bot-Bubble bekommt ein 🔊-Icon."""
        # Das Icon wird in addMessage() hinzugefügt, wenn sender == 'bot'
        addmsg_block = nala_src.split("function addMessage(")[1][:5000]
        assert "🔊" in addmsg_block
        assert "speakText(text)" in addmsg_block or "speakText(" in addmsg_block
