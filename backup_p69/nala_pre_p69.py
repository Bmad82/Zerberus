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
        .pw-toggle:hover { color: var(--color-gold); }
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
        #login-submit:hover { background: var(--color-gold-dark); }
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
            background: var(--color-primary-mid);
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
        .logout-btn {
            font-size: 0.6em;
            font-weight: normal;
            cursor: pointer;
            opacity: 0.8;
            padding: 4px 10px;
            border: 1px solid var(--color-gold);
            border-radius: 12px;
            white-space: nowrap;
            color: var(--color-gold);
        }
        .logout-btn:hover { opacity: 1; }

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
        }
        .session-item:hover { background: #0f1e38; }
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
        .my-prompt-save-btn:hover { background: var(--color-gold-dark); }
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
            padding: 12px 16px;
            border-radius: 20px;
            word-wrap: break-word;
            animation: fadeIn 0.3s;
        }
        .user-message {
            align-self: flex-end;
            color: var(--color-primary);
            background: var(--color-gold);
            border-bottom-right-radius: 5px;
        }
        .bot-message {
            align-self: flex-start;
            background: var(--color-primary-mid);
            color: var(--color-text-light);
            border-bottom-left-radius: 5px;
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
            border: 2px solid #2a4068;
            border-radius: 20px;
            font-size: 16px;
            outline: none;
            background: var(--color-primary);
            color: var(--color-text-light);
            transition: border 0.2s;
            resize: none;
            overflow-y: hidden;
            min-height: 48px;
            max-height: 140px;
            line-height: 1.45;
            font-family: inherit;
        }
        #text-input:focus { border-color: var(--color-gold); }
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
            transition: transform 0.2s, background 0.2s;
            box-shadow: 0 3px 8px rgba(0,0,0,0.3);
        }
        .send-btn { background: var(--color-gold); }
        .mic-btn  { background: var(--color-gold); }
        .mic-btn.recording {
            background: #f44336;
            animation: pulse 1.5s infinite;
        }
        .send-btn:hover { background: var(--color-gold-dark); }
        .mic-btn:hover  { background: var(--color-gold-dark); }
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

        /* ── Sidebar-Aktionen (Patch 67) ── */
        .sidebar-actions {
            display: flex;
            gap: 8px;
            margin-bottom: 14px;
        }
        .sidebar-action-btn {
            flex: 1;
            padding: 9px 6px;
            background: var(--color-primary);
            color: var(--color-gold);
            border: 1px solid var(--color-gold);
            border-radius: 10px;
            font-size: 0.8em;
            cursor: pointer;
            text-align: center;
        }
        .sidebar-action-btn:hover { background: #0f1e38; }

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
        .expand-btn:hover { color: var(--color-gold); border-color: var(--color-gold); }

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
        .copy-btn {
            background: none;
            border: none;
            cursor: pointer;
            color: #6a8ab8;
            font-size: 0.95em;
            padding: 0 1px;
            line-height: 1;
        }
        .copy-btn:hover { color: var(--color-gold); }
        .copy-ok { color: #4caf50 !important; }

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
    </style>
</head>
<body>
<div class="app-container">

    <!-- ── Login-Screen (Patch 45: offenes Textfeld) ── -->
    <div id="login-screen">
        <h2>👑 Nala</h2>
        <input type="text" id="login-username" placeholder="Benutzername" autocomplete="username">
        <div class="pw-wrapper">
            <input type="password" id="login-password" placeholder="Passwort" autocomplete="current-password">
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
            <div class="logout-btn" onclick="doLogout()">Abmelden</div>
        </div>

        <!-- Status-Bar (Patch 46: SSE Pipeline-Status) -->
        <div id="status-bar"></div>

        <!-- Sidebar -->
        <div class="sidebar" id="sidebar">
            <div class="close-btn" onclick="toggleSidebar()">✖</div>
            <!-- Patch 67: Sidebar-Aktionen -->
            <div class="sidebar-actions">
                <button class="sidebar-action-btn" onclick="newSession()">➕ Neue Session</button>
                <button class="sidebar-action-btn" onclick="exportChat()">💾 Exportieren</button>
            </div>
            <h3>📋 Letzte Chats</h3>
            <ul class="session-list" id="session-list"><li class="session-item">Lade...</li></ul>
            <h3>📌 Angepinnt</h3>
            <ul class="session-list" id="pinned-list"></ul>
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
                <button class="send-btn" onclick="sendTextMessage()">➤</button>
                <button class="mic-btn" id="micBtn" onclick="toggleRecording()">🎤</button>
            </div>
            <div id="transcript-hint"></div>
            <div id="mic-error" class="mic-error"></div>
        </div>
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

    let currentProfile = null;  // { name, display_name, theme_color, token, permission_level, allowed_model, temperature }
    let mediaRecorder, audioChunks = [], isRecording = false;
    let pwVisible = false;
    let evtSource = null;

    const messagesDiv    = document.getElementById('chatMessages');
    const textInput      = document.getElementById('text-input');
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

    // ── SSE EventSource (Patch 46) ──
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
                    statusBar.textContent = evt.message;
                    statusBar.style.opacity = '1';
                }
            } catch (_) {}
        };
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
            mainHeader.style.background = currentProfile.theme_color;
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

    // ── Archiv ──
    async function loadSessions() {
        try {
            // Patch 67 fix: Auth-Header mitschicken – /archive/* ist JWT-geschützt
            const response = await fetch('/archive/sessions', { headers: profileHeaders() });
            if (!response.ok) {
                sessionList.innerHTML = '<li class="session-item">Keine Chats (Auth-Fehler)</li>';
                return;
            }
            const sessions = await response.json();
            sessionList.innerHTML = '';
            if (sessions.length === 0) {
                sessionList.innerHTML = '<li class="session-item">Keine Chats</li>';
            } else {
                sessions.forEach(s => {
                    const li = document.createElement('li');
                    li.className = 'session-item';
                    li.innerHTML = `<div>${s.first_message || 'Neuer Chat'}</div>
                                    <div class="session-date">${new Date(s.created_at).toLocaleString()}</div>`;
                    li.onclick = () => loadSession(s.session_id);
                    sessionList.appendChild(li);
                });
            }
        } catch (e) {
            sessionList.innerHTML = '<li class="session-item">Fehler beim Laden</li>';
        }
    }

    async function loadSession(sid) {
        try {
            const response = await fetch(`/archive/session/${sid}`, { headers: profileHeaders() });
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
        addMessage(text, 'user');
        textInput.value = '';
        // Status-Bar zurücksetzen bei neuem Chat
        statusBar.textContent = '';
        statusBar.style.opacity = '0';

        try {
            const response = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ messages: [{ role: 'user', content: text }] })
            });
            if (response.status === 401) { handle401(); return; }
            const data = await response.json();
            const reply = data.choices?.[0]?.message?.content || 'Keine Antwort';
            addMessage(reply, 'bot');
            loadSessions();
        } catch (error) {
            addMessage('❌ Fehler: ' + error.message, 'bot');
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
                            // Transkript ins Eingabefeld – KEIN Auto-Send
                            textInput.value = transcript;
                            textInput.setSelectionRange(transcript.length, transcript.length);
                            textInput.focus();
                            transcriptHint.textContent = '🎤 Transkript – prüfen und mit Enter senden';
                        }
                    } catch (e) {
                        addMessage('❌ Fehler bei Spracherkennung', 'bot');
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
        if (!textInput.value.trim()) {
            textInput.style.height = '48px';
        }
    });

    // Patch 67: keydown statt keypress (Shift+Enter = Zeilenumbruch, Enter = Senden)
    textInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendTextMessage();
        }
    });

    // ── Nachrichten anzeigen (Patch 65: Export-Dropdown / Patch 67: Toolbar + Tracking) ──
    function addMessage(text, sender, tsOverride) {
        const now = new Date();
        const timeStr = tsOverride || now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
        chatMessages.push({ text, sender, timestamp: timeStr });

        const color = (currentProfile && sender === 'user') ? currentProfile.theme_color : null;
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${sender === 'user' ? 'user-message' : 'bot-message'}`;
        if (color) msgDiv.style.background = color;
        msgDiv.textContent = text;

        const wrapper = document.createElement('div');
        wrapper.className = sender === 'user' ? 'msg-wrapper user-wrapper' : 'msg-wrapper';
        wrapper.appendChild(msgDiv);

        // Patch 67: Toolbar mit Timestamp + Kopieren-Button
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

    // Patch 67: Kopieren-Feedback
    function copyBubble(text, btn) {
        navigator.clipboard.writeText(text).then(() => {
            const orig = btn.textContent;
            btn.textContent = '✓';
            btn.classList.add('copy-ok');
            setTimeout(() => { btn.textContent = orig; btn.classList.remove('copy-ok'); }, 1500);
        }).catch(() => {});
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
        const content = lines.join('\n');
        const blob = new Blob([content], { type: 'text/plain; charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `nala_chat_${new Date().toISOString().slice(0, 10)}.txt`;
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

    // ── Patch 67: Dynamische Begrüßung ──
    async function fetchGreeting() {
        let greeting = 'Hallo! Wie kann ich dir helfen?';
        try {
            const res = await fetch('/nala/greeting', { headers: profileHeaders() });
            if (res.ok) {
                const data = await res.json();
                greeting = data.greeting || greeting;
            }
        } catch (_) {}
        addMessage(greeting, 'bot');
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
    Timeout: 30 Sekunden ohne Event → Verbindung schließen.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id ist erforderlich")

    bus = get_event_bus()
    q = bus.subscribe_sse(session_id)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    # Event-Typ auf SSE-Nachricht mappen
                    msg_template = _SSE_MESSAGES.get(event.type)
                    if msg_template:
                        message = msg_template.format(**event.data) if '{' in msg_template else msg_template
                        payload = json.dumps({"type": event.type, "message": message}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                    elif event.type == "done":
                        payload = json.dumps({"type": "done", "message": ""}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # 30 Sekunden ohne Event → Verbindung schließen
                    break
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
    key = req.profile.lower()
    if key not in profiles:
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")

    profile = profiles[key]
    stored_hash = profile.get("password_hash", "")

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
        if not bcrypt.checkpw(req.password.encode(), stored_hash.encode()):
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

        if not cleaned:
            return {"transcript": "", "response": "", "sentiment": "neutral"}

        # ------------------------------------------------------------------
        # 3. Dialekterkennung – Kurzschluss
        # ------------------------------------------------------------------
        dialect_name, rest = detect_dialect_marker(cleaned)
        if dialect_name:
            dialect_response = apply_dialect(rest, dialect_name)
            logger.info(f"🗣️ Dialekt erkannt: {dialect_name}")
            session_id = request.headers.get("X-Session-ID") or "nala-default"
            try:
                await store_interaction("user", cleaned, session_id=session_id, profile_name=profile_name or "")
                await store_interaction("assistant", dialect_response, session_id=session_id, profile_name=profile_name or "")
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
            await store_interaction("whisper_input", raw_transcript, integrity=0.9, profile_name=profile_name or "")
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
    Patch 67: Personalisierte Begrüßung aus dem aktiven System-Prompt.
    Sucht nach 'Du bist/Ich bin [Name]' im System-Prompt des eingeloggten Profils.
    Fallback: generische Begrüßung.
    """
    import re
    profile_name = getattr(request.state, "profile_name", None)
    if not profile_name:
        return {"greeting": "Hallo! Wie kann ich dir helfen?"}

    profiles = _load_profiles()
    profile = profiles.get(profile_name.lower(), {})
    display_name = profile.get("display_name", "")

    # System-Prompt-Datei ermitteln (gleiche Fallback-Kette wie profile_login)
    prompt_file = profile.get("system_prompt_file", "")
    candidates = []
    if prompt_file:
        candidates.append(Path(prompt_file))
    candidates += [
        Path(f"system_prompt_{profile_name.lower()}.json"),
        Path("system_prompt.json"),
    ]

    char_name = display_name  # Fallback = display_name aus config.yaml
    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    prompt = data.get("prompt", "")
                    if prompt:
                        m = re.search(r'(?:du bist|ich bin|you are|i am)\s+(\w+)', prompt, re.IGNORECASE)
                        if m:
                            char_name = m.group(1)
            except Exception:
                pass
            break  # erste existierende Datei reicht

    if char_name:
        return {"greeting": f"Hallo! Ich bin {char_name}. Wie kann ich dir helfen?"}
    return {"greeting": "Hallo! Wie kann ich dir helfen?"}


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
