"""Patch 184 - Nala-Persona-Bug (Ton-Einstellung kommt nicht beim LLM an).

Diagnose-Ergebnis: load_system_prompt(profile_name) verdrahtet korrekt zur
profil-spezifischen Datei. Der Bug liegt nicht in der Verdrahtung sondern
am LLM-Verhalten — DeepSeek v3.2 ignorierte abstrakte System-Prompts bei
kurzen User-Anfragen ("wie geht's?") und fiel auf den Assistant-Default
zurueck ("Alles gut hier, danke").

Fix: profil-spezifische Personas mit explizitem AKTIVE-PERSONA-Marker
einleiten (_wrap_persona) und den finalen System-Prompt persistent als
[PERSONA-184] INFO-Log mitschneiden, damit der Verdrahtungs-Pfad bei
zukuenftigen Bug-Reports im Server-Log nachweisbar ist.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────────
#  Block 1 - load_system_prompt liest profil-spezifische Datei
# ──────────────────────────────────────────────────────────────────────


class TestLoadSystemPromptResolution:
    def test_profile_specific_file_takes_precedence(self, tmp_path, monkeypatch):
        # Frisches CWD ohne reale Files
        monkeypatch.chdir(tmp_path)
        Path("system_prompt.json").write_text(
            json.dumps({"prompt": "GENERIC"}), encoding="utf-8"
        )
        Path("system_prompt_alice.json").write_text(
            json.dumps({"prompt": "ALICE-PERSONA"}), encoding="utf-8"
        )
        from zerberus.app.routers.legacy import load_system_prompt
        assert load_system_prompt("alice") == "ALICE-PERSONA"
        assert load_system_prompt("ALICE") == "ALICE-PERSONA"  # case-insensitive
        # Falls profil-spezifische Datei fehlt → fallback auf generic
        assert load_system_prompt("bob") == "GENERIC"
        # Falls profile_name None → direkt generic (kein KeyError)
        assert load_system_prompt(None) == "GENERIC"

    def test_returns_empty_when_no_file_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zerberus.app.routers.legacy import load_system_prompt
        assert load_system_prompt("noone") == ""
        assert load_system_prompt(None) == ""


# ──────────────────────────────────────────────────────────────────────
#  Block 2 - _is_profile_specific_prompt erkennt Mein-Ton-Settings
# ──────────────────────────────────────────────────────────────────────


class TestIsProfileSpecificPrompt:
    def test_true_when_file_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path("system_prompt_alice.json").write_text(
            json.dumps({"prompt": "x"}), encoding="utf-8"
        )
        from zerberus.app.routers.legacy import _is_profile_specific_prompt
        assert _is_profile_specific_prompt("alice") is True
        assert _is_profile_specific_prompt("Alice") is True

    def test_false_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zerberus.app.routers.legacy import _is_profile_specific_prompt
        assert _is_profile_specific_prompt("alice") is False

    def test_false_for_none_profile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zerberus.app.routers.legacy import _is_profile_specific_prompt
        assert _is_profile_specific_prompt(None) is False
        assert _is_profile_specific_prompt("") is False


# ──────────────────────────────────────────────────────────────────────
#  Block 3 - _wrap_persona setzt den AKTIVE-PERSONA-Marker davor
# ──────────────────────────────────────────────────────────────────────


class TestWrapPersona:
    def test_wrap_prepends_aktive_persona_marker(self):
        from zerberus.app.routers.legacy import _wrap_persona
        original = "Du bist Josefine, eine Wiener Kurtisane."
        wrapped = _wrap_persona(original)
        assert wrapped.startswith("# AKTIVE PERSONA")
        assert "VERBINDLICH" in wrapped
        assert original in wrapped, "Original-Persona muss im Wrapping erhalten bleiben"

    def test_wrap_emphasizes_short_messages(self):
        """Kernpunkt des Bugs: bei kurzen Anfragen wurde Persona ignoriert.
        Der Wrap-Text muss explizit darauf hinweisen, dass die Persona
        AUCH bei kurzen Nachrichten gilt."""
        from zerberus.app.routers.legacy import _wrap_persona
        wrapped = _wrap_persona("X")
        assert "kurzen" in wrapped.lower() or "kurz" in wrapped.lower()
        assert "rolle" in wrapped.lower() or "persona" in wrapped.lower()


# ──────────────────────────────────────────────────────────────────────
#  Block 4 - chat_completions injiziert die gewrappte Persona
# ──────────────────────────────────────────────────────────────────────


def _build_request(messages, profile_name="alice"):
    """Minimal-Mock fuer FastAPI Request mit profile_name in state."""
    from types import SimpleNamespace
    state = SimpleNamespace(
        profile_name=profile_name,
        permission_level="admin",
        allowed_model=None,
        temperature=None,
    )
    headers = {"X-Session-ID": "test-session"}
    return SimpleNamespace(
        state=state,
        headers=headers,
    )


class TestChatCompletionsInjectsPersona:
    """Unit-Test des chat_completions-Pfads. Wir mocken den Orchestrator-
    Aufruf und LLMService.call, um nur die System-Prompt-Assembly zu
    pruefen."""

    @pytest.fixture
    def setup_persona_file(self, tmp_path, monkeypatch):
        # Settings VOR chdir laden (Singleton-Cache), sonst sucht load_settings
        # config.yaml in tmp_path und scheitert.
        from zerberus.core.config import get_settings
        get_settings()
        monkeypatch.chdir(tmp_path)
        Path("system_prompt_alice.json").write_text(
            json.dumps({
                "prompt": "Du bist Josefine, Wiener Kurtisane mit scharfem Schmaeh."
            }),
            encoding="utf-8",
        )
        Path("system_prompt.json").write_text(
            json.dumps({"prompt": "Default Nala-Stil."}),
            encoding="utf-8",
        )
        return tmp_path

    def test_persona_with_marker_reaches_llm(self, setup_persona_file, monkeypatch):
        """Mock LLM.call und pruefe dass der system-Prompt im messages-Array
        sowohl den AKTIVE-PERSONA-Marker als auch die Mutzenbacher-Phrase
        enthaelt — beides muss durchgehen, sonst hat der Wrap nichts gebracht."""
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService

        captured: dict = {}

        async def fake_call(self, messages, session_id, model_override=None, temperature_override=None):
            captured["messages"] = list(messages)
            return ("test-antwort", "test-model", 10, 5, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)
        # Orchestrator-Pipeline ausschalten, sonst zieht RAG/Intent rein
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        # Settings-Stub
        from zerberus.core.config import get_settings
        settings = get_settings()

        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content="Wie gehts?")]
        )
        request = _build_request(req.messages, profile_name="alice")

        result = asyncio.run(
            legacy_mod.chat_completions(request, req, settings)
        )

        assert "messages" in captured, "LLMService.call wurde nicht aufgerufen"
        sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
        assert sys_msgs, "Kein System-Message im LLM-Call"
        sys_content = sys_msgs[0]["content"]
        assert "AKTIVE PERSONA" in sys_content, (
            "P184: AKTIVE-PERSONA-Marker fehlt im LLM-Call"
        )
        assert "Josefine" in sys_content, (
            "P184: Originale Persona-Phrase fehlt im LLM-Call"
        )
        assert "Wiener" in sys_content

    def test_no_wrap_for_default_profile(self, tmp_path, monkeypatch):
        """Wenn KEINE profil-spezifische Datei existiert, soll der generische
        Nala-Default OHNE AKTIVE-PERSONA-Marker reingehen (das ist Default-
        Verhalten, nicht Persona)."""
        from zerberus.core.config import get_settings
        get_settings()  # Singleton-Cache befuellen vor chdir
        monkeypatch.chdir(tmp_path)
        Path("system_prompt.json").write_text(
            json.dumps({"prompt": "Default Nala-Stil."}),
            encoding="utf-8",
        )
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService

        captured: dict = {}

        async def fake_call(self, messages, session_id, model_override=None, temperature_override=None):
            captured["messages"] = list(messages)
            return ("test-antwort", "test-model", 10, 5, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        from zerberus.core.config import get_settings
        settings = get_settings()

        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content="Wie gehts?")]
        )
        request = _build_request(req.messages, profile_name="bob")  # ohne eigene .json

        asyncio.run(legacy_mod.chat_completions(request, req, settings))

        sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
        assert sys_msgs
        assert "AKTIVE PERSONA" not in sys_msgs[0]["content"], (
            "P184: Generischer Default darf NICHT als AKTIVE PERSONA gewrappt sein"
        )
        assert "Default Nala-Stil." in sys_msgs[0]["content"]

    def test_existing_system_message_not_overwritten(self, setup_persona_file, monkeypatch):
        """Wenn der Caller bereits eine system-Message in req.messages mitschickt,
        DARF die Persona NICHT eingefuegt werden — der Caller hat dann eine
        explizite Wahl getroffen (z.B. interne Pipeline-Aufrufe)."""
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.llm import LLMService

        captured: dict = {}

        async def fake_call(self, messages, session_id, model_override=None, temperature_override=None):
            captured["messages"] = list(messages)
            return ("ok", "m", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        from zerberus.core.config import get_settings
        settings = get_settings()

        req = legacy_mod.ChatCompletionRequest(
            messages=[
                legacy_mod.Message(role="system", content="CUSTOM-CALLER-PROMPT"),
                legacy_mod.Message(role="user", content="hi"),
            ]
        )
        request = _build_request(req.messages, profile_name="alice")

        asyncio.run(legacy_mod.chat_completions(request, req, settings))

        sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
        # Es darf nur EINE system-Message geben (die vom Caller), keine zweite
        # mit AKTIVE PERSONA.
        assert len(sys_msgs) == 1
        assert "CUSTOM-CALLER-PROMPT" in sys_msgs[0]["content"]
        assert "AKTIVE PERSONA" not in sys_msgs[0]["content"]


# ──────────────────────────────────────────────────────────────────────
#  Block 5 - Source-Audit: Debug-Log + Pfad-Doku
# ──────────────────────────────────────────────────────────────────────


class TestSourceAudit:
    @pytest.fixture(scope="class")
    def legacy_src(self) -> str:
        path = Path(__file__).resolve().parents[1] / "app" / "routers" / "legacy.py"
        return path.read_text(encoding="utf-8")

    def test_persona_log_marker_present(self, legacy_src):
        """Der [PERSONA-184]-Log muss persistent drin sein (kein TEMP-Tag),
        damit kuenftige Persona-Verdrahtungs-Bugs sofort im Server-Log
        diagnostizierbar sind."""
        assert "[PERSONA-184]" in legacy_src
        # Log muss auf INFO-Level liegen, nicht DEBUG
        assert "logger.info(" in legacy_src.split("[PERSONA-184]")[0].rsplit("\n", 5)[1] or \
               "logger.info" in legacy_src[
                   max(0, legacy_src.find("[PERSONA-184]") - 200):
                   legacy_src.find("[PERSONA-184]")
               ]

    def test_wrap_persona_function_exists(self, legacy_src):
        assert "def _wrap_persona(" in legacy_src
        assert "AKTIVE PERSONA" in legacy_src
        assert "VERBINDLICH" in legacy_src

    def test_wrap_only_for_profile_specific(self, legacy_src):
        """Der Wrap-Aufruf muss durch _is_profile_specific_prompt geschuetzt
        sein — sonst wird der generische Default als Persona markiert."""
        assert "_is_profile_specific_prompt" in legacy_src
        # Der Wrap und der Specific-Check muessen in derselben Region stehen
        idx = legacy_src.find("persona_active = _is_profile_specific_prompt")
        assert idx > 0
        snippet = legacy_src[idx:idx + 500]
        assert "_wrap_persona" in snippet
