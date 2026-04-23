"""
Nala Router – The Palace (Frontend + Voice + Archiv-Seitenleiste).
Patch 41: /nala/voice nutzt vollständige Pipeline:
          Whisper → Cleaner → Dialekterkennung → Orchestrator (RAG + LLM + Auto-Index).
Patch 43: Session-Kontext vollständig im Orchestrator (_run_pipeline).
          nala.py delegiert History, RAG, LLM und Store an _run_pipeline().
Patch 44: User-Profile (Chris / Rosa) + editierbares Transkript.
Patch 45: Offene Profile (Textfeld statt feste Buttons), Rollenstabilität,
          Input sticky, neue Session beim Start, Passwort-Toggle.
Patch 46: SSE EventBus Streaming – GET /nala/events streamt Pipeline-Events
          live ans Frontend (Status-Bar unter Header).
"""
import asyncio
import io
import logging
import uuid
import json
from pathlib import Path

import bcrypt
import jwt as _jwt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
import httpx

from zerberus.core.config import get_settings, Settings
from zerberus.core.dialect import detect_dialect_marker, apply_dialect
from zerberus.core.cleaner import clean_transcript
from zerberus.core.event_bus import get_event_bus, Event
from zerberus.core.database import store_interaction
from zerberus.app.pacemaker import update_interaction

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nala", tags=["Nala"])

# Orchestrator-Pipeline direkt importieren (kein HTTP-Roundtrip)
try:
    from zerberus.app.routers.orchestrator import _run_pipeline
    _ORCH_PIPELINE_OK = True
except Exception as _orch_err:
    logger.warning(f"Orchestrator-Pipeline nicht verfügbar: {_orch_err}")
    _ORCH_PIPELINE_OK = False


# ---------------------------------------------------------------------------
# Hilfsfunktionen für Profil-Authentifizierung
# ---------------------------------------------------------------------------

def _load_profiles() -> dict:
    """Liest den profiles-Abschnitt aus config.yaml."""
    import yaml
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("profiles", {})


def _save_profile_hash(profile_key: str, hashed: str) -> None:
    """Schreibt den bcrypt-Hash für ein Profil zurück in config.yaml."""
    import yaml
    cfg_path = Path("config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data.setdefault("profiles", {})[profile_key]["password_hash"] = hashed
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Pydantic-Modelle
# ---------------------------------------------------------------------------

class ProfileLoginRequest(BaseModel):
    profile: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ---------------------------------------------------------------------------
# SSE Event-Mapping
# ---------------------------------------------------------------------------

_SSE_MESSAGES = {
    "rag_search":       "🔍 Suche im Gedächtnis...",
    "intent_detected":  "💡 Verstanden: {intent}",
    "llm_start":        "✍️ Antwort kommt...",
    "llm_response":     "✍️ Antwort kommt...",
    "rag_indexed":      "📝 Gespeichert.",
}


# ---------------------------------------------------------------------------
# NALA_HTML – Patch 46: SSE Status-Bar + EventSource
# ---------------------------------------------------------------------------
NALA_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes, interactive-widget=resizes-content">
    <title>Nala – Der Palast</title>
    <link rel="icon" href="/static/favicon.ico">
    <style>
        :root {
            --color-primary: #0a1628;
            --color-primary-mid: #1a2f4e;
            --color-gold: #f0b429;
            --color-gold-dark: #c8941f;
            --color-text-light: #e8eaf0;
            --color-accent: #ec407a;
            /* Patch 86/109: Bubble-Farben — Defaults als rgba (leicht transparent
               für optische Tiefe). Gleiche Farbwerte wie --color-accent / --color-primary-mid,
               nur mit Alpha. Anti-Invariante: NIE schwarz auf schwarz — Bubbles müssen
               stets lesbar sein, selbst ohne Theme/Favorit. */
            --bubble-user-bg: rgba(236, 64, 122, 0.88);
            --bubble-user-text: var(--color-primary);
            --bubble-llm-bg: rgba(26, 47, 78, 0.85);
            --bubble-llm-text: var(--color-text-light);
            /* Patch 86: Chat-Schriftgröße */
            --font-size-base: 15px;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Segoe UI', Roboto, system-ui, sans-serif;
            background: var(--color-primary);
            min-height: 100dvh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 0;
        }
        .app-container {
            width: 100%;
            max-width: 500px;
            height: 100dvh;
            background: var(--color-primary);
            display: flex;
            flex-direction: column;
            position: relative;
            box-shadow: 0 0 20px rgba(0,0,0,0.4);
        }

        /* ── Login-Screen ── */
        #login-screen {
            position: absolute;
            inset: 0;
            background: var(--color-primary-mid);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 16px;
            z-index: 2000;
            padding: 30px;
        }
        #login-screen h2 {
            font-size: 1.8em;
            color: var(--color-gold);
            margin-bottom: 8px;
        }
        #login-username {
            width: 100%;
            max-width: 280px;
            padding: 12px 18px;
            border: 2px solid #2a4068;
            border-radius: 30px;
            font-size: 16px;
            outline: none;
            background: var(--color-primary);
            color: var(--color-text-light);
            transition: border 0.2s;
        }
        #login-username:focus { border-color: var(--color-gold); }
        .pw-wrapper {
            position: relative;
            width: 100%;
            max-width: 280px;
        }
        #login-password {
            width: 100%;
            padding: 12px 44px 12px 18px;
            border: 2px solid #2a4068;
            border-radius: 30px;
            font-size: 16px;
            outline: none;
            background: var(--color-primary);
            color: var(--color-text-light);
            transition: border 0.2s;
        }
        #login-password:focus { border-color: var(--color-gold); }
        .pw-toggle {
            position: absolute;
            right: 14px;
            top: 50%;
            transform: translateY(-50%);
            background: none;
            border: none;
            cursor: pointer;
            padding: 0;
            color: #6a8ab8;
            display: flex;
            align-items: center;
            line-height: 1;
        }
        .pw-toggle:hover, .pw-toggle:active { color: var(--color-gold); }
        #login-submit {
            padding: 12px 40px;
            background: var(--color-gold);
            color: var(--color-primary);
            border: none;
            border-radius: 30px;
            font-size: 1.1em;
            font-weight: bold;
            cursor: pointer;
            transition: background 0.2s;
        }
        #login-submit:hover, #login-submit:active { background: var(--color-gold-dark); }
        .login-hint {
            color: #6a8ab8;
            font-size: 0.82em;
        }
        #login-error {
            color: #ff6b6b;
            font-size: 0.9em;
            min-height: 20px;
        }

        /* ── Chat-Oberfläche ── */
        #chat-screen { display: none; flex-direction: column; height: 100%; }
        .header {
            color: var(--color-text-light);
            padding: 15px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 1.5em;
            font-weight: bold;
            background: var(--color-accent);
            border-bottom: 2px solid var(--color-gold);
            transition: background 0.4s;
        }
        .hamburger {
            font-size: 1.8em;
            cursor: pointer;
            width: 40px;
            text-align: center;
        }
        .title {
            flex: 1;
            text-align: center;
            color: var(--color-gold);
        }
        .profile-badge {
            font-size: 0.55em;
            font-weight: normal;
            opacity: 0.85;
            margin-left: 6px;
            color: var(--color-text-light);
        }
        /* ── Status-Bar (Patch 46: SSE) ── */
        #status-bar {
            text-align: center;
            font-size: 0.78em;
            color: var(--color-gold);
            padding: 4px 12px;
            min-height: 22px;
            background: var(--color-primary-mid);
            border-bottom: 1px solid #2a4068;
            opacity: 0;
            transition: opacity 0.3s ease;
        }

        .sidebar {
            position: fixed;
            top: 0;
            left: -300px;
            width: 280px;
            height: 100dvh;
            background: var(--color-primary-mid);
            color: var(--color-text-light);
            transition: left 0.3s ease;
            z-index: 1000;
            overflow-y: auto;
            padding: 20px;
            box-shadow: 2px 0 10px rgba(0,0,0,0.5);
        }
        .sidebar.open { left: 0; }
        .sidebar .close-btn {
            font-size: 2em;
            cursor: pointer;
            text-align: right;
            margin-bottom: 20px;
            color: var(--color-gold);
        }
        .sidebar h3 {
            margin: 20px 0 10px;
            color: var(--color-gold);
        }
        .session-list { list-style: none; }
        .session-item {
            padding: 12px;
            margin-bottom: 8px;
            background: var(--color-primary);
            border-radius: 10px;
            cursor: pointer;
            word-wrap: break-word;
            color: var(--color-text-light);
            border-left: 3px solid transparent;
            transition: all 0.15s ease;
        }
        .session-item:hover, .session-item:active { background: #0f1e38; border-left-color: var(--color-gold); }
        .session-item.pinned { border-left-color: var(--color-gold); }
        .session-date { font-size: 0.8em; color: #6a8ab8; }
        .my-prompt-area {
            width: 100%;
            min-height: 120px;
            background: var(--color-primary);
            color: var(--color-text-light);
            border: 1px solid #2a4068;
            border-radius: 8px;
            padding: 10px;
            font-size: 0.82em;
            font-family: inherit;
            resize: vertical;
            margin-top: 6px;
        }
        .my-prompt-area:focus { outline: none; border-color: var(--color-gold); }
        .my-prompt-save-btn {
            margin-top: 8px;
            width: 100%;
            padding: 9px;
            background: var(--color-gold);
            color: var(--color-primary);
            border: none;
            border-radius: 8px;
            font-size: 0.9em;
            font-weight: bold;
            cursor: pointer;
        }
        .my-prompt-save-btn:hover, .my-prompt-save-btn:active { background: var(--color-gold-dark); }
        .my-prompt-status { font-size: 0.78em; color: #6a8ab8; margin-top: 5px; min-height: 16px; }
        .overlay {
            display: none;
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: rgba(0,0,0,0.6);
            z-index: 999;
        }
        .overlay.show { display: block; }
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            background: var(--color-primary);
        }
        .message {
            max-width: 80%;
            padding: 13px 18px;
            border-radius: 20px;
            word-wrap: break-word;
            animation: fadeIn 0.3s;
            font-size: var(--font-size-base);
        }
        .user-message {
            align-self: flex-end;
            color: var(--bubble-user-text);
            background: var(--bubble-user-bg);
            border-radius: 18px 18px 4px 18px;
            box-shadow: 0 1px 6px rgba(240,180,41,0.15);
        }
        .bot-message {
            align-self: flex-start;
            background: var(--bubble-llm-bg);
            color: var(--bubble-llm-text);
            border-radius: 18px 18px 18px 4px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.3);
        }
        .input-area {
            display: flex;
            flex-direction: column;
            padding: 12px 15px 10px;
            background: var(--color-primary-mid);
            border-top: 1px solid #2a4068;
            gap: 6px;
            position: sticky;
            bottom: 0;
            z-index: 10;
            backdrop-filter: blur(8px);
        }
        .input-row {
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }
        /* Patch 67: textarea statt input */
        #text-input {
            flex: 1;
            padding: 10px 16px;
            border: 1px solid rgba(240,180,41,0.3);
            border-radius: 20px;
            font-size: var(--font-size-base);
            outline: none;
            background: var(--color-primary);
            color: var(--color-text-light);
            transition: border 0.2s, box-shadow 0.2s;
            resize: none;
            overflow-y: hidden;
            min-height: 48px;
            max-height: 140px;
            line-height: 1.45;
            font-family: inherit;
        }
        #text-input:focus { border-color: var(--color-gold); box-shadow: 0 0 0 2px rgba(240,180,41,0.15); }
        .send-btn, .mic-btn {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            border: none;
            color: var(--color-primary);
            font-size: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.15s ease;
            box-shadow: 0 2px 8px rgba(240,180,41,0.3);
        }
        .send-btn { background: var(--color-gold); }
        .mic-btn  { background: var(--color-gold); }
        .mic-btn.recording {
            background: #f44336;
            animation: pulse 1.5s infinite;
        }
        .send-btn:hover, .send-btn:active { background: var(--color-gold-dark); transform: scale(1.05); box-shadow: 0 4px 14px rgba(240,180,41,0.5); }
        .mic-btn:hover, .mic-btn:active  { background: var(--color-gold-dark); transform: scale(1.05); box-shadow: 0 4px 14px rgba(240,180,41,0.5); }
        .send-btn:active, .mic-btn:active { transform: scale(0.95); }
        #transcript-hint {
            font-size: 0.78em;
            color: var(--color-gold);
            padding: 0 6px;
            min-height: 18px;
            transition: opacity 0.2s;
        }
        .mic-error {
            color: #ff6b6b;
            font-size: 0.8em;
            text-align: center;
        }
        @keyframes pulse {
            0%  { box-shadow: 0 0 0 0 rgba(244,67,54,0.7); }
            70% { box-shadow: 0 0 0 10px rgba(244,67,54,0); }
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        /* Patch 76: Whisper-Verarbeitung – Gold-Puls */
        @keyframes pulseGold {
            0%   { box-shadow: 0 0 0 0 rgba(198,160,51,0.8); }
            70%  { box-shadow: 0 0 0 12px rgba(198,160,51,0); }
            100% { box-shadow: 0 0 0 0 rgba(198,160,51,0); }
        }
        .mic-btn.processing {
            background: var(--color-gold);
            animation: pulseGold 1.2s infinite;
            opacity: 0.85;
        }
        /* Patch 76: Typing-Indicator (drei springende Punkte) */
        .typing-indicator {
            align-self: flex-start;
            background: var(--bubble-llm-bg);
            border-radius: 20px;
            border-bottom-left-radius: 5px;
            padding: 14px 18px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.3);
            animation: fadeIn 0.3s;
            display: flex;
            gap: 6px;
            align-items: center;
        }
        .typing-dot {
            width: 8px; height: 8px;
            background: var(--color-gold);
            border-radius: 50%;
            animation: typingBounce 1.2s infinite;
            opacity: 0.6;
        }
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typingBounce {
            0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
            30%            { transform: translateY(-6px); opacity: 1; }
        }
        /* Patch 102 (B-03/B-09/B-11): Spinner-Rad + Status-Text + Error-Bubble */
        .spinner-rad {
            width: 16px; height: 16px;
            border: 2px solid rgba(240, 180, 41, 0.25);
            border-top-color: var(--color-gold);
            border-radius: 50%;
            animation: spin 0.9s linear infinite;
            flex-shrink: 0;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .typing-status {
            margin-left: 8px;
            font-size: 0.85em;
            color: var(--color-text-dim, #9aa);
        }
        .typing-indicator.frozen .spinner-rad {
            animation-play-state: paused;
            border-top-color: rgba(240, 180, 41, 0.4);
        }
        .typing-indicator.error-state {
            background: rgba(220, 80, 60, 0.15);
            border-left: 3px solid #d64;
        }
        .typing-indicator .retry-inline {
            margin-left: 10px;
            background: var(--color-gold);
            color: var(--color-primary, #001f3f);
            border: none;
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 0.8em;
            cursor: pointer;
        }
        .typing-indicator .retry-inline:hover { opacity: 0.85; }

        /* ── Sidebar-Aktionen (Patch 67) ── */
        .sidebar-actions {
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
        }
        .sidebar-action-btn {
            flex: 1;
            padding: 9px 6px;
            background: var(--color-primary);
            color: var(--color-gold);
            border: 1px solid var(--color-gold);
            border-radius: 8px;
            font-size: 0.8em;
            cursor: pointer;
            text-align: center;
            transition: all 0.15s ease;
        }
        .sidebar-action-btn:hover, .sidebar-action-btn:active { background: rgba(240,180,41,0.08); border-color: var(--color-gold); }

        /* ── Expand-Button Vollbild (Patch 67) ── */
        .expand-btn {
            width: 36px;
            height: 48px;
            border-radius: 10px;
            border: 1px solid #2a4068;
            background: transparent;
            color: #6a8ab8;
            font-size: 18px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .expand-btn:hover, .expand-btn:active { color: var(--color-gold); border-color: var(--color-gold); }

        /* ── Easter Egg Overlay (Patch 100) ── */
        #ee-modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.88);
            z-index: 9999;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 1.5s ease;
        }
        #ee-modal.open { display: flex; opacity: 1; }
        .ee-inner {
            position: relative;
            width: 88vw;
            max-width: 720px;
            max-height: 80vh;
            overflow-y: auto;
            background: rgba(10, 22, 40, 0.92);
            border: 2px solid #DAA520;
            border-radius: 16px;
            padding: 28px 24px 22px;
            text-align: center;
            color: #f0f0f0;
            font-family: inherit;
            box-shadow: 0 0 60px rgba(218, 165, 32, 0.35);
        }
        .ee-close {
            position: absolute;
            top: 10px;
            right: 14px;
            background: transparent;
            color: #DAA520;
            border: none;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            min-width: 44px;
            min-height: 44px;
            -webkit-tap-highlight-color: transparent;
        }
        .ee-close:active { color: #ffd700; }
        .ee-img {
            display: block;
            max-width: 100%;
            max-height: 60vh;
            margin: 0 auto 18px;
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.6);
        }
        .ee-title {
            font-size: 1.3em;
            font-weight: bold;
            color: #DAA520;
            margin-bottom: 10px;
        }
        .ee-quote {
            font-style: italic;
            color: #ffd700;
            margin: 6px 0 16px;
            font-size: 1.05em;
        }
        .ee-body {
            color: #e0e8f8;
            line-height: 1.6;
            margin-bottom: 14px;
        }
        .ee-emojis {
            color: #DAA520;
            line-height: 1.8;
            margin-bottom: 16px;
        }
        .ee-close-btn {
            padding: 10px 28px;
            background: #DAA520;
            color: #0a1628;
            border: none;
            border-radius: 24px;
            font-weight: bold;
            cursor: pointer;
            font-size: 15px;
            -webkit-tap-highlight-color: transparent;
        }
        .ee-close-btn:active, .ee-close-btn:hover { background: #ffd700; }

        /* ── Fullscreen-Modal (Patch 67) ── */
        #fullscreen-modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.88);
            z-index: 5000;
            align-items: center;
            justify-content: center;
        }
        #fullscreen-modal.open { display: flex; }
        .fullscreen-inner {
            width: 88vw;
            max-width: 680px;
            height: 68vh;
            background: var(--color-primary-mid);
            border-radius: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            padding: 20px;
            border: 1px solid #2a4068;
        }
        .fullscreen-inner h4 { color: var(--color-gold); font-size: 1em; }
        #fullscreen-textarea {
            flex: 1;
            width: 100%;
            background: var(--color-primary);
            color: var(--color-text-light);
            border: 2px solid #2a4068;
            border-radius: 12px;
            padding: 14px;
            font-size: 15px;
            font-family: inherit;
            resize: none;
            outline: none;
            line-height: 1.5;
        }
        #fullscreen-textarea:focus { border-color: var(--color-gold); }
        .fullscreen-btn-row {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
        }
        .fs-accept-btn {
            padding: 10px 22px;
            background: var(--color-gold);
            color: var(--color-primary);
            border: none;
            border-radius: 10px;
            font-weight: bold;
            cursor: pointer;
        }
        .fs-cancel-btn {
            padding: 10px 14px;
            background: transparent;
            color: #6a8ab8;
            border: 1px solid #2a4068;
            border-radius: 10px;
            cursor: pointer;
        }

        /* ── Bubble-Toolbar: Timestamp + Kopieren (Patch 67) ── */
        .msg-toolbar {
            display: flex;
            align-items: center;
            gap: 6px;
            opacity: 0;
            transition: opacity 0.18s;
            font-size: 0.7em;
            color: #6a8ab8;
            padding: 1px 6px;
            min-height: 18px;
        }
        .msg-wrapper:hover .msg-toolbar { opacity: 1; }
        .copy-btn, .bubble-action-btn {
            background: none;
            border: none;
            cursor: pointer;
            color: #6a8ab8;
            font-size: 0.95em;
            padding: 0 1px;
            line-height: 1;
            min-width: 28px;
            min-height: 28px;
        }
        .copy-btn:hover, .copy-btn:active,
        .bubble-action-btn:hover, .bubble-action-btn:active { color: var(--color-gold); }
        .copy-ok { color: #4caf50 !important; }
        /* Patch 98: Touch-Geräte dauerhaft sichtbar (kein hover) */
        @media (hover: none) and (pointer: coarse) {
            .msg-toolbar { opacity: 0.55; }
            .copy-btn, .bubble-action-btn { min-width: 44px; min-height: 44px; font-size: 1.05em; }
        }

        /* ── Export-Dropdown (Patch 65) ── */
        .msg-wrapper {
            display: flex;
            flex-direction: column;
            align-self: flex-start;
            gap: 3px;
            max-width: 80%;
        }
        .msg-wrapper.user-wrapper {
            align-self: flex-end;
        }
        .export-row {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 0 4px;
        }
        .export-select {
            font-size: 0.72em;
            background: transparent;
            color: #6a8ab8;
            border: 1px solid #2a4068;
            border-radius: 8px;
            padding: 2px 6px;
            cursor: pointer;
            outline: none;
            appearance: none;
            -webkit-appearance: none;
        }
        .export-select:focus { border-color: var(--color-gold); color: var(--color-gold); }
        .export-select option { background: var(--color-primary-mid); color: var(--color-text-light); }

        /* ── Patch 77: Session-Item-Struktur (A1) ── */
        .session-item-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 4px; }
        .session-title { font-size: 0.88em; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; color: var(--color-text-light); }
        .session-ts { font-size: 0.7em; color: #6a8ab8; white-space: nowrap; flex-shrink: 0; padding-top: 1px; }
        .session-preview { font-size: 0.77em; color: #8aa0c0; margin-top: 3px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.4; }
        .session-pin-btn { background: none; border: none; cursor: pointer; color: #6a8ab8; font-size: 0.85em; padding: 0 2px; flex-shrink: 0; line-height: 1; }
        .session-pin-btn:hover, .session-pin-btn:active, .session-pin-btn.pinned { color: var(--color-gold); }
        /* ── Patch 77: Archiv-Suche (A2) ── */
        .archive-search { width: 100%; padding: 8px 12px; background: var(--color-primary); border: 1px solid rgba(240,180,41,0.3); border-radius: 20px; color: var(--color-text-light); font-size: 0.85em; outline: none; margin-bottom: 10px; box-sizing: border-box; }
        .archive-search:focus { border-color: var(--color-gold); }
        /* ── Patch 77: Sidebar-Header-Trennlinie (B4) ── */
        .sidebar-header { border-bottom: 1px solid rgba(240,180,41,0.2); padding-bottom: 14px; margin-bottom: 10px; }
        /* ── Patch 77: Theme-Modal (C) — Patch 86: zu Settings-Modal erweitert ── */
        #settings-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 3000; align-items: center; justify-content: center; }
        #settings-modal.open { display: flex; }
        .theme-modal-inner { background: var(--color-primary-mid); border: 1px solid var(--color-gold); border-radius: 12px; padding: 24px 28px; min-width: 280px; max-width: 360px; width: 90%; }
        .theme-modal-inner h4 { color: var(--color-gold); margin: 0 0 18px; }
        .theme-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
        .theme-row label { font-size: 0.88em; color: var(--color-text-light); }
        .theme-row input[type="color"] { width: 48px; height: 30px; border: 1px solid #2a4068; border-radius: 6px; cursor: pointer; background: none; padding: 2px; }
        .theme-fav-row { display: flex; gap: 8px; margin-bottom: 10px; }
        .theme-fav-btn { flex: 1; padding: 7px 4px; background: var(--color-primary); color: var(--color-text-light); border: 1px solid #2a4068; border-radius: 6px; font-size: 0.78em; cursor: pointer; text-align: center; }
        .theme-fav-btn:hover, .theme-fav-btn:active { border-color: var(--color-gold); color: var(--color-gold); }
        .theme-btn-row { display: flex; gap: 8px; margin-top: 8px; }

        /* ── Patch 86: Top-Bar Icon-Buttons (Settings + Export) ── */
        .icon-btn {
            width: 44px;
            height: 44px;
            border: 1px solid var(--color-gold);
            border-radius: 10px;
            background: transparent;
            color: var(--color-gold);
            font-size: 1.1em;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-left: 6px;
            transition: background 0.15s ease;
        }
        .icon-btn:hover, .icon-btn:active { background: rgba(240,180,41,0.15); }

        /* ── Patch 86: Abmelden-Button dezent ── */
        .sidebar-action-btn-muted {
            opacity: 0.7;
            background: transparent !important;
            color: var(--color-text-light) !important;
            border-color: #2a4068 !important;
        }
        .sidebar-action-btn-muted:hover, .sidebar-action-btn-muted:active {
            opacity: 1;
            border-color: var(--color-gold) !important;
            color: var(--color-gold) !important;
        }

        /* ── Patch 86: Settings-Modal Sektionen ── */
        .settings-section { margin-top: 14px; padding-top: 12px; border-top: 1px solid rgba(240,180,41,0.2); }
        .settings-section h5 { color: var(--color-gold); margin: 0 0 10px; font-size: 0.92em; }
        .bubble-picker-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; gap: 8px; }
        .bubble-picker-row label { font-size: 0.84em; color: var(--color-text-light); flex: 1; }
        .bubble-picker-row input[type="color"] { width: 44px; height: 30px; border: 1px solid #2a4068; border-radius: 6px; cursor: pointer; background: none; padding: 2px; }
        .bubble-reset-btn {
            background: transparent;
            border: 1px solid #2a4068;
            border-radius: 6px;
            color: #6a8ab8;
            width: 28px;
            height: 28px;
            cursor: pointer;
            font-size: 0.9em;
            padding: 0;
        }
        .bubble-reset-btn:hover, .bubble-reset-btn:active { color: var(--color-gold); border-color: var(--color-gold); }
        .font-preset-row { display: flex; gap: 6px; margin-bottom: 8px; }
        .font-preset-btn {
            flex: 1;
            min-height: 44px;
            background: var(--color-primary);
            color: var(--color-text-light);
            border: 1px solid #2a4068;
            border-radius: 8px;
            font-size: 0.82em;
            cursor: pointer;
            padding: 6px 4px;
            transition: all 0.15s ease;
        }
        .font-preset-btn:hover, .font-preset-btn:active { border-color: var(--color-gold); color: var(--color-gold); }
        .font-preset-btn.active { border-color: var(--color-gold); color: var(--color-gold); background: rgba(240,180,41,0.08); }

        /* ── Patch 86: Settings-Modal responsiv (scrollbar auf Mobile) ── */
        .settings-modal-inner { max-height: 88vh; overflow-y: auto; }

        /* ── Patch 86: Export-Modal ── */
        #export-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 3000; align-items: center; justify-content: center; }
        #export-modal.open { display: flex; }
        .export-modal-inner { background: var(--color-primary-mid); border: 1px solid var(--color-gold); border-radius: 12px; padding: 22px 24px; min-width: 260px; max-width: 340px; width: 90%; }
        .export-modal-inner h4 { color: var(--color-gold); margin: 0 0 16px; }
        .export-opt-btn {
            display: block;
            width: 100%;
            min-height: 44px;
            padding: 10px 12px;
            margin-bottom: 8px;
            background: var(--color-primary);
            color: var(--color-text-light);
            border: 1px solid #2a4068;
            border-radius: 8px;
            font-size: 0.9em;
            cursor: pointer;
            text-align: left;
            transition: all 0.15s ease;
        }
        .export-opt-btn:hover, .export-opt-btn:active { border-color: var(--color-gold); color: var(--color-gold); }
        .export-cancel-btn {
            width: 100%;
            min-height: 44px;
            margin-top: 4px;
            background: transparent;
            color: #6a8ab8;
            border: 1px solid #2a4068;
            border-radius: 8px;
            font-size: 0.88em;
            cursor: pointer;
        }
        .export-toast {
            position: fixed;
            bottom: 90px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--color-primary-mid);
            color: var(--color-gold);
            border: 1px solid var(--color-gold);
            border-radius: 8px;
            padding: 10px 18px;
            font-size: 0.88em;
            z-index: 4000;
            animation: fadeIn 0.2s;
            box-shadow: 0 2px 12px rgba(0,0,0,0.5);
        }

        /* Patch 83: Sternenregen bei Tap */
        .tap-star {
            position: fixed;
            pointer-events: none;
            z-index: 9999;
            opacity: 1;
            animation: starFall 1.2s cubic-bezier(0.25, 0, 0.7, 1) forwards;
        }
        @keyframes starFall {
            0%   { transform: translateY(0) translateX(0) scale(1); opacity: 1; }
            60%  { opacity: 0.8; }
            100% { transform: translateY(120px) translateX(var(--fall-x)) scale(0.3); opacity: 0; }
        }
        /* ── Patch 90 (N-F10): Landscape mit niedrigem Viewport (z.B. iPhone 14 quer ~390px hoch) ── */
        @media (orientation: landscape) and (max-height: 500px) {
            .header { padding: 8px 12px; font-size: 1.15em; }
            .hamburger { font-size: 1.4em; width: 34px; }
            #status-bar { font-size: 0.72em; padding: 2px 12px; min-height: 16px; }
            .input-area { padding: 6px 12px 6px; }
            #text-input { min-height: 40px; max-height: 96px; padding: 7px 14px; }
            .my-prompt-area { min-height: 80px; }
            .fullscreen-inner { height: 90vh; max-height: 90dvh; padding: 12px; }
            .settings-modal-inner { max-height: 92vh; }
            .sidebar { padding: 14px; }
            .sidebar .close-btn { margin-bottom: 10px; }
        }
    </style>
    <script>
        /* Patch 77 C3 + Patch 86: Theme + Bubble + Schriftgröße aus localStorage – vor erstem Render
           Patch 103 B-13/14: Wenn ein Favoriten-Slot als "last active" markiert ist, hat der Vorrang. */
        (function() {
            var r = document.documentElement.style;
            var favApplied = false;
            try {
                var lastFav = localStorage.getItem('nala_last_active_favorite');
                if (lastFav) {
                    var favStored = localStorage.getItem('nala_theme_fav_' + lastFav);
                    if (favStored) {
                        var fav = JSON.parse(favStored);
                        var t = (fav && fav.v === 2) ? fav.theme : fav;
                        if (t) {
                            if (t.primary)   r.setProperty('--color-primary', t.primary);
                            if (t.mid)       r.setProperty('--color-primary-mid', t.mid);
                            if (t.gold)      r.setProperty('--color-gold', t.gold);
                            if (t.textLight) r.setProperty('--color-text-light', t.textLight);
                            if (t.accent)    r.setProperty('--color-accent', t.accent);
                        }
                        if (fav && fav.v === 2 && fav.bubble) {
                            if (fav.bubble.userBg)   r.setProperty('--bubble-user-bg',   fav.bubble.userBg);
                            if (fav.bubble.userText) r.setProperty('--bubble-user-text', fav.bubble.userText);
                            if (fav.bubble.llmBg)    r.setProperty('--bubble-llm-bg',    fav.bubble.llmBg);
                            if (fav.bubble.llmText)  r.setProperty('--bubble-llm-text',  fav.bubble.llmText);
                        }
                        if (fav && fav.v === 2 && fav.fontSize) {
                            r.setProperty('--font-size-base', fav.fontSize);
                        }
                        favApplied = true;
                    }
                }
            } catch(_) {}
            if (favApplied) return;
            try {
                var t = localStorage.getItem('nala_theme');
                if (t) {
                    var v = JSON.parse(t);
                    if (v.primary)   r.setProperty('--color-primary', v.primary);
                    if (v.mid)       r.setProperty('--color-primary-mid', v.mid);
                    if (v.gold)      r.setProperty('--color-gold', v.gold);
                    if (v.textLight) r.setProperty('--color-text-light', v.textLight);
                    if (v.accent)    r.setProperty('--color-accent', v.accent);
                }
            } catch(_) {}
            try {
                var map = {
                    nala_bubble_user_bg:   '--bubble-user-bg',
                    nala_bubble_user_text: '--bubble-user-text',
                    nala_bubble_llm_bg:    '--bubble-llm-bg',
                    nala_bubble_llm_text:  '--bubble-llm-text'
                };
                Object.keys(map).forEach(function(k) {
                    var v = localStorage.getItem(k);
                    if (v) r.setProperty(map[k], v);
                });
                var fs = localStorage.getItem('nala_font_size');
                if (fs) r.setProperty('--font-size-base', fs);
            } catch(_) {}
        })();
    </script>
    <!-- Patch 86: jsPDF für Chat-Export als PDF -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
</head>
<body>
<div class="app-container">

    <!-- ── Login-Screen (Patch 45: offenes Textfeld) ── -->
    <div id="login-screen">
        <h2>👑 Nala</h2>
        <input type="text" id="login-username" placeholder="Benutzername" autocomplete="off">
        <div class="pw-wrapper">
            <input type="password" id="login-password" placeholder="Passwort" autocomplete="new-password">
            <button type="button" class="pw-toggle" id="pw-toggle-btn" onclick="togglePw()" aria-label="Passwort anzeigen">
                <svg id="eye-icon" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                    <circle cx="12" cy="12" r="3"/>
                </svg>
            </button>
        </div>
        <button type="button" id="login-submit" onclick="doLogin()">Anmelden</button>
        <small class="login-hint">Erstes Login? Passwort wird gesetzt.</small>
        <div id="login-error"></div>
    </div>

    <!-- ── Chat-Screen ── -->
    <div id="chat-screen">
        <div class="header" id="main-header">
            <div class="hamburger" onclick="toggleSidebar()">☰</div>
            <div class="title">
                👑 Nala
                <span class="profile-badge" id="profile-badge"></span>
            </div>
            <!-- Patch 86: Settings + Export als Icon-Buttons (Abmelden ist ins Menü gewandert) -->
            <button class="icon-btn" onclick="openSettingsModal()" aria-label="Einstellungen" title="Einstellungen">🔧</button>
            <button class="icon-btn" onclick="openExportMenu()" aria-label="Exportieren" title="Exportieren">💾</button>
        </div>

        <!-- Status-Bar (Patch 46: SSE Pipeline-Status) -->
        <div id="status-bar"></div>

        <!-- Sidebar -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="close-btn" onclick="toggleSidebar()">✖</div>
                <!-- Patch 86: Navigation-Aktionen (Einstellungen + Export sind jetzt in der Top-Bar) -->
                <div class="sidebar-actions">
                    <button class="sidebar-action-btn" onclick="newSession()">➕ Neue Session</button>
                    <button class="sidebar-action-btn" onclick="openPwModal()">🔑 Passwort</button>
                </div>
                <div style="margin-bottom:4px;">
                    <button class="sidebar-action-btn sidebar-action-btn-muted" style="width:100%;" onclick="doLogout()">🚪 Abmelden</button>
                </div>
                <div id="pw-status" style="font-size:0.82em;min-height:1.2em;margin-top:4px;"></div>
            </div>
            <h3 id="pinned-heading" style="display:none;">📌 Angepinnt</h3>
            <ul class="session-list" id="pinned-list"></ul>
            <h3>📋 Letzte Chats</h3>
            <input type="search" class="archive-search" id="archive-search" placeholder="Archiv durchsuchen…">
            <ul class="session-list" id="session-list"><li class="session-item">Lade...</li></ul>
            <!-- Patch 47: Mein Ton -->
            <h3>✏️ Mein Ton</h3>
            <textarea id="my-prompt-area" class="my-prompt-area" placeholder="Dein persönlicher System-Prompt..."></textarea>
            <button class="my-prompt-save-btn" onclick="saveMyPrompt()">Speichern</button>
            <div id="my-prompt-status" class="my-prompt-status"></div>
        </div>
        <div class="overlay" id="overlay" onclick="toggleSidebar()"></div>

        <!-- Chat-Nachrichten -->
        <div class="chat-messages" id="chatMessages"></div>

        <!-- Eingabebereich (sticky) -->
        <div class="input-area">
            <div class="input-row">
                <!-- Patch 67: textarea statt input, + Vollbild-Button -->
                <textarea id="text-input" rows="1" placeholder="Schreib mir…"></textarea>
                <button class="expand-btn" onclick="fullscreenOpen()" title="Vollbild">⛶</button>
                <button class="send-btn" id="sendBtn" onclick="sendTextMessage()">➤</button>
                <button class="mic-btn" id="micBtn" onclick="toggleRecording()">🎤</button>
            </div>
            <div id="transcript-hint"></div>
            <div id="mic-error" class="mic-error"></div>
        </div>
    </div>

</div>

<!-- Patch 100: Easter-Egg-Overlay (Trigger: "Rosendornen" / "Patch 100") -->
<div id="ee-modal" aria-hidden="true">
    <div class="ee-inner">
        <button class="ee-close" onclick="closeEasterEgg()" aria-label="Schließen">&times;</button>
        <img src="/static/pics/Architekt_und_Sonnenblume.png" alt="Architekt und Sonnenblume" class="ee-img">
        <div class="ee-title">🏺 Patch 100 – Zerberus Pro 4.0 🏺</div>
        <div class="ee-quote">„Das Gebrochene sichtbar machen."</div>
        <div class="ee-body">
            Kein Code wurde von Hand geschrieben.<br>
            Jede Zeile entstand im Dialog zwischen<br>
            Architekt und Maschine.<br><br>
            Von Patch 1 bis Patch 100.
        </div>
        <div class="ee-emojis">
            🐕‍🦺 Zerberus · 🐱 Nala · 👑 Hel · 🌹 Rosa<br>
            🦊 Loki · 🐺 Fenrir · 🐿️ Ratatoskr · 🌈 Heimdall
        </div>
        <button class="ee-close-btn" onclick="closeEasterEgg()">Schließen</button>
    </div>
</div>

<!-- Patch 67: Vollbild-Modal -->
<div id="fullscreen-modal">
    <div class="fullscreen-inner">
        <h4>✏️ Eingabe</h4>
        <textarea id="fullscreen-textarea" placeholder="Schreib hier…"></textarea>
        <div class="fullscreen-btn-row">
            <button class="fs-cancel-btn" onclick="fullscreenClose(false)">Abbrechen</button>
            <button class="fs-accept-btn" onclick="fullscreenClose(true)">✓ Übernehmen</button>
        </div>
    </div>
</div>

<!-- Patch 73: Passwort-ändern-Modal -->
<div id="pw-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:3000;align-items:center;justify-content:center;">
    <div style="background:var(--color-surface,#0a1628);border:1px solid var(--color-gold,#c9a84c);border-radius:12px;padding:28px 32px;min-width:320px;max-width:420px;width:90%;">
        <h4 style="margin:0 0 18px;color:var(--color-gold,#c9a84c);">🔑 Passwort ändern</h4>
        <input type="password" id="pw-old" placeholder="Aktuelles Passwort" style="width:100%;box-sizing:border-box;margin-bottom:10px;padding:9px 12px;background:#0d1f3c;border:1px solid #2a4a7f;border-radius:6px;color:#e0e8f8;font-size:0.95em;" />
        <input type="password" id="pw-new1" placeholder="Neues Passwort" style="width:100%;box-sizing:border-box;margin-bottom:10px;padding:9px 12px;background:#0d1f3c;border:1px solid #2a4a7f;border-radius:6px;color:#e0e8f8;font-size:0.95em;" />
        <input type="password" id="pw-new2" placeholder="Neues Passwort wiederholen" style="width:100%;box-sizing:border-box;margin-bottom:14px;padding:9px 12px;background:#0d1f3c;border:1px solid #2a4a7f;border-radius:6px;color:#e0e8f8;font-size:0.95em;" />
        <div id="pw-modal-error" style="color:#e57373;font-size:0.85em;min-height:1.2em;margin-bottom:10px;"></div>
        <div style="display:flex;gap:10px;">
            <button onclick="closePwModal()" style="flex:1;padding:9px;background:#1a3a6a;border:none;border-radius:6px;color:#e0e8f8;cursor:pointer;">Abbrechen</button>
            <button onclick="submitPwChange()" style="flex:1;padding:9px;background:var(--color-gold,#c9a84c);border:none;border-radius:6px;color:#0a1628;font-weight:700;cursor:pointer;">Speichern</button>
        </div>
    </div>
</div>

<!-- Patch 86: Settings-Modal (Theme + Bubble + Schriftgröße) -->
<div id="settings-modal">
    <div class="theme-modal-inner settings-modal-inner">
        <h4>🔧 Einstellungen</h4>

        <!-- Sektion A: Theme-Farben (Patch 77/79) -->
        <div class="settings-section" style="border-top:none;padding-top:0;margin-top:0;">
            <h5>🎨 Theme-Farben</h5>
            <div class="theme-row"><label>Hintergrund</label><input type="color" id="tc-primary" oninput="themePreview()"></div>
            <div class="theme-row"><label>Panel / Header</label><input type="color" id="tc-mid" oninput="themePreview()"></div>
            <div class="theme-row"><label>Akzentfarbe</label><input type="color" id="tc-gold" oninput="themePreview()"></div>
            <div class="theme-row"><label>Textfarbe</label><input type="color" id="tc-text" oninput="themePreview()"></div>
            <div class="theme-row"><label>Akzent / Header</label><input type="color" id="tc-accent" oninput="themePreview()"></div>
        </div>

        <!-- Sektion B: Bubble-Farben (Patch 86 / N-F07) -->
        <div class="settings-section">
            <h5>💬 Bubble-Farben</h5>
            <div class="bubble-picker-row">
                <label>User-Bubble Hintergrund</label>
                <input type="color" id="bc-user-bg" oninput="bubblePreview()">
                <button class="bubble-reset-btn" onclick="resetBubble('user-bg')" title="Zurücksetzen">↺</button>
            </div>
            <div class="bubble-picker-row">
                <label>User-Bubble Text</label>
                <input type="color" id="bc-user-text" oninput="bubblePreview()">
                <button class="bubble-reset-btn" onclick="resetBubble('user-text')" title="Zurücksetzen">↺</button>
            </div>
            <div class="bubble-picker-row">
                <label>LLM-Bubble Hintergrund</label>
                <input type="color" id="bc-llm-bg" oninput="bubblePreview()">
                <button class="bubble-reset-btn" onclick="resetBubble('llm-bg')" title="Zurücksetzen">↺</button>
            </div>
            <div class="bubble-picker-row">
                <label>LLM-Bubble Text</label>
                <input type="color" id="bc-llm-text" oninput="bubblePreview()">
                <button class="bubble-reset-btn" onclick="resetBubble('llm-text')" title="Zurücksetzen">↺</button>
            </div>
            <button class="export-opt-btn" style="margin-top:4px;" onclick="resetAllBubbles()">↺ Alle Bubble-Farben zurücksetzen</button>
        </div>

        <!-- Sektion C: Schriftgröße (Patch 86 / N-F09) -->
        <div class="settings-section">
            <h5>🔤 Schriftgröße</h5>
            <div class="font-preset-row">
                <button class="font-preset-btn" data-size="13px" onclick="setFontSize('13px')">Klein</button>
                <button class="font-preset-btn" data-size="15px" onclick="setFontSize('15px')">Normal</button>
                <button class="font-preset-btn" data-size="17px" onclick="setFontSize('17px')">Groß</button>
                <button class="font-preset-btn" data-size="19px" onclick="setFontSize('19px')">Extra</button>
            </div>
            <button class="export-opt-btn" onclick="resetFontSize()">↺ Zurücksetzen</button>
        </div>

        <!-- Patch 102 (B-07): Eingabe-Verhalten — nur Desktop sichtbar.
             Mobile (Touch): Enter macht IMMER Zeilenumbruch (kein Soft-Keyboard-Senden). -->
        <div class="settings-section" id="enter-behavior-section" style="display:none;">
            <h5>⌨️ Eingabe-Verhalten (Desktop)</h5>
            <div class="theme-row" style="flex-direction:column;align-items:stretch;gap:8px;">
                <label for="enter-behavior-select" style="text-align:left;">Wie soll Enter funktionieren?</label>
                <select id="enter-behavior-select" onchange="setEnterBehavior(this.value)"
                        style="padding:8px;background:#0a1628;color:#e0e8f8;border:1px solid #2a4068;border-radius:6px;">
                    <option value="true">Enter sendet — Shift+Enter = Zeilenumbruch</option>
                    <option value="false">Shift+Enter sendet — Enter = Zeilenumbruch</option>
                </select>
            </div>
        </div>

        <!-- Sektion D: Favoriten (Patch 77 erweitert zu v2) -->
        <div class="settings-section">
            <h5>⭐ Favoriten (Theme + Bubble + Schrift)</h5>
            <div class="theme-fav-row">
                <button class="theme-fav-btn" onclick="saveFav(1)">💾 Fav 1</button>
                <button class="theme-fav-btn" onclick="loadFav(1)">📂 Fav 1</button>
            </div>
            <div class="theme-fav-row">
                <button class="theme-fav-btn" onclick="saveFav(2)">💾 Fav 2</button>
                <button class="theme-fav-btn" onclick="loadFav(2)">📂 Fav 2</button>
            </div>
            <div class="theme-fav-row">
                <button class="theme-fav-btn" onclick="saveFav(3)">💾 Fav 3</button>
                <button class="theme-fav-btn" onclick="loadFav(3)">📂 Fav 3</button>
            </div>
        </div>

        <div class="theme-btn-row" style="margin-top:14px;">
            <button onclick="closeSettingsModal()" style="flex:1;padding:11px;background:#1a3a6a;border:none;border-radius:6px;color:#e0e8f8;cursor:pointer;min-height:44px;">Schließen</button>
            <button onclick="resetTheme()" style="flex:1;padding:11px;background:transparent;border:1px solid #2a4068;border-radius:6px;color:#6a8ab8;cursor:pointer;min-height:44px;">Theme-Reset</button>
            <button onclick="saveTheme()" style="flex:1;padding:11px;background:var(--color-gold);border:none;border-radius:6px;color:#0a1628;font-weight:700;cursor:pointer;min-height:44px;">Theme-Save</button>
        </div>
    </div>
</div>

<!-- Patch 86: Export-Menü-Modal -->
<div id="export-modal">
    <div class="export-modal-inner">
        <h4>💾 Chat exportieren</h4>
        <button class="export-opt-btn" onclick="exportAsPDF()">📄 Als PDF</button>
        <button class="export-opt-btn" onclick="exportAsText()">📝 Als Text (.txt)</button>
        <button class="export-opt-btn" onclick="exportAsClipboard()">📋 In Zwischenablage</button>
        <button class="export-cancel-btn" onclick="closeExportMenu()">Abbrechen</button>
    </div>
</div>

<script>
    // ── State ──
    // Patch 68: Fallback für crypto.randomUUID() (nicht in HTTP-Nicht-Secure-Kontexten verfügbar)
    function generateUUID() {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    }
    // Patch 45: Immer neue Session beim Start – keine gespeicherte sessionId laden
    let sessionId = generateUUID();
    let chatMessages = [];  // Patch 67: { text, sender, timestamp } für Chat-Export
    // Patch 102 (B-02/B-06): AbortController für aktiven LLM-Request,
    // ermöglicht Abbruch bei Session-Wechsel + Frontend-Timeout.
    let currentChatAbort = null;

    let currentProfile = null;  // { name, display_name, theme_color, token, permission_level, allowed_model, temperature }
    let mediaRecorder, audioChunks = [], isRecording = false;
    let pwVisible = false;
    let evtSource = null;

    const messagesDiv    = document.getElementById('chatMessages');
    const textInput      = document.getElementById('text-input');
    const sendBtn        = document.getElementById('sendBtn');
    const micErrorDiv    = document.getElementById('mic-error');
    const sessionList    = document.getElementById('session-list');
    const transcriptHint = document.getElementById('transcript-hint');
    const loginScreen    = document.getElementById('login-screen');
    const chatScreen     = document.getElementById('chat-screen');
    const mainHeader     = document.getElementById('main-header');
    const profileBadge   = document.getElementById('profile-badge');
    const statusBar      = document.getElementById('status-bar');

    // ── Profil aus localStorage wiederherstellen ──
    (function restoreProfile() {
        const stored = localStorage.getItem('nala_profile');
        if (stored) {
            try {
                currentProfile = JSON.parse(stored);
                showChatScreen();
            } catch (_) {
                localStorage.removeItem('nala_profile');
            }
        }
    })();

    // ── SSE EventSource (Patch 46 / 114a: Heartbeat-Reset) ──
    function connectSSE() {
        if (evtSource) {
            evtSource.close();
            evtSource = null;
        }
        evtSource = new EventSource(`/nala/events?session_id=${sessionId}`);
        evtSource.onmessage = (e) => {
            try {
                const evt = JSON.parse(e.data);
                if (evt.type === 'done') {
                    statusBar.style.opacity = '0';
                } else {
                    // Patch 76: Typing-Indicator bei llm_start einblenden
                    if (evt.type === 'llm_start') showTypingIndicator();
                    statusBar.textContent = evt.message;
                    statusBar.style.opacity = '1';
                }
            } catch (_) {}
        };
        // Patch 114a: Heartbeat-Events verlängern den Watchdog.
        // Server sendet alle 5 s ein heartbeat-Event während LLM-Verarbeitung.
        evtSource.addEventListener('heartbeat', () => {
            if (typeof window.__nalaSseWatchdogReset === 'function') {
                window.__nalaSseWatchdogReset();
            }
        });
        evtSource.onerror = () => {
            // Reconnect nach Fehler – EventSource macht das automatisch
        };
    }

    function disconnectSSE() {
        if (evtSource) {
            evtSource.close();
            evtSource = null;
        }
        statusBar.style.opacity = '0';
        statusBar.textContent = '';
    }

    // ── Passwort-Toggle ──
    function togglePw() {
        pwVisible = !pwVisible;
        const pwField = document.getElementById('login-password');
        pwField.type = pwVisible ? 'text' : 'password';
        document.getElementById('eye-icon').innerHTML = pwVisible
            ? '<path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'
            : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
    }

    // ── Login-Logik (Patch 68: keydown statt keypress – konsistent mit Textarea) ──
    document.getElementById('login-username').addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); document.getElementById('login-password').focus(); }
    });
    document.getElementById('login-password').addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); doLogin(); }
    });

    async function doLogin() {
        const errorDiv = document.getElementById('login-error');
        const username = document.getElementById('login-username').value.trim();
        if (!username) {
            errorDiv.textContent = 'Benutzername eingeben.';
            return;
        }
        const pw = document.getElementById('login-password').value;
        if (!pw) {
            errorDiv.textContent = 'Passwort eingeben.';
            return;
        }
        errorDiv.textContent = '';
        try {
            const resp = await fetch('/nala/profile/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile: username, password: pw })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                errorDiv.textContent = err.detail || 'Falsches Passwort.';
                return;
            }
            const data = await resp.json();
            currentProfile = {
                name:             username,
                display_name:     data.display_name,
                theme_color:      data.theme_color,
                token:            data.token,
                permission_level: data.permission_level || 'guest',
                allowed_model:    data.allowed_model    || null,
                temperature:      data.temperature      ?? null,  // Patch 61: Per-User Temperatur-Override
            };
            localStorage.setItem('nala_profile', JSON.stringify(currentProfile));
            document.getElementById('login-password').value = '';
            document.getElementById('login-username').value = '';
            showChatScreen();
        } catch (e) {
            errorDiv.textContent = 'Verbindungsfehler.';
        }
    }

    function doLogout() {
        // Patch 102 (B-02): Laufenden Chat abbrechen vor Logout
        abortActiveChat('logout');
        localStorage.removeItem('nala_profile');
        currentProfile = null;
        disconnectSSE();
        // Neue sessionId für nächsten Login
        sessionId = generateUUID();
        chatMessages = [];  // Patch 67
        messagesDiv.innerHTML = '';
        chatScreen.style.display = 'none';
        loginScreen.style.display = 'flex';
    }

    function showChatScreen() {
        loginScreen.style.display = 'none';
        chatScreen.style.display  = 'flex';
        if (currentProfile) {
            document.documentElement.style.setProperty('--color-accent', currentProfile.theme_color);
            profileBadge.textContent    = '– ' + currentProfile.display_name;
        }
        loadSessions();
        connectSSE();
        if (messagesDiv.children.length === 0) {
            fetchGreeting();  // Patch 67: dynamische Begrüßung per API
        }
    }

    // ── Profile-Header für API-Requests (Patch 54: Bearer-Token statt X-Permission-Level) ──
    function profileHeaders(extra) {
        const h = Object.assign({ 'X-Session-ID': sessionId }, extra || {});
        if (currentProfile && currentProfile.token) {
            h['Authorization'] = 'Bearer ' + currentProfile.token;
        }
        return h;
    }

    // ── 401-Handler: automatisch ausloggen ──
    function handle401() {
        // Patch 102 (B-02): Laufenden Chat abbrechen bei 401
        abortActiveChat('auth-expired');
        localStorage.removeItem('nala_profile');
        currentProfile = null;
        disconnectSSE();
        sessionId = generateUUID();
        chatMessages = [];  // Patch 67
        messagesDiv.innerHTML = '';
        chatScreen.style.display = 'none';
        loginScreen.style.display = 'flex';
        document.getElementById('login-error').textContent = 'Sitzung abgelaufen – bitte erneut einloggen.';
    }

    // ── Archiv: Pin-Hilfsfunktionen (A3, Patch 103 B-23: localStorage statt sessionStorage) ──
    function getPinnedIds() {
        try {
            const ls = localStorage.getItem('pinned_sessions');
            if (ls !== null) return JSON.parse(ls);
            // Einmalige Migration aus sessionStorage (Alt-Daten aktueller Tab-Session)
            const ss = sessionStorage.getItem('pinned_sessions');
            if (ss) {
                localStorage.setItem('pinned_sessions', ss);
                return JSON.parse(ss);
            }
            return [];
        } catch(_) { return []; }
    }
    function setPinnedIds(ids) {
        localStorage.setItem('pinned_sessions', JSON.stringify(ids));
    }
    function togglePin(sid, btn) {
        const ids = getPinnedIds();
        const idx = ids.indexOf(sid);
        if (idx === -1) {
            ids.push(sid);
        } else {
            ids.splice(idx, 1);
        }
        setPinnedIds(ids);
        renderSessionList(window._lastSessions || []);
    }

    // ── Archiv: Session-Eintrag als <li> bauen (Helper für Patch 103 Zwei-Listen-Render) ──
    function buildSessionItem(s, isPinned) {
        const rawMsg   = s.first_message || 'Neuer Chat';
        const title    = rawMsg.length > 40 ? rawMsg.slice(0, 40) + '…' : rawMsg;
        const preview  = rawMsg.length > 42 ? rawMsg.slice(0, 80) + (rawMsg.length > 80 ? '…' : '') : '';
        const ts       = s.created_at
            ? new Date(s.created_at).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit' })
            : '';

        const li = document.createElement('li');
        li.className = 'session-item' + (isPinned ? ' pinned' : '');

        const header = document.createElement('div');
        header.className = 'session-item-header';

        const titleEl = document.createElement('span');
        titleEl.className = 'session-title';
        titleEl.textContent = title;

        const tsEl = document.createElement('span');
        tsEl.className = 'session-ts';
        tsEl.textContent = ts;

        const pinBtn = document.createElement('button');
        pinBtn.className = 'session-pin-btn' + (isPinned ? ' pinned' : '');
        pinBtn.title = isPinned ? 'Entpinnen' : 'Anpinnen';
        pinBtn.textContent = isPinned ? '📌' : '📍';
        pinBtn.onclick = (e) => { e.stopPropagation(); togglePin(s.session_id, pinBtn); };

        header.appendChild(titleEl);
        header.appendChild(tsEl);
        header.appendChild(pinBtn);
        li.appendChild(header);

        if (preview) {
            const previewEl = document.createElement('div');
            previewEl.className = 'session-preview';
            previewEl.textContent = preview;
            li.appendChild(previewEl);
        }

        li.onclick = () => loadSession(s.session_id);
        return li;
    }

    // ── Archiv: Session-Liste rendern (A1+A2+A3, Patch 103 B-23: getrennte Pinned-/Normal-Sektionen) ──
    function renderSessionList(sessions) {
        window._lastSessions = sessions;
        const searchEl = document.getElementById('archive-search');
        const q = (searchEl ? searchEl.value : '').toLowerCase();
        const pinnedIds = getPinnedIds();

        const pinnedList   = document.getElementById('pinned-list');
        const pinnedHead   = document.getElementById('pinned-heading');

        const pinned   = sessions.filter(s => pinnedIds.includes(s.session_id));
        const unpinned = sessions.filter(s => !pinnedIds.includes(s.session_id));

        const filterFn = (s) => !q || (s.first_message || '').toLowerCase().includes(q);
        const pinnedFiltered   = pinned.filter(filterFn);
        const unpinnedFiltered = unpinned.filter(filterFn);

        // Pinned-Sektion
        if (pinnedList) {
            pinnedList.innerHTML = '';
            pinnedFiltered.forEach(s => pinnedList.appendChild(buildSessionItem(s, true)));
        }
        if (pinnedHead) {
            pinnedHead.style.display = pinnedFiltered.length > 0 ? '' : 'none';
        }

        // Haupt-Sektion
        sessionList.innerHTML = '';
        if (unpinnedFiltered.length === 0 && pinnedFiltered.length === 0) {
            sessionList.innerHTML = '<li class="session-item">Keine Chats</li>';
            return;
        }
        unpinnedFiltered.forEach(s => sessionList.appendChild(buildSessionItem(s, false)));
    }

    // ── Archiv laden ──
    async function loadSessions() {
        try {
            // Patch 67 fix: Auth-Header mitschicken – /archive/* ist JWT-geschützt
            const response = await fetch('/archive/sessions', { headers: profileHeaders() });
            // Patch 103 B-10: Bei 401 direkt Login anzeigen statt stiller Auth-Fehler-Meldung
            if (response.status === 401) { handle401(); return; }
            if (!response.ok) {
                sessionList.innerHTML = '<li class="session-item">Keine Chats</li>';
                return;
            }
            const sessions = await response.json();
            renderSessionList(sessions);
        } catch (e) {
            sessionList.innerHTML = '<li class="session-item">Fehler beim Laden</li>';
        }
    }

    async function loadSession(sid) {
        try {
            // Patch 102 (B-02/B-06): Aktiven Chat-Request abbrechen, bevor wir zur neuen Session wechseln
            abortActiveChat('session-switch');
            const response = await fetch(`/archive/session/${sid}`, { headers: profileHeaders() });
            // Patch 103 B-10: 401-Check auch beim Session-Laden
            if (response.status === 401) { handle401(); return; }
            const messages = await response.json();
            // sessionId im Speicher aktualisieren (kein localStorage-Eintrag)
            sessionId = sid;
            messagesDiv.innerHTML = '';
            chatMessages = [];  // Patch 67: Reset für neue Session
            messages.forEach(m => {
                const ts = m.timestamp
                    ? new Date(m.timestamp).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
                    : null;
                addMessage(m.content, m.role === 'user' ? 'user' : 'bot', ts);
            });
            toggleSidebar();
            // SSE neu verbinden mit neuer sessionId
            connectSSE();
        } catch (e) {
            alert('Fehler beim Laden des Chats');
        }
    }

    // ── Chat ──
    async function sendTextMessage() {
        const text = textInput.value.trim();
        if (!text) return;
        transcriptHint.textContent = '';
        sendMessage(text);
    }

    async function sendMessage(text) {
        // Patch 100: Easter-Egg-Trigger — "Rosendornen" oder "Patch 100" zeigen Meilenstein-Overlay
        const _eeTrigger = (text || '').trim().toLowerCase();
        if (_eeTrigger === 'rosendornen' || _eeTrigger === 'patch 100') {
            textInput.value = '';
            openEasterEgg();
            return;
        }
        addMessage(text, 'user');
        textInput.value = '';
        statusBar.textContent = '';
        statusBar.style.opacity = '0';

        // Patch 102 (B-02/B-03/B-06/B-08): Sofortige UI-Reaktion + Lock + Abort-Tracking
        // Falls noch ein älterer Chat-Request läuft (User klickt schnell), abbrechen.
        if (currentChatAbort) { try { currentChatAbort.abort('superseded'); } catch (_) {} }
        showTypingIndicator();
        setTypingState('running');
        lockInput();
        currentChatAbort = new AbortController();
        const myAbort = currentChatAbort;
        const reqSessionId = sessionId;  // Snapshot für Stale-Response-Check

        // Patch 114a: Heartbeat-Watchdog. Initial 15 s (GPU-Pfad), jedes SSE-Heartbeat
        // aus `/nala/events` setzt den Timer zurück → CPU-Fallback bleibt handlungsfähig
        // solange der Server alle 5 s lebenszeichen sendet. Hard-Stop nach 120 s gesamt.
        const WATCHDOG_MS = 15000;
        const WATCHDOG_MAX_MS = 120000;
        let timeoutId = null;
        const requestStartTs = Date.now();
        const resetWatchdog = () => {
            if (timeoutId) clearTimeout(timeoutId);
            if (!myAbort || myAbort.signal.aborted) return;
            if (Date.now() - requestStartTs > WATCHDOG_MAX_MS) {
                console.warn('[TIMEOUT-114a] Hard-Stop nach 120s erreicht');
                try { myAbort.abort('timeout'); } catch (_) {}
                return;
            }
            timeoutId = setTimeout(() => {
                if (myAbort && !myAbort.signal.aborted) {
                    console.warn('[TIMEOUT-114a] Kein Heartbeat für 15s — fetch wird abgebrochen');
                    try { myAbort.abort('timeout'); } catch (_) {}
                }
            }, WATCHDOG_MS);
        };
        window.__nalaSseWatchdogReset = resetWatchdog;
        resetWatchdog();

        try {
            const response = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ messages: [{ role: 'user', content: text }] }),
                signal: myAbort.signal
            });
            if (response.status === 401) { handle401(); return; }
            const data = await response.json();

            // Patch 102 (B-02/B-06): Stale-Response-Check — Session inzwischen gewechselt?
            if (reqSessionId !== sessionId) {
                console.warn('[SESSION-102] Response für veraltete Session verworfen', { req: reqSessionId, current: sessionId });
                return;
            }

            const reply = data.choices?.[0]?.message?.content || 'Keine Antwort';
            removeTypingIndicator();
            addMessage(reply, 'bot');
            loadSessions();
        } catch (error) {
            // AbortError = Timeout ODER Session-Wechsel/Superseded
            if (error.name === 'AbortError' || (error.message || '').includes('aborted')) {
                if (reqSessionId === sessionId && myAbort.signal.reason === 'timeout') {
                    // Patch 109: reqSessionId mitgeben, damit Retry erst per REST-Fallback prüft
                    setTypingState('timeout', text, reqSessionId);
                } else {
                    removeTypingIndicator();
                }
                return;
            }
            console.error('[ERROR-102] Chat-Request fehlgeschlagen:', error);
            removeTypingIndicator();
            if (reqSessionId === sessionId) {
                // Patch 109: reqSessionId mitgeben für REST-Fallback
                showErrorBubble('Verbindungsfehler — bitte erneut versuchen', text, reqSessionId);
            }
        } finally {
            if (timeoutId) clearTimeout(timeoutId);
            if (window.__nalaSseWatchdogReset === resetWatchdog) {
                window.__nalaSseWatchdogReset = null;
            }
            if (currentChatAbort === myAbort) currentChatAbort = null;
            // Input nur freigeben wenn kein Timeout-Bubble aktiv ist (User soll Retry sehen können).
            // Bei Timeout bleibt Bubble + Retry-Button stehen — Input wird trotzdem freigegeben damit
            // User auch eine neue Nachricht tippen kann.
            unlockInput();
        }
    }

    // ── Mikrofon + editierbares Transkript ──
    async function toggleRecording() {
        if (!isRecording) {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
                mediaRecorder.onstop = async () => {
                    // Patch 76: Ladeindikator während Whisper-Verarbeitung
                    const micBtnEl = document.getElementById('micBtn');
                    micBtnEl.disabled = true;
                    micBtnEl.classList.add('processing');
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const formData  = new FormData();
                    formData.append('file', audioBlob, 'recording.webm');
                    try {
                        const response = await fetch('/nala/voice', {
                            method: 'POST',
                            headers: profileHeaders(),
                            body: formData
                        });
                        if (response.status === 401) { handle401(); return; }
                        const data = await response.json();
                        const transcript = data.transcript || '';
                        if (transcript) {
                            // Patch 76: Selektion=ersetzen / Cursor=einfügen / leer=setzen
                            const sel0 = textInput.selectionStart;
                            const sel1 = textInput.selectionEnd;
                            const cur  = textInput.value;
                            if (!cur) {
                                textInput.value = transcript;
                            } else if (sel0 !== sel1) {
                                textInput.value = cur.substring(0, sel0) + transcript + cur.substring(sel1);
                                textInput.setSelectionRange(sel0 + transcript.length, sel0 + transcript.length);
                            } else {
                                textInput.value = cur.substring(0, sel0) + transcript + cur.substring(sel0);
                                textInput.setSelectionRange(sel0 + transcript.length, sel0 + transcript.length);
                            }
                            textInput.focus();
                            transcriptHint.textContent = '🎤 Transkript – prüfen und mit Enter senden';
                        }
                    } catch (e) {
                        addMessage('❌ Fehler bei Spracherkennung', 'bot');
                    } finally {
                        micBtnEl.disabled = false;
                        micBtnEl.classList.remove('processing');
                    }
                };
                mediaRecorder.start();
                isRecording = true;
                document.getElementById('micBtn').classList.add('recording');
                micErrorDiv.textContent = '';
            } catch (error) {
                micErrorDiv.textContent = '❌ Mikrofon nicht erlaubt. Bitte erlaube den Zugriff in den Browser-Einstellungen.';
            }
        } else {
            mediaRecorder.stop();
            mediaRecorder.stream.getTracks().forEach(track => track.stop());
            isRecording = false;
            document.getElementById('micBtn').classList.remove('recording');
        }
    }

    // Hinweistext ausblenden sobald der User zu tippen beginnt
    textInput.addEventListener('input', () => {
        if (transcriptHint.textContent) transcriptHint.textContent = '';
        // Patch 67: auto-expand
        textInput.style.height = 'auto';
        textInput.style.height = Math.min(textInput.scrollHeight, 140) + 'px';
    });

    // Patch 67: Textarea auto-expand on focus/blur
    textInput.addEventListener('focus', () => {
        textInput.style.height = 'auto';
        const sh = textInput.scrollHeight;
        textInput.style.height = Math.min(Math.max(sh, 96), 140) + 'px';
    });
    textInput.addEventListener('blur', () => {
        // Patch 76: Leerfeld → CSS-Default (1 Zeile); Inhalt vorhanden → Höhe beibehalten
        if (!textInput.value.trim()) {
            textInput.style.height = '';
        }
    });

    // Patch 102 (B-07): Mobile-First Input-Verhalten.
    // - Mobile (Touch): Enter macht IMMER Zeilenumbruch — Soft-Keyboards können nicht
    //   zwischen Enter und Shift+Enter unterscheiden, deshalb wäre 'Enter sendet' fatal.
    //   Senden geht ausschließlich über den Send-Button.
    // - Desktop: Per Setting umschaltbar (localStorage 'zerberus_enter_sends').
    //   Default = true (Enter sendet, wie Patch 67) für Rückwärtskompatibilität.
    function isTouchDevice() {
        return ('ontouchstart' in window)
            || (navigator.maxTouchPoints > 0)
            || (window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
    }
    function getEnterSendsSetting() {
        if (isTouchDevice()) return false;  // Mobile: NIE Enter sendet
        const v = localStorage.getItem('zerberus_enter_sends');
        return v === null ? true : v === 'true';  // Desktop-Default
    }
    function setEnterBehavior(value) {
        // value = "true" / "false" (string aus <select>)
        localStorage.setItem('zerberus_enter_sends', value);
    }

    textInput.addEventListener('keydown', e => {
        if (e.key !== 'Enter') return;
        const enterSends = getEnterSendsSetting();
        if (enterSends && !e.shiftKey) {
            e.preventDefault();
            sendTextMessage();
        } else if (!enterSends && e.shiftKey) {
            e.preventDefault();
            sendTextMessage();
        }
        // sonst: natürliches Verhalten (Zeilenumbruch in der Textarea)
    });

    // ── Nachrichten anzeigen (Patch 65: Export-Dropdown / Patch 67: Toolbar + Tracking) ──
    function addMessage(text, sender, tsOverride) {
        const now = new Date();
        const timeStr = tsOverride || now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
        chatMessages.push({ text, sender, timestamp: timeStr });

        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${sender === 'user' ? 'user-message' : 'bot-message'}`;
        msgDiv.textContent = text;

        const wrapper = document.createElement('div');
        wrapper.className = sender === 'user' ? 'msg-wrapper user-wrapper' : 'msg-wrapper';
        wrapper.appendChild(msgDiv);

        // Patch 67: Toolbar mit Timestamp + Kopieren-Button
        // Patch 98: Wiederholen + Bearbeiten nur an User-Bubbles
        const toolbar = document.createElement('div');
        toolbar.className = 'msg-toolbar';
        const timeSpan = document.createElement('span');
        timeSpan.textContent = timeStr;
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.title = 'Kopieren';
        copyBtn.textContent = '📋';
        copyBtn.onclick = () => copyBubble(text, copyBtn);
        toolbar.appendChild(timeSpan);
        toolbar.appendChild(copyBtn);
        if (sender === 'user') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'bubble-action-btn';
            retryBtn.title = 'Wiederholen';
            retryBtn.textContent = '🔄';
            retryBtn.onclick = () => retryMessage(text, retryBtn);
            toolbar.appendChild(retryBtn);
            const editBtn = document.createElement('button');
            editBtn.className = 'bubble-action-btn';
            editBtn.title = 'Bearbeiten';
            editBtn.textContent = '✏️';
            editBtn.onclick = () => editMessage(text, editBtn);
            toolbar.appendChild(editBtn);
        }
        wrapper.appendChild(toolbar);

        if (sender === 'bot') {
            const exportRow = document.createElement('div');
            exportRow.className = 'export-row';
            const sel = document.createElement('select');
            sel.className = 'export-select';
            sel.title = 'Antwort exportieren';
            sel.innerHTML = '<option value="">⬇ Export…</option>'
                + '<option value="pdf">Als PDF</option>'
                + '<option value="docx">Als DOCX</option>'
                + '<option value="md">Als Markdown</option>'
                + '<option value="txt">Als TXT</option>';
            sel.addEventListener('change', function() {
                const fmt = this.value;
                if (!fmt) return;
                this.value = '';
                exportMessage(text, fmt);
            });
            exportRow.appendChild(sel);
            wrapper.appendChild(exportRow);
        }
        messagesDiv.appendChild(wrapper);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }

    // Patch 76 + Patch 102 (B-03): Typing-Indicator mit Spinner + Status-Text
    function showTypingIndicator() {
        if (document.getElementById('typing-indicator')) return;
        const wrapper = document.createElement('div');
        wrapper.id = 'typing-indicator';
        wrapper.className = 'msg-wrapper';
        const bubble = document.createElement('div');
        bubble.className = 'typing-indicator';
        bubble.innerHTML =
            '<span class="spinner-rad"></span>' +
            '<span class="typing-status">Antwort wird generiert…</span>';
        wrapper.appendChild(bubble);
        messagesDiv.appendChild(wrapper);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
    function removeTypingIndicator() {
        const el = document.getElementById('typing-indicator');
        if (el) el.remove();
    }

    // Patch 109: REST-Fallback — prüft ob Backend inzwischen eine Antwort gespeichert hat.
    // Sucht die letzte user-Message mit gleichem Content (rückwärts), liefert die erste
    // nachfolgende assistant-Message zurück. null = keine späte Antwort gefunden.
    async function fetchLateAnswer(sid, userText) {
        if (!sid || !userText) return null;
        try {
            const r = await fetch('/archive/session/' + encodeURIComponent(sid), { headers: profileHeaders() });
            if (!r.ok) return null;
            const msgs = await r.json();
            if (!Array.isArray(msgs) || msgs.length === 0) return null;
            const needle = (userText || '').trim();
            let lastUserIdx = -1;
            for (let i = msgs.length - 1; i >= 0; i--) {
                if (msgs[i].role === 'user' && (msgs[i].content || '').trim() === needle) {
                    lastUserIdx = i;
                    break;
                }
            }
            if (lastUserIdx < 0) return null;
            for (let j = lastUserIdx + 1; j < msgs.length; j++) {
                if (msgs[j].role === 'assistant' && msgs[j].content) {
                    return msgs[j].content;
                }
            }
            return null;
        } catch (_) {
            return null;
        }
    }

    // Patch 109: Retry-Handler mit Fallback-Logik. Entfernt "wrapperOrRemover" vom DOM,
    // zeigt späte Antwort an falls vorhanden, sonst echter Retry via sendMessage.
    async function retryOrRecover(retryText, retrySid, cleanupFn) {
        const late = await fetchLateAnswer(retrySid, retryText);
        if (typeof cleanupFn === 'function') cleanupFn();
        if (late && retrySid === sessionId) {
            addMessage(late, 'bot');
            loadSessions();
            return;
        }
        sendMessage(retryText);
    }

    // Patch 102 (B-03/B-09/B-11/B-17): Zustands-Helfer.
    // Patch 109: dritter Parameter retrySid — für REST-Fallback im Retry-Button.
    function setTypingState(state, retryText, retrySid) {
        const wrapper = document.getElementById('typing-indicator');
        if (!wrapper) return;
        const bubble = wrapper.querySelector('.typing-indicator');
        const statusEl = wrapper.querySelector('.typing-status');
        if (state === 'timeout') {
            bubble.classList.add('frozen');
            if (statusEl) statusEl.textContent = 'Keine Antwort vom Server';
            if (!bubble.querySelector('.retry-inline')) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'retry-inline';
                btn.textContent = '🔄 Erneut versuchen';
                btn.onclick = async () => {
                    btn.disabled = true;
                    btn.textContent = '⏳ Prüfe Server…';
                    await retryOrRecover(retryText, retrySid, removeTypingIndicator);
                };
                bubble.appendChild(btn);
            }
        } else if (state === 'running') {
            bubble.classList.remove('frozen', 'error-state');
            if (statusEl) statusEl.textContent = 'Antwort wird generiert…';
        }
    }

    // Patch 109: dritter Parameter retrySid — für REST-Fallback im Retry-Button.
    function showErrorBubble(text, retryText, retrySid) {
        const wrapper = document.createElement('div');
        wrapper.className = 'msg-wrapper';
        const bubble = document.createElement('div');
        bubble.className = 'typing-indicator error-state frozen';
        bubble.innerHTML = '<span class="typing-status">' + text + '</span>';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'retry-inline';
        btn.textContent = '🔄 Erneut versuchen';
        btn.onclick = async () => {
            btn.disabled = true;
            btn.textContent = '⏳ Prüfe Server…';
            await retryOrRecover(retryText, retrySid, () => wrapper.remove());
        };
        bubble.appendChild(btn);
        wrapper.appendChild(bubble);
        messagesDiv.appendChild(wrapper);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }

    function lockInput() {
        if (textInput) textInput.disabled = true;
        if (sendBtn) { sendBtn.disabled = true; sendBtn.style.opacity = '0.5'; }
    }
    function unlockInput() {
        if (textInput) textInput.disabled = false;
        if (sendBtn) { sendBtn.disabled = false; sendBtn.style.opacity = ''; }
    }
    // Patch 102 (B-02/B-06): Aktiven LLM-Request abbrechen — bei Session-Wechsel/Logout.
    function abortActiveChat(reason) {
        if (currentChatAbort) {
            try { currentChatAbort.abort(reason || 'session-switch'); } catch (_) {}
            currentChatAbort = null;
        }
        removeTypingIndicator();
        unlockInput();
    }

    // Patch 67 + Patch 86: Kopieren-Feedback mit execCommand-Fallback
    async function copyBubble(text, btn) {
        const ok = await copyToClipboard(text);
        if (!ok) return;
        const orig = btn.textContent;
        btn.textContent = '✓';
        btn.classList.add('copy-ok');
        setTimeout(() => { btn.textContent = orig; btn.classList.remove('copy-ok'); }, 1500);
    }

    // Patch 98 (N-F03): Wiederholen an User-Bubbles — sendet exakt denselben Text
    // erneut. Kein Fork / kein History-Rewrite; frühere Nachrichten werden einfach
    // als neue Message ans Ende gehängt.
    function retryMessage(text, btn) {
        if (btn) {
            btn.classList.add('copy-ok');
            setTimeout(() => btn.classList.remove('copy-ok'), 800);
        }
        sendMessage(text);
    }

    // Patch 98 (N-F04): Bearbeiten — Text in Textarea kopieren, Fokus + Auto-Expand.
    // NICHT senden; der User editiert und drückt selbst Enter.
    function editMessage(text, btn) {
        if (btn) {
            btn.classList.add('copy-ok');
            setTimeout(() => btn.classList.remove('copy-ok'), 800);
        }
        textInput.value = text;
        textInput.focus();
        textInput.style.height = 'auto';
        textInput.style.height = Math.min(Math.max(textInput.scrollHeight, 96), 140) + 'px';
        const end = textInput.value.length;
        textInput.setSelectionRange(end, end);
    }

    // ── Export-Funktion (Patch 65) ──
    async function exportMessage(text, fmt) {
        if (!currentProfile) { alert('Nicht eingeloggt'); return; }
        try {
            const res = await fetch('/nala/export', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ text, format: fmt, filename: 'nala_antwort' })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert('Export fehlgeschlagen: ' + (err.detail || res.status));
                return;
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `nala_antwort.${fmt}`;
            document.body.appendChild(a);
            a.click();
            setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
        } catch (e) {
            alert('Export-Fehler: ' + e.message);
        }
    }

    function toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        sidebar.classList.toggle('open');
        document.getElementById('overlay').classList.toggle('show');
        // Patch 47: Mein-Ton-Prompt laden wenn Sidebar öffnet
        if (sidebar.classList.contains('open')) loadMyPrompt();
    }

    // ── Patch 47: Mein Ton ──
    async function loadMyPrompt() {
        if (!currentProfile) return;
        try {
            const res = await fetch('/nala/profile/my_prompt', {
                headers: profileHeaders()
            });
            if (!res.ok) return;
            const data = await res.json();
            document.getElementById('my-prompt-area').value = data.prompt || '';
        } catch (_) {}
    }

    // ── Patch 67: Neue Session ──
    function newSession() {
        // Patch 102 (B-02/B-06): Aktiven Chat-Request abbrechen vor Session-Wechsel
        abortActiveChat('session-switch');
        sessionId = generateUUID();
        chatMessages = [];
        messagesDiv.innerHTML = '';
        toggleSidebar();
        connectSSE();
        fetchGreeting();
    }

    // ── Patch 67: Chat als .txt exportieren ──
    function exportChat() {
        if (chatMessages.length === 0) {
            alert('Kein Chat zum Exportieren.');
            return;
        }
        const lines = chatMessages.map(m =>
            `[${m.timestamp}] ${m.sender === 'user' ? 'Du' : 'Nala'}: ${m.text}`
        );
        const content = lines.join('\\n');
        const blob = new Blob([content], { type: 'text/plain; charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const now = new Date();
        const stamp = now.toISOString().slice(0,16).replace('T','_').replace(':','-');
        const a = document.createElement('a');
        a.href = url;
        a.download = `nala_export_${stamp}.txt`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    }

    // ── Patch 67: Vollbild-Modal ──
    function fullscreenOpen() {
        document.getElementById('fullscreen-modal').classList.add('open');
        document.getElementById('fullscreen-textarea').value = textInput.value;
        document.getElementById('fullscreen-textarea').focus();
    }
    function fullscreenClose(accept) {
        if (accept) {
            textInput.value = document.getElementById('fullscreen-textarea').value;
            textInput.style.height = 'auto';
            textInput.style.height = Math.min(textInput.scrollHeight, 140) + 'px';
            textInput.focus();
        }
        document.getElementById('fullscreen-modal').classList.remove('open');
    }

    // ── Patch 67 + Patch 102 (B-05): Dynamische Begrüßung mit Varianz ──
    // Backend liefert {prefix, name}; Frontend wählt zufällig aus 4 Templates.
    const GREETING_VARIANTS = [
        (p, n) => n ? `${p}, ${n}!` : `${p}!`,
        (p, n) => n ? `Hey ${n}! ${p}.` : `Hey! ${p}.`,
        (p, n) => n ? `${p}! Schön, dass du da bist, ${n}.` : `${p}! Schön, dass du da bist.`,
        (p, n) => n ? `${n}! ${p} — was kann ich für dich tun?` : `${p} — was kann ich für dich tun?`,
    ];
    async function fetchGreeting() {
        let prefix = 'Hallo', name = null;
        try {
            const res = await fetch('/nala/greeting', { headers: profileHeaders() });
            if (res.ok) {
                const data = await res.json();
                // Neues Format {prefix, name}; alter Fallback {greeting} wird ignoriert.
                if (typeof data.prefix === 'string') prefix = data.prefix;
                if (data.name) name = data.name;
            }
        } catch (_) {}
        const variant = GREETING_VARIANTS[Math.floor(Math.random() * GREETING_VARIANTS.length)];
        addMessage(variant(prefix, name), 'bot');
    }

    async function saveMyPrompt() {
        if (!currentProfile) return;
        const prompt = document.getElementById('my-prompt-area').value;
        const statusEl = document.getElementById('my-prompt-status');
        try {
            const res = await fetch('/nala/profile/my_prompt', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ prompt })
            });
            if (res.ok) {
                statusEl.textContent = '✓ Gespeichert';
                setTimeout(() => { statusEl.textContent = ''; }, 2500);
            } else {
                statusEl.textContent = '❌ Fehler beim Speichern';
            }
        } catch (_) {
            statusEl.textContent = '❌ Verbindungsfehler';
        }
    }

    // ── Patch 73: Passwort ändern ──
    function openPwModal() {
        document.getElementById('pw-old').value = '';
        document.getElementById('pw-new1').value = '';
        document.getElementById('pw-new2').value = '';
        document.getElementById('pw-modal-error').textContent = '';
        const modal = document.getElementById('pw-modal');
        modal.style.display = 'flex';
        document.getElementById('pw-old').focus();
    }

    function closePwModal() {
        document.getElementById('pw-modal').style.display = 'none';
    }

    async function submitPwChange() {
        const oldPw = document.getElementById('pw-old').value;
        const newPw1 = document.getElementById('pw-new1').value;
        const newPw2 = document.getElementById('pw-new2').value;
        const errEl = document.getElementById('pw-modal-error');
        errEl.textContent = '';

        if (newPw1 !== newPw2) { errEl.textContent = 'Passwörter stimmen nicht überein'; return; }
        if (newPw1.length < 6) { errEl.textContent = 'Neues Passwort muss mindestens 6 Zeichen haben'; return; }

        try {
            const res = await fetch('/nala/change-password', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ old_password: oldPw, new_password: newPw1 })
            });
            if (res.ok) {
                closePwModal();
                const statusEl = document.getElementById('pw-status');
                statusEl.style.color = '#66bb6a';
                statusEl.textContent = '✓ Passwort gespeichert';
                setTimeout(() => { statusEl.textContent = ''; }, 3000);
            } else {
                const data = await res.json().catch(() => ({}));
                errEl.textContent = data.detail || 'Fehler beim Speichern';
            }
        } catch (_) {
            errEl.textContent = 'Verbindungsfehler';
        }
    }

    // ── Patch 77 A2: Archiv-Suche live ──
    (function() {
        const searchEl = document.getElementById('archive-search');
        if (searchEl) {
            searchEl.addEventListener('input', () => renderSessionList(window._lastSessions || []));
        }
    })();

    // ── Patch 77 C: Theme-Editor ──
    function cssToHex(css) {
        if (!css) return '#000000';
        css = css.trim();
        if (css.startsWith('#')) {
            if (css.length === 4) return '#' + css[1]+css[1]+css[2]+css[2]+css[3]+css[3];
            return css.slice(0, 7);
        }
        const m = css.match(/\d+/g);
        if (!m || m.length < 3) return '#000000';
        return '#' + [m[0], m[1], m[2]].map(n => (+n).toString(16).padStart(2, '0')).join('');
    }
    // ── Patch 86: Settings-Modal (ersetzt Theme-Modal) ──
    function openSettingsModal() {
        const r = getComputedStyle(document.documentElement);
        document.getElementById('tc-primary').value = cssToHex(r.getPropertyValue('--color-primary'));
        document.getElementById('tc-mid').value     = cssToHex(r.getPropertyValue('--color-primary-mid'));
        document.getElementById('tc-gold').value    = cssToHex(r.getPropertyValue('--color-gold'));
        document.getElementById('tc-text').value    = cssToHex(r.getPropertyValue('--color-text-light'));
        document.getElementById('tc-accent').value  = cssToHex(r.getPropertyValue('--color-accent'));
        document.getElementById('bc-user-bg').value   = cssToHex(r.getPropertyValue('--bubble-user-bg'));
        document.getElementById('bc-user-text').value = cssToHex(r.getPropertyValue('--bubble-user-text'));
        document.getElementById('bc-llm-bg').value    = cssToHex(r.getPropertyValue('--bubble-llm-bg'));
        document.getElementById('bc-llm-text').value  = cssToHex(r.getPropertyValue('--bubble-llm-text'));
        markActiveFontPreset();
        // Patch 102 (B-07): Enter-Behavior nur auf Desktop sichtbar; Wert aus localStorage setzen.
        const enterSection = document.getElementById('enter-behavior-section');
        const enterSelect  = document.getElementById('enter-behavior-select');
        if (enterSection && enterSelect) {
            if (isTouchDevice()) {
                enterSection.style.display = 'none';
            } else {
                enterSection.style.display = '';
                const v = localStorage.getItem('zerberus_enter_sends');
                enterSelect.value = (v === null) ? 'true' : v;
            }
        }
        document.getElementById('settings-modal').classList.add('open');
    }
    function closeSettingsModal() {
        document.getElementById('settings-modal').classList.remove('open');
    }
    function themePreview() {
        const r = document.documentElement.style;
        r.setProperty('--color-primary',     document.getElementById('tc-primary').value);
        r.setProperty('--color-primary-mid', document.getElementById('tc-mid').value);
        r.setProperty('--color-gold',        document.getElementById('tc-gold').value);
        r.setProperty('--color-text-light',  document.getElementById('tc-text').value);
        r.setProperty('--color-accent',      document.getElementById('tc-accent').value);
    }
    function saveTheme() {
        const theme = {
            primary:   document.getElementById('tc-primary').value,
            mid:       document.getElementById('tc-mid').value,
            gold:      document.getElementById('tc-gold').value,
            textLight: document.getElementById('tc-text').value,
            accent:    document.getElementById('tc-accent').value,
        };
        localStorage.setItem('nala_theme', JSON.stringify(theme));
        closeSettingsModal();
    }
    function resetTheme() {
        const d = { primary: '#0a1628', mid: '#1a2f4e', gold: '#f0b429', textLight: '#e8eaf0', accent: '#ec407a' };
        const r = document.documentElement.style;
        r.setProperty('--color-primary', d.primary);
        r.setProperty('--color-primary-mid', d.mid);
        r.setProperty('--color-gold', d.gold);
        r.setProperty('--color-text-light', d.textLight);
        r.setProperty('--color-accent', d.accent);
        document.getElementById('tc-primary').value = d.primary;
        document.getElementById('tc-mid').value     = d.mid;
        document.getElementById('tc-gold').value    = d.gold;
        document.getElementById('tc-text').value    = d.textLight;
        document.getElementById('tc-accent').value  = d.accent;
        localStorage.removeItem('nala_theme');
        localStorage.removeItem('nala_last_active_favorite');  // Patch 103 B-13/14
        // Patch 109: Vollständiger Reset — inkl. Bubble-Overrides und Font-Size,
        // sonst können alte Overrides (z.B. schwarzer LLM-Hintergrund) die rgba-
        // Defaults überschreiben und unlesbare Bubbles erzeugen.
        if (typeof resetAllBubbles === 'function') resetAllBubbles();
        if (typeof resetFontSize === 'function') resetFontSize();
    }

    // ── Patch 86: Bubble-Farben (N-F07) ──
    const BUBBLE_KEYS = {
        'user-bg':   { css: '--bubble-user-bg',   ls: 'nala_bubble_user_bg',   picker: 'bc-user-bg',   fallback: '--color-accent' },
        'user-text': { css: '--bubble-user-text', ls: 'nala_bubble_user_text', picker: 'bc-user-text', fallback: '--color-primary' },
        'llm-bg':    { css: '--bubble-llm-bg',    ls: 'nala_bubble_llm_bg',    picker: 'bc-llm-bg',    fallback: '--color-primary-mid' },
        'llm-text':  { css: '--bubble-llm-text',  ls: 'nala_bubble_llm_text',  picker: 'bc-llm-text',  fallback: '--color-text-light' },
    };
    function bubblePreview() {
        const r = document.documentElement.style;
        Object.keys(BUBBLE_KEYS).forEach(k => {
            const cfg = BUBBLE_KEYS[k];
            const val = document.getElementById(cfg.picker).value;
            r.setProperty(cfg.css, val);
            localStorage.setItem(cfg.ls, val);
        });
    }
    function resetBubble(which) {
        const cfg = BUBBLE_KEYS[which];
        if (!cfg) return;
        // Property entfernen → Fallback auf Theme-Farbe greift
        document.documentElement.style.removeProperty(cfg.css);
        localStorage.removeItem(cfg.ls);
        // Picker-Wert auf aktuellen Fallback synchronisieren
        const r = getComputedStyle(document.documentElement);
        document.getElementById(cfg.picker).value = cssToHex(r.getPropertyValue(cfg.fallback));
    }
    function resetAllBubbles() {
        Object.keys(BUBBLE_KEYS).forEach(resetBubble);
    }

    // ── Patch 86: Schriftgröße (N-F09) ──
    function setFontSize(px) {
        document.documentElement.style.setProperty('--font-size-base', px);
        localStorage.setItem('nala_font_size', px);
        markActiveFontPreset();
    }
    function resetFontSize() {
        document.documentElement.style.removeProperty('--font-size-base');
        localStorage.removeItem('nala_font_size');
        markActiveFontPreset();
    }
    function markActiveFontPreset() {
        const current = getComputedStyle(document.documentElement).getPropertyValue('--font-size-base').trim();
        document.querySelectorAll('.font-preset-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.size === current);
        });
    }

    // ── Patch 86: Favoriten v2 (Theme + Bubble + Schrift) ──
    function saveFav(n) {
        const payload = {
            v: 2,
            theme: {
                primary:   document.getElementById('tc-primary').value,
                mid:       document.getElementById('tc-mid').value,
                gold:      document.getElementById('tc-gold').value,
                textLight: document.getElementById('tc-text').value,
                accent:    document.getElementById('tc-accent').value,
            },
            bubble: {
                userBg:   localStorage.getItem('nala_bubble_user_bg')   || null,
                userText: localStorage.getItem('nala_bubble_user_text') || null,
                llmBg:    localStorage.getItem('nala_bubble_llm_bg')    || null,
                llmText:  localStorage.getItem('nala_bubble_llm_text')  || null,
            },
            fontSize: localStorage.getItem('nala_font_size') || null,
        };
        localStorage.setItem('nala_theme_fav_' + n, JSON.stringify(payload));
        localStorage.setItem('nala_last_active_favorite', String(n));  // Patch 103 B-13/14
    }
    function loadFav(n) {
        const stored = localStorage.getItem('nala_theme_fav_' + n);
        if (!stored) return;
        localStorage.setItem('nala_last_active_favorite', String(n));  // Patch 103 B-13/14
        try {
            const raw = JSON.parse(stored);
            // Schema-Detect: v2 hat .theme / .bubble / .fontSize, v1 ist flach (primary/mid/gold/...)
            const t = (raw && raw.v === 2) ? raw.theme : raw;
            if (t) {
                document.getElementById('tc-primary').value = t.primary   || '#0a1628';
                document.getElementById('tc-mid').value     = t.mid       || '#1a2f4e';
                document.getElementById('tc-gold').value    = t.gold      || '#f0b429';
                document.getElementById('tc-text').value    = t.textLight || '#e8eaf0';
                document.getElementById('tc-accent').value  = t.accent    || '#ec407a';
                themePreview();
            }
            if (raw && raw.v === 2) {
                const r = document.documentElement.style;
                const bMap = [
                    ['userBg',   BUBBLE_KEYS['user-bg']],
                    ['userText', BUBBLE_KEYS['user-text']],
                    ['llmBg',    BUBBLE_KEYS['llm-bg']],
                    ['llmText',  BUBBLE_KEYS['llm-text']],
                ];
                bMap.forEach(([key, cfg]) => {
                    const v = raw.bubble && raw.bubble[key];
                    if (v) {
                        r.setProperty(cfg.css, v);
                        localStorage.setItem(cfg.ls, v);
                        document.getElementById(cfg.picker).value = v;
                    } else {
                        r.removeProperty(cfg.css);
                        localStorage.removeItem(cfg.ls);
                        const rr = getComputedStyle(document.documentElement);
                        document.getElementById(cfg.picker).value = cssToHex(rr.getPropertyValue(cfg.fallback));
                    }
                });
                if (raw.fontSize) setFontSize(raw.fontSize); else resetFontSize();
            }
        } catch(_) {}
    }

    // ── Patch 86: Clipboard-Helper mit execCommand-Fallback (HTTP-LAN-Kontexte) ──
    async function copyToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
        }
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.top = '0';
        ta.style.left = '0';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (_) {}
        ta.remove();
        return ok;
    }

    function showToast(msg) {
        const old = document.querySelector('.export-toast');
        if (old) old.remove();
        const t = document.createElement('div');
        t.className = 'export-toast';
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => { t.remove(); }, 2000);
    }

    // ── Patch 86: Export-Menü (N-F05) ──
    function openExportMenu() {
        if (chatMessages.length === 0) {
            showToast('Kein Chat zum Exportieren.');
            return;
        }
        document.getElementById('export-modal').classList.add('open');
    }
    function closeExportMenu() {
        document.getElementById('export-modal').classList.remove('open');
    }
    function _chatStamp() {
        const now = new Date();
        return now.toISOString().slice(0,16).replace('T','_').replace(':','-');
    }
    function _chatLines() {
        return chatMessages.map(m =>
            `[${m.timestamp}] ${m.sender === 'user' ? 'Du' : 'Nala'}: ${m.text}`
        );
    }
    function exportAsText() {
        closeExportMenu();
        exportChat();  // bestehende Patch-67-Funktion
    }
    function exportAsPDF() {
        closeExportMenu();
        if (!window.jspdf || !window.jspdf.jsPDF) {
            showToast('PDF-Bibliothek nicht geladen.');
            return;
        }
        try {
            const { jsPDF } = window.jspdf;
            const doc = new jsPDF();
            doc.setFontSize(11);
            const margin = 12, pageWidth = 210 - 2 * margin, pageBottom = 285;
            let y = 15;
            _chatLines().forEach(line => {
                const wrapped = doc.splitTextToSize(line, pageWidth);
                wrapped.forEach(w => {
                    if (y > pageBottom) { doc.addPage(); y = 15; }
                    doc.text(w, margin, y);
                    y += 6;
                });
                y += 2;
            });
            doc.save(`nala_export_${_chatStamp()}.pdf`);
        } catch (e) {
            showToast('PDF-Export fehlgeschlagen.');
        }
    }
    async function exportAsClipboard() {
        closeExportMenu();
        const text = _chatLines().join('\\n');
        const ok = await copyToClipboard(text);
        showToast(ok ? 'Chat in Zwischenablage kopiert.' : 'Kopieren fehlgeschlagen.');
    }

    /* Patch 100: Easter-Egg-Overlay */
    function openEasterEgg() {
        const modal = document.getElementById('ee-modal');
        if (!modal) return;
        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
        // Sternenregen im Hintergrund (Patch 83): mehrere Wellen während der Overlay offen ist
        const starsInterval = setInterval(function () {
            if (!modal.classList.contains('open')) { clearInterval(starsInterval); return; }
            const x = Math.random() * window.innerWidth;
            const y = Math.random() * window.innerHeight * 0.3;
            spawnStars(x, y, 4);
        }, 400);
        // Klick auf leeren Bereich (außer .ee-inner) schließt ebenfalls
        modal.onclick = function (e) { if (e.target === modal) closeEasterEgg(); };
    }
    function closeEasterEgg() {
        const modal = document.getElementById('ee-modal');
        if (!modal) return;
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
    }

    /* Patch 83: Sternenregen bei Tap */
    function spawnStars(x, y, count) {
        count = count || 5;
        for (var i = 0; i < count; i++) {
            var star = document.createElement('div');
            star.className = 'tap-star';
            star.textContent = '\u2b50';
            star.style.left = (x + (Math.random() - 0.5) * 40) + 'px';
            star.style.top = y + 'px';
            star.style.fontSize = (12 + Math.random() * 12) + 'px';
            star.style.setProperty('--fall-x', (Math.random() - 0.5) * 60 + 'px');
            document.body.appendChild(star);
            setTimeout(function(el) { return function() { el.remove(); }; }(star), 1200);
        }
    }
    document.addEventListener('touchstart', function(e) {
        var touch = e.touches[0];
        spawnStars(touch.clientX, touch.clientY);
    });
    document.addEventListener('click', function(e) {
        if (!e.target.closest('button, a, input, textarea, select')) {
            spawnStars(e.clientX, e.clientY, 3);
        }
    });

    /* Patch 103 – C3: Backdrop-Klick schließt Modals (außer ee-modal, das hat eigenen Handler) */
    (function() {
        document.querySelectorAll('#settings-modal, #export-modal, #fullscreen-modal').forEach(function(m) {
            m.addEventListener('click', function(e) {
                if (e.target === m) m.classList.remove('open');
            });
        });
        var pwModal = document.getElementById('pw-modal');
        if (pwModal) {
            pwModal.addEventListener('click', function(e) {
                if (e.target === pwModal) pwModal.style.display = 'none';
            });
        }
    })();
</script>
</body>
</html>
"""


# ---------- Routen ----------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def nala_interface():
    return NALA_HTML


# ---------------------------------------------------------------------------
# SSE-Endpunkt (Patch 46)
# ---------------------------------------------------------------------------

@router.get("/events")
async def sse_events(session_id: str = ""):
    """
    Server-Sent Events Endpunkt.
    Streamt Pipeline-Events (RAG-Suche, Intent, LLM-Start) live ans Frontend.

    Patch 114a: Heartbeat alle 5 s. Frontend-Watchdog wird bei jedem Heartbeat
    zurückgesetzt, damit langsame CPU-Reranker-Läufe nicht den 15-s-Timeout auslösen.
    Harte Obergrenze 300 s verhindert leaks.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id ist erforderlich")

    bus = get_event_bus()
    q = bus.subscribe_sse(session_id)

    async def event_generator():
        # Patch 114a: Retry-Hint für EventSource-Reconnect nach Disconnect.
        yield "retry: 5000\n\n"
        loop = asyncio.get_event_loop()
        start_ts = loop.time()
        MAX_DURATION = 300.0  # s — harte Obergrenze
        HEARTBEAT_INTERVAL = 5.0  # s
        try:
            while True:
                if loop.time() - start_ts > MAX_DURATION:
                    # Stream sauber schließen — Client kann neu verbinden
                    yield "event: timeout\ndata: max_duration\n\n"
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_INTERVAL)
                    msg_template = _SSE_MESSAGES.get(event.type)
                    if msg_template:
                        message = msg_template.format(**event.data) if '{' in msg_template else msg_template
                        payload = json.dumps({"type": event.type, "message": message}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                    elif event.type == "done":
                        payload = json.dumps({"type": "done", "message": ""}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Patch 114a: Heartbeat statt Disconnect — hält die Verbindung
                    # warm und signalisiert dem Client, dass der Server noch lebt.
                    yield "event: heartbeat\ndata: processing\n\n"
        finally:
            bus.unsubscribe_sse(session_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ---------------------------------------------------------------------------
# TEIL 2: Profil-Endpunkte
# ---------------------------------------------------------------------------

@router.post("/profile/login")
async def profile_login(req: ProfileLoginRequest, settings: Settings = Depends(get_settings)):
    """
    Profil-Login mit bcrypt.
    - Erster Login (password_hash leer): setzt Hash und speichert in config.yaml.
    - Gibt display_name, theme_color und einen session_token (uuid4) zurück.
    - Kein JWT, kein Cookie – Browser speichert in localStorage.
    """
    profiles = _load_profiles()
    matched_key = None
    for k, p in profiles.items():
        if req.profile.lower() in [k.lower(), p.get("display_name", "").lower()]:
            matched_key = k
            break
    if not matched_key:
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")

    key = matched_key
    profile = profiles[key]
    stored_hash = profile.get("password_hash", "")

    logger.warning(f"[DEBUG-83] Login attempt: profile_key='{key}' | hash_exists={bool(stored_hash)} | hash_prefix='{stored_hash[:10] if stored_hash else 'EMPTY'}'")

    if not stored_hash:
        # First-Run-Setup: Hash setzen und speichern
        new_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
        try:
            _save_profile_hash(key, new_hash)
            logger.info(f"🔑 Profil '{key}': Passwort-Hash beim ersten Login gesetzt.")
        except Exception as e:
            logger.error(f"❌ Konnte Hash nicht in config.yaml speichern: {e}")
            raise HTTPException(status_code=500, detail="Hash konnte nicht gespeichert werden")
    else:
        # Passwort prüfen
        password_match = bcrypt.checkpw(req.password.encode(), stored_hash.encode())
        logger.warning(f"[DEBUG-83] Login result: match={password_match}")
        if not password_match:
            raise HTTPException(status_code=401, detail="Falsches Passwort")

    # System-Prompt für dieses Profil laden
    prompt_file = profile.get("system_prompt_file", "system_prompt.json")
    prompt_path = Path(prompt_file)
    system_prompt = ""
    if prompt_path.exists():
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = json.load(f).get("prompt", "")
    elif Path("system_prompt.json").exists():
        with open("system_prompt.json", "r", encoding="utf-8") as f:
            system_prompt = json.load(f).get("prompt", "")

    # Patch 54: JWT-Token generieren statt uuid4-session_token
    # Patch 61: temperature aus Profil-Konfiguration in JWT-Payload
    profile_temperature = profile.get("temperature")  # None = globale Einstellung
    jwt_payload = {
        "sub": key,
        "permission_level": profile.get("permission_level", "guest"),
        "allowed_model": profile.get("allowed_model"),
        "temperature": profile_temperature,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.auth.token_expire_minutes),
    }
    token = _jwt.encode(jwt_payload, settings.auth.token_secret, algorithm="HS256")

    return {
        "success": True,
        "display_name": profile.get("display_name", key),
        "theme_color": profile.get("theme_color", "#ec407a"),
        "system_prompt": system_prompt,
        "token": token,
        # Patch 47: Permission Layer
        "permission_level": profile.get("permission_level", "guest"),
        "allowed_model": profile.get("allowed_model", None),
        # Patch 61: Per-User Temperatur-Override (null = globale Einstellung)
        "temperature": profile_temperature,
    }


@router.get("/profile/prompts")
async def profile_list():
    """Gibt die verfügbaren Profile zurück (nur Namen + display_name + theme_color, kein Hash)."""
    profiles = _load_profiles()
    result = []
    for key, val in profiles.items():
        result.append({
            "profile": key,
            "display_name": val.get("display_name", key),
            "theme_color": val.get("theme_color", "#ec407a"),
        })
    return result


@router.get("/profile/my_prompt")
async def get_my_prompt(request: Request):
    """
    Patch 47: Gibt den eigenen System-Prompt zurück.
    Nur eigenes Profil, kein fremdes.
    Fallback-Kette: system_prompt_{profil}.json → system_prompt.json → ""
    """
    profile_name = getattr(request.state, "profile_name", None)
    if not profile_name:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    candidates = [
        Path(f"system_prompt_{profile_name.lower()}.json"),
        Path("system_prompt.json"),
    ]
    for p in candidates:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {"prompt": data.get("prompt", ""), "file": p.name}
    return {"prompt": "", "file": f"system_prompt_{profile_name.lower()}.json"}


@router.post("/profile/my_prompt")
async def save_my_prompt(request: Request):
    """
    Patch 47: Speichert den eigenen System-Prompt.
    Schreibt system_prompt_{profil}.json (nur eigenes Profil).
    Atomares Schreiben via tempfile + os.replace.
    """
    import os
    import tempfile
    profile_name = getattr(request.state, "profile_name", None)
    if not profile_name:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    data = await request.json()
    prompt_text = data.get("prompt", "")
    prompt_path = Path(f"system_prompt_{profile_name.lower()}.json")

    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=".", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
                json.dump({"prompt": prompt_text}, tmp_f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(prompt_path))
        except Exception:
            os.unlink(tmp_path)
            raise
    except Exception as e:
        logger.error(f"❌ Konnte System-Prompt nicht speichern: {e}")
        raise HTTPException(status_code=500, detail="Speichern fehlgeschlagen")

    logger.info(f"✏️ System-Prompt für '{profile_name}' gespeichert: {prompt_path}")
    return {"success": True, "file": prompt_path.name}


# ---------------------------------------------------------------------------
# Patch 73: Self-Service Passwort-Reset
# ---------------------------------------------------------------------------

@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, request: Request, settings: Settings = Depends(get_settings)):
    """
    Patch 73: User ändert sein eigenes Passwort.
    - Profil-Key aus JWT (request.state.profile_name) — kein Selbst-Angeben möglich
    - Altes Passwort per bcrypt prüfen
    - Neues Passwort hashen (rounds=12), config.yaml schreiben, reload_settings()
    """
    profile_key = getattr(request.state, "profile_name", None)
    if not profile_key:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    profiles = _load_profiles()
    if profile_key not in profiles:
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")

    stored_hash = profiles[profile_key].get("password_hash", "")
    if not stored_hash or not bcrypt.checkpw(req.old_password.encode(), stored_hash.encode()):
        raise HTTPException(status_code=401, detail="Altes Passwort falsch")

    new_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt(rounds=12)).decode()
    try:
        _save_profile_hash(profile_key, new_hash)
    except Exception as e:
        logger.error(f"❌ Passwort-Reset für '{profile_key}' fehlgeschlagen: {e}")
        raise HTTPException(status_code=500, detail="Passwort konnte nicht gespeichert werden")

    from zerberus.core.config import reload_settings
    reload_settings()
    logger.info(f"🔑 Passwort für Profil '{profile_key}' geändert.")
    return {"detail": "Passwort geändert"}


# ---------------------------------------------------------------------------
# TEIL 1: Voice-Endpunkt (mit Profil-Unterstützung)
# ---------------------------------------------------------------------------

@router.post("/voice")
async def voice_endpoint(
    request: Request,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings)
):
    """
    Voice-Pipeline: Whisper → Cleaner → Dialekterkennung → Transkript ans Frontend.
    Patch 44: gibt Transkript zurück (kein Auto-Response mehr – der Browser zeigt es editierbar an).
    Patch 54: liest profile_name aus request.state (JWT-Middleware).

    VOICE-PFAD (Patch 60 – verifiziert):
      POST /nala/voice → Whisper-Transkript → zurück ans Frontend → User korrigiert/sendet →
      POST /v1/chat/completions (legacy.py) → store_interaction() user + assistant ✅
      Whisper-Roheingabe wird hier als "whisper_input" gespeichert (integrity=0.9).
    """
    profile_name = getattr(request.state, "profile_name", None)

    try:
        # ------------------------------------------------------------------
        # 1. Whisper – Audio transkribieren
        # ------------------------------------------------------------------
        audio_data = await file.read()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                settings.legacy.urls.whisper_url,
                files={"file": (file.filename, audio_data, file.content_type)},
                data={"model": "whisper-1"}
            )
            resp.raise_for_status()
            whisper_result = resp.json()

        raw_transcript = whisper_result.get("text", "")

        # ------------------------------------------------------------------
        # 2. Cleaner
        # ------------------------------------------------------------------
        cleaned = clean_transcript(raw_transcript)
        logger.info(f"🎤 Transkript: '{raw_transcript}' -> '{cleaned}'")

        if not cleaned or not cleaned.strip():
            logger.info("[DEBUG-83] Stille erkannt — leeres Transkript nach Cleaner (nala/voice)")
            return {"transcript": "", "response": "", "sentiment": "neutral", "note": "silence_detected"}

        # ------------------------------------------------------------------
        # 3. Dialekterkennung – Kurzschluss
        # ------------------------------------------------------------------
        dialect_name, rest = detect_dialect_marker(cleaned)
        if dialect_name:
            dialect_response = apply_dialect(rest, dialect_name)
            logger.info(f"🗣️ Dialekt erkannt: {dialect_name}")
            session_id = request.headers.get("X-Session-ID") or "nala-default"
            try:
                await store_interaction("user", cleaned, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
                await store_interaction("assistant", dialect_response, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
            except Exception as e:
                logger.warning(f"⚠️ store_interaction fehlgeschlagen (non-fatal): {e}")
            # Transkript zurückgeben – kein Auto-Send im Browser
            return {"transcript": cleaned, "response": "", "sentiment": "neutral"}

        # ------------------------------------------------------------------
        # 4. Session-ID
        # ------------------------------------------------------------------
        session_id = request.headers.get("X-Session-ID") or "nala-default"

        # ------------------------------------------------------------------
        # 5. Whisper-Roheingabe speichern (voice-spezifisch)
        # ------------------------------------------------------------------
        try:
            await store_interaction("whisper_input", raw_transcript, integrity=0.9, profile_name=profile_name or "", profile_key=profile_name or None)
        except Exception as e:
            logger.warning(f"⚠️ store_interaction (whisper_input) fehlgeschlagen (non-fatal): {e}")

        # ------------------------------------------------------------------
        # 6. Event + Pacemaker
        # ------------------------------------------------------------------
        bus = get_event_bus()
        await bus.publish(Event(
            type="voice_input",
            data={"transcript": cleaned, "profile": profile_name}
        ))
        await update_interaction()

        # Transkript ans Frontend zurückgeben – kein LLM-Aufruf hier.
        # Der Browser zeigt es editierbar an; der User sendet selbst.
        return {
            "transcript": cleaned,
            "response": "",
            "sentiment": "neutral"
        }

    except Exception as e:
        logger.error(f"❌ Voice processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/greeting")
async def get_greeting(request: Request):
    """
    Patch 102 (B-05): Begrüßungs-Endpoint liefert nur Tageszeit-Prefix + User-Anzeigenamen.
    Frontend pickt zufällig aus mehreren Templates → echte Varianz.

    Vorher (Patch 72) wurde der Charakter-Name aus dem System-Prompt extrahiert
    ('Du bist Nala'), was zu 'Hallo, Nala!' führte — Nala spricht sich selbst an.
    Jetzt: name = display_name aus dem Profil (also 'Chris'), nicht der Charakter.
    """
    from datetime import datetime

    hour = datetime.now().hour
    if 6 <= hour < 11:
        prefix = "Guten Morgen"
    elif 11 <= hour < 18:
        prefix = "Hallo"
    elif 18 <= hour < 22:
        prefix = "Guten Abend"
    else:
        prefix = "Hallo"

    name = None
    profile_name = getattr(request.state, "profile_name", None)
    if profile_name:
        profiles = _load_profiles()
        profile = profiles.get(profile_name.lower(), {})
        dn = profile.get("display_name", "").strip()
        if dn:
            name = dn

    return {"prefix": prefix, "name": name}


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "nala", "orch_pipeline": _ORCH_PIPELINE_OK}


# ---------------------------------------------------------------------------
# Export-Endpunkt (Patch 65)
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    text: str
    format: str  # "pdf" | "docx" | "md" | "txt"
    filename: str = "nala_export"


@router.post("/export")
async def export_message(req: ExportRequest):
    """
    Exportiert einen Text als PDF, DOCX, Markdown oder TXT.
    Input: { text, format, filename }
    Output: Datei-Download mit korrektem Content-Type.
    Authentifizierung: Bearer-JWT oder X-API-Key (via Middleware).
    """
    fmt = req.format.lower().strip()
    text = req.text.strip()
    base_name = req.filename or "nala_export"

    if not text:
        raise HTTPException(400, "text darf nicht leer sein.")

    if fmt == "txt":
        content = text.encode("utf-8")
        media_type = "text/plain; charset=utf-8"
        dl_name = f"{base_name}.txt"

    elif fmt == "md":
        content = text.encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        dl_name = f"{base_name}.md"

    elif fmt == "docx":
        try:
            from docx import Document as DocxDoc
            from docx.shared import Pt, RGBColor
        except ImportError:
            raise HTTPException(503, "python-docx nicht installiert.")
        buf = io.BytesIO()
        doc = DocxDoc()
        # Header
        heading = doc.add_heading("Nala – Antwort", level=1)
        heading.runs[0].font.color.rgb = RGBColor(0x0A, 0x16, 0x28)
        # Inhalt
        for line in text.splitlines():
            p = doc.add_paragraph(line if line.strip() else "")
            p.runs[0].font.size = Pt(11) if p.runs else None
        doc.save(buf)
        content = buf.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        dl_name = f"{base_name}.docx"

    elif fmt == "pdf":
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.enums import TA_LEFT
        except ImportError:
            raise HTTPException(503, "reportlab nicht installiert.")
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=2.5 * cm,
            rightMargin=2.5 * cm,
            topMargin=2.5 * cm,
            bottomMargin=2.5 * cm,
        )
        styles = getSampleStyleSheet()
        body_style = ParagraphStyle(
            "NalaBody",
            parent=styles["Normal"],
            fontSize=11,
            leading=16,
            alignment=TA_LEFT,
            fontName="Helvetica",
        )
        title_style = ParagraphStyle(
            "NalaTitle",
            parent=styles["Heading1"],
            fontSize=16,
            leading=20,
            fontName="Helvetica-Bold",
        )
        story = [
            Paragraph("Nala – Antwort", title_style),
            Spacer(1, 0.4 * cm),
        ]
        for line in text.splitlines():
            if line.strip():
                story.append(Paragraph(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style))
            else:
                story.append(Spacer(1, 0.3 * cm))
        doc.build(story)
        content = buf.getvalue()
        media_type = "application/pdf"
        dl_name = f"{base_name}.pdf"

    else:
        raise HTTPException(400, f"Unbekanntes Format '{fmt}'. Erlaubt: pdf, docx, md, txt.")

    from fastapi.responses import Response
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
    )
