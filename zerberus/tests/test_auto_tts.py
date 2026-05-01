"""
Patch 186: Auto-TTS — globaler Toggle in Nala-Settings, der jede neue Bot-Antwort
automatisch über den bestehenden edge-tts-Endpunkt vorlesen lässt.

Source-Audit-Tests gegen zerberus/app/routers/nala.py — kein Backend-Change nötig,
der bestehende /nala/tts/speak-Endpoint bleibt unverändert.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def nala_src() -> str:
    return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")


class TestAutoTtsToggleHtml:
    def test_auto_tts_toggle_in_settings_html(self, nala_src):
        """Der Toggle 'autoTtsToggle' steht im 'Ausdruck'-Tab (settings-tab-voice)."""
        voice_start = nala_src.find('id="settings-tab-voice"')
        assert voice_start > 0, "settings-tab-voice nicht gefunden"
        voice_block = nala_src[voice_start:voice_start + 6000]
        assert 'id="autoTtsToggle"' in voice_block

    def test_auto_tts_toggle_under_tts_controls(self, nala_src):
        """Toggle muss UNTER Stimmen-Dropdown + Rate-Slider stehen."""
        voice_start = nala_src.find('id="settings-tab-voice"')
        voice_block = nala_src[voice_start:voice_start + 6000]
        idx_voice_select = voice_block.find('id="tts-voice-select"')
        idx_rate_slider = voice_block.find('id="tts-rate-slider"')
        idx_auto_tts = voice_block.find('id="autoTtsToggle"')
        assert idx_voice_select > 0 and idx_rate_slider > 0 and idx_auto_tts > 0
        assert idx_auto_tts > idx_voice_select
        assert idx_auto_tts > idx_rate_slider

    def test_auto_tts_toggle_44px_target(self, nala_src):
        """Touch-Target 44px wie andere Mobil-Controls."""
        voice_start = nala_src.find('id="settings-tab-voice"')
        voice_block = nala_src[voice_start:voice_start + 6000]
        toggle_idx = voice_block.find('id="autoTtsToggle"')
        toggle_block = voice_block[max(0, toggle_idx - 500):toggle_idx + 500]
        assert "44px" in toggle_block


class TestAutoTtsLocalStorage:
    def test_auto_tts_localstorage_key_used(self, nala_src):
        """localStorage-Key 'nala_auto_tts' muss im JS auftauchen."""
        assert "nala_auto_tts" in nala_src

    def test_auto_tts_default_off(self, nala_src):
        """Default-Verhalten: ohne Eintrag → false (kein Auto-Play)."""
        # isAutoTtsEnabled() prüft auf === 'true' — alles andere → false
        assert "=== 'true'" in nala_src or "=== \"true\"" in nala_src
        assert "isAutoTtsEnabled" in nala_src


class TestAutoTtsPlayFunction:
    def test_auto_tts_play_function_exists(self, nala_src):
        """JS-Funktion autoTtsPlay(text) muss definiert sein."""
        assert "function autoTtsPlay" in nala_src or "autoTtsPlay = " in nala_src

    def test_auto_tts_uses_existing_tts_endpoint(self, nala_src):
        """autoTtsPlay nutzt /nala/tts/speak — denselben Endpoint wie der 🔊-Button."""
        # Den autoTtsPlay-Block extrahieren
        idx = nala_src.find("function autoTtsPlay")
        assert idx > 0
        block = nala_src[idx:idx + 2000]
        assert "/nala/tts/speak" in block

    def test_auto_tts_uses_voice_and_rate_settings(self, nala_src):
        """autoTtsPlay nutzt dieselbe Stimme + Rate wie die Settings."""
        idx = nala_src.find("function autoTtsPlay")
        block = nala_src[idx:idx + 2000]
        assert "nala_tts_voice" in block
        assert "nala_tts_rate" in block

    def test_auto_tts_window_audio_reference(self, nala_src):
        """window.__nalaAutoTtsAudio wird als Referenz auf das aktive Audio-Objekt genutzt."""
        assert "__nalaAutoTtsAudio" in nala_src

    def test_auto_tts_silent_degradation_on_error(self, nala_src):
        """Bei Fehler: console.warn statt Error-Popup (stille Degradation)."""
        idx = nala_src.find("function autoTtsPlay")
        block = nala_src[idx:idx + 2500]
        assert "console.warn" in block
        assert "[AUTO-TTS-186]" in block


class TestAutoTtsLifecycle:
    def test_auto_tts_stops_on_session_switch(self, nala_src):
        """Beim Session-Wechsel muss Auto-TTS-Audio gestoppt werden."""
        # loadSession-Block extrahieren
        idx = nala_src.find("async function loadSession")
        assert idx > 0
        block = nala_src[idx:idx + 2000]
        assert "_stopAutoTtsAudio" in block

    def test_auto_tts_stops_on_logout(self, nala_src):
        """Bei doLogout muss Auto-TTS-Audio gestoppt werden."""
        idx = nala_src.find("function doLogout()")
        assert idx > 0
        block = nala_src[idx:idx + 1500]
        assert "_stopAutoTtsAudio" in block

    def test_auto_tts_stops_on_401(self, nala_src):
        """Bei handle401 (Session abgelaufen) muss Auto-TTS gestoppt werden."""
        idx = nala_src.find("function handle401()")
        assert idx > 0
        block = nala_src[idx:idx + 1500]
        assert "_stopAutoTtsAudio" in block

    def test_auto_tts_toggle_off_stops_running_audio(self, nala_src):
        """onAutoTtsToggle(false) stoppt laufendes Audio sofort."""
        idx = nala_src.find("function onAutoTtsToggle")
        assert idx > 0
        block = nala_src[idx:idx + 800]
        assert "_stopAutoTtsAudio" in block


class TestAutoTtsTriggerTiming:
    def test_auto_tts_waits_for_done_event(self, nala_src):
        """autoTtsPlay wird NACH addMessage(reply, 'bot') aufgerufen, nicht pro Chunk.

        Der Chat-Pfad ist non-streaming → genau ein Trigger pro Antwort,
        der semantisch dem SSE-done-Moment entspricht.
        """
        idx = nala_src.find("addMessage(reply, 'bot')")
        assert idx > 0, "addMessage(reply, 'bot') nicht gefunden"
        # Das nächste autoTtsPlay-Vorkommen muss innerhalb der nächsten paar Zeilen sein
        post = nala_src[idx:idx + 600]
        assert "autoTtsPlay(reply)" in post or "autoTtsPlay(" in post

    def test_auto_tts_only_when_enabled(self, nala_src):
        """autoTtsPlay-Aufruf im Chat-Pfad muss durch isAutoTtsEnabled() geschützt sein."""
        idx = nala_src.find("addMessage(reply, 'bot')")
        post = nala_src[idx:idx + 600]
        assert "isAutoTtsEnabled" in post

    def test_auto_tts_skips_empty_text(self, nala_src):
        """Leere Bot-Antwort → kein TTS-Call."""
        # Schutz im Aufruf ODER in autoTtsPlay selbst
        idx = nala_src.find("function autoTtsPlay")
        block = nala_src[idx:idx + 800]
        assert ".trim()" in block


class TestAutoTtsBackendUntouched:
    def test_tts_endpoint_still_works(self, nala_src):
        """Regression: bestehender /nala/tts/speak-Endpoint unverändert vorhanden."""
        assert '@router.post("/tts/speak")' in nala_src
        assert "async def tts_speak" in nala_src

    def test_voices_endpoint_still_works(self, nala_src):
        """Regression: /nala/tts/voices unverändert vorhanden."""
        assert '@router.get("/tts/voices")' in nala_src

    def test_speak_text_still_exists(self, nala_src):
        """Regression: speakText() für 🔊-Button bleibt erhalten."""
        assert "async function speakText" in nala_src
