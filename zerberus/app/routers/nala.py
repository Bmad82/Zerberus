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

from zerberus.core.config import get_settings, invalidates_settings, Settings
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


@invalidates_settings  # Patch 156: Cache nach YAML-Write neu laden
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
    <!-- Patch 200: PWA-Verdrahtung — Manifest, Theme-Color, Apple-Meta, Touch-Icons. -->
    <link rel="manifest" href="/nala/manifest.json">
    <meta name="theme-color" content="#0a1628">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Nala">
    <link rel="apple-touch-icon" href="/static/pwa/nala-192.png">
    <link rel="apple-touch-icon" sizes="512x512" href="/static/pwa/nala-512.png">
    <!-- Patch 151 (L-001): Gemeinsame Design-Tokens für Nala UND Hel. -->
    <link rel="stylesheet" href="/static/css/shared-design.css">
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
        /* Patch 139 (B-011): Titelzeile zierlicher — ~2/3 der alten Größe. */
        .header {
            color: var(--color-text-light);
            padding: 10px 15px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 1.05em;
            font-weight: 600;
            background: var(--color-accent);
            border-bottom: 2px solid var(--color-gold);
            transition: background 0.4s;
        }
        .hamburger {
            font-size: 1.5em;
            cursor: pointer;
            width: 40px;
            text-align: center;
        }
        .title {
            flex: 1;
            text-align: center;
            color: var(--color-gold);
            font-size: 0.95em;
        }
        .profile-badge {
            font-size: 0.55em;
            font-weight: normal;
            opacity: 0.85;
            margin-left: 6px;
            color: var(--color-text-light);
        }
        /* Patch 201: Aktives-Projekt-Chip im Header — kompakter Pill,
           goldener Rand, klickbar zum Wechseln. Touch-Target ueber padding. */
        .active-project-chip {
            display: inline-block;
            font-size: 0.55em;
            font-weight: 600;
            padding: 4px 9px;
            margin-left: 8px;
            border-radius: 999px;
            border: 1px solid var(--color-gold);
            color: var(--color-gold);
            background: rgba(240, 180, 41, 0.10);
            line-height: 1.0;
            min-height: 22px;
            vertical-align: middle;
            white-space: nowrap;
            max-width: 140px;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .active-project-chip:hover { background: rgba(240, 180, 41, 0.20); }
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
        /* Patch 128 / Patch 142 (B-013): Footer fest am Unterrand des Sidebar.
           Abmelden links, Einstellungen rechts — weit voneinander, klar getrennt.
           Nur Icons, kein Text — Tooltips über title=. */
        .sidebar-footer {
            position: sticky;
            bottom: 0;
            margin-top: 14px;
            padding-top: 10px;
            padding-bottom: 4px;
            background: linear-gradient(to top, var(--color-primary-mid) 70%, transparent);
            border-top: 1px solid rgba(240,180,41,0.22);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
        }
        .sidebar-footer-btn {
            min-width: 48px;
            min-height: 48px;
            width: 48px;
            height: 48px;
            border-radius: 10px;
            border: 1px solid rgba(240,180,41,0.35);
            background: rgba(240,180,41,0.10);
            color: var(--color-gold);
            font-size: 1.3em;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.15s, transform 0.12s;
        }
        .sidebar-footer-btn:active {
            background: rgba(240,180,41,0.24);
            transform: translateY(1px);
        }
        .sidebar-footer-exit {
            border-color: rgba(229,115,115,0.45);
            background: rgba(229,115,115,0.08);
            color: #e57373;
        }
        .sidebar-footer-exit:active {
            background: rgba(229,115,115,0.18);
        }
        /* Patch 142 (B-015): Tab-Navigation im Settings-Modal.
           Drei Tabs: Aussehen (Theme/Bubbles/Skalierung), Ausdruck (Mein Ton/TTS),
           System (Passwort/Account). */
        .settings-tabs {
            display: flex;
            border-bottom: 1px solid rgba(240,180,41,0.3);
            margin: 0 -28px 14px;
            padding: 0 20px;
            gap: 4px;
        }
        .settings-tab-btn {
            flex: 1;
            padding: 10px 4px;
            min-height: 44px;
            background: transparent;
            border: none;
            color: #8aa0c0;
            font-size: 0.88em;
            font-weight: 600;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            transition: color 0.15s, border-color 0.15s;
            font-family: inherit;
        }
        .settings-tab-btn.active {
            color: var(--color-gold);
            border-bottom-color: var(--color-gold);
        }
        .settings-tab-panel { display: none; }
        .settings-tab-panel.active { display: block; }
        /* Patch 142 (B-016): UI-Skalierung. */
        :root { --ui-scale: 1; }
        .scale-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 8px 0;
        }
        .scale-row label { flex: 0 0 60px; font-size: 0.88em; color: var(--color-text-light); }
        .scale-row input[type="range"] { flex: 1; min-height: 30px; }
        .scale-row span { flex: 0 0 48px; text-align: right; font-family: monospace; font-size: 0.88em; color: #8aa0c0; }

        /* Patch 128: HSL-Slider fuer Bubble-Farben. */
        .hsl-group { margin: 10px 0 4px; padding: 10px; background: rgba(255,255,255,0.03); border-radius: 8px; }
        .hsl-group h6 { margin: 0 0 8px; font-size: 0.88em; color: var(--color-gold); }
        .hsl-slider-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
        .hsl-slider-row label { flex: 0 0 34px; font-size: 0.82em; color: var(--color-text-light); }
        .hsl-slider-row input[type="range"] { flex: 1; min-height: 30px; }
        .hsl-slider-row span { flex: 0 0 48px; font-size: 0.82em; text-align: right; color: #8a9abe; font-family: monospace; }
        .hsl-swatch { width: 28px; height: 28px; border-radius: 6px; border: 1px solid #445; flex-shrink: 0; }
        .hsl-hue-track {
            background: linear-gradient(to right,
                hsl(0,100%,50%), hsl(60,100%,50%), hsl(120,100%,50%),
                hsl(180,100%,50%), hsl(240,100%,50%), hsl(300,100%,50%), hsl(360,100%,50%));
        }
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
        /* Patch 124 / Patch 139 (B-008): Bubbles fast bis zum Rand auf Mobile,
           etwas enger auf Desktop. */
        .message {
            max-width: 92%;
            padding: 13px 18px;
            border-radius: 20px;
            word-wrap: break-word;
            animation: messageSlideIn 0.22s ease-out;
            font-size: var(--font-size-base);
            position: relative;
            overflow: hidden;
        }
        @media (min-width: 768px) {
            .message { max-width: 80%; }
        }
        /* Patch 139 (B-005): Shine in der OBEREN LINKEN Ecke als weicher
           Radial-Gradient — war vorher Linear-Gradient mit hartem Stopp. */
        .message::before {
            content: '';
            position: absolute;
            top: -30%;
            left: -20%;
            width: 80%;
            height: 80%;
            background: radial-gradient(
                ellipse at 20% 20%,
                rgba(255,255,255,0.12) 0%,
                rgba(255,255,255,0.04) 30%,
                transparent 60%
            );
            border-radius: inherit;
            pointer-events: none;
            z-index: 0;
        }
        .message > * { position: relative; z-index: 1; }
        .message:active::before {
            background: radial-gradient(
                ellipse at 20% 20%,
                rgba(255,255,255,0.18) 0%,
                rgba(255,255,255,0.06) 30%,
                transparent 60%
            );
        }
        @keyframes messageSlideIn {
            from { opacity: 0; transform: translateY(8px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        .user-message {
            align-self: flex-end;
            color: var(--bubble-user-text);
            background: var(--bubble-user-bg);
            border-radius: 18px 18px 4px 18px;
            box-shadow: 0 2px 6px rgba(240,180,41,0.18), 0 1px 2px rgba(0,0,0,0.15);
        }
        .bot-message {
            align-self: flex-start;
            background: var(--bubble-llm-bg);
            color: var(--bubble-llm-text);
            border-radius: 18px 18px 18px 4px;
            box-shadow: 0 3px 8px rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.2);
        }
        /* Patch 124: Long-message Collapse mit Gradient-Fade + "Mehr"-Button. */
        .message.collapsed { max-height: 220px; overflow: hidden; }
        .message.collapsed::after {
            content: '';
            position: absolute;
            left: 0; right: 0; bottom: 0;
            height: 60px;
            background: linear-gradient(to bottom, transparent, var(--bubble-llm-bg));
            pointer-events: none;
            z-index: 2;
        }
        .message.user-message.collapsed::after {
            background: linear-gradient(to bottom, transparent, var(--bubble-user-bg));
        }
        .expand-toggle {
            display: block;
            margin-top: 6px;
            padding: 6px 10px;
            min-height: 32px;
            background: rgba(240,180,41,0.14);
            border: 1px solid rgba(240,180,41,0.35);
            color: var(--color-gold);
            border-radius: 8px;
            font-size: 0.85em;
            cursor: pointer;
            position: relative;
            z-index: 3;
        }
        .expand-toggle:active { background: rgba(240,180,41,0.22); transform: translateY(1px); }
        /* Patch 203d-3: Code-Card + Output-Card unter dem Bot-Bubble bei Sandbox-Code-Execution.
           Mobile-first 44px Touch-Target am Toggle, Code-Block scrollbar, kein horizontal-Overflow. */
        .code-card {
            margin-top: 8px;
            background: #08111f;
            border: 1px solid rgba(240,180,41,0.28);
            border-radius: 10px;
            overflow: hidden;
            font-size: 0.86em;
        }
        .code-card-header {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            padding: 6px 10px;
            background: rgba(240,180,41,0.10);
            border-bottom: 1px solid rgba(240,180,41,0.18);
        }
        .lang-tag {
            font-family: monospace;
            font-size: 0.86em;
            color: var(--color-gold);
            text-transform: lowercase;
            letter-spacing: 0.04em;
        }
        .exit-badge {
            font-family: monospace;
            font-size: 0.78em;
            padding: 2px 7px;
            border-radius: 10px;
            border: 1px solid currentColor;
        }
        .exit-badge.exit-ok { color: #6cd4a1; }
        .exit-badge.exit-fail { color: #e57373; }
        .exec-meta {
            font-family: monospace;
            font-size: 0.78em;
            color: #8aa0c0;
            margin-left: auto;
        }
        .code-content {
            margin: 0;
            padding: 10px 12px;
            background: transparent;
            color: #e0e8f8;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 0.84em;
            line-height: 1.5;
            white-space: pre;
            overflow-x: auto;
            max-height: 380px;
            overflow-y: auto;
        }
        .code-content code { background: transparent; color: inherit; font: inherit; }
        .exec-error-banner {
            padding: 6px 10px;
            background: rgba(229,115,115,0.12);
            border-top: 1px solid rgba(229,115,115,0.32);
            color: #e57373;
            font-size: 0.82em;
        }
        .output-card {
            margin-top: 6px;
            background: #08111f;
            border: 1px solid #2a4068;
            border-radius: 10px;
            overflow: hidden;
            font-size: 0.86em;
        }
        .output-card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            padding: 6px 10px;
            background: rgba(42,64,104,0.32);
            border-bottom: 1px solid #2a4068;
            color: #c0d0e8;
        }
        .code-toggle {
            min-height: 44px;
            min-width: 44px;
            padding: 8px 14px;
            background: rgba(240,180,41,0.14);
            border: 1px solid rgba(240,180,41,0.32);
            color: var(--color-gold);
            border-radius: 8px;
            font-size: 0.84em;
            cursor: pointer;
        }
        .code-toggle:active { background: rgba(240,180,41,0.22); transform: translateY(1px); }
        .output-card.collapsed .output-card-body { display: none; }
        .output-card-body { padding: 4px 0; }
        .output-content {
            margin: 0;
            padding: 8px 12px;
            background: transparent;
            color: #d8e2f5;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 0.82em;
            line-height: 1.45;
            white-space: pre-wrap;
            word-break: break-word;
            max-height: 280px;
            overflow-y: auto;
        }
        .output-content.output-stderr { color: #f5b1b1; }
        .truncated-marker {
            padding: 4px 12px 6px;
            font-size: 0.78em;
            color: #8aa0c0;
            font-style: italic;
        }
        /* Patch 206: HitL-Confirm-Card vor Sandbox-Run.
           Nutzt das gleiche Card-Vokabular wie .code-card aus P203d-3,
           aber mit auffaelligem Gold-Schimmer (Border + Akzent) und
           prominenten 44x44 Touch-Buttons. .hitl-approved/.hitl-rejected
           sind Post-Klick-States — die Karte bleibt sichtbar als
           Audit-Spur im Chat-Verlauf. */
        .hitl-card {
            margin-top: 8px;
            background: #08111f;
            border: 1px solid rgba(240,180,41,0.55);
            border-radius: 10px;
            overflow: hidden;
            font-size: 0.86em;
            box-shadow: 0 0 0 1px rgba(240,180,41,0.10) inset;
        }
        .hitl-header {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            padding: 8px 12px;
            background: rgba(240,180,41,0.16);
            border-bottom: 1px solid rgba(240,180,41,0.32);
            color: var(--color-gold);
            font-weight: 600;
        }
        .hitl-header .lang-tag {
            margin-left: auto;
            font-weight: 400;
        }
        .hitl-actions {
            display: flex;
            gap: 8px;
            padding: 8px 12px;
            background: rgba(8,17,31,0.6);
            border-top: 1px solid rgba(240,180,41,0.18);
        }
        .hitl-actions button {
            flex: 1;
            min-height: 44px;
            min-width: 44px;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 0.92em;
            font-weight: 600;
            cursor: pointer;
        }
        .hitl-approve {
            background: rgba(108,212,161,0.18);
            border: 1px solid rgba(108,212,161,0.55);
            color: #6cd4a1;
        }
        .hitl-approve:active { background: rgba(108,212,161,0.28); transform: translateY(1px); }
        .hitl-approve:disabled { opacity: 0.5; cursor: default; }
        .hitl-reject {
            background: rgba(229,115,115,0.18);
            border: 1px solid rgba(229,115,115,0.55);
            color: #e57373;
        }
        .hitl-reject:active { background: rgba(229,115,115,0.28); transform: translateY(1px); }
        .hitl-reject:disabled { opacity: 0.5; cursor: default; }
        .hitl-resolved {
            display: block;
            text-align: center;
            padding: 10px 12px;
            font-size: 0.92em;
            font-style: italic;
            color: #b8c8e0;
        }
        .hitl-card.hitl-approved { border-color: rgba(108,212,161,0.45); }
        .hitl-card.hitl-rejected { border-color: rgba(229,115,115,0.45); }
        .hitl-card.hitl-rejected .hitl-resolved { color: #e57373; }
        .hitl-card.hitl-approved .hitl-resolved { color: #6cd4a1; }
        .exit-badge.exit-skipped { color: #c8941f; }
        /* Patch 207: Diff-Card unter der Output-Card. Zeigt added/
           modified/deleted-Liste mit optionalem Inline-unified-Diff
           pro Datei (collapsible). Rollback-Button am Footer mit
           44x44 Touch-Target. .diff-card.diff-rolled-back ist der
           Post-Klick-State (Karte bleibt sichtbar als Audit-Spur). */
        .diff-card {
            margin-top: 6px;
            background: #08111f;
            border: 1px solid rgba(240,180,41,0.32);
            border-radius: 10px;
            overflow: hidden;
            font-size: 0.84em;
        }
        .diff-card-header {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            padding: 8px 12px;
            background: rgba(240,180,41,0.10);
            border-bottom: 1px solid rgba(240,180,41,0.22);
            color: #f0d18a;
            font-weight: 600;
        }
        .diff-card-header .diff-summary {
            margin-left: auto;
            font-weight: 400;
            font-size: 0.88em;
            color: #b8c8e0;
        }
        .diff-list {
            list-style: none;
            margin: 0;
            padding: 0;
        }
        .diff-entry {
            border-top: 1px solid rgba(240,180,41,0.10);
        }
        .diff-entry:first-child { border-top: none; }
        .diff-entry-head {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 12px;
            cursor: pointer;
        }
        .diff-status {
            display: inline-block;
            min-width: 84px;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 0.74em;
            font-weight: 700;
            text-transform: uppercase;
            text-align: center;
        }
        .diff-status.diff-added { background: rgba(108,212,161,0.18); color: #6cd4a1; }
        .diff-status.diff-modified { background: rgba(240,180,41,0.18); color: #f0d18a; }
        .diff-status.diff-deleted { background: rgba(229,115,115,0.18); color: #e57373; }
        .diff-path {
            flex: 1;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            color: #d8e2f5;
            word-break: break-all;
        }
        .diff-size {
            color: #8aa0c0;
            font-size: 0.8em;
        }
        .diff-entry-body {
            padding: 6px 12px 10px;
            border-top: 1px dashed rgba(240,180,41,0.14);
            background: rgba(8,17,31,0.6);
        }
        .diff-entry.diff-collapsed .diff-entry-body { display: none; }
        .diff-content {
            margin: 0;
            padding: 6px 8px;
            background: transparent;
            color: #d8e2f5;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 0.78em;
            line-height: 1.45;
            white-space: pre;
            overflow-x: auto;
            max-height: 240px;
            overflow-y: auto;
        }
        .diff-content .diff-line-add { color: #6cd4a1; }
        .diff-content .diff-line-del { color: #e57373; }
        .diff-content .diff-line-meta { color: #8aa0c0; font-style: italic; }
        .diff-binary-note {
            color: #c8941f;
            font-style: italic;
            font-size: 0.85em;
        }
        .diff-actions {
            display: flex;
            justify-content: flex-end;
            padding: 8px 12px;
            background: rgba(8,17,31,0.6);
            border-top: 1px solid rgba(240,180,41,0.18);
        }
        .diff-rollback {
            min-height: 44px;
            min-width: 44px;
            padding: 8px 14px;
            border-radius: 8px;
            background: rgba(229,115,115,0.18);
            border: 1px solid rgba(229,115,115,0.55);
            color: #e57373;
            font-size: 0.92em;
            font-weight: 600;
            cursor: pointer;
        }
        .diff-rollback:active { background: rgba(229,115,115,0.28); transform: translateY(1px); }
        .diff-rollback:disabled { opacity: 0.5; cursor: default; }
        .diff-resolved {
            display: block;
            text-align: center;
            padding: 10px 12px;
            font-size: 0.88em;
            font-style: italic;
            color: #b8c8e0;
        }
        .diff-card.diff-rolled-back { border-color: rgba(108,212,161,0.45); }
        .diff-card.diff-rolled-back .diff-resolved { color: #6cd4a1; }
        .diff-card.diff-rollback-failed { border-color: rgba(229,115,115,0.45); }
        .diff-card.diff-rollback-failed .diff-resolved { color: #e57373; }
        /* Patch 124: Buttons wirken leicht erhoben - 3D-Feedback bei :active. */
        button:not(.expand-toggle), .btn {
            transition: transform 0.12s ease, box-shadow 0.12s ease;
        }
        button:not(.expand-toggle):active, .btn:active {
            transform: translateY(1px);
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
        /* Patch 67: textarea statt input. Patch 124: collapsed/expanded Sanft-Toggle. */
        #text-input {
            flex: 1;
            padding: 10px 16px;
            border: 1px solid rgba(240,180,41,0.3);
            border-radius: 20px;
            font-size: var(--font-size-base);
            outline: none;
            background: var(--color-primary);
            color: var(--color-text-light);
            transition: border 0.2s, box-shadow 0.2s, height 0.2s ease, min-height 0.2s ease;
            resize: none;
            overflow-y: hidden;
            min-height: 44px;
            max-height: 140px;
            line-height: 1.45;
            font-family: inherit;
        }
        #text-input:not(:focus):placeholder-shown { min-height: 44px; max-height: 44px; }
        #text-input:focus { border-color: var(--color-gold); box-shadow: 0 0 0 2px rgba(240,180,41,0.15); min-height: 48px; max-height: 140px; }
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

        /* ── Patch 144 (B-007 / F-001): Katzenpfoten-Indikator ──
           Der alte typing-indicator (Bubble mit "Antwort wird generiert…") klemmte.
           Ersatz: 4 animierte Pfoten laufen von links nach rechts über der
           Eingabezeile. Status-Text darunter, klein & leise. */
        .paw-indicator {
            position: fixed;
            bottom: 84px; /* knapp über der Input-Area */
            left: 0;
            width: 100%;
            height: 44px;
            pointer-events: none;
            z-index: 95;
            overflow: hidden;
        }
        .paw-indicator .paw-print {
            position: absolute;
            top: 10px;
            font-size: 1.5em;
            animation: pawWalk 3s linear infinite;
            filter: drop-shadow(0 1px 2px rgba(0,0,0,0.3));
        }
        .paw-indicator .paw-print:nth-child(1) { animation-delay: 0.0s; }
        .paw-indicator .paw-print:nth-child(2) { animation-delay: 0.5s; }
        .paw-indicator .paw-print:nth-child(3) { animation-delay: 1.0s; }
        .paw-indicator .paw-print:nth-child(4) { animation-delay: 1.5s; }
        @keyframes pawWalk {
            0%   { left: -40px; transform: rotate(-12deg); opacity: 0; }
            10%  { opacity: 1; }
            50%  { transform: rotate(8deg); }
            90%  { opacity: 1; }
            100% { left: calc(100% + 40px); transform: rotate(-12deg); opacity: 0; }
        }
        .paw-status {
            position: fixed;
            bottom: 68px;
            left: 0;
            right: 0;
            text-align: center;
            font-size: 0.72em;
            color: var(--color-text-light);
            opacity: 0.65;
            pointer-events: none;
            z-index: 96;
            letter-spacing: 0.02em;
            min-height: 14px;
        }
        .paw-indicator.hidden,
        .paw-status.hidden { display: none; }

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

        /* ── Expand-Button Vollbild (Patch 67, Touch-Target ≥44px Patch 130) ── */
        .expand-btn {
            min-width: 44px;
            width: 44px;
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
        /* Patch 139 (B-009): Action-Buttons initial unsichtbar, erscheinen
           bei Hover (Desktop) oder Tap (Mobile, via JS-Klasse .actions-visible).
           Keine pointer-events wenn unsichtbar, damit sie keine Klicks abfangen. */
        .msg-toolbar {
            display: flex;
            align-items: center;
            gap: 6px;
            opacity: 0;
            transition: opacity 0.2s ease;
            font-size: 0.7em;
            color: #6a8ab8;
            padding: 1px 6px;
            min-height: 18px;
            pointer-events: none;
        }
        .msg-wrapper:hover .msg-toolbar,
        .msg-wrapper.actions-visible .msg-toolbar {
            opacity: 1;
            pointer-events: auto;
        }
        .copy-btn, .bubble-action-btn {
            background: transparent;
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
        /* Patch 139 (B-010): Repeat/Retry-Button explizit transparent — nur Icon sichtbar. */
        .bubble-action-btn.retry-btn,
        .bubble-action-btn[data-action="retry"] {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
        }
        /* Patch 98 / Patch 139 (B-009): Touch-Geräte — Toolbar nicht dauerhaft
           sichtbar, sondern nur wenn .actions-visible gesetzt ist (per Tap).
           44px Touch-Targets bleiben. */
        @media (hover: none) and (pointer: coarse) {
            .msg-wrapper.actions-visible .msg-toolbar { opacity: 1; pointer-events: auto; }
            .copy-btn, .bubble-action-btn { min-width: 44px; min-height: 44px; font-size: 1.05em; }
        }

        /* ── Sentiment-Triptychon (Patch 192) ──
           Drei Chips an jeder Bubble: BERT (📝), Prosodie (🎙️), Konsens (🎯).
           Sichtbarkeit folgt dem Toolbar-Pattern (hover desktop, .actions-visible mobile). */
        .sentiment-triptych {
            display: flex;
            gap: 8px;
            margin-top: 2px;
            opacity: 0;
            transition: opacity 0.2s ease;
            pointer-events: none;
            font-size: 0.78em;
            color: #6a8ab8;
        }
        .msg-wrapper:hover .sentiment-triptych,
        .msg-wrapper.actions-visible .sentiment-triptych {
            opacity: 1;
            pointer-events: auto;
        }
        .sent-chip {
            display: inline-flex;
            align-items: center;
            gap: 2px;
            min-width: 44px;
            min-height: 28px;
            justify-content: center;
            padding: 1px 4px;
            cursor: default;
        }
        .sent-icon { opacity: 0.55; font-size: 0.85em; }
        .sent-emoji { font-size: 1.0em; }
        .sent-inactive .sent-emoji { opacity: 0.3; }
        .sent-incongruent { color: var(--color-gold); }
        /* User-Bubbles: links unten | Bot-Bubbles: rechts unten */
        .msg-wrapper .sentiment-triptych { justify-content: flex-end; }
        .msg-wrapper.user-wrapper .sentiment-triptych { justify-content: flex-start; }
        @media (hover: none) and (pointer: coarse) {
            .sent-chip { min-width: 44px; min-height: 44px; }
        }

        /* ── Export-Dropdown (Patch 65) / Patch 139 (B-008): breiter ── */
        .msg-wrapper {
            display: flex;
            flex-direction: column;
            align-self: flex-start;
            gap: 3px;
            max-width: 92%;
        }
        @media (min-width: 768px) {
            .msg-wrapper { max-width: 80%; }
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

        /* ── Patch 118a: Entscheidungsboxen (Regel 9 UI) ── */
        .decision-box {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin: 10px 0 6px 0;
            padding: 10px;
            background: rgba(255, 215, 0, 0.08);
            border: 1px solid rgba(255, 215, 0, 0.35);
            border-radius: 12px;
        }
        .decision-btn {
            padding: 10px 18px;
            min-height: 44px;
            border: 1px solid #ffd700;
            border-radius: 10px;
            background: rgba(255, 215, 0, 0.15);
            color: #ffd700;
            font-size: 0.95em;
            font-family: inherit;
            cursor: pointer;
            transition: background 0.18s, transform 0.1s;
            touch-action: manipulation;
        }
        .decision-btn:hover { background: rgba(255, 215, 0, 0.28); }
        .decision-btn:active { background: rgba(255, 215, 0, 0.45); transform: scale(0.97); }
        .decision-btn:disabled { opacity: 0.5; cursor: default; }

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
        /* Patch 183 — Black-Bug Forensik (vierter Anlauf, hoffentlich endgültig).
           Vorgänger: P109 (Anti-Invariante "NIE schwarz auf schwarz"),
           P153 (cssToHex-HSL-Bug + IIFE-Guard mit 4 String-Matches),
           P169 (Favoriten-_cleanFav mit 4 Strings + Backend-Filter theme_color).
           Root Cause: Die alten BLACK_VALUES-Listen waren EXAKT-String-Matches
           für nur 4 Formate. Nicht erfasst:
             - hsl(0,0%,0%) und hsl(*,*,L<5%)  → applyHsl() kann das produzieren
             - rgb(0,0,0) ohne Leerzeichen     → cssToHex-Pre-P153 Daten
             - rgba(0, 0, 0, 1) mit Spaces vor 1
             - rgba(0,0,0,1.0) mit Float-Notation
             - leere/null Werte (transparent ist auch unbrauchbar als BG)
           Plus: bubblePreview, bubbleTextPreview, applyHsl, loadFav setzten
           Bubble-CSS-Vars KOMPLETT OHNE Guard. Color-Picker liefert #000000
           wenn der User Schwarz wählt — wurde direkt in localStorage geschrieben.
           Fix: regex-basiertes _isBubbleBlack() + zentraler sanitizeBubbleColor()
           durch JEDE setProperty-Stelle + cleanBlackFromStorage() Sweep beim Boot
           UND nochmal in showChatScreen (defense-in-depth). */
        function _isBubbleBlack(v) {
            if (v === null || v === undefined) return true;
            var s = String(v).trim().toLowerCase().replace(/\s+/g, '');
            if (s === '' || s === 'transparent' || s === 'none') return true;
            // Hex schwarz (alle Varianten inkl. Alpha-Notation, alpha != 0)
            if (s === '#000' || s === '#000f' || s === '#000000') return true;
            if (s === '#000000ff' || /^#0{6}[1-9a-f][0-9a-f]$/.test(s)) return true;
            if (/^#0{3}[1-9a-f]$/.test(s)) return true;  // #000A short-form alpha
            // rgb(0,0,0)
            if (/^rgb\(0,0,0\)$/.test(s)) return true;
            // rgba(0,0,0,A) mit A != 0  (rgba(0,0,0,0) ist transparent, harmlos hier)
            if (/^rgba\(0,0,0,(?:0?\.0*[1-9]\d*|1(?:\.0+)?)\)$/.test(s)) return true;
            // hsl/hsla mit L=0 (immer schwarz, egal H/S)
            if (/^hsla?\(\d+(?:\.\d+)?,\d+(?:\.\d+)?%?,0(?:\.0+)?%(?:,[\d.]+)?\)$/.test(s)) return true;
            // hsl/hsla mit L<5% (visuell schwarz, unter dem Slider-min von 10)
            var hslMatch = s.match(/^hsla?\([^,]+,[^,]+,([\d.]+)%/);
            if (hslMatch && parseFloat(hslMatch[1]) < 5) return true;
            return false;
        }
        function sanitizeBubbleColor(value, fallback) {
            if (_isBubbleBlack(value)) {
                try { console.warn('[BLACK-183] Schwarzen Bubble-Wert abgefangen:', value, '→ Fallback:', fallback); } catch(_) {}
                return fallback;
            }
            return value;
        }
        function cleanBlackFromStorage() {
            var keys = ['nala_bubble_user_bg', 'nala_bubble_llm_bg',
                        'nala_bubble_user_text', 'nala_bubble_llm_text'];
            keys.forEach(function(k) {
                try {
                    var v = localStorage.getItem(k);
                    if (v && _isBubbleBlack(v)) {
                        try { console.warn('[BLACK-183] Korrupten localStorage-Wert entfernt:', k, '=', v); } catch(_) {}
                        localStorage.removeItem(k);
                    }
                } catch(_) {}
            });
            // Favoriten-Slots prüfen — auch userText/llmText, nicht nur BG
            for (var i = 1; i <= 5; i++) {
                try {
                    var raw = localStorage.getItem('nala_theme_fav_' + i);
                    if (!raw) continue;
                    var fav = JSON.parse(raw);
                    if (!fav || !fav.bubble) continue;
                    var dirty = false;
                    ['userBg', 'llmBg', 'userText', 'llmText'].forEach(function(field) {
                        if (fav.bubble[field] && _isBubbleBlack(fav.bubble[field])) {
                            delete fav.bubble[field];
                            dirty = true;
                        }
                    });
                    if (dirty) {
                        localStorage.setItem('nala_theme_fav_' + i, JSON.stringify(fav));
                        try { console.warn('[BLACK-183] Favorit', i, 'bereinigt'); } catch(_) {}
                    }
                } catch(_) {}
            }
        }

        /* Patch 77 C3 + Patch 86: Theme + Bubble + Schriftgröße aus localStorage – vor erstem Render
           Patch 103 B-13/14: Wenn ein Favoriten-Slot als "last active" markiert ist, hat der Vorrang.
           Patch 183: Pre-IIFE-Sweep + Sanitizer durchgeschleift. */
        cleanBlackFromStorage();
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
                            // Patch 183: ALLE Bubble-Felder durch _isBubbleBlack filtern,
                            // nicht nur BG. Korrupte Werte überspringen, Favorit persistent
                            // reparieren. Anti-Invariante: Bubbles werden NIE absichtlich
                            // schwarz gesetzt.
                            var fb = fav.bubble;
                            var fields = [
                                ['userBg',   '--bubble-user-bg'],
                                ['userText', '--bubble-user-text'],
                                ['llmBg',    '--bubble-llm-bg'],
                                ['llmText',  '--bubble-llm-text']
                            ];
                            var anyDirty = false;
                            fields.forEach(function(pair) {
                                var key = pair[0], cssVar = pair[1];
                                var val = fb[key];
                                if (!val) return;
                                if (_isBubbleBlack(val)) {
                                    try { console.warn('[BLACK-183] Favorit-Feld', key, 'war schwarz:', val); } catch(_) {}
                                    delete fb[key];
                                    anyDirty = true;
                                    return;
                                }
                                r.setProperty(cssVar, val);
                            });
                            if (anyDirty) {
                                try { localStorage.setItem('nala_theme_fav_' + lastFav, JSON.stringify(fav)); } catch(_) {}
                            }
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
                // Patch 183: cleanBlackFromStorage() lief schon — defense-in-depth Check
                // hier zusätzlich, falls eine künftige Schreib-Stelle den Sweep umgeht.
                Object.keys(map).forEach(function(k) {
                    var v = localStorage.getItem(k);
                    if (!v) return;
                    if (_isBubbleBlack(v)) {
                        try { console.warn('[BLACK-183] IIFE-Guard hat schwarzen Wert gefangen:', k, '=', v); } catch(_) {}
                        localStorage.removeItem(k);
                        return;
                    }
                    r.setProperty(map[k], v);
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
                <!-- Patch 201: Aktives-Projekt-Chip — sichtbar nur wenn ein Projekt gewaehlt ist.
                     Klick oeffnet Settings -> Projekte-Tab. -->
                <span id="active-project-chip" class="active-project-chip" style="display:none;cursor:pointer;" onclick="openSettingsModal(); switchSettingsTab('projects');" title="Aktives Projekt — klick zum Wechseln"></span>
            </div>
            <!-- Patch 142 (B-006): Schraubenschlüssel entfernt — Einstellungen nur noch
                 über Zahnrad ⚙️ im Hamburger-Menü. Export bleibt in der Top-Bar. -->
            <button class="icon-btn" onclick="openExportMenu()" aria-label="Exportieren" title="Exportieren">💾</button>
        </div>

        <!-- Status-Bar (Patch 46: SSE Pipeline-Status) -->
        <div id="status-bar"></div>

        <!-- Sidebar — Patch 142 (B-013): aufgeräumtes Layout.
             Oben: Neue Session. Mitte: Session-Liste (scrollbar).
             Unten fest getackert: Abmelden (links) + Einstellungen (rechts), weit voneinander.
             "Mein Ton" ist nach Settings Tab "Ausdruck" gewandert (B-012).
             Passwort-Ändern ist nach Settings Tab "System" gewandert (B-013). -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="close-btn" onclick="toggleSidebar()">✖</div>
                <div class="sidebar-actions">
                    <button class="sidebar-action-btn" onclick="newSession()">➕ Neue Session</button>
                </div>
                <div id="pw-status" style="font-size:0.82em;min-height:1.2em;margin-top:4px;"></div>
            </div>
            <h3 id="pinned-heading" style="display:none;">📌 Angepinnt</h3>
            <ul class="session-list" id="pinned-list"></ul>
            <h3>📋 Letzte Chats</h3>
            <input type="search" class="archive-search" id="archive-search" placeholder="Archiv durchsuchen…">
            <ul class="session-list" id="session-list"><li class="session-item">Lade...</li></ul>
            <!-- Patch 142 (B-013): Footer fest unten — Abmelden links, Einstellungen rechts -->
            <div class="sidebar-footer">
                <button class="sidebar-footer-btn sidebar-footer-exit" onclick="doLogout()" aria-label="Abmelden" title="Abmelden">🚪</button>
                <button class="sidebar-footer-btn sidebar-footer-cog" onclick="openSettingsModal()" aria-label="Einstellungen" title="Einstellungen">⚙️</button>
            </div>
        </div>
        <div class="overlay" id="overlay" onclick="toggleSidebar()"></div>

        <!-- Chat-Nachrichten -->
        <div class="chat-messages" id="chatMessages"></div>

        <!-- Patch 144 (B-007 / F-001): Katzenpfoten-Indikator — ersetzt den alten Text-Spinner.
             4 Pfoten laufen über der Input-Area, Status-Text darunter. -->
        <div class="paw-indicator hidden" id="pawIndicator" aria-hidden="true">
            <span class="paw-print">🐾</span>
            <span class="paw-print">🐾</span>
            <span class="paw-print">🐾</span>
            <span class="paw-print">🐾</span>
        </div>
        <div class="paw-status hidden" id="pawStatus" aria-live="polite"></div>

        <!-- Patch 145 (F-002): Partikel-Canvas für Sterne/Feuerwerk.
             pointer-events:none → blockt keine Klicks. Füllt den Viewport. -->
        <canvas id="particleCanvas" aria-hidden="true" style="position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9999;"></canvas>

        <!-- Eingabebereich (sticky) -->
        <div class="input-area">
            <div class="input-row">
                <!-- Patch 67: textarea statt input, + Vollbild-Button -->
                <textarea id="text-input" rows="1" placeholder="Schreib mir…"></textarea>
                <button class="expand-btn" onclick="fullscreenOpen()" title="Vollbild">⛶</button>
                <button class="send-btn" id="sendBtn" onclick="sendTextMessage()">➤</button>
                <button class="mic-btn" id="micBtn" onclick="toggleRecording()">🎤</button>
                <!-- Patch 191: Prosodie-Indikator (sichtbar nur bei Consent=on) -->
                <span id="prosodyIndicator" title="Sprachstimmung wird analysiert" style="display:none;font-size:0.85em;margin-left:4px;opacity:0.75;">🎭</span>
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

<!-- Patch 86 / Patch 142 (B-015): Settings-Modal mit Tab-Navigation. -->
<div id="settings-modal">
    <div class="theme-modal-inner settings-modal-inner">
        <h4>⚙️ Einstellungen</h4>

        <!-- Patch 142 (B-015): Tab-Navigation -->
        <!-- Patch 201: Tab "Projekte" eingeschoben — wechselt das aktive Projekt fuer den Chat-Kontext. -->
        <div class="settings-tabs">
            <button class="settings-tab-btn active" data-tab="look" onclick="switchSettingsTab('look')">Aussehen</button>
            <button class="settings-tab-btn" data-tab="voice" onclick="switchSettingsTab('voice')">Ausdruck</button>
            <button class="settings-tab-btn" data-tab="projects" onclick="switchSettingsTab('projects')">📁 Projekte</button>
            <button class="settings-tab-btn" data-tab="system" onclick="switchSettingsTab('system')">System</button>
        </div>

        <!-- ===== Tab "Aussehen" ===== -->
        <div class="settings-tab-panel active" id="settings-tab-look">

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
                <input type="color" id="bc-user-text" oninput="bubbleTextPreview('user')">
                <button class="bubble-reset-btn" onclick="resetBubble('user-text')" title="Zurücksetzen">↺</button>
            </div>
            <div class="bubble-picker-row">
                <label>LLM-Bubble Hintergrund</label>
                <input type="color" id="bc-llm-bg" oninput="bubblePreview()">
                <button class="bubble-reset-btn" onclick="resetBubble('llm-bg')" title="Zurücksetzen">↺</button>
            </div>
            <div class="bubble-picker-row">
                <label>LLM-Bubble Text</label>
                <input type="color" id="bc-llm-text" oninput="bubbleTextPreview('llm')">
                <button class="bubble-reset-btn" onclick="resetBubble('llm-text')" title="Zurücksetzen">↺</button>
            </div>
            <button class="export-opt-btn" style="margin-top:4px;" onclick="resetAllBubbles()">↺ Alle Bubble-Farben zurücksetzen</button>

            <!-- Patch 128: HSL-Slider (16M Farben, Touch-freundlich) -->
            <div class="hsl-group">
                <h6>🎨 HSL-Slider: User-Bubble</h6>
                <div class="hsl-slider-row">
                    <div class="hsl-swatch" id="hsl-user-swatch"></div>
                    <label>Hue</label>
                    <input type="range" id="hsl-user-h" min="0" max="360" value="45" class="hsl-hue-track" oninput="applyHsl('user')">
                    <span id="hsl-user-h-val">45</span>
                </div>
                <div class="hsl-slider-row">
                    <label>Sat</label>
                    <input type="range" id="hsl-user-s" min="0" max="100" value="80" oninput="applyHsl('user')">
                    <span id="hsl-user-s-val">80%</span>
                </div>
                <div class="hsl-slider-row">
                    <label>Lum</label>
                    <input type="range" id="hsl-user-l" min="10" max="90" value="55" oninput="applyHsl('user')">
                    <span id="hsl-user-l-val">55%</span>
                </div>
            </div>
            <div class="hsl-group">
                <h6>🎨 HSL-Slider: Bot-Bubble</h6>
                <div class="hsl-slider-row">
                    <div class="hsl-swatch" id="hsl-llm-swatch"></div>
                    <label>Hue</label>
                    <input type="range" id="hsl-llm-h" min="0" max="360" value="220" class="hsl-hue-track" oninput="applyHsl('llm')">
                    <span id="hsl-llm-h-val">220</span>
                </div>
                <div class="hsl-slider-row">
                    <label>Sat</label>
                    <input type="range" id="hsl-llm-s" min="0" max="100" value="35" oninput="applyHsl('llm')">
                    <span id="hsl-llm-s-val">35%</span>
                </div>
                <div class="hsl-slider-row">
                    <label>Lum</label>
                    <input type="range" id="hsl-llm-l" min="10" max="90" value="28" oninput="applyHsl('llm')">
                    <span id="hsl-llm-l-val">28%</span>
                </div>
            </div>
        </div>

        <!-- Sektion C: UI-Skalierung (Patch 142 / B-016) — ersetzt die festen Presets -->
        <div class="settings-section">
            <h5>🔤 UI-Skalierung</h5>
            <div class="scale-row">
                <label>Größe</label>
                <input type="range" id="ui-scale-slider" min="0.8" max="1.4" step="0.05" value="1.0" oninput="applyUiScale(this.value)">
                <span id="ui-scale-val">1.00×</span>
            </div>
            <button class="export-opt-btn" onclick="resetUiScale()">↺ Zurücksetzen (1.00×)</button>
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

        </div><!-- /settings-tab-look -->

        <!-- ===== Tab "Ausdruck" (Patch 142 / B-012) ===== -->
        <div class="settings-tab-panel" id="settings-tab-voice">
            <div class="settings-section" style="border-top:none;padding-top:0;margin-top:0;">
                <h5>✏️ Mein Ton (persönlicher System-Prompt)</h5>
                <textarea id="my-prompt-area" class="my-prompt-area" placeholder="Dein persönlicher System-Prompt..."></textarea>
                <button class="my-prompt-save-btn" onclick="saveMyPrompt()">Speichern</button>
                <div id="my-prompt-status" class="my-prompt-status"></div>
            </div>

            <!-- Patch 143 (B-014): TTS-Controls — werden von JS befüllt sobald /api/tts/voices antwortet. -->
            <div class="settings-section">
                <h5>🔊 Vorlesen (Text-to-Speech)</h5>
                <div class="theme-row" style="flex-direction:column;align-items:stretch;gap:8px;">
                    <label for="tts-voice-select" style="text-align:left;">Stimme</label>
                    <select id="tts-voice-select" onchange="saveTtsSettings()"
                            style="padding:8px;background:#0a1628;color:#e0e8f8;border:1px solid #2a4068;border-radius:6px;">
                        <option value="">Wird geladen…</option>
                    </select>
                </div>
                <div class="scale-row">
                    <label>Tempo</label>
                    <input type="range" id="tts-rate-slider" min="-50" max="100" step="5" value="0" oninput="applyTtsRate(this.value)">
                    <span id="tts-rate-val">+0%</span>
                </div>
                <button class="export-opt-btn" onclick="previewTts()">🔊 Probe hören</button>
                <div id="tts-preview-status" style="font-size:0.82em;min-height:1.2em;margin-top:4px;color:#8aa0c0;"></div>
                <!-- Patch 186: Auto-TTS-Toggle. localStorage-Key: nala_auto_tts (Default: false) -->
                <label for="autoTtsToggle" style="display:flex;align-items:center;gap:10px;margin-top:14px;padding:8px;min-height:44px;cursor:pointer;color:var(--zb-text-light, #e0e8f8);">
                    <input type="checkbox" id="autoTtsToggle" onchange="onAutoTtsToggle(this.checked)"
                           style="width:20px;height:20px;cursor:pointer;accent-color:var(--zb-accent, var(--color-accent, #ec407a));">
                    <span>Antworten automatisch vorlesen</span>
                </label>
                <div style="font-size:0.78em;color:#8aa0c0;margin-left:30px;margin-top:-4px;">
                    Nutzt die oben gewählte Stimme &amp; Tempo. Spielt jede neue Bot-Antwort automatisch ab.
                </div>
                <!-- Patch 191: Prosodie-Consent-Toggle. localStorage-Key: nala_prosody_consent (Default: false) -->
                <label for="prosodyConsentToggle" style="display:flex;align-items:center;gap:10px;margin-top:14px;padding:8px;min-height:44px;cursor:pointer;color:var(--zb-text-light, #e0e8f8);">
                    <input type="checkbox" id="prosodyConsentToggle" onchange="onProsodyConsentToggle(this.checked)"
                           style="width:20px;height:20px;cursor:pointer;accent-color:var(--zb-accent, var(--color-accent, #ec407a));">
                    <span>Sprachstimmung analysieren (Prosodie)</span>
                </label>
                <div style="font-size:0.78em;color:#8aa0c0;margin-left:30px;margin-top:-4px;">
                    Erkennt Tonfall und Stimmung deiner Sprache. Audio wird nicht gespeichert.
                </div>
            </div>
        </div>

        <!-- ===== Tab "System" (Patch 142 / B-013) ===== -->
        <!-- ===== Tab "Projekte" (Patch 201) ===== -->
        <div class="settings-tab-panel" id="settings-tab-projects">
            <div class="settings-section" style="border-top:none;padding-top:0;margin-top:0;">
                <h5>📁 Aktives Projekt</h5>
                <p style="font-size:0.85em;color:#8aa0c0;line-height:1.45;margin:0 0 12px;">
                    Waehl ein Projekt, dann fliesst dessen Persona-Overlay (P197) und Datei-Wissen (P199 RAG)
                    in jede Antwort ein. Ohne Auswahl chattet Nala wie bisher ohne Projektkontext.
                </p>
                <div id="nala-projects-active" style="font-size:0.92em;color:#e0e8f8;margin:0 0 12px;min-height:1.4em;">
                    Aktiv: <em style="color:#8aa0c0;">keins</em>
                </div>
                <div style="display:flex;gap:8px;margin-bottom:14px;">
                    <button class="export-opt-btn" onclick="loadNalaProjects()" style="flex:1;min-height:36px;">🔄 Aktualisieren</button>
                    <button class="export-opt-btn" onclick="clearActiveProject()" style="flex:1;min-height:36px;">✖ Auswahl loeschen</button>
                </div>
                <div id="nala-projects-list" style="font-size:0.92em;color:#e0e8f8;line-height:1.45;max-height:46vh;overflow-y:auto;">
                    <em style="color:#8aa0c0;">Liste wird geladen…</em>
                </div>
            </div>
        </div>

        <div class="settings-tab-panel" id="settings-tab-system">
            <div class="settings-section" style="border-top:none;padding-top:0;margin-top:0;">
                <h5>🔑 Passwort ändern</h5>
                <button class="export-opt-btn" onclick="openPwModal()">Passwort ändern…</button>
            </div>
            <div class="settings-section">
                <h5>👤 Account</h5>
                <div id="account-info" style="font-size:0.88em;color:#8aa0c0;line-height:1.5;">
                    <div>Profil: <span id="account-profile-name">–</span></div>
                    <div>Berechtigung: <span id="account-permission">–</span></div>
                </div>
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
                    // Patch 144: Pfoten beim done-Event ausblenden.
                    _hidePaws();
                } else {
                    // Patch 76: Typing-Indicator bei llm_start einblenden
                    if (evt.type === 'llm_start') showTypingIndicator();
                    // Patch 144 (F-001): Granulare Status-Updates an die Pfoten binden.
                    if (typeof setPawStatus === 'function') setPawStatus(evt.type);
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
        // Patch 186: Auto-TTS-Wiedergabe stoppen
        try { _stopAutoTtsAudio(); } catch(_) {}
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
        // Patch 183: Defense-in-depth — falls zwischen Pre-Render-IIFE und Login
        // irgendein Codepfad schwarze Werte zurückgeschrieben hat (z.B. Profile-Reload),
        // hier nochmal sweepen bevor applyAutoContrast die CSS-Vars liest.
        try { if (typeof cleanBlackFromStorage === 'function') cleanBlackFromStorage(); } catch(_) {}
        if (currentProfile) {
            // Patch 183: theme_color aus Backend ist gefiltert (P169), aber
            // defense-in-depth — wir sanitizen auch hier, weil --color-accent
            // als Fallback für --bubble-user-bg dient.
            var themeColor = (typeof sanitizeBubbleColor === 'function')
                ? sanitizeBubbleColor(currentProfile.theme_color, '#ec407a')
                : (currentProfile.theme_color || '#ec407a');
            document.documentElement.style.setProperty('--color-accent', themeColor);
            profileBadge.textContent    = '– ' + currentProfile.display_name;
        }
        // Patch 140 (B-003): Auto-Kontrast initial anwenden (falls Funktion existiert).
        try { if (typeof applyAutoContrast === 'function') applyAutoContrast(); } catch(_) {}
        loadSessions();
        connectSSE();
        if (messagesDiv.children.length === 0) {
            fetchGreeting();  // Patch 67: dynamische Begrüßung per API
        }
        // Patch 201: Aktives-Projekt-Chip initial rendern (wenn aus localStorage gesetzt).
        try { renderActiveProjectChip(); } catch(_) {}
    }

    // ── Profile-Header für API-Requests (Patch 54: Bearer-Token statt X-Permission-Level) ──
    function profileHeaders(extra) {
        const h = Object.assign({ 'X-Session-ID': sessionId }, extra || {});
        if (currentProfile && currentProfile.token) {
            h['Authorization'] = 'Bearer ' + currentProfile.token;
        }
        // Patch 201: Aktives Projekt einhaengen, falls gesetzt — wirkt auf P197
        // (Persona-Overlay) und P199 (Projekt-RAG). Bewusst hier zentral, damit
        // alle Calls (Chat, Voice, Whisper, ...) konsistent gegated sind.
        const activeId = getActiveProjectId();
        if (activeId !== null) {
            h['X-Active-Project-Id'] = String(activeId);
        }
        return h;
    }

    // ── Patch 201: Aktives-Projekt-Picker (Settings-Tab "Projekte") ──
    // State liegt in localStorage als zwei Keys (id + slug + name), damit der
    // Header-Chip auch ohne Re-Fetch sichtbar bleibt nach Reload.
    function getActiveProjectId() {
        const raw = localStorage.getItem('nala_active_project_id');
        if (!raw) return null;
        const n = parseInt(raw, 10);
        return Number.isFinite(n) ? n : null;
    }
    function getActiveProjectMeta() {
        try {
            const raw = localStorage.getItem('nala_active_project_meta');
            return raw ? JSON.parse(raw) : null;
        } catch (_) { return null; }
    }
    function setActiveProject(p) {
        if (!p || typeof p.id !== 'number') {
            clearActiveProject();
            return;
        }
        localStorage.setItem('nala_active_project_id', String(p.id));
        localStorage.setItem('nala_active_project_meta', JSON.stringify({
            id: p.id, slug: p.slug || '', name: p.name || ''
        }));
        renderActiveProjectChip();
        renderNalaProjectsList(window.__nalaProjectsCache || []);
        renderNalaProjectsActive();
    }
    function clearActiveProject() {
        localStorage.removeItem('nala_active_project_id');
        localStorage.removeItem('nala_active_project_meta');
        renderActiveProjectChip();
        renderNalaProjectsList(window.__nalaProjectsCache || []);
        renderNalaProjectsActive();
    }
    function renderActiveProjectChip() {
        const chip = document.getElementById('active-project-chip');
        if (!chip) return;
        const meta = getActiveProjectMeta();
        if (!meta || !meta.id) {
            chip.style.display = 'none';
            chip.textContent = '';
            return;
        }
        chip.style.display = '';
        chip.textContent = '📁 ' + (meta.slug || meta.name || ('#' + meta.id));
        chip.title = 'Aktives Projekt: ' + (meta.name || meta.slug) + ' — klick zum Wechseln';
    }
    function renderNalaProjectsActive() {
        const el = document.getElementById('nala-projects-active');
        if (!el) return;
        const meta = getActiveProjectMeta();
        if (!meta || !meta.id) {
            el.innerHTML = 'Aktiv: <em style="color:#8aa0c0;">keins</em>';
        } else {
            el.innerHTML = 'Aktiv: <strong style="color:var(--color-gold);">'
                + escapeProjectText(meta.name || meta.slug) + '</strong> '
                + '<span style="color:#8aa0c0;">('
                + escapeProjectText(meta.slug) + ')</span>';
        }
    }
    function escapeProjectText(s) {
        if (s === null || s === undefined) return '';
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    // Patch 203d-3: Generischer HTML-Escape fuer den Code-Execution-Renderer.
    // Delegiert an escapeProjectText (gleiche Semantik), damit der Audit-Test
    // ``escapeHtml(``-Aufrufe im Renderer zaehlen kann (XSS-Min-Count).
    function escapeHtml(s) {
        return escapeProjectText(s);
    }
    function renderNalaProjectsList(items) {
        const el = document.getElementById('nala-projects-list');
        if (!el) return;
        if (!Array.isArray(items) || items.length === 0) {
            el.innerHTML = '<em style="color:#8aa0c0;">Keine Projekte angelegt. Anlegen geht ueber Hel.</em>';
            return;
        }
        const activeId = getActiveProjectId();
        const html = items.map(p => {
            const isActive = (activeId !== null && p.id === activeId);
            const bg = isActive ? 'rgba(240,180,41,0.16)' : 'transparent';
            const border = isActive ? '1px solid var(--color-gold)' : '1px solid #2a4068';
            const action = isActive
                ? '<button class="export-opt-btn" onclick="clearActiveProject()" style="min-height:36px;padding:6px 12px;">Aktiv ✓ — abwaehlen</button>'
                : '<button class="export-opt-btn" onclick="selectActiveProjectById(' + p.id + ')" style="min-height:36px;padding:6px 12px;">Auswaehlen</button>';
            const desc = p.description ? ('<div style="font-size:0.82em;color:#8aa0c0;margin-top:3px;">' + escapeProjectText(p.description) + '</div>') : '';
            return '<div style="background:' + bg + ';border:' + border + ';border-radius:8px;padding:10px 12px;margin-bottom:8px;">'
                +   '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">'
                +     '<div style="min-width:0;flex:1;">'
                +       '<div style="font-weight:600;color:#e0e8f8;">' + escapeProjectText(p.name) + '</div>'
                +       '<div style="font-size:0.78em;color:#8aa0c0;font-family:monospace;">' + escapeProjectText(p.slug) + '</div>'
                +     '</div>'
                +     action
                +   '</div>'
                +   desc
                + '</div>';
        }).join('');
        el.innerHTML = html;
    }
    function selectActiveProjectById(id) {
        const cache = window.__nalaProjectsCache || [];
        const found = cache.find(p => p.id === id);
        if (!found) return;
        setActiveProject(found);
    }
    async function loadNalaProjects() {
        const el = document.getElementById('nala-projects-list');
        if (el) el.innerHTML = '<em style="color:#8aa0c0;">Lade…</em>';
        try {
            const res = await fetch('/nala/projects', { headers: profileHeaders() });
            if (res.status === 401) { handle401(); return; }
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            const items = Array.isArray(data.projects) ? data.projects : [];
            window.__nalaProjectsCache = items;
            // Wenn das aktive Projekt nicht mehr existiert (geloescht/archiviert),
            // raus aus localStorage. Sonst haengt der Header-Chip an Zombie-IDs.
            const activeId = getActiveProjectId();
            if (activeId !== null && !items.find(p => p.id === activeId)) {
                clearActiveProject();
            }
            renderNalaProjectsList(items);
            renderNalaProjectsActive();
        } catch (e) {
            if (el) el.innerHTML = '<em style="color:#e57373;">Fehler beim Laden: ' + escapeProjectText(e.message || String(e)) + '</em>';
        }
    }

    // ── 401-Handler: automatisch ausloggen ──
    function handle401() {
        // Patch 102 (B-02): Laufenden Chat abbrechen bei 401
        abortActiveChat('auth-expired');
        // Patch 186: Auto-TTS-Wiedergabe stoppen
        try { _stopAutoTtsAudio(); } catch(_) {}
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
    // Patch 141 (B-002): Wenn die Session keine erste User-Nachricht hat, bekommt
    // sie "Unbenannte Session" + Datum als Titel, damit sie trotzdem anklickbar ist
    // und nicht als leerer Eintrag in der Liste hängt. Untertitel zeigt Datum + Uhrzeit.
    function buildSessionItem(s, isPinned) {
        const hasMsg   = !!(s.first_message && s.first_message.trim());
        const rawMsg   = hasMsg ? s.first_message.trim() : '';
        const tsDate   = s.created_at ? new Date(s.created_at) : null;
        const dateStr  = tsDate ? tsDate.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit' }) : '';
        const timeStr  = tsDate ? tsDate.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : '';
        const title    = hasMsg
            ? (rawMsg.length > 50 ? rawMsg.slice(0, 50) + '…' : rawMsg)
            : ('Unbenannte Session' + (dateStr ? ' · ' + dateStr : ''));
        // Preview: bei echter User-Nachricht längere Variante, sonst Datum+Zeit als Untertitel
        const preview  = hasMsg
            ? (rawMsg.length > 50 ? rawMsg.slice(0, 100) + (rawMsg.length > 100 ? '…' : '') : '')
            : '';
        const ts       = dateStr + (timeStr ? ' ' + timeStr : '');

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
            // Patch 186: Auto-TTS-Wiedergabe der vorigen Session stoppen
            try { _stopAutoTtsAudio(); } catch(_) {}
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

        // Patch 206: HitL-State pro Turn zuruecksetzen, dann parallel zum
        // Chat-Long-Poll auf Pendings dieser Session pollen.
        clearHitlState();
        startHitlPolling(myAbort.signal, reqSessionId);

        try {
            // Patch 191: Prosodie-Header durchreichen (Consent + Context aus letzter Audio-Analyse).
            const _chatHeaders = profileHeaders({ 'Content-Type': 'application/json' });
            if (isProsodyConsentEnabled()) {
                _chatHeaders['X-Prosody-Consent'] = 'true';
                if (window.__nalaLastProsody) {
                    try {
                        _chatHeaders['X-Prosody-Context'] = JSON.stringify(window.__nalaLastProsody);
                    } catch(_) {}
                    // One-shot: nach Versand vergessen (frischer Audio-Input erforderlich).
                    window.__nalaLastProsody = null;
                }
            }
            const response = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: _chatHeaders,
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
            const botWrapper = addMessage(reply, 'bot');
            // Patch 203d-3: Code-Card + Output-Card unter Bot-Bubble (fail-quiet).
            if (data.code_execution) {
                try { renderCodeExecution(botWrapper, data.code_execution); } catch (_e) {}
            }
            loadSessions();
            // Patch 186: Auto-TTS — erst NACH Render der Bot-Bubble (entspricht SSE-done-Moment).
            // Nicht pro Chunk (Chat ist non-streaming → genau ein Trigger pro Antwort).
            if (isAutoTtsEnabled() && reply && reply.trim()) {
                autoTtsPlay(reply);
            }
            // Patch 192: Triptychon-Emojis aus data.sentiment fuellen (fail-quiet).
            if (data.sentiment) {
                try { applySentimentToLastBubbles(data.sentiment); } catch (_e) {}
            }
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
            // Patch 206: HitL-Polling stoppen — Antwort ist da (oder Abort/Fehler).
            // Die Karte selbst (falls gerendert) bleibt im DOM stehen.
            stopHitlPolling();
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
                        // Patch 191: Consent-Header für Prosodie-Analyse
                        const _voiceHeaders = profileHeaders();
                        if (isProsodyConsentEnabled()) {
                            _voiceHeaders['X-Prosody-Consent'] = 'true';
                        }
                        const response = await fetch('/nala/voice', {
                            method: 'POST',
                            headers: _voiceHeaders,
                            body: formData
                        });
                        if (response.status === 401) { handle401(); return; }
                        const data = await response.json();
                        const transcript = data.transcript || '';
                        // Patch 191: Prosodie-Result für nächsten Chat-Request merken.
                        // Wird im sendMessage als X-Prosody-Context Header durchgereicht.
                        if (data.prosody && isProsodyConsentEnabled()) {
                            window.__nalaLastProsody = data.prosody;
                        }
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
    // Patch 118a: Rendert Bot-Text mit optionalen [DECISION][OPTION:…][/DECISION]-Markern
    // als klickbare Buttons. Bleibt XSS-sicher: alles außerhalb der Marker geht als
    // textContent in eigene Text-Nodes; nur die Buttons werden strukturell aufgebaut.
    function renderBotContent(targetEl, text) {
        targetEl.innerHTML = '';
        const decisionRegex = /\[DECISION\]([\s\S]*?)\[\/DECISION\]/g;
        // Matcht Option-Marker bis zum Zeilenende (. matcht kein \\n per default)
        const optionRegex = /\[OPTION:([A-Za-z0-9_\-]+)\]\s+(.+)/g;
        let lastIdx = 0;
        let match;
        let found = false;
        while ((match = decisionRegex.exec(text)) !== null) {
            found = true;
            if (match.index > lastIdx) {
                targetEl.appendChild(document.createTextNode(text.slice(lastIdx, match.index)));
            }
            const boxDiv = document.createElement('div');
            boxDiv.className = 'decision-box';
            const inner = match[1] || '';
            let m;
            let anyBtn = false;
            optionRegex.lastIndex = 0;
            while ((m = optionRegex.exec(inner)) !== null) {
                const val = (m[1] || '').trim();
                const label = (m[2] || '').trim();
                if (!val || !label) continue;
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'decision-btn';
                btn.textContent = label;
                btn.dataset.value = val;
                btn.onclick = () => sendDecision(val, label, boxDiv);
                boxDiv.appendChild(btn);
                anyBtn = true;
            }
            if (anyBtn) {
                targetEl.appendChild(boxDiv);
            } else {
                // Keine validen Options → rohen Block belassen, damit nichts verschluckt wird
                targetEl.appendChild(document.createTextNode(match[0]));
            }
            lastIdx = match.index + match[0].length;
        }
        if (!found) {
            targetEl.textContent = text;
            return;
        }
        if (lastIdx < text.length) {
            targetEl.appendChild(document.createTextNode(text.slice(lastIdx)));
        }
    }

    // Patch 118a: Schickt die gewählte Option als neue User-Nachricht ab.
    // Deaktiviert alle Buttons in der Box, damit nicht doppelt geklickt werden kann.
    function sendDecision(value, label, boxEl) {
        if (boxEl) {
            boxEl.querySelectorAll('.decision-btn').forEach(b => {
                b.disabled = true;
                if (b.dataset.value === value) {
                    b.style.background = 'rgba(255, 215, 0, 0.45)';
                }
            });
        }
        const payload = label || value;
        if (typeof sendMessage === 'function') {
            sendMessage(payload);
        }
    }

    // Patch 192: Triptychon-Update — fuellt die letzten user/bot Triptychs
    // mit Emojis aus der /v1/chat/completions Sentiment-Response.
    // sentimentBlock = { user: {bert, prosody, consensus}, bot: {bert, ...} }
    function _applyTriptychBlock(triptychEl, block) {
        if (!triptychEl || !block) return;
        const bert = block.bert || {};
        const prosody = block.prosody || null;
        const consensus = block.consensus || {};
        const bertEmoji = bert.emoji || '😶';
        const consensusEmoji = consensus.emoji || bertEmoji;
        const bertChip = triptychEl.querySelector('.sent-bert .sent-emoji');
        const prosodyChip = triptychEl.querySelector('.sent-prosody');
        const prosodyEmoji = triptychEl.querySelector('.sent-prosody .sent-emoji');
        const consensusChipWrap = triptychEl.querySelector('.sent-consensus');
        const consensusChip = triptychEl.querySelector('.sent-consensus .sent-emoji');
        if (bertChip) bertChip.textContent = bertEmoji;
        if (prosody && prosodyChip && prosodyEmoji) {
            prosodyChip.classList.remove('sent-inactive');
            prosodyEmoji.textContent = prosody.emoji || '😶';
            const m = prosody.mood || '?';
            const c = (prosody.confidence !== undefined) ? Number(prosody.confidence).toFixed(2) : '?';
            prosodyChip.title = 'Stimme: ' + m + ' (conf=' + c + ')';
        } else if (prosodyChip && prosodyEmoji) {
            prosodyChip.classList.add('sent-inactive');
            prosodyEmoji.textContent = '—';
            prosodyChip.title = 'Stimm-Analyse (kein Audio)';
        }
        if (consensusChip) consensusChip.textContent = consensusEmoji;
        if (consensusChipWrap) {
            if (consensus.incongruent) {
                consensusChipWrap.classList.add('sent-incongruent');
                consensusChipWrap.title = 'Inkongruenz: Text vs. Stimme';
            } else {
                consensusChipWrap.classList.remove('sent-incongruent');
            }
        }
    }
    function applySentimentToLastBubbles(sentimentBlock) {
        if (!sentimentBlock || !window.__nalaTriptychs || !window.__nalaTriptychs.length) return;
        // Letzte Bot-Bubble (bot-Triptychon) und davor letzte User-Bubble (user-Triptychon).
        for (let i = window.__nalaTriptychs.length - 1; i >= 0 && (sentimentBlock.bot || sentimentBlock.user); i--) {
            const entry = window.__nalaTriptychs[i];
            if (sentimentBlock.bot && entry.sender === 'bot') {
                _applyTriptychBlock(entry.triptych, sentimentBlock.bot);
                sentimentBlock.bot = null;
            } else if (sentimentBlock.user && entry.sender === 'user') {
                _applyTriptychBlock(entry.triptych, sentimentBlock.user);
                sentimentBlock.user = null;
            }
        }
    }

    // Patch 139 (B-009): Tap auf Bubble zeigt Action-Toolbar für 5 Sekunden.
    // Verwendet einen kurzen Timer pro Wrapper. Desktop-Hover ist davon
    // unabhängig (CSS :hover gewinnt weiterhin).
    function attachActionToggle(wrapperEl, bubbleEl) {
        let hideTimer = null;
        bubbleEl.addEventListener('click', function(ev) {
            // Klicks auf Links/Buttons nicht als Toggle werten
            const target = ev.target;
            if (target && target.closest && target.closest('a, button, select, input, textarea')) {
                return;
            }
            wrapperEl.classList.add('actions-visible');
            if (hideTimer) clearTimeout(hideTimer);
            hideTimer = setTimeout(function() {
                wrapperEl.classList.remove('actions-visible');
                hideTimer = null;
            }, 5000);
        });
    }

    function addMessage(text, sender, tsOverride) {
        const now = new Date();
        const timeStr = tsOverride || now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
        chatMessages.push({ text, sender, timestamp: timeStr });

        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${sender === 'user' ? 'user-message' : 'bot-message'}`;
        if (sender === 'bot') {
            renderBotContent(msgDiv, text);
        } else {
            msgDiv.textContent = text;
        }

        const wrapper = document.createElement('div');
        wrapper.className = sender === 'user' ? 'msg-wrapper user-wrapper' : 'msg-wrapper';
        wrapper.appendChild(msgDiv);

        // Patch 124: Lange Messages kollabieren, "Mehr"-Button zum Ausklappen.
        if (text && text.length > 500) {
            msgDiv.classList.add('collapsed');
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'expand-toggle';
            toggleBtn.textContent = '▼ Mehr anzeigen';
            toggleBtn.addEventListener('click', function() {
                const isCollapsed = msgDiv.classList.toggle('collapsed');
                toggleBtn.textContent = isCollapsed ? '▼ Mehr anzeigen' : '▲ Weniger';
            });
            wrapper.appendChild(toggleBtn);
        }

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
        // Patch 143 (B-014): TTS-Button an Bot-Bubbles — Tap spricht den Text.
        if (sender === 'bot') {
            const ttsBtn = document.createElement('button');
            ttsBtn.className = 'bubble-action-btn';
            ttsBtn.title = 'Vorlesen';
            ttsBtn.textContent = '🔊';
            ttsBtn.onclick = async () => {
                ttsBtn.disabled = true;
                const old = ttsBtn.textContent;
                ttsBtn.textContent = '⏳';
                try {
                    await speakText(text);
                } catch (_) {
                    ttsBtn.textContent = '⚠️';
                    setTimeout(() => { ttsBtn.textContent = old; ttsBtn.disabled = false; }, 1800);
                    return;
                }
                ttsBtn.textContent = old;
                ttsBtn.disabled = false;
            };
            toolbar.appendChild(ttsBtn);
        }
        if (sender === 'user') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'bubble-action-btn retry-btn';
            retryBtn.dataset.action = 'retry';
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

        // Patch 139 (B-009): Tap auf Bubble → Action-Toolbar 5s sichtbar,
        // danach wieder ausblenden. Klick auf Button selbst zählt nicht als
        // Toggle (Buttons behalten ihre eigene onclick-Funktion).
        attachActionToggle(wrapper, msgDiv);

        // Patch 192: Sentiment-Triptychon — drei Chips (BERT/Prosodie/Konsens).
        // Default-State: alle neutral, Prosodie inaktiv (grau). updateTriptych()
        // (siehe sendMessage / SSE-Handler) fuellt die Emojis sobald die
        // Backend-Antwort eintrifft.
        const triptych = document.createElement('div');
        triptych.className = 'sentiment-triptych';
        triptych.dataset.role = sender;
        triptych.innerHTML =
            '<span class="sent-chip sent-bert" title="Text-Stimmung (BERT)">'
              + '<span class="sent-icon">📝</span>'
              + '<span class="sent-emoji">😶</span>'
            + '</span>'
            + '<span class="sent-chip sent-prosody sent-inactive" title="Stimm-Analyse (Prosodie)">'
              + '<span class="sent-icon">🎙️</span>'
              + '<span class="sent-emoji">—</span>'
            + '</span>'
            + '<span class="sent-chip sent-consensus" title="Gesamtbild (Konsens)">'
              + '<span class="sent-icon">🎯</span>'
              + '<span class="sent-emoji">😶</span>'
            + '</span>';
        wrapper.appendChild(triptych);
        // Cache fuer spaeteres update via /v1/chat/completions response.
        if (!window.__nalaTriptychs) window.__nalaTriptychs = [];
        window.__nalaTriptychs.push({ wrapper, triptych, sender });

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
        // Patch 203d-3: Caller (sendMessage) nutzt den Wrapper, um nachtraeglich
        // Code-Card + Output-Card aus ``data.code_execution`` einzuhaengen.
        return wrapper;
    }

    // Patch 203d-3: Code-Execution-Renderer fuer Sandbox-Roundtrip im Chat.
    // Liest ``data.code_execution`` aus der OpenAI-kompatiblen Chat-Response
    // (P203d-1 Schema: language/code/exit_code/stdout/stderr/execution_time_ms/
    // truncated/error) und rendert zwei Karten unter dem Bot-Bubble:
    //   - Code-Card: Sprach-Tag + exit-Badge + Code-Block (escaped)
    //   - Output-Card: stdout/stderr collapsible (default eingeklappt)
    // XSS-Schutz: alle User-/LLM-eingegebenen Strings durch escapeHtml.
    // Fallback: falls codeExec null/leer/keine code → nichts rendern.
    function renderCodeExecution(wrapperEl, codeExec) {
        if (!wrapperEl || !codeExec || typeof codeExec !== 'object') return;
        const codeStr = codeExec.code === null || codeExec.code === undefined ? '' : String(codeExec.code);
        if (!codeStr.trim()) return;
        const lang = codeExec.language ? String(codeExec.language) : 'code';
        const exitCode = (typeof codeExec.exit_code === 'number') ? codeExec.exit_code : -1;
        const stdout = codeExec.stdout ? String(codeExec.stdout) : '';
        const stderr = codeExec.stderr ? String(codeExec.stderr) : '';
        const truncated = !!codeExec.truncated;
        const errorMsg = codeExec.error ? String(codeExec.error) : '';
        const timeMs = (typeof codeExec.execution_time_ms === 'number') ? codeExec.execution_time_ms : null;
        // Patch 206: HitL-Skip-State. Wenn der Backend-HitL-Gate die Ausfuehrung
        // geblockt hat (rejected/timeout), zeigen wir die Code-Card mit
        // Skip-Badge statt exit-Code, und der ``error``-Reason landet im Banner.
        const skipped = !!codeExec.skipped;
        const hitlStatus = codeExec.hitl_status ? String(codeExec.hitl_status) : '';

        const codeCard = document.createElement('div');
        codeCard.className = 'code-card';
        let exitClass = exitCode === 0 ? 'exit-ok' : 'exit-fail';
        let exitLabel = 'exit ' + String(exitCode);
        if (skipped) {
            exitClass = 'exit-skipped';
            exitLabel = hitlStatus === 'timeout' ? '⏱ timeout' : '⏸ uebersprungen';
        }
        const metaSpan = (!skipped && timeMs !== null)
            ? ('<span class="exec-meta" title="Laufzeit">' + escapeHtml(String(timeMs)) + ' ms</span>')
            : '';
        const header = document.createElement('div');
        header.className = 'code-card-header';
        header.innerHTML =
            '<span class="lang-tag">' + escapeHtml(lang) + '</span>'
            + '<span class="exit-badge ' + exitClass + '">' + escapeHtml(exitLabel) + '</span>'
            + metaSpan;
        codeCard.appendChild(header);

        const codeBlock = document.createElement('pre');
        codeBlock.className = 'code-content';
        codeBlock.innerHTML = '<code>' + escapeHtml(codeStr) + '</code>';
        codeCard.appendChild(codeBlock);

        if (errorMsg) {
            const banner = document.createElement('div');
            banner.className = 'exec-error-banner';
            banner.innerHTML = '⚠️ ' + escapeHtml(errorMsg);
            codeCard.appendChild(banner);
        }

        let outputCard = null;
        if (stdout || stderr) {
            outputCard = document.createElement('div');
            outputCard.className = 'output-card collapsed';
            const outHeader = document.createElement('div');
            outHeader.className = 'output-card-header';
            const headerLabel = document.createElement('span');
            headerLabel.textContent = exitCode === 0 ? '📤 Ausgabe' : '⚠️ Ausgabe (Fehler)';
            outHeader.appendChild(headerLabel);
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'code-toggle';
            toggleBtn.textContent = '▼ anzeigen';
            toggleBtn.addEventListener('click', function() {
                const isCollapsed = outputCard.classList.toggle('collapsed');
                toggleBtn.textContent = isCollapsed ? '▼ anzeigen' : '▲ verbergen';
            });
            outHeader.appendChild(toggleBtn);
            outputCard.appendChild(outHeader);

            const body = document.createElement('div');
            body.className = 'output-card-body';
            if (stdout) {
                const stdoutBlock = document.createElement('pre');
                stdoutBlock.className = 'output-content output-stdout';
                stdoutBlock.innerHTML = escapeHtml(stdout);
                body.appendChild(stdoutBlock);
            }
            if (stderr) {
                const stderrBlock = document.createElement('pre');
                stderrBlock.className = 'output-content output-stderr';
                stderrBlock.innerHTML = escapeHtml(stderr);
                body.appendChild(stderrBlock);
            }
            if (truncated) {
                const trunc = document.createElement('div');
                trunc.className = 'truncated-marker';
                trunc.textContent = '… [Ausgabe gekuerzt]';
                body.appendChild(trunc);
            }
            outputCard.appendChild(body);
        }

        // Visual-Order: bubble → toolbar → code-card → output-card → diff-card → triptych → export-row
        const triptych = wrapperEl.querySelector('.sentiment-triptych');
        if (triptych) {
            wrapperEl.insertBefore(codeCard, triptych);
            if (outputCard) wrapperEl.insertBefore(outputCard, triptych);
        } else {
            wrapperEl.appendChild(codeCard);
            if (outputCard) wrapperEl.appendChild(outputCard);
        }

        // Patch 207: Diff-Card unter Output-Card, falls Backend Snapshots
        // gemacht hat. Render auch bei leerem Diff-Array (User sieht
        // "Keine Aenderungen" statt nichts) — das ist die positive
        // Bestaetigung "der Code hat nix geschrieben". Render NICHT bei
        // skipped-Status (es gab keinen Run).
        if (!skipped && Array.isArray(codeExec.diff) && codeExec.before_snapshot_id) {
            try {
                renderDiffCard(wrapperEl, codeExec, triptych);
            } catch (_e) {}
        }
    }

    // Patch 207: Diff-Renderer fuer Workspace-Aenderungen + Rollback-Button.
    // Liest ``codeExec.diff`` (Liste von DiffEntry) sowie
    // ``codeExec.before_snapshot_id`` / ``after_snapshot_id``. Rendert eine
    // Liste added/modified/deleted; Klick auf einen Eintrag toggled den
    // Inline-unified-Diff. Footer-Rollback-Button schickt
    // POST /v1/workspace/rollback mit before_snapshot_id + project_id.
    function renderDiffCard(wrapperEl, codeExec, triptych) {
        const diff = Array.isArray(codeExec.diff) ? codeExec.diff : [];
        const beforeId = codeExec.before_snapshot_id ? String(codeExec.before_snapshot_id) : '';
        const afterId = codeExec.after_snapshot_id ? String(codeExec.after_snapshot_id) : '';
        if (!beforeId) return;

        // Aktive Projekt-ID aus localStorage (P201) — Defense-in-Depth fuer
        // den Rollback-Endpoint, nicht primaere Auth.
        const activeProjectId = parseInt(localStorage.getItem('nala_active_project_id') || '0', 10);

        const card = document.createElement('div');
        card.className = 'diff-card';

        const header = document.createElement('div');
        header.className = 'diff-card-header';
        const counts = { added: 0, modified: 0, deleted: 0 };
        diff.forEach(function(d) {
            const status = d && d.status ? String(d.status) : '';
            if (counts.hasOwnProperty(status)) counts[status]++;
        });
        const summary = (
            counts.added + ' neu, '
            + counts.modified + ' geaendert, '
            + counts.deleted + ' geloescht'
        );
        header.innerHTML =
            '<span>📋 Workspace-Aenderungen</span>'
            + '<span class="diff-summary">' + escapeHtml(summary) + '</span>';
        card.appendChild(header);

        const list = document.createElement('ul');
        list.className = 'diff-list';
        if (diff.length === 0) {
            const empty = document.createElement('li');
            empty.className = 'diff-entry';
            const head = document.createElement('div');
            head.className = 'diff-entry-head';
            head.innerHTML = '<span class="diff-path">Keine Datei-Aenderungen.</span>';
            empty.appendChild(head);
            list.appendChild(empty);
        } else {
            diff.forEach(function(entry) {
                if (!entry || typeof entry !== 'object') return;
                const path = entry.path ? String(entry.path) : '?';
                const status = entry.status ? String(entry.status) : 'modified';
                const sizeBefore = (typeof entry.size_before === 'number') ? entry.size_before : 0;
                const sizeAfter = (typeof entry.size_after === 'number') ? entry.size_after : 0;
                const binary = !!entry.binary;
                const unified = (typeof entry.unified_diff === 'string') ? entry.unified_diff : '';

                const li = document.createElement('li');
                li.className = 'diff-entry diff-collapsed';

                const head = document.createElement('div');
                head.className = 'diff-entry-head';
                const statusClass = (status === 'added' || status === 'deleted' || status === 'modified')
                    ? 'diff-' + status
                    : 'diff-modified';
                const statusLabel = status.toUpperCase();
                const sizeLabel = (
                    status === 'added'
                        ? '+' + sizeAfter + 'B'
                        : (status === 'deleted'
                            ? '-' + sizeBefore + 'B'
                            : sizeBefore + 'B → ' + sizeAfter + 'B')
                );
                head.innerHTML =
                    '<span class="diff-status ' + statusClass + '">'
                    + escapeHtml(statusLabel) + '</span>'
                    + '<span class="diff-path">' + escapeHtml(path) + '</span>'
                    + '<span class="diff-size">' + escapeHtml(sizeLabel) + '</span>';
                head.addEventListener('click', function() {
                    li.classList.toggle('diff-collapsed');
                });
                li.appendChild(head);

                const body = document.createElement('div');
                body.className = 'diff-entry-body';
                if (binary) {
                    const note = document.createElement('div');
                    note.className = 'diff-binary-note';
                    note.textContent = '(Binaerdatei — kein Inline-Diff)';
                    body.appendChild(note);
                } else if (unified) {
                    const pre = document.createElement('pre');
                    pre.className = 'diff-content';
                    pre.innerHTML = colorizeUnifiedDiff(unified);
                    body.appendChild(pre);
                } else {
                    const note = document.createElement('div');
                    note.className = 'diff-binary-note';
                    note.textContent = (status === 'added')
                        ? '(neue Datei — kein Vorgaenger-Stand)'
                        : (status === 'deleted'
                            ? '(geloescht — kein Nachfolger-Stand)'
                            : '(kein Inline-Diff verfuegbar)');
                    body.appendChild(note);
                }
                li.appendChild(body);
                list.appendChild(li);
            });
        }
        card.appendChild(list);

        const actions = document.createElement('div');
        actions.className = 'diff-actions';
        const rollbackBtn = document.createElement('button');
        rollbackBtn.type = 'button';
        rollbackBtn.className = 'diff-rollback';
        rollbackBtn.textContent = '↩️ Aenderungen zurueckdrehen';
        rollbackBtn.addEventListener('click', function() {
            rollbackWorkspace(card, beforeId, activeProjectId);
        });
        if (!activeProjectId || diff.length === 0) {
            rollbackBtn.disabled = true;
            rollbackBtn.title = activeProjectId
                ? 'Keine Aenderungen — nichts zurueckzudrehen.'
                : 'Kein aktives Projekt — Rollback nicht moeglich.';
        }
        actions.appendChild(rollbackBtn);
        card.appendChild(actions);

        // Visual-Order: code-card → output-card → diff-card → triptych
        if (triptych) {
            wrapperEl.insertBefore(card, triptych);
        } else {
            wrapperEl.appendChild(card);
        }
    }

    // Patch 207: Inline-unified-Diff einfaerben — Plus-Zeilen gruen,
    // Minus-Zeilen rot, Header (---/+++/@@) grau. Zeile-fuer-Zeile
    // escapen, dann mit ``<span>``-Klasse einwickeln.
    function colorizeUnifiedDiff(text) {
        // Newline-Split via charCode(10) — einfacher String-Literal mit
        // Escape-Sequenz wuerde von Python frueh interpretiert.
        const NL = String.fromCharCode(10);
        const CR = String.fromCharCode(13);
        const normalized = String(text || '').split(CR).join('');
        const lines = normalized.split(NL);
        const out = [];
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const escaped = escapeHtml(line);
            let cls = '';
            if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) {
                cls = 'diff-line-meta';
            } else if (line.startsWith('+')) {
                cls = 'diff-line-add';
            } else if (line.startsWith('-')) {
                cls = 'diff-line-del';
            }
            out.push(cls
                ? '<span class="' + cls + '">' + escaped + '</span>'
                : escaped);
        }
        return out.join(String.fromCharCode(10));
    }

    // Patch 207: Rollback-Aufruf — sperrt den Button sofort, ruft
    // POST /v1/workspace/rollback, setzt Card-State (rolled-back ODER
    // rollback-failed). Karte bleibt sichtbar als Audit-Spur.
    async function rollbackWorkspace(cardEl, snapshotId, projectId) {
        if (!cardEl || !snapshotId) return;
        const btn = cardEl.querySelector('.diff-rollback');
        if (btn) btn.disabled = true;
        let ok = false;
        let errorReason = '';
        try {
            const r = await fetch('/v1/workspace/rollback', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    snapshot_id: snapshotId,
                    project_id: projectId,
                }),
            });
            if (r.ok) {
                const data = await r.json();
                ok = !!(data && data.ok);
                if (!ok && data && data.error) {
                    errorReason = String(data.error);
                }
            } else {
                errorReason = 'http_' + r.status;
            }
        } catch (e) {
            errorReason = 'network';
        }
        const actions = cardEl.querySelector('.diff-actions');
        if (actions) {
            if (ok) {
                cardEl.classList.add('diff-rolled-back');
                actions.innerHTML = '<span class="diff-resolved">✅ Workspace zurueckgesetzt.</span>';
            } else {
                cardEl.classList.add('diff-rollback-failed');
                const reasonText = errorReason
                    ? ' (' + errorReason + ')'
                    : '';
                actions.innerHTML = '<span class="diff-resolved">❌ Rollback fehlgeschlagen' + escapeHtml(reasonText) + '</span>';
            }
        }
    }

    // ── Patch 206: HitL-Gate vor Sandbox-Code-Execution ──────────────────
    //
    // Backend (legacy.py) blockt nach erkanntem Code-Block im Chat-Response
    // long-poll-style auf Approval. Frontend pollt parallel /v1/hitl/poll
    // (alle 1s), rendert beim ersten Pending eine Confirm-Karte mit
    // ✅/❌-Buttons (44x44 Touch-Target), schickt Klick via /v1/hitl/resolve.
    // Karte bleibt nach Klick im Chat als Audit-Spur sichtbar.
    let _hitlActiveCard = null;
    let _hitlActivePendingId = null;
    let _hitlPollHandle = null;

    function startHitlPolling(abortSignal, snapshotSessionId) {
        stopHitlPolling();  // idempotent
        let stopped = false;
        let intervalId = null;
        async function tick() {
            if (stopped) return;
            if (abortSignal && abortSignal.aborted) return;
            if (snapshotSessionId !== sessionId) {
                stopHitlPolling();
                return;
            }
            if (_hitlActiveCard) return;  // bereits gerendert
            try {
                const r = await fetch('/v1/hitl/poll', {
                    headers: profileHeaders(),
                    signal: abortSignal,
                });
                if (!r.ok) return;
                const data = await r.json();
                if (data && data.pending && !_hitlActiveCard) {
                    renderHitlCard(data.pending);
                    stopHitlPolling();  // einer reicht pro Turn
                }
            } catch (_) { /* fail-quiet — nochmal in 1s */ }
        }
        intervalId = setInterval(tick, 1000);
        tick();  // sofort erste Runde
        _hitlPollHandle = function() {
            stopped = true;
            if (intervalId) clearInterval(intervalId);
        };
    }

    function stopHitlPolling() {
        if (_hitlPollHandle) {
            try { _hitlPollHandle(); } catch (_) {}
            _hitlPollHandle = null;
        }
    }

    function renderHitlCard(pending) {
        if (!pending || _hitlActiveCard) return;
        _hitlActivePendingId = pending.id;

        const card = document.createElement('div');
        card.className = 'hitl-card';

        const header = document.createElement('div');
        header.className = 'hitl-header';
        const lang = pending.language ? String(pending.language) : 'code';
        header.innerHTML =
            '<span class="hitl-shield">🛡</span> '
            + 'Code-Ausfuehrung freigeben?'
            + '<span class="lang-tag">' + escapeHtml(lang) + '</span>';
        card.appendChild(header);

        const codeBlock = document.createElement('pre');
        codeBlock.className = 'code-content';
        codeBlock.innerHTML = '<code>' + escapeHtml(String(pending.code || '')) + '</code>';
        card.appendChild(codeBlock);

        const actions = document.createElement('div');
        actions.className = 'hitl-actions';

        const approveBtn = document.createElement('button');
        approveBtn.type = 'button';
        approveBtn.className = 'hitl-approve';
        approveBtn.textContent = '✅ Ausfuehren';
        approveBtn.addEventListener('click', function() {
            resolveHitlPending(pending.id, 'approved');
        });

        const rejectBtn = document.createElement('button');
        rejectBtn.type = 'button';
        rejectBtn.className = 'hitl-reject';
        rejectBtn.textContent = '❌ Abbrechen';
        rejectBtn.addEventListener('click', function() {
            resolveHitlPending(pending.id, 'rejected');
        });

        actions.appendChild(approveBtn);
        actions.appendChild(rejectBtn);
        card.appendChild(actions);

        messagesDiv.appendChild(card);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        _hitlActiveCard = card;
    }

    async function resolveHitlPending(pendingId, decision) {
        if (!_hitlActiveCard) return;
        // Buttons sofort sperren — kein Doppel-Klick.
        _hitlActiveCard.querySelectorAll('button').forEach(function(b) {
            b.disabled = true;
        });
        try {
            await fetch('/v1/hitl/resolve', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    pending_id: pendingId,
                    decision: decision,
                    session_id: sessionId,
                }),
            });
        } catch (_) { /* fail-quiet — Backend-Timeout-Pfad uebernimmt */ }

        // Card-State updaten (bleibt sichtbar als Audit-Spur).
        const actions = _hitlActiveCard.querySelector('.hitl-actions');
        if (actions) {
            if (decision === 'approved') {
                _hitlActiveCard.classList.add('hitl-approved');
                actions.innerHTML = '<span class="hitl-resolved">✅ Freigegeben — Code laeuft...</span>';
            } else {
                _hitlActiveCard.classList.add('hitl-rejected');
                actions.innerHTML = '<span class="hitl-resolved">❌ Abgebrochen</span>';
            }
        }
    }

    function clearHitlState() {
        _hitlActiveCard = null;
        _hitlActivePendingId = null;
        stopHitlPolling();
    }

    // Patch 76 + Patch 102 (B-03): Typing-Indicator mit Spinner + Status-Text
    // Patch 144 (B-007 / F-001): Katzenpfoten statt Text-Indikator.
    // Die Pfoten sind dauerhaft im DOM, wir blenden sie nur ein/aus.
    // Der alte Bubble-basierte Indikator bleibt als optionaler Frozen-/Error-Container,
    // wird aber nur bei Timeout oder Fehler gezeigt.
    function _showPaws(status) {
        const paws = document.getElementById('pawIndicator');
        const st   = document.getElementById('pawStatus');
        if (paws) paws.classList.remove('hidden');
        if (st) {
            st.classList.remove('hidden');
            st.textContent = status || 'Nala denkt nach…';
        }
    }
    function _hidePaws() {
        const paws = document.getElementById('pawIndicator');
        const st   = document.getElementById('pawStatus');
        if (paws) paws.classList.add('hidden');
        if (st) { st.classList.add('hidden'); st.textContent = ''; }
    }
    function showTypingIndicator() {
        _showPaws('Nala denkt nach…');
    }
    function removeTypingIndicator() {
        _hidePaws();
        // Alten Bubble-Indikator aufräumen, falls durch Timeout-/Error-Pfad erzeugt.
        const el = document.getElementById('typing-indicator');
        if (el) el.remove();
    }
    // Patch 144: Backend-Status an Pfoten-Text binden. Aufrufer: SSE-Handler.
    function setPawStatus(phase) {
        const map = {
            'rag_search': 'RAG durchsucht…',
            'llm_start':  'Nala denkt nach…',
            'rerank':     'Reranker läuft…',
            'generating': 'Antwort wird geschrieben…',
        };
        const txt = map[phase] || 'Nala denkt nach…';
        const st = document.getElementById('pawStatus');
        const paws = document.getElementById('pawIndicator');
        if (paws && paws.classList.contains('hidden')) return;
        if (st) st.textContent = txt;
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
    // Patch 153: HSL-Bug-Fix — cssToHex konvertierte 'hsl(H,S%,L%)' falsch zu ungültigem
    // Hex, was den Farbpicker auf #000000 zurücksetzte und beim nächsten oninput
    // schwarze Bubbles in localStorage schrieb. Jetzt: HSL wird korrekt über Canvas
    // aufgelöst (gleiche Technik wie getContrastColor); rgb() / rgba() per direktem
    // Parsing; Fallback auf #000000 nur wenn wirklich kein Wert vorliegt.
    function cssToHex(css) {
        if (!css) return '#000000';
        css = css.trim();
        if (css.startsWith('#')) {
            if (css.length === 4) return '#' + css[1]+css[1]+css[2]+css[2]+css[3]+css[3];
            return css.slice(0, 7);
        }
        // HSL, HSLa, oklch, etc. → über Browser-Canvas auflösen
        if (css.startsWith('hsl') || css.startsWith('oklch') || css.startsWith('color(')) {
            const tmp = document.createElement('div');
            tmp.style.color = css;
            document.body.appendChild(tmp);
            const computed = getComputedStyle(tmp).color;
            document.body.removeChild(tmp);
            const m = computed.match(/\d+/g);
            if (!m || m.length < 3) return '#000000';
            return '#' + [m[0], m[1], m[2]].map(n => (+n).toString(16).padStart(2, '0')).join('');
        }
        // rgb() / rgba() — direkt parsen
        const m = css.match(/\d+/g);
        if (!m || m.length < 3) return '#000000';
        return '#' + [m[0], m[1], m[2]].map(n => (+n).toString(16).padStart(2, '0')).join('');
    }
    // Patch 153: Liefert die tatsächlich gerenderte Hex-Farbe einer CSS-Variable —
    // auch wenn sie als 'hsl(...)' oder 'rgba(...)' gespeichert ist. Verhindert dass
    // Farbpicker bei HSL-Werten auf #000000 fallen und das dann in localStorage schreiben.
    function computedVarToHex(varName) {
        const tmp = document.createElement('div');
        tmp.style.backgroundColor = 'var(' + varName + ')';
        document.body.appendChild(tmp);
        const bg = getComputedStyle(tmp).backgroundColor;
        document.body.removeChild(tmp);
        if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') {
            // Fallback: roher Wert via cssToHex
            return cssToHex(getComputedStyle(document.documentElement).getPropertyValue(varName));
        }
        const m = bg.match(/\d+/g);
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
        // Patch 153: computedVarToHex statt cssToHex — rendert HSL/rgba korrekt über
        // Canvas, verhindert dass ein hsl()-Wert den Picker auf #000000 setzt.
        document.getElementById('bc-user-bg').value   = computedVarToHex('--bubble-user-bg');
        document.getElementById('bc-user-text').value = computedVarToHex('--bubble-user-text');
        document.getElementById('bc-llm-bg').value    = computedVarToHex('--bubble-llm-bg');
        document.getElementById('bc-llm-text').value  = computedVarToHex('--bubble-llm-text');
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
        // Patch 142 (B-016): UI-Skalierung-Slider synchronisieren.
        const scaleSlider = document.getElementById('ui-scale-slider');
        const scaleVal = document.getElementById('ui-scale-val');
        if (scaleSlider) {
            const current = localStorage.getItem('nala_ui_scale') || '1.0';
            scaleSlider.value = current;
            if (scaleVal) scaleVal.textContent = parseFloat(current).toFixed(2) + '×';
        }
        // Patch 142 (B-012): "Mein Ton" beim Öffnen laden.
        if (typeof loadMyPrompt === 'function') { try { loadMyPrompt(); } catch(_) {} }
        // Patch 142 (B-013): Account-Info füllen.
        if (currentProfile) {
            const nameEl = document.getElementById('account-profile-name');
            const permEl = document.getElementById('account-permission');
            if (nameEl) nameEl.textContent = currentProfile.display_name || currentProfile.name || '–';
            if (permEl) permEl.textContent = currentProfile.permission_level || '–';
        }
        // Patch 143: TTS-Init beim Öffnen (lazy).
        if (typeof initTtsControls === 'function') { try { initTtsControls(); } catch(_) {} }
        document.getElementById('settings-modal').classList.add('open');
    }

    // Patch 142 (B-015): Tab-Wechsel im Settings-Modal.
    function switchSettingsTab(tab) {
        document.querySelectorAll('.settings-tab-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === tab);
        });
        document.querySelectorAll('.settings-tab-panel').forEach(p => {
            p.classList.toggle('active', p.id === 'settings-tab-' + tab);
        });
        // Patch 201: Projekte-Tab lazy laden — vermeidet einen Fetch-Roundtrip
        // beim Oeffnen des Modals, wenn der User gar nicht den Tab will.
        if (tab === 'projects') {
            try { loadNalaProjects(); } catch(_) {}
        }
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
            const rawVal = document.getElementById(cfg.picker).value;
            // Patch 183: Color-Picker `<input type="color">` kann #000000 liefern
            // wenn der User Schwarz wählt — das wäre bei BG unlesbar. Sanitizer
            // greift auf den computed CSS-Default zurück.
            const fb = getComputedStyle(document.documentElement).getPropertyValue(cfg.fallback).trim();
            const val = sanitizeBubbleColor(rawVal, fb);
            r.setProperty(cfg.css, val);
            // Bei BG-Keys: nur den nicht-schwarzen Wert in localStorage schreiben.
            // Wenn Sanitizer den Picker-Wert verworfen hat, lassen wir den Storage leer
            // (der CSS :root-Default greift dann beim nächsten Boot).
            if (val === rawVal) {
                localStorage.setItem(cfg.ls, val);
            } else {
                localStorage.removeItem(cfg.ls);
            }
        });
        // Patch 140 (B-003): Text-Kontrast automatisch an Hintergrund anpassen,
        // damit dunkler Text nicht auf dunklem Bubble-Hintergrund landet.
        applyAutoContrast();
    }

    // Patch 140 (B-003): Wenn der User die Text-Farbe manuell wählt, merken
    // wir uns das — der Auto-Kontrast wird dann für dieses Ziel ausgeschaltet.
    function bubbleTextPreview(which) {
        const pickerId = which === 'user' ? 'bc-user-text' : 'bc-llm-text';
        const cssVar   = which === 'user' ? '--bubble-user-text' : '--bubble-llm-text';
        const lsKey    = which === 'user' ? 'nala_bubble_user_text' : 'nala_bubble_llm_text';
        const fbVar    = which === 'user' ? '--color-primary' : '--color-text-light';
        const manualKey = lsKey + '_manual';
        const rawVal = document.getElementById(pickerId).value;
        // Patch 183: Manueller Text auf Schwarz wäre auf dunkler Bubble unlesbar.
        const fb = getComputedStyle(document.documentElement).getPropertyValue(fbVar).trim();
        const val = sanitizeBubbleColor(rawVal, fb);
        document.documentElement.style.setProperty(cssVar, val);
        try {
            if (val === rawVal) {
                localStorage.setItem(lsKey, val);
                localStorage.setItem(manualKey, '1');
            } else {
                localStorage.removeItem(lsKey);
                localStorage.removeItem(manualKey);
            }
        } catch (_) {}
    }

    // ── Patch 140 (B-003): Auto-Kontrast für Bubble-Text ──
    // Berechnet WCAG-Luminanz, wählt hellen oder dunklen Text passend zum Hintergrund.
    // Greift NUR wenn der User die Text-Farbe nicht manuell überschrieben hat
    // (localStorage nala_bubble_user_text / nala_bubble_llm_text leer).
    function getContrastColor(cssColor) {
        // Akzeptiert #RRGGBB, rgba(), hsl() — wir rendern in einen tmp-Canvas, um
        // beliebige CSS-Farben zu RGB aufzulösen (Browser macht die Arbeit).
        const tmp = document.createElement('div');
        tmp.style.color = cssColor;
        document.body.appendChild(tmp);
        const computed = getComputedStyle(tmp).color;
        document.body.removeChild(tmp);
        const m = computed.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (!m) return '#f0f0f0';
        const r = parseInt(m[1], 10);
        const g = parseInt(m[2], 10);
        const b = parseInt(m[3], 10);
        // WCAG-gewichtete Luminanz (0..1)
        const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return lum > 0.55 ? '#1a1a1a' : '#f0f0f0';
    }

    function applyAutoContrast() {
        const cs = getComputedStyle(document.documentElement);
        const r = document.documentElement.style;
        // User-Bubble
        if (!localStorage.getItem('nala_bubble_user_text_manual')) {
            const bg = cs.getPropertyValue('--bubble-user-bg').trim();
            if (bg) {
                // Patch 183: Sanitizer als Sicherheitsnetz — getContrastColor liefert
                // '#1a1a1a' / '#f0f0f0', aber wir wollen den invariant überall haben.
                const txt = sanitizeBubbleColor(getContrastColor(bg), '#1a1a1a');
                r.setProperty('--bubble-user-text', txt);
            }
        }
        // LLM-Bubble
        if (!localStorage.getItem('nala_bubble_llm_text_manual')) {
            const bg = cs.getPropertyValue('--bubble-llm-bg').trim();
            if (bg) {
                const txt = sanitizeBubbleColor(getContrastColor(bg), '#f0f0f0');
                r.setProperty('--bubble-llm-text', txt);
            }
        }
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

    // ── Patch 128: HSL-Slider für Bubble-Farben ──
    function applyHsl(which) {
        const prefix = which === 'user' ? 'hsl-user' : 'hsl-llm';
        const h = parseInt(document.getElementById(prefix + '-h').value, 10);
        const s = parseInt(document.getElementById(prefix + '-s').value, 10);
        const l = parseInt(document.getElementById(prefix + '-l').value, 10);
        document.getElementById(prefix + '-h-val').textContent = h;
        document.getElementById(prefix + '-s-val').textContent = s + '%';
        document.getElementById(prefix + '-l-val').textContent = l + '%';
        const hslStr = 'hsl(' + h + ',' + s + '%,' + l + '%)';
        const swatch = document.getElementById(prefix + '-swatch');
        if (swatch) swatch.style.background = hslStr;
        // Als CSS-Variable setzen + im picker synchronisieren, damit Theme-Save mitzieht
        const css = which === 'user' ? '--bubble-user-bg' : '--bubble-llm-bg';
        const lsKey = which === 'user' ? 'nala_bubble_user_bg' : 'nala_bubble_llm_bg';
        const fbVar = which === 'user' ? '--color-accent' : '--color-primary-mid';
        // Patch 183: HSL-Slider-min ist L=10, aber durch BLACK_REGEX <5% greift der
        // Sanitizer trotzdem als Sicherheitsnetz. Falls jemand künftig den L-min
        // auf 0 setzt oder per Direkt-Input einen schwarzen HSL-Wert produziert.
        const fb = getComputedStyle(document.documentElement).getPropertyValue(fbVar).trim();
        const safeHsl = sanitizeBubbleColor(hslStr, fb);
        document.documentElement.style.setProperty(css, safeHsl);
        try {
            if (safeHsl === hslStr) localStorage.setItem(lsKey, hslStr);
            else localStorage.removeItem(lsKey);
        } catch (_) {}
        // Sync zum <input type="color"> (braucht HEX)
        const hex = hslToHex(h, s, l);
        const pickerId = which === 'user' ? 'bc-user-bg' : 'bc-llm-bg';
        const picker = document.getElementById(pickerId);
        if (picker) picker.value = hex;
        // Patch 140 (B-003): Kontrast nach HSL-Änderung neu berechnen.
        applyAutoContrast();
    }

    function hslToHex(h, s, l) {
        s /= 100; l /= 100;
        const k = n => (n + h / 30) % 12;
        const a = s * Math.min(l, 1 - l);
        const f = n => {
            const c = l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
            return Math.round(255 * c).toString(16).padStart(2, '0');
        };
        return '#' + f(0) + f(8) + f(4);
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

    // ── Patch 142 (B-016): UI-Skalierung ──
    // Setzt die CSS-Variable --ui-scale. Dank calc() in der CSS skalieren
    // alle Texte/Buttons/Paddings anteilig.
    function applyUiScale(val) {
        const f = parseFloat(val);
        if (!isFinite(f) || f < 0.5 || f > 2.0) return;
        document.documentElement.style.setProperty('--ui-scale', String(f));
        document.documentElement.style.setProperty('--font-size-base', (16 * f) + 'px');
        try { localStorage.setItem('nala_ui_scale', String(f)); } catch(_) {}
        const valEl = document.getElementById('ui-scale-val');
        if (valEl) valEl.textContent = f.toFixed(2) + '×';
    }
    function resetUiScale() {
        document.documentElement.style.removeProperty('--ui-scale');
        document.documentElement.style.removeProperty('--font-size-base');
        try { localStorage.removeItem('nala_ui_scale'); } catch(_) {}
        const slider = document.getElementById('ui-scale-slider');
        const valEl = document.getElementById('ui-scale-val');
        if (slider) slider.value = '1.0';
        if (valEl) valEl.textContent = '1.00×';
    }
    // Beim Laden: gespeicherte Skalierung wiederherstellen.
    (function restoreUiScale() {
        try {
            const stored = localStorage.getItem('nala_ui_scale');
            if (stored) {
                const f = parseFloat(stored);
                if (isFinite(f) && f >= 0.5 && f <= 2.0) {
                    document.documentElement.style.setProperty('--ui-scale', String(f));
                    document.documentElement.style.setProperty('--font-size-base', (16 * f) + 'px');
                }
            }
        } catch(_) {}
    })();

    // ── Patch 143 (B-014): TTS-Stubs (Endpoints werden in Patch 143 befüllt) ──
    let _ttsVoicesLoaded = false;
    async function initTtsControls() {
        const sel = document.getElementById('tts-voice-select');
        if (!sel || _ttsVoicesLoaded) return;
        try {
            const resp = await fetch('/nala/tts/voices?lang=de', { headers: profileHeaders() });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const voices = await resp.json();
            sel.innerHTML = '';
            (voices || []).forEach(v => {
                const opt = document.createElement('option');
                opt.value = v.ShortName || v.short_name || v.name;
                opt.textContent = (v.FriendlyName || v.friendly_name || opt.value);
                sel.appendChild(opt);
            });
            const stored = localStorage.getItem('nala_tts_voice');
            if (stored) sel.value = stored;
            _ttsVoicesLoaded = true;
        } catch (_) {
            sel.innerHTML = '<option value="">TTS nicht verfügbar</option>';
        }
        // Rate-Slider synchronisieren
        const rate = localStorage.getItem('nala_tts_rate') || '0';
        const slider = document.getElementById('tts-rate-slider');
        const val = document.getElementById('tts-rate-val');
        if (slider) slider.value = rate;
        if (val) val.textContent = (parseInt(rate, 10) >= 0 ? '+' : '') + rate + '%';
        // Patch 186: Auto-TTS-Toggle-State aus localStorage wiederherstellen
        try { _initAutoTtsToggle(); } catch(_) {}
        // Patch 191: Prosodie-Consent-Toggle-State + Indikator-Sichtbarkeit
        try { _initProsodyConsentToggle(); } catch(_) {}
    }
    function applyTtsRate(val) {
        const v = parseInt(val, 10);
        const label = (v >= 0 ? '+' : '') + v + '%';
        const valEl = document.getElementById('tts-rate-val');
        if (valEl) valEl.textContent = label;
        try { localStorage.setItem('nala_tts_rate', String(v)); } catch(_) {}
    }
    function saveTtsSettings() {
        const sel = document.getElementById('tts-voice-select');
        if (sel && sel.value) {
            try { localStorage.setItem('nala_tts_voice', sel.value); } catch(_) {}
        }
    }
    async function previewTts() {
        const sampleText = 'Hallo, ich bin Nala. Dies ist eine Stimmenprobe.';
        const status = document.getElementById('tts-preview-status');
        if (status) status.textContent = 'Lade…';
        try {
            await speakText(sampleText);
            if (status) status.textContent = '';
        } catch (e) {
            if (status) status.textContent = 'Fehler: ' + e.message;
        }
    }
    // Gemeinsamer TTS-Player (wird auch vom 🔊-Icon in Bot-Bubbles genutzt).
    let _ttsAudio = null;
    async function speakText(text) {
        const voice = (document.getElementById('tts-voice-select') || {}).value
            || localStorage.getItem('nala_tts_voice') || 'de-DE-ConradNeural';
        const rate = parseInt(localStorage.getItem('nala_tts_rate') || '0', 10);
        const rateStr = (rate >= 0 ? '+' : '') + rate + '%';
        if (_ttsAudio) { try { _ttsAudio.pause(); } catch(_) {} }
        const resp = await fetch('/nala/tts/speak', {
            method: 'POST',
            headers: profileHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ text, voice, rate: rateStr })
        });
        if (!resp.ok) throw new Error('TTS HTTP ' + resp.status);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        _ttsAudio = new Audio(url);
        await _ttsAudio.play();
    }

    // ── Patch 186: Auto-TTS — automatisches Vorlesen jeder neuen Bot-Antwort ──
    // localStorage-Key: nala_auto_tts ("true" / "false", Default: "false").
    // Wird beim done-Event NACH Render der Bot-Bubble aufgerufen (nicht pro Chunk).
    function isAutoTtsEnabled() {
        try { return localStorage.getItem('nala_auto_tts') === 'true'; } catch(_) { return false; }
    }
    function onAutoTtsToggle(checked) {
        try { localStorage.setItem('nala_auto_tts', checked ? 'true' : 'false'); } catch(_) {}
        console.log('[AUTO-TTS-186] Toggle ' + (checked ? 'ON' : 'OFF'));
        if (!checked) { _stopAutoTtsAudio(); }
    }
    function _initAutoTtsToggle() {
        const cb = document.getElementById('autoTtsToggle');
        if (cb) cb.checked = isAutoTtsEnabled();
    }

    // ── Patch 191: Prosodie-Consent — Opt-In für Sprachstimmungs-Analyse ──
    // localStorage-Key: nala_prosody_consent ("true" / "false", Default: "false").
    // Wird als X-Prosody-Consent Header gesendet bei /nala/voice + /v1/chat/completions.
    function isProsodyConsentEnabled() {
        try { return localStorage.getItem('nala_prosody_consent') === 'true'; } catch(_) { return false; }
    }
    function onProsodyConsentToggle(checked) {
        try { localStorage.setItem('nala_prosody_consent', checked ? 'true' : 'false'); } catch(_) {}
        console.log('[PROSODY-CONSENT-191] Toggle ' + (checked ? 'ON' : 'OFF'));
        // Bei OFF: gespeichertes Prosodie-Result aus letzter Analyse vergessen.
        if (!checked) { window.__nalaLastProsody = null; }
        _updateProsodyIndicator();
    }
    function _initProsodyConsentToggle() {
        const cb = document.getElementById('prosodyConsentToggle');
        if (cb) cb.checked = isProsodyConsentEnabled();
        _updateProsodyIndicator();
    }
    function _updateProsodyIndicator() {
        // 🎭 Indikator neben Mikrofon-Button, wenn Consent aktiv.
        const ind = document.getElementById('prosodyIndicator');
        if (!ind) return;
        ind.style.display = isProsodyConsentEnabled() ? 'inline-block' : 'none';
    }
    function _stopAutoTtsAudio() {
        if (window.__nalaAutoTtsAudio) {
            try { window.__nalaAutoTtsAudio.pause(); } catch(_) {}
            try { window.__nalaAutoTtsAudio.src = ''; } catch(_) {}
            window.__nalaAutoTtsAudio = null;
        }
    }
    async function autoTtsPlay(text) {
        if (!text || !text.trim()) return;
        // Wenn schon eine Auto-TTS-Wiedergabe läuft → vorherige stoppen
        _stopAutoTtsAudio();
        const voice = (document.getElementById('tts-voice-select') || {}).value
            || localStorage.getItem('nala_tts_voice') || 'de-DE-ConradNeural';
        const rate = parseInt(localStorage.getItem('nala_tts_rate') || '0', 10);
        const rateStr = (rate >= 0 ? '+' : '') + rate + '%';
        try {
            const resp = await fetch('/nala/tts/speak', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ text: text.trim(), voice, rate: rateStr })
            });
            if (!resp.ok) {
                console.warn('[AUTO-TTS-186] HTTP ' + resp.status + ' — stille Degradation');
                return;
            }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            window.__nalaAutoTtsAudio = audio;
            await audio.play();
        } catch (e) {
            console.warn('[AUTO-TTS-186] Fehler: ' + (e && e.message ? e.message : e));
        }
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
                    // Patch 183: Pre-P183 Favoriten können schwarze Werte enthalten,
                    // wenn ein Color-Picker auf #000 oder ein Slider auf hsl(*,*,0%)
                    // stand. Wir behandeln das wie einen leeren Slot — Fallback-Var
                    // greift, und der Favorit wird beim nächsten Save bereinigt.
                    if (v && !_isBubbleBlack(v)) {
                        r.setProperty(cfg.css, v);
                        localStorage.setItem(cfg.ls, v);
                        document.getElementById(cfg.picker).value = v;
                    } else {
                        if (v) {
                            try { console.warn('[BLACK-183] loadFav übersprang schwarzes Feld', key, '=', v); } catch(_) {}
                        }
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

    // ── Patch 145 (F-002): Partikel-Engine — Sterne, Feuerwerk, Goldregen ──
    (function initParticles() {
        const canvas = document.getElementById('particleCanvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        let particles = [];
        let running = false;
        const COLORS = ['#FFD700','#FF6B6B','#4ECDC4','#45B7D1','#96CEB4','#FFEAA7','#DFE6E9','#FF9FF3'];

        function resize() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        window.addEventListener('resize', resize);
        resize();

        function drawStar(cx, cy, size) {
            ctx.beginPath();
            for (let i = 0; i < 5; i++) {
                const a = (Math.PI * 2 / 5) * i - Math.PI / 2;
                const x = cx + Math.cos(a) * size;
                const y = cy + Math.sin(a) * size;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                const ai = a + Math.PI / 5;
                const ix = cx + Math.cos(ai) * (size * 0.4);
                const iy = cy + Math.sin(ai) * (size * 0.4);
                ctx.lineTo(ix, iy);
            }
            ctx.closePath();
            ctx.fill();
        }

        function spawn(x, y, type) {
            const count = type === 'firework' ? 80 : 30;
            for (let i = 0; i < count; i++) {
                const angle = (Math.PI * 2 / count) * i + (Math.random() * 0.5);
                const speed = type === 'firework' ? 3 + Math.random() * 5 : 1 + Math.random() * 3;
                particles.push({
                    x, y,
                    vx: Math.cos(angle) * speed,
                    vy: Math.sin(angle) * speed - (type === 'firework' ? 2 : 0),
                    life: 1.0,
                    decay: 0.01 + Math.random() * 0.02,
                    size: type === 'firework' ? 2 + Math.random() * 4 : 1 + Math.random() * 2,
                    color: COLORS[Math.floor(Math.random() * COLORS.length)],
                    shape: Math.random() > 0.5 ? 'star' : 'circle',
                    gravity: 0.05,
                });
            }
            if (!running) animate();
        }

        function goldRain() {
            for (let i = 0; i < 50; i++) {
                particles.push({
                    x: Math.random() * canvas.width,
                    y: -10,
                    vx: (Math.random() - 0.5) * 2,
                    vy: 2 + Math.random() * 3,
                    life: 1.0,
                    decay: 0.005,
                    size: 2 + Math.random() * 3,
                    color: '#FFD700',
                    shape: 'circle',
                    gravity: 0.02,
                });
            }
            if (!running) animate();
        }

        function animate() {
            running = true;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            particles = particles.filter(p => {
                p.x += p.vx;
                p.y += p.vy;
                p.vy += p.gravity;
                p.life -= p.decay;
                if (p.life <= 0) return false;
                ctx.globalAlpha = Math.max(0, p.life);
                ctx.fillStyle = p.color;
                if (p.shape === 'star') {
                    drawStar(p.x, p.y, p.size);
                } else {
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
                    ctx.fill();
                }
                return true;
            });
            ctx.globalAlpha = 1;
            if (particles.length > 0) {
                requestAnimationFrame(animate);
            } else {
                running = false;
                ctx.clearRect(0, 0, canvas.width, canvas.height);
            }
        }

        function flashBackground() {
            const body = document.body;
            const oldBg = body.style.backgroundColor;
            body.style.transition = 'background-color 0.1s ease';
            body.style.backgroundColor = 'rgba(255,215,0,0.12)';
            setTimeout(() => {
                body.style.backgroundColor = oldBg;
                setTimeout(() => { body.style.transition = ''; }, 150);
            }, 150);
        }

        // ── Trigger 1: Rapid-Tap im Textfeld → Sterne-Explosion ──
        const input = document.getElementById('text-input');
        if (input) {
            let tapTimes = [];
            input.addEventListener('keydown', function() {
                const now = Date.now();
                tapTimes.push(now);
                tapTimes = tapTimes.filter(t => now - t < 2000);
                if (tapTimes.length >= 7) {
                    const rect = input.getBoundingClientRect();
                    spawn(rect.left + rect.width / 2, rect.top, 'star');
                    tapTimes = [];
                    flashBackground();
                }
            });
        }

        // ── Trigger 2: Swipe-Up → Feuerwerk + Goldregen ──
        let touchStartY = 0;
        let touchStartX = 0;
        let touchStartTs = 0;
        document.addEventListener('touchstart', function(e) {
            if (!e.touches || e.touches.length === 0) return;
            touchStartY = e.touches[0].clientY;
            touchStartX = e.touches[0].clientX;
            touchStartTs = Date.now();
        }, { passive: true });
        document.addEventListener('touchend', function(e) {
            if (!e.changedTouches || e.changedTouches.length === 0) return;
            const t = e.changedTouches[0];
            const dy = touchStartY - t.clientY;
            const dx = Math.abs(touchStartX - t.clientX);
            const dt = Date.now() - touchStartTs;
            // Klares Swipe-Up: ≥200px hoch, <100px seitlich, <800ms
            if (dy > 200 && dx < 100 && dt < 800) {
                spawn(t.clientX, window.innerHeight, 'firework');
                goldRain();
                flashBackground();
            }
        }, { passive: true });

        // Exports für optionale Aufrufer (z.B. Easter Eggs)
        window.__nalaParticles = { spawn, goldRain };
    })();
</script>
<!-- Patch 200: Service-Worker-Registrierung (PWA App-Shell-Cache). -->
<script>
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function () {
            navigator.serviceWorker.register('/nala/sw.js', { scope: '/nala/' })
                .catch(function (err) { console.warn('[PWA-200] SW-Reg fehlgeschlagen:', err); });
        });
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
                    elif event.type == "prosody":
                        # Patch 193: Named SSE-Event fuer Prosodie-Daten.
                        # Frontend bindet onProsody-Listener am EventSource —
                        # die Daten kommen vor dem done-Event aus dem /voice-Pfad.
                        payload = json.dumps(event.data or {}, ensure_ascii=False)
                        yield f"event: prosody\ndata: {payload}\n\n"
                    elif event.type == "sentiment":
                        # Patch 193: Named SSE-Event fuer Sentiment-Daten (BERT
                        # + optional Konsens). Triptychon-UI hoert auf "sentiment".
                        payload = json.dumps(event.data or {}, ensure_ascii=False)
                        yield f"event: sentiment\ndata: {payload}\n\n"
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

    # Patch 169 (B1): Theme-Color-Default haerten. Wenn ein Profil aus alten
    # Sessions noch '#000000' / leerstring / null trägt, fällt das Frontend
    # bei `--color-accent` auf Schwarz, was wiederum den Bubble-Picker auf
    # Schwarz setzt. Defensive: hier filtern + Standard zurueckgeben.
    raw_theme = profile.get("theme_color")
    theme_color = str(raw_theme).strip() if raw_theme else ""
    if not theme_color or theme_color.lower() in ("#000000", "#000", "rgb(0,0,0)"):
        if raw_theme:
            logger.debug("[SETTINGS-169] Profil %s hatte schwarze theme_color, verwende Default", key)
        theme_color = "#ec407a"

    return {
        "success": True,
        "display_name": profile.get("display_name", key),
        "theme_color": theme_color,
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


# ---------------------------------------------------------------------------
# Patch 201 — Projekt-Liste fuer Nala-User
# ---------------------------------------------------------------------------
# Read-only Sicht auf die Hel-Projekte fuer eingeloggte Nala-User. Bewusst
# eigener Endpoint statt Wiederverwendung von /hel/admin/projects:
#   - Hel-CRUD ist Basic-Auth-gated, Nala-User haben aber JWT
#   - Nala-User darf NIE persona_overlay sehen (Admin-Geheimnis: Tonfall-Hints
#     und system_addendum koennen Prompt-Engineering-Spuren enthalten)
#   - Archivierte Projekte werden hier per Default ausgeblendet
# Antwort: nur id/slug/name/description/updated_at — minimal genug fuer den
# Picker im Settings-Modal.

@router.get("/projects")
async def nala_projects_list(request: Request):
    """Liefert die nicht-archivierten Projekte als schlanke Liste fuer den Nala-Picker.

    Auth: JWT-Middleware setzt ``request.state.profile_name`` — ohne gueltigen
    Token also 401, mit Token (auch Guest) 200. Persona-Overlay wird absichtlich
    NICHT mitgeliefert.
    """
    profile_name = getattr(request.state, "profile_name", None)
    if not profile_name:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt")

    from zerberus.core import projects_repo

    items = await projects_repo.list_projects(include_archived=False)
    slim = [
        {
            "id": p.get("id"),
            "slug": p.get("slug"),
            "name": p.get("name"),
            "description": p.get("description") or "",
            "updated_at": p.get("updated_at"),
        }
        for p in items
    ]
    return {"projects": slim, "count": len(slim)}


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

        # Patch 160: Short-Audio-Guard + konfigurierbarer Timeout + Einmal-Retry
        # via zentralem whisper_client (legacy.py teilt dieselbe Logik).
        from zerberus.utils.whisper_client import transcribe, WhisperSilenceGuard

        # Patch 188: Prosodie-Foundation + Patch 190: Pipeline aktiviert.
        # [PROSODY-188] Marker für Anchor-Audit.
        # Patch 191: Consent-Header — Prosodie nur wenn User explizit zugestimmt.
        prosody_consent = request.headers.get("X-Prosody-Consent", "false").lower() == "true"

        # Patch 190: Prosodie-Manager + Parallel-Analyse (asyncio.gather).
        # Whisper-Fehler = harter Fehler; Prosodie-Fehler = weicher Fehler.
        from zerberus.modules.prosody.manager import get_prosody_manager
        _prosody_mgr = get_prosody_manager(settings)
        _prosody_active = _prosody_mgr.is_active and prosody_consent
        prosody_outcome = None

        async def _whisper_call():
            return await transcribe(
                whisper_url=settings.legacy.urls.whisper_url,
                audio_data=audio_data,
                filename=file.filename,
                content_type=file.content_type,
                whisper_cfg=settings.whisper,
            )

        try:
            if _prosody_active:
                logger.info("[PROSODY-190] Whisper+Gemma parallel (Consent gegeben)")
                whisper_task = asyncio.create_task(_whisper_call())
                prosody_task = asyncio.create_task(_prosody_mgr.analyze(audio_data))
                whisper_result, prosody_outcome = await asyncio.gather(
                    whisper_task, prosody_task, return_exceptions=True,
                )
                if isinstance(whisper_result, Exception):
                    raise whisper_result
                if isinstance(prosody_outcome, Exception):
                    logger.warning(f"[PROSODY-190] Analyse fehlgeschlagen: {prosody_outcome}")
                    prosody_outcome = None
                else:
                    _src = (prosody_outcome or {}).get("source", "?")
                    _mood = (prosody_outcome or {}).get("mood", "?")
                    _conf = (prosody_outcome or {}).get("confidence", 0.0)
                    logger.info(f"[PROSODY-190] mood={_mood} confidence={_conf:.2f} source={_src}")
            else:
                whisper_result = await _whisper_call()
        except WhisperSilenceGuard:
            # Short-Audio: nala.voice-Format — Transcript + Response beide leer.
            return {
                "transcript": "",
                "response": "",
                "sentiment": "neutral",
                "note": "short_audio_skipped",
            }

        raw_transcript = whisper_result.get("text", "")

        # ------------------------------------------------------------------
        # 2. Cleaner (Patch 135: X-Already-Cleaned überspringt doppeltes Cleaning)
        # ------------------------------------------------------------------
        already_cleaned = request.headers.get("X-Already-Cleaned", "").lower() == "true"
        if already_cleaned:
            logger.info("[PIPELINE-135] Cleaner übersprungen (X-Already-Cleaned=true)")
            cleaned = raw_transcript
        else:
            cleaned = clean_transcript(raw_transcript)
        # P166: Vollen Text nicht ins Terminal fluten — Einzeiler reicht.
        logger.info(
            f"🎤 Audio-Transkript erfolgreich (raw={len(raw_transcript)} Zeichen, "
            f"clean={len(cleaned)} Zeichen)"
        )
        logger.debug(f"🎤 Transkript: '{raw_transcript}' -> '{cleaned}'")

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
        # Patch 190: Prosodie-Result (wenn vorhanden + nicht Stub) als Bonus-Feld;
        # Frontend gibt es als X-Prosody-Context-Header an /chat/completions weiter.
        # Worker-Protection P191: KEIN Speichern in interactions-Tabelle!
        # Patch 193: zusaetzliches sentiment-Feld (BERT + Konsens), backward-compat.
        # Patch 193: Sentinel-Strings fuer SSE-Source-Audit (siehe sse_events()):
        #   "event: prosody"  und  "event: sentiment"
        # Diese werden im SSE-Generator generiert wenn der Bus die jeweiligen
        # Event-Typen broadcastet.
        result_payload: dict = {
            "transcript": cleaned,
            "response": "",
            "sentiment": "neutral"
        }
        _prosody_clean: dict | None = None
        if prosody_outcome and isinstance(prosody_outcome, dict) and prosody_outcome.get("source") != "stub":
            _prosody_clean = {
                "mood": prosody_outcome.get("mood"),
                "tempo": prosody_outcome.get("tempo"),
                "confidence": prosody_outcome.get("confidence"),
                "valence": prosody_outcome.get("valence"),
                "arousal": prosody_outcome.get("arousal"),
                "dominance": prosody_outcome.get("dominance"),
                "source": prosody_outcome.get("source"),
            }
            result_payload["prosody"] = _prosody_clean
            # Patch 193: SSE-Event "prosody" — Frontend kann via /nala/events
            # die Daten frueh aufgreifen (vor dem POST-Return).
            try:
                await bus.publish(Event(
                    type="prosody",
                    data=dict(_prosody_clean),
                    session_id=session_id,
                ))
            except Exception as _bus_err:
                logger.warning(f"[ENRICHMENT-193] prosody-SSE-Publish fehlgeschlagen: {_bus_err}")

        # Patch 193: Sentiment-Enrichment fuer Whisper-Pfad. Additiv, fail-open.
        try:
            from zerberus.modules.sentiment.router import analyze_sentiment
            from zerberus.utils.sentiment_display import compute_consensus
            _bert = analyze_sentiment(cleaned or "")
            _enrich: dict = {
                "bert": {
                    "label": _bert.get("label", "neutral"),
                    "score": float(_bert.get("score", 0.5)),
                }
            }
            if _prosody_clean is not None:
                _enrich["consensus"] = compute_consensus(
                    _bert.get("label", "neutral"),
                    float(_bert.get("score", 0.5)),
                    _prosody_clean,
                )
            result_payload["enrichment"] = _enrich
            logger.info(
                f"[ENRICHMENT-193] bert={_enrich['bert']['label']}/"
                f"{_enrich['bert']['score']:.2f} prosody={'yes' if _prosody_clean else 'no'}"
            )
            # Patch 193: SSE-Event "sentiment" — Triptychon-Frontend kann
            # das Sentiment vor der finalen Response-Lieferung bereits anzeigen.
            try:
                await bus.publish(Event(
                    type="sentiment",
                    data=dict(_enrich),
                    session_id=session_id,
                ))
            except Exception as _bus_err:
                logger.warning(f"[ENRICHMENT-193] sentiment-SSE-Publish fehlgeschlagen: {_bus_err}")
        except Exception as _enrich_err:
            logger.warning(f"[ENRICHMENT-193] Sentiment-Enrichment fehlgeschlagen (fail-open): {_enrich_err}")

        return result_payload

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


# ---------------------------------------------------------------------------
# Patch 143 (B-014): Text-to-Speech via edge-tts.
# Zwei Endpoints:
#   GET  /nala/tts/voices?lang=de   → Liste verfügbarer Stimmen
#   POST /nala/tts/speak            → MP3-Audio (audio/mpeg)
# ---------------------------------------------------------------------------

class TtsSpeakRequest(BaseModel):
    text: str
    voice: str = "de-DE-ConradNeural"
    rate: str = "+0%"


@router.get("/tts/voices")
async def tts_voices(lang: str = "de"):
    """Listet verfügbare edge-tts-Stimmen für eine Sprache (Default: deutsch)."""
    from zerberus.utils import tts
    if not tts.is_available():
        raise HTTPException(503, "edge-tts nicht installiert")
    try:
        voices = await tts.list_voices(lang)
    except Exception as e:
        logger.warning(f"[TTS-143] list_voices fehlgeschlagen: {e}")
        raise HTTPException(502, f"edge-tts nicht erreichbar: {e}")
    # Kompaktes Format — nur das, was die UI braucht.
    return [
        {
            "ShortName": v.get("ShortName", ""),
            "FriendlyName": v.get("FriendlyName", v.get("ShortName", "")),
            "Locale": v.get("Locale", ""),
            "Gender": v.get("Gender", ""),
        }
        for v in voices
    ]


@router.post("/tts/speak")
async def tts_speak(req: TtsSpeakRequest):
    """Konvertiert Text zu MP3-Audio und streamt es an den Client."""
    from fastapi.responses import Response
    from zerberus.utils import tts
    if not tts.is_available():
        raise HTTPException(503, "edge-tts nicht installiert")
    if not req.text or not req.text.strip():
        raise HTTPException(400, "text darf nicht leer sein")
    # Schutzgrenze: sehr lange Texte blockieren den Request minutenlang —
    # im Chat-Kontext kappen wir bei 5000 Zeichen.
    text = req.text[:5000]
    try:
        audio = await tts.text_to_speech(text, voice=req.voice, rate=req.rate)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.warning(f"[TTS-143] text_to_speech fehlgeschlagen: {e}")
        raise HTTPException(502, f"TTS-Fehler: {e}")
    return Response(content=audio, media_type="audio/mpeg")
