"""
Hel Router – Admin-Dashboard und Konfiguration.
"""
import logging
import json
import secrets
import os
import tempfile
import asyncio
import io
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Depends, status, UploadFile, File
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse
import httpx
import yaml

from zerberus.core.config import get_settings
from zerberus.core.database import get_all_sessions, get_session_messages, get_latest_metrics, get_metrics_summary, get_message_costs, get_last_cost
from zerberus.core.config import get_settings, reload_settings, Settings
from zerberus.core.cleaner import clean_transcript
from zerberus.core.dialect import detect_dialect_marker, apply_dialect
from zerberus.core.llm import LLMService
import hashlib

try:
    from docx import Document as DocxDocument
    _DOCX_OK = True
except ImportError:
    _DOCX_OK = False

logger = logging.getLogger(__name__)


def _sanitize_unicode(text: str) -> str:
    """Entfernt Surrogate-Pairs und andere ungültige Unicode-Zeichen."""
    if not isinstance(text, str):
        return str(text)
    return text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')


security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """Prüft Admin-Credentials über HTTP Basic Auth."""
    correct_username = os.getenv("ADMIN_USER", "admin")
    correct_password = os.getenv("ADMIN_PASSWORD", "admin")
    if correct_username == "admin" and correct_password == "admin":
        print("⚠️  WARNUNG: Admin-Zugang mit Standard-Credentials (admin/admin) aktiv! Bitte in .env ändern.")
    is_correct_username = secrets.compare_digest(credentials.username, correct_username)
    is_correct_password = secrets.compare_digest(credentials.password, correct_password)
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username





router = APIRouter(prefix="/hel", tags=["Hel"], dependencies=[Depends(verify_admin)])

# ========== DEBUG‑ENDKNOTEN ==========
@router.get("/debug/trace/{session_id}")
async def debug_trace(session_id: str, request: Request):
    """Zeigt detaillierte Trace‑Info für eine Session."""
    from zerberus.core.database import get_session_messages
    from zerberus.core.config import get_settings
    import json as jsonlib

    messages = await get_session_messages(session_id)
    if not messages:
        raise HTTPException(404, "Session nicht gefunden")

    # Zusätzlich: letzte LLM‑Prompt aus dem Cost‑Table? (nicht direkt)
    # Wir holen die letzte Assistant‑Nachricht und deren Metadaten
    last_assistant = None
    for m in reversed(messages):
        if m["role"] == "assistant":
            last_assistant = m
            break

    # Aktive Konfiguration (aus get_settings und config.json)
    settings = get_settings()
    config_json = {}
    config_json_path = Path("config.json")
    if config_json_path.exists():
        with open(config_json_path, "r") as f:
            config_json = jsonlib.load(f)

    return {
        "session_id": session_id,
        "message_count": len(messages),
        "last_assistant": last_assistant,
        "config": {
            "from_yaml": {
                "cloud_model": settings.legacy.models.cloud_model,
                "temperature": settings.legacy.settings.ai_temperature,
                "threshold": settings.legacy.settings.threshold_length,
            },
            "from_json": config_json.get("llm", {}),
        },
        "active_modules": ["emotional", "nudge", "preparer", "rag"],  # später dynamisch
    }

@router.get("/debug/state")
async def debug_state():
    """Zeigt System‑Zustand: FAISS‑Status, Sessions, Config‑Hash, Modul‑Health."""
    from zerberus.core.database import _async_session_maker
    from sqlalchemy import text
    import hashlib

    # FAISS‑Status prüfen
    faiss_ok = False
    try:
        import faiss
        # Prüfe, ob Index geladen (später)
        faiss_ok = True
    except ImportError:
        faiss_ok = False

    # Session‑Anzahl
    session_count = 0
    async with _async_session_maker() as session:
        result = await session.execute(text("SELECT COUNT(DISTINCT session_id) FROM interactions"))
        session_count = result.scalar() or 0

    # Config‑Hash
    config_hash = None
    config_path = Path("config.json")
    if config_path.exists():
        with open(config_path, "rb") as f:
            config_hash = hashlib.sha256(f.read()).hexdigest()[:8]

    return {
        "faiss_available": faiss_ok,
        "active_sessions": session_count,
        "config_hash": config_hash,
        "modules": {
            "emotional": {"enabled": True, "last_ok": None},
            "nudge": {"enabled": True, "last_ok": None},
            "preparer": {"enabled": True, "last_ok": None},
            "rag": {"enabled": True, "last_ok": None},
        }
    }


# Konfigurationsdateien im Projekt-Root
WHISPER_CLEANER_PATH = Path("whisper_cleaner.json")
FUZZY_DICT_PATH = Path("fuzzy_dictionary.json")
DIALECT_PATH = Path("dialect.json")
CONFIG_PATH = Path("config.json")
SYSTEM_PROMPT_PATH = Path("system_prompt.json")

# HTML-Template mit HTML-Entities für Emojis
ADMIN_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>&#9889; Hel – Zerberus Admin</title>
    <link rel="icon" href="/static/favicon.ico">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: #1a1a1a;
            color: #e0e0e0;
            margin: 0;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { color: #ff6b6b; border-bottom: 2px solid #ff6b6b; padding-bottom: 10px; }
        .tabs { display: flex; gap: 5px; margin: 20px 0; flex-wrap: wrap; }
        .tab-btn {
            background: #333;
            border: none;
            color: #ccc;
            padding: 12px 24px;
            border-radius: 30px;
            cursor: pointer;
            font-size: 16px;
            transition: 0.2s;
        }
        .tab-btn.active { background: #ff6b6b; color: #1a1a1a; font-weight: bold; }
        .tab-content { display: none; background: #2d2d2d; border-radius: 15px; padding: 25px; }
        .tab-content.active { display: block; }
        .card { background: #3d3d3d; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
        label { display: block; margin: 15px 0 5px; color: #ffa5a5; }
        select, textarea, input {
            width: 100%;
            padding: 12px;
            background: #3d3d3d;
            border: 1px solid #555;
            color: white;
            border-radius: 8px;
            font-size: 16px;
        }
        button {
            background: #ff6b6b;
            color: black;
            border: none;
            padding: 12px 30px;
            border-radius: 30px;
            font-weight: bold;
            cursor: pointer;
            margin-top: 10px;
            font-size: 16px;
        }
        button:hover { background: #ff5252; }
        .balance {
            background: #252525;
            padding: 15px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 1.2em;
            margin: 10px 0;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: #3d3d3d;
            border-radius: 8px;
            overflow: hidden;
        }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #555; }
        th { background: #4d4d4d; }
        .session-item {
            background: #3d3d3d;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 8px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
        }
        .session-item:hover { background: #4d4d4d; }
        #chart-container { height: 300px; margin-top: 20px; }
        .slider-row { display: flex; align-items: center; gap: 15px; }
        .slider-row input { flex: 1; }
        .value-display { min-width: 50px; text-align: center; background: #252525; padding: 8px; border-radius: 8px; }
        .nav-links { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; }
        .nav-link {
            display: inline-block;
            background: #1a3a5c;
            color: #ffd700;
            border: 1px solid #ffd700;
            padding: 12px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-size: 15px;
            font-weight: bold;
            transition: 0.2s;
        }
        .nav-link:hover { background: #ffd700; color: #1a1a1a; }
        .metric-toggles { display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 10px; }
        .metric-toggle label { display: flex; align-items: center; gap: 6px; cursor: pointer; color: #e0e0e0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>&#9889; Hel – Admin-Konsole</h1>
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('llm')">LLM &amp; Guthaben</button>
            <button class="tab-btn" onclick="switchTab('cleaner')">Whisper-Cleaner</button>
            <button class="tab-btn" onclick="switchTab('dialect')">Dialekte</button>
            <button class="tab-btn" onclick="switchTab('system')">System-Prompt</button>
            <button class="tab-btn" onclick="switchTab('metrics')">Metriken</button>
            <button class="tab-btn" onclick="switchTab('gedaechtnis')">Ged&#228;chtnis / RAG</button>
            <button class="tab-btn" onclick="switchTab('sysctl')">Systemsteuerung</button>
            <button class="tab-btn" onclick="switchTab('nav')">&#128279; Navigation</button>
        </div>

        <!-- Tab LLM -->
        <div id="tab-llm" class="tab-content active">
            <div class="card">
                <h2>OpenRouter Modell & Guthaben</h2>
                <label>Modell auswählen:</label>
                <div style="display:flex;gap:8px;margin-bottom:6px;">
                    <button onclick="sortModels('name')" id="sortByName" style="padding:6px 14px;font-size:13px;background:#1a3a5c;color:#ffd700;border:1px solid #ffd700;border-radius:6px;cursor:pointer;">Nach Name (A&#8211;Z)</button>
                    <button onclick="sortModels('price')" id="sortByPrice" style="padding:6px 14px;font-size:13px;background:#252525;color:#aaa;border:1px solid #555;border-radius:6px;cursor:pointer;">Nach Preis (&#8593;)</button>
                </div>
                <select id="modelSelect" onchange="changeModel()"></select>
                <div class="balance" id="balanceDisplay">Guthaben wird geladen...</div>
            </div>
            <div class="card">
                <h2>LLM-Parameter</h2>
                <label>Temperatur (0–2):</label>
                <div class="slider-row">
                    <button onclick="stepSlider('temperature','tempValue',-0.1,0,2)" style="padding:4px 10px;font-size:16px;background:#333;color:#fff;border:1px solid #555;border-radius:6px;cursor:pointer;min-width:34px;">&#9660;</button>
                    <input type="range" id="temperature" min="0" max="2" step="0.1" value="0.7" oninput="document.getElementById('tempValue').innerText=parseFloat(this.value).toFixed(1)">
                    <button onclick="stepSlider('temperature','tempValue',0.1,0,2)" style="padding:4px 10px;font-size:16px;background:#333;color:#fff;border:1px solid #555;border-radius:6px;cursor:pointer;min-width:34px;">&#9650;</button>
                    <span class="value-display" id="tempValue">0.7</span>
                </div>
                <label>Threshold (W&#246;rter f&#252;r Cloud):</label>
                <div class="slider-row">
                    <button onclick="stepSlider('threshold','threshValue',-0.1,0,100)" style="padding:4px 10px;font-size:16px;background:#333;color:#fff;border:1px solid #555;border-radius:6px;cursor:pointer;min-width:34px;">&#9660;</button>
                    <input type="range" id="threshold" min="1" max="100" step="1" value="10" oninput="document.getElementById('threshValue').innerText=parseFloat(this.value).toFixed(1)">
                    <button onclick="stepSlider('threshold','threshValue',0.1,0,100)" style="padding:4px 10px;font-size:16px;background:#333;color:#fff;border:1px solid #555;border-radius:6px;cursor:pointer;min-width:34px;">&#9650;</button>
                    <span class="value-display" id="threshValue">10</span>
                </div>
                <button onclick="saveLLMConfig()">Einstellungen speichern</button>
                <div id="llmStatus"></div>
            </div>
        </div>

        <!-- Tab Cleaner -->
        <div id="tab-cleaner" class="tab-content">
            <div class="card">
                <h2>Whisper-Cleaner JSON bearbeiten</h2>
                <p style="color:#aaa; font-size:0.9em; margin-bottom:10px;">F&#252;llw&#246;rter, Korrekturen, Wiederholungs-Limit (<code>whisper_cleaner.json</code>)</p>
                <textarea id="cleanerEditor" rows="18" style="font-family: monospace;"></textarea>
                <button onclick="saveCleaner()">Speichern</button>
                <div id="cleanerStatus"></div>
            </div>
            <div class="card">
                <h2>Fuzzy-Dictionary bearbeiten</h2>
                <p style="color:#aaa; font-size:0.9em; margin-bottom:10px;">Projektspezifische Begriffe f&#252;r Whisper-Fehlerkorrektur via Fuzzy-Matching (<code>fuzzy_dictionary.json</code>). JSON-Array von Strings.</p>
                <textarea id="fuzzyDictEditor" rows="12" style="font-family: monospace;"></textarea>
                <button onclick="saveFuzzyDict()">Speichern</button>
                <div id="fuzzyDictStatus"></div>
            </div>
        </div>

        <!-- Tab Dialekt -->
        <div id="tab-dialect" class="tab-content">
            <div class="card">
                <h2>Dialekt-JSON bearbeiten</h2>
                <textarea id="dialectEditor" rows="20" style="font-family: monospace;"></textarea>
                <button onclick="saveDialect()">Speichern</button>
                <div id="dialectStatus"></div>
            </div>
        </div>

        <!-- Tab System-Prompt -->
        <div id="tab-system" class="tab-content">
            <div class="card">
                <h2>System-Prompt (für alle Chats)</h2>
                <textarea id="systemPromptEditor" rows="8" style="width:100%; font-family: monospace;"></textarea>
                <button onclick="saveSystemPrompt()">Speichern</button>
                <div id="systemPromptStatus"></div>
            </div>
        </div>

        <!-- Tab Metriken -->
        <div id="tab-metrics" class="tab-content">
            <div class="card">
                <h2>Letzte Nachrichten</h2>
                <table id="messagesTable">
                    <thead><tr><th>Zeit</th><th>Rolle</th><th>Inhalt</th><th>W&#246;rter</th><th>Sentiment</th><th>Kosten (USD)</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
            <div class="card">
                <h2>Metrik-Verlauf</h2>
                <div class="metric-toggles">
                    <div class="metric-toggle">
                        <label><input type="checkbox" id="tog-bert" checked onchange="updateChart()"> <span style="color:#ff6b6b;">&#9632;</span> BERT Sentiment</label>
                    </div>
                    <div class="metric-toggle">
                        <label><input type="checkbox" id="tog-wc" onchange="updateChart()"> <span style="color:#4ecdc4;">&#9632;</span> Word Count</label>
                    </div>
                    <div class="metric-toggle">
                        <label><input type="checkbox" id="tog-ttr" onchange="updateChart()"> <span style="color:#ffd700;">&#9632;</span> TTR</label>
                    </div>
                    <div class="metric-toggle">
                        <label><input type="checkbox" id="tog-entropy" onchange="updateChart()"> <span style="color:#a29bfe;">&#9632;</span> Shannon Entropy</label>
                    </div>
                </div>
                <div id="chart-container"><canvas id="sentimentChart"></canvas></div>
            </div>
            <div class="card">
                <h2>Gespeicherte Sessions</h2>
                <div id="sessionList"></div>
            </div>
        </div>

        <!-- Tab Gedächtnis / RAG -->
        <div id="tab-gedaechtnis" class="tab-content">
            <div class="card">
                <h2>&#128196; Dokument hochladen</h2>
                <p style="color:#aaa; font-size:0.9em; margin-bottom:12px;">Unterst&#252;tzte Formate: <strong>.txt</strong> und <strong>.docx</strong>. Das Dokument wird automatisch in Chunks von ~300 W&#246;rtern zerlegt und in Nalas Gedächtnis (FAISS) indiziert.</p>
                <label>Datei ausw&#228;hlen:</label>
                <input type="file" id="ragFileInput" accept=".txt,.docx" style="margin-bottom:10px;">
                <br>
                <button onclick="uploadRagFile()">&#128196; Hochladen &amp; Indizieren</button>
                <div id="ragUploadStatus" style="margin-top:12px; font-size:1.1em;"></div>
            </div>
            <div class="card">
                <h2>&#129504; Index-&#220;bersicht</h2>
                <div id="ragIndexInfo" style="background:#252525; padding:15px; border-radius:8px; font-family:monospace; margin-bottom:15px;">Wird geladen...</div>
                <h3 style="color:#ffa5a5; margin-bottom:8px;">Indizierte Quellen</h3>
                <div id="ragSourcesList" style="background:#252525; padding:15px; border-radius:8px; min-height:40px;"></div>
                <br>
                <button onclick="clearRagIndex()" style="background:#888;">&#128465; Index leeren</button>
                <div id="ragClearStatus" style="margin-top:8px;"></div>
            </div>
        </div>

        <!-- Tab Navigation -->
        <div id="tab-nav" class="tab-content">
            <div class="card">
                <h2>&#128279; Schnell-Navigation</h2>
                <p style="color:#aaa; font-size:0.9em; margin-bottom:14px;">Alle Links &#246;ffnen in einem neuen Tab.</p>
                <div class="nav-links">
                    <a href="/nala" target="_blank" class="nav-link">&#128172; Nala-Chat</a>
                    <a href="/hel" target="_blank" class="nav-link">&#9889; Hel-Dashboard</a>
                    <a href="/health" target="_blank" class="nav-link">&#10084;&#65039; API Health</a>
                    <a href="/archive/sessions" target="_blank" class="nav-link">&#128196; Session-Archiv</a>
                    <a href="/hel/metrics/latest_with_costs" target="_blank" class="nav-link">&#128202; Metrics Latest</a>
                    <a href="/hel/debug/state" target="_blank" class="nav-link">&#128736;&#65039; Debug State</a>
                    <a href="/docs" target="_blank" class="nav-link">&#128216; API Docs (Swagger)</a>
                </div>
            </div>
        </div>

        <!-- Tab Systemsteuerung -->
        <div id="tab-sysctl" class="tab-content">
            <div class="card">
                <h2>&#128147; Pacemaker-Konfiguration</h2>
                <p style="color:#aaa; font-size:0.9em; margin-bottom:12px;">Der Pacemaker h&#228;lt den Whisper-Dienst aktiv, indem er regelm&#228;&#223;ig stille WAV-Pings sendet. Er startet automatisch mit der ersten Interaktion und stoppt nach der eingestellten Laufzeit ohne weitere Aktivit&#228;t.</p>
                <label>Pacemaker-Laufzeit (Minuten):</label>
                <input type="number" id="pacemakerMinutes" min="1" max="480" value="120" style="width:150px;">
                <p style="color:#888; font-size:0.85em; margin-top:6px;">&#8505;&#65039; &#196;nderungen wirken erst nach Neustart des Servers.</p>
                <button onclick="savePacemakerConfig()">Speichern</button>
                <div id="pacemakerStatus" style="margin-top:8px;"></div>
            </div>
        </div>
    </div>

    <script>
        let currentModel = '';
        let chart;
        let _allModels = [];
        let _currentSort = 'name';

        function sortModels(mode) {
            _currentSort = mode;
            document.getElementById('sortByName').style.background = mode === 'name' ? '#1a3a5c' : '#252525';
            document.getElementById('sortByName').style.color = mode === 'name' ? '#ffd700' : '#aaa';
            document.getElementById('sortByName').style.borderColor = mode === 'name' ? '#ffd700' : '#555';
            document.getElementById('sortByPrice').style.background = mode === 'price' ? '#1a3a5c' : '#252525';
            document.getElementById('sortByPrice').style.color = mode === 'price' ? '#ffd700' : '#aaa';
            document.getElementById('sortByPrice').style.borderColor = mode === 'price' ? '#ffd700' : '#555';
            renderModelSelect(_allModels, currentModel);
        }

        function stepSlider(sliderId, displayId, delta, minVal, maxVal) {
            const slider = document.getElementById(sliderId);
            const display = document.getElementById(displayId);
            let val = Math.round((parseFloat(slider.value) + delta) * 10) / 10;
            if (val < minVal) val = minVal;
            if (val > maxVal) val = maxVal;
            slider.value = val;
            display.innerText = val.toFixed(1);
        }

        function renderModelSelect(models, selectedModel) {
            const sorted = [...models].sort((a, b) => {
                if (_currentSort === 'price') {
                    return parseFloat(a.pricing?.prompt || 0) - parseFloat(b.pricing?.prompt || 0);
                }
                return (a.name || a.id).localeCompare(b.name || b.id);
            });
            const select = document.getElementById('modelSelect');
            select.innerHTML = '';
            sorted.forEach(m => {
                const option = document.createElement('option');
                option.value = m.id;
                const promptPrice = parseFloat(m.pricing?.prompt || 0);
                const priceStr = promptPrice === 0 ? 'kostenlos' : `$${promptPrice.toFixed(6)}/1k`;
                option.textContent = `${m.name} (${priceStr})`;
                select.appendChild(option);
            });
            if (selectedModel && select.querySelector(`option[value="${selectedModel}"]`)) {
                select.value = selectedModel;
            }
        }
        let _chartHistory = [];

        function switchTab(name) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab-btn[onclick*="${name}"]`).classList.add('active');
            document.getElementById(`tab-${name}`).classList.add('active');
            if (name === 'metrics') loadMetrics();
            if (name === 'system') loadSystemPrompt();
            if (name === 'gedaechtnis') loadRagStatus();
            if (name === 'sysctl') loadPacemakerConfig();
        }

        async function loadModelsAndBalance() {
            const balanceEl = document.getElementById('balanceDisplay');
            try {
                const [modelsRes, balanceRes] = await Promise.all([
                    fetch('/hel/admin/models'),
                    fetch('/hel/admin/balance')
                ]);

                if (!modelsRes.ok) {
                    const err = await modelsRes.json().catch(() => ({}));
                    balanceEl.innerText = 'OpenRouter nicht erreichbar: ' + (err.detail || modelsRes.status);
                    return;
                }
                if (!balanceRes.ok) {
                    const err = await balanceRes.json().catch(() => ({}));
                    balanceEl.innerText = 'OpenRouter nicht erreichbar: ' + (err.detail || balanceRes.status);
                    return;
                }

                const models = await modelsRes.json();
                const balance = await balanceRes.json();

                _allModels = Array.isArray(models) ? models : [];

                const configRes = await fetch('/hel/admin/config');
                const config = await configRes.json();
                currentModel = config.llm?.cloud_model || '';
                renderModelSelect(_allModels, currentModel);

                // Guthaben und letzte Anfrage getrennt anzeigen
                const balanceStr = balance.balance != null
                    ? `Guthaben: $${parseFloat(balance.balance).toFixed(2)}`
                    : 'Guthaben: nicht verfügbar';
                const lastCostStr = `Letzte Anfrage: $${parseFloat(balance.last_cost || 0).toFixed(6)}`;
                balanceEl.innerHTML = `${balanceStr}<br><span style="font-size:0.85em;color:#aaa;">${lastCostStr}</span>`;

                // Temperatur und Threshold aus config laden
                const tempSlider = document.getElementById('temperature');
                const threshSlider = document.getElementById('threshold');
                tempSlider.value = config.llm?.temperature ?? 0.7;
                threshSlider.value = config.llm?.threshold ?? 10;
                document.getElementById('tempValue').innerText = parseFloat(tempSlider.value).toFixed(1);
                document.getElementById('threshValue').innerText = parseFloat(threshSlider.value).toFixed(1);
            } catch (e) {
                balanceEl.innerText = 'Fehler beim Laden: ' + e.message;
            }
        }

        async function changeModel() {
            const model = document.getElementById('modelSelect').value;
            await fetch('/hel/admin/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ llm: { cloud_model: model } })
            });
            alert('Modell gespeichert');
        }

        async function saveLLMConfig() {
            const temp = parseFloat(document.getElementById('temperature').value);
            const thresh = parseInt(document.getElementById('threshold').value);
            const config = { llm: { temperature: temp, threshold: thresh } };
            const res = await fetch('/hel/admin/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            });
            if (res.ok) document.getElementById('llmStatus').innerText = '✅ Einstellungen gespeichert';
            else document.getElementById('llmStatus').innerText = '❌ Fehler';
        }

        async function loadCleaner() {
            const res = await fetch('/hel/admin/whisper_cleaner');
            const data = await res.json();
            document.getElementById('cleanerEditor').value = JSON.stringify(data, null, 2);
        }
        async function saveCleaner() {
            const raw = document.getElementById('cleanerEditor').value;
            try { JSON.parse(raw); } catch (e) { alert('Ungültiges JSON'); return; }
            const res = await fetch('/hel/admin/whisper_cleaner', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: raw
            });
            if (res.ok) document.getElementById('cleanerStatus').innerText = '✅ Gespeichert';
            else document.getElementById('cleanerStatus').innerText = '❌ Fehler';
        }

        async function loadFuzzyDict() {
            const res = await fetch('/hel/admin/fuzzy_dict');
            const data = await res.json();
            document.getElementById('fuzzyDictEditor').value = JSON.stringify(data, null, 2);
        }
        async function saveFuzzyDict() {
            const raw = document.getElementById('fuzzyDictEditor').value;
            let parsed;
            try { parsed = JSON.parse(raw); } catch (e) { alert('Ungültiges JSON'); return; }
            if (!Array.isArray(parsed)) { alert('Muss ein JSON-Array sein, z.B. ["Zerberus", "FastAPI"]'); return; }
            const res = await fetch('/hel/admin/fuzzy_dict', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(parsed)
            });
            if (res.ok) document.getElementById('fuzzyDictStatus').innerText = '&#10003; Gespeichert';
            else document.getElementById('fuzzyDictStatus').innerText = '&#10007; Fehler';
        }

        async function loadDialect() {
            const res = await fetch('/hel/admin/dialect');
            const data = await res.json();
            document.getElementById('dialectEditor').value = JSON.stringify(data, null, 2);
        }
        async function saveDialect() {
            const raw = document.getElementById('dialectEditor').value;
            try { JSON.parse(raw); } catch (e) { alert('Ungültiges JSON'); return; }
            const res = await fetch('/hel/admin/dialect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: raw
            });
            if (res.ok) document.getElementById('dialectStatus').innerText = '✅ Gespeichert';
            else document.getElementById('dialectStatus').innerText = '❌ Fehler';
        }

        async function loadSystemPrompt() {
            const res = await fetch('/hel/admin/system_prompt');
            const data = await res.json();
            document.getElementById('systemPromptEditor').value = data.prompt || '';
        }
        async function saveSystemPrompt() {
            const prompt = document.getElementById('systemPromptEditor').value;
            const res = await fetch('/hel/admin/system_prompt', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ prompt: prompt })
            });
            const result = await res.json();
            document.getElementById('systemPromptStatus').innerText = result.status === 'ok' ? '✅ Gespeichert' : '❌ Fehler';
        }

        async function deleteSession(sessionId) {
            if (!confirm('Chat wirklich löschen?')) return;
            const res = await fetch(`/hel/admin/session/${sessionId}`, { method: 'DELETE' });
            if (res.ok) {
                loadMetrics();
                document.getElementById('systemPromptStatus').innerText = '✅ Session gelöscht';
            } else {
                document.getElementById('systemPromptStatus').innerText = '❌ Fehler beim Löschen';
            }
        }

        async function loadMetrics() {
            const [res, histRes] = await Promise.all([
                fetch('/hel/metrics/latest_with_costs?limit=20'),
                fetch('/hel/metrics/history?limit=50')
            ]);
            const data = await res.json();
            _chartHistory = histRes.ok ? await histRes.json() : [];

            const tbody = document.querySelector('#messagesTable tbody');
            tbody.innerHTML = '';
            data.forEach((msg) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td>${new Date(msg.timestamp).toLocaleString()}</td>
                                  <td>${msg.role}</td>
                                  <td>${(msg.content || '').substring(0,50)}...</td>
                                  <td>${msg.word_count ?? ''}</td>
                                  <td>${msg.vader_compound ?? ''}</td>
                                  <td>${msg.cost !== null && msg.cost !== undefined ? msg.cost.toFixed(6) : ''}</td>`;
                tbody.appendChild(tr);
            });

            updateChart();

            const sessionsRes = await fetch('/hel/admin/sessions');
            const sessions = await sessionsRes.json();
            const sessionDiv = document.getElementById('sessionList');
            sessionDiv.innerHTML = sessions.map(s => `
                <div class="session-item" style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="cursor: pointer;" onclick="exportSession('${s.session_id}')">${s.first_message || 'Neuer Chat'}</span>
                    <span style="display: flex; gap: 10px;">
                        <button onclick="exportSession('${s.session_id}')" style="background: #666; padding: 5px 10px;">&#128196;</button>
                        <button onclick="deleteSession('${s.session_id}')" style="background: #ff6b6b; padding: 5px 10px;">&#128465;</button>
                    </span>
                    <span class="session-date">${new Date(s.created_at).toLocaleString()}</span>
                </div>
            `).join('');
        }

        function updateChart() {
            const rows = [..._chartHistory].reverse();
            const labels = rows.map((_, i) => i + 1);
            const datasets = [];

            if (document.getElementById('tog-bert')?.checked) {
                datasets.push({
                    label: 'BERT Sentiment',
                    data: rows.map(r => r.bert_sentiment_score ?? null),
                    borderColor: '#ff6b6b',
                    backgroundColor: 'rgba(255,107,107,0.08)',
                    tension: 0.3,
                    spanGaps: true,
                });
            }
            if (document.getElementById('tog-wc')?.checked) {
                datasets.push({
                    label: 'Word Count',
                    data: rows.map(r => r.word_count ?? null),
                    borderColor: '#4ecdc4',
                    backgroundColor: 'rgba(78,205,196,0.08)',
                    tension: 0.3,
                    spanGaps: true,
                    yAxisID: 'y2',
                });
            }
            if (document.getElementById('tog-ttr')?.checked) {
                datasets.push({
                    label: 'TTR',
                    data: rows.map(r => r.ttr ?? null),
                    borderColor: '#ffd700',
                    backgroundColor: 'rgba(255,215,0,0.08)',
                    tension: 0.3,
                    spanGaps: true,
                });
            }
            if (document.getElementById('tog-entropy')?.checked) {
                datasets.push({
                    label: 'Shannon Entropy',
                    data: rows.map(r => r.shannon_entropy ?? null),
                    borderColor: '#a29bfe',
                    backgroundColor: 'rgba(162,155,254,0.08)',
                    tension: 0.3,
                    spanGaps: true,
                });
            }

            if (window.chart) window.chart.destroy();
            const ctx = document.getElementById('sentimentChart').getContext('2d');
            window.chart = new Chart(ctx, {
                type: 'line',
                data: { labels, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    scales: {
                        y: { position: 'left', title: { display: true, text: 'Score / TTR / Entropy' } },
                        y2: { position: 'right', title: { display: true, text: 'Word Count' }, grid: { drawOnChartArea: false } }
                    }
                }
            });
        }

        async function exportSession(sessionId) {
            window.location.href = `/hel/admin/export/session/${sessionId}`;
        }

        // ========== RAG / Gedächtnis ==========
        async function loadRagStatus() {
            const res = await fetch('/hel/admin/rag/status');
            if (!res.ok) { document.getElementById('ragIndexInfo').innerText = 'Fehler beim Laden'; return; }
            const data = await res.json();
            document.getElementById('ragIndexInfo').innerText =
                `Index-Gr\u00f6\u00dfe: ${data.total_chunks} Chunk(s)`;
            const sourcesDiv = document.getElementById('ragSourcesList');
            if (data.sources && data.sources.length > 0) {
                const counts = {};
                data.sources.forEach(s => { counts[s] = (counts[s] || 0) + 1; });
                sourcesDiv.innerHTML = Object.entries(counts)
                    .map(([src, cnt]) => `<div style="padding:4px 0; border-bottom:1px solid #444;">\uD83D\uDCC4 <strong>${src}</strong> &mdash; ${cnt} Chunk(s)</div>`)
                    .join('');
            } else {
                sourcesDiv.innerHTML = '<span style="color:#888;">Noch keine Dokumente indiziert.</span>';
            }
        }

        async function uploadRagFile() {
            const input = document.getElementById('ragFileInput');
            const status = document.getElementById('ragUploadStatus');
            if (!input.files || input.files.length === 0) {
                status.innerText = '\u274C Bitte zuerst eine Datei ausw\u00e4hlen.';
                return;
            }
            const file = input.files[0];
            const formData = new FormData();
            formData.append('file', file);
            status.innerText = '\u23F3 Wird hochgeladen und indiziert...';
            try {
                const res = await fetch('/hel/admin/rag/upload', { method: 'POST', body: formData });
                const data = await res.json();
                if (res.ok) {
                    status.innerHTML = `\u2705 <strong>${data.chunks_indexed} Chunks indiziert</strong> aus <em>${data.filename}</em>`;
                    loadRagStatus();
                } else {
                    status.innerText = '\u274C Fehler: ' + (data.detail || JSON.stringify(data));
                }
            } catch(e) {
                status.innerText = '\u274C Netzwerkfehler: ' + e.message;
            }
        }

        async function clearRagIndex() {
            if (!confirm('Wirklich den gesamten RAG-Index l\u00f6schen? Das kann nicht r\u00fcckg\u00e4ngig gemacht werden.')) return;
            const res = await fetch('/hel/admin/rag/clear', { method: 'DELETE' });
            const data = await res.json();
            document.getElementById('ragClearStatus').innerText =
                res.ok ? '\u2705 Index geleert.' : '\u274C Fehler: ' + (data.detail || '');
            if (res.ok) loadRagStatus();
        }

        // ========== Systemsteuerung / Pacemaker ==========
        async function loadPacemakerConfig() {
            try {
                const res = await fetch('/hel/admin/pacemaker/config');
                const data = await res.json();
                document.getElementById('pacemakerMinutes').value = data.keep_alive_minutes ?? 120;
            } catch(e) {}
        }

        async function savePacemakerConfig() {
            const minutes = parseInt(document.getElementById('pacemakerMinutes').value);
            const res = await fetch('/hel/admin/pacemaker/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ keep_alive_minutes: minutes })
            });
            const data = await res.json();
            document.getElementById('pacemakerStatus').innerText =
                res.ok ? '\u2705 Gespeichert. Wirkt nach Neustart.' : '\u274C Fehler: ' + (data.detail || '');
        }

        loadModelsAndBalance();
        loadCleaner();
        loadFuzzyDict();
        loadDialect();
    </script>
</body>
</html>
"""

def load_json_or_empty(path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {} if path == DIALECT_PATH else []

@router.get("/", response_class=HTMLResponse)
async def hel_interface():
    return _sanitize_unicode(ADMIN_HTML)

# Admin-Endpunkte
@router.get("/admin/models")
async def get_models():
    """Gibt verfügbare OpenRouter-Modelle als Array zurück."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter nicht erreichbar: {e.response.status_code}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter nicht erreichbar: {e}")


@router.get("/admin/balance")
async def get_balance():
    """Gibt aktuelles OpenRouter-Guthaben und Kosten der letzten Anfrage zurück."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
        last_cost = await get_last_cost()
        limit_usd = data.get("limit_usd")
        usage = data.get("usage") or 0.0
        balance = float(limit_usd) - float(usage) if limit_usd is not None else None
        return {"balance": balance, "last_cost": last_cost}
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter nicht erreichbar: {e.response.status_code}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter nicht erreichbar: {e}")

@router.get("/admin/whisper_cleaner")
async def get_whisper_cleaner():
    return JSONResponse(content=load_json_or_empty(WHISPER_CLEANER_PATH))

@router.post("/admin/whisper_cleaner")
async def post_whisper_cleaner(request: Request):
    data = await request.json()
    temp_fd, temp_path = tempfile.mkstemp(dir=WHISPER_CLEANER_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, WHISPER_CLEANER_PATH)
    except Exception as e:
        os.unlink(temp_path)
        raise e
    return {"status": "ok"}

@router.get("/admin/fuzzy_dict")
async def get_fuzzy_dict():
    return JSONResponse(content=load_json_or_empty(FUZZY_DICT_PATH))

@router.post("/admin/fuzzy_dict")
async def post_fuzzy_dict(request: Request):
    data = await request.json()
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="fuzzy_dictionary.json muss ein JSON-Array sein")
    temp_fd, temp_path = tempfile.mkstemp(dir=FUZZY_DICT_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, FUZZY_DICT_PATH)
    except Exception as e:
        os.unlink(temp_path)
        raise e
    return {"status": "ok"}

@router.get("/admin/dialect")
async def get_dialect():
    return JSONResponse(content=load_json_or_empty(DIALECT_PATH))

@router.post("/admin/dialect")
async def post_dialect(request: Request):
    data = await request.json()
    temp_fd, temp_path = tempfile.mkstemp(dir=DIALECT_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, DIALECT_PATH)
    except Exception as e:
        os.unlink(temp_path)
        raise e
    return {"status": "ok"}

@router.get("/admin/config")
async def get_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return JSONResponse(content=json.load(f))
    return JSONResponse(content={})

@router.post("/admin/config")
async def post_config(request: Request):
    data = await request.json()
    current = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            current = json.load(f)
    if "llm" in data:
        current["llm"] = {**current.get("llm", {}), **data["llm"]}
    temp_fd, temp_path = tempfile.mkstemp(dir=CONFIG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.replace(temp_path, CONFIG_PATH)
    except Exception as e:
        os.unlink(temp_path)
        raise e
    reload_settings()
    return {"status": "ok", "reloaded": True}

@router.get("/admin/sessions")
async def get_sessions():
    sessions = await get_all_sessions(limit=50)
    return sessions

@router.get("/admin/export/session/{session_id}")
async def export_session(session_id: str):
    messages = await get_session_messages(session_id)
    if not messages:
        raise HTTPException(404, "Session nicht gefunden")
    return messages

@router.delete("/admin/session/{session_id}")
async def delete_session_admin(session_id: str):
    from zerberus.app.routers.archive import delete_session_endpoint as archive_delete
    return await archive_delete(session_id)

@router.get("/admin/system_prompt")
async def get_system_prompt():
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

@router.post("/admin/system_prompt")
async def post_system_prompt(request: Request):
    data = await request.json()
    temp_fd, temp_path = tempfile.mkstemp(dir=SYSTEM_PROMPT_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, SYSTEM_PROMPT_PATH)
    except Exception as e:
        os.unlink(temp_path)
        raise e
    return {"status": "ok"}

@router.get("/metrics/latest_with_costs")
async def metrics_latest_with_costs(limit: int = 20, session_id: str = None):
    messages = await get_latest_metrics(limit, session_id)
    ids = [m['id'] for m in messages if m.get('role') == 'assistant']
    costs = await get_message_costs(ids)
    for m in messages:
        m['cost'] = costs.get(m['id'], None)
    return messages

@router.get("/metrics/summary")
async def metrics_summary(session_id: str = None):
    return await get_metrics_summary(session_id)


# ============================================================
# RAG-Upload (Säule 1 – Patch 56)
# ============================================================

CONFIG_YAML_PATH = Path("config.yaml")


def _chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> list[str]:
    """Zerlegt Text in Chunks von ~chunk_size Wörtern mit overlap Wörtern Überlapp."""
    words = text.split()
    if not words:
        return []
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i:i + chunk_size]
        chunks.append(" ".join(chunk_words))
        i += chunk_size - overlap
    return chunks


@router.post("/admin/rag/upload")
async def rag_upload(file: UploadFile = File(...)):
    """Lädt .txt oder .docx hoch, zerlegt in Chunks und indiziert sie im FAISS-Index."""
    from zerberus.modules.rag.router import (
        _ensure_init, _encode, _add_to_index, RAG_AVAILABLE
    )
    settings = get_settings()
    mod_cfg = settings.modules.get("rag", {})
    if not mod_cfg.get("enabled", False):
        raise HTTPException(503, "RAG-Modul ist in config.yaml deaktiviert.")
    if not RAG_AVAILABLE:
        raise HTTPException(503, "RAG-Abhängigkeiten (faiss, sentence-transformers) nicht installiert.")

    filename = file.filename or "unbekannt"
    suffix = Path(filename).suffix.lower()

    if suffix == ".txt":
        raw_bytes = await file.read()
        text = raw_bytes.decode("utf-8", errors="replace")
    elif suffix == ".docx":
        if not _DOCX_OK:
            raise HTTPException(422, "python-docx nicht installiert. Bitte 'pip install python-docx' ausführen.")
        raw_bytes = await file.read()
        doc = DocxDocument(io.BytesIO(raw_bytes))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    else:
        raise HTTPException(422, f"Nicht unterstütztes Format '{suffix}'. Nur .txt und .docx erlaubt.")

    text = text.strip()
    if not text:
        raise HTTPException(400, "Datei ist leer oder enthält keinen lesbaren Text.")

    chunks = _chunk_text(text, chunk_size=300, overlap=50)
    if not chunks:
        raise HTTPException(400, "Kein Text nach dem Chunking übrig.")

    await _ensure_init(settings)

    indexed = 0
    for chunk in chunks:
        vec = await asyncio.to_thread(_encode, chunk)
        await asyncio.to_thread(_add_to_index, vec, chunk, {"source": filename}, settings)
        indexed += 1

    logger.info(f"📚 RAG-Upload: {indexed} Chunks aus '{filename}' indiziert")
    return {"status": "ok", "filename": filename, "chunks_indexed": indexed}


@router.get("/admin/rag/status")
async def rag_status():
    """Gibt aktuelle Index-Größe und Liste der indizierten Quellen zurück."""
    from zerberus.modules.rag.router import _index, _metadata
    total = _index.ntotal if _index is not None else 0
    sources = [m.get("source", "unbekannt") for m in _metadata]
    return {"total_chunks": total, "sources": sources}


@router.delete("/admin/rag/clear")
async def rag_clear():
    """Leert den FAISS-Index und metadata.json komplett."""
    from zerberus.modules.rag.router import _reset_sync, RAG_AVAILABLE
    settings = get_settings()
    mod_cfg = settings.modules.get("rag", {})
    if not mod_cfg.get("enabled", False):
        raise HTTPException(503, "RAG-Modul ist in config.yaml deaktiviert.")
    if not RAG_AVAILABLE:
        raise HTTPException(503, "RAG-Abhängigkeiten nicht installiert.")
    await asyncio.to_thread(_reset_sync, settings)
    return {"status": "ok", "message": "RAG-Index geleert."}


# ============================================================
# Pacemaker-Konfiguration (Säule 2 – Patch 56)
# ============================================================

@router.get("/admin/pacemaker/config")
async def get_pacemaker_config():
    """Gibt aktuelle Pacemaker-Konfiguration zurück."""
    settings = get_settings()
    pm = settings.legacy.pacemaker
    return {
        "interval_seconds": pm.interval_seconds,
        "keep_alive_minutes": pm.keep_alive_minutes,
    }


@router.post("/admin/pacemaker/config")
async def post_pacemaker_config(request: Request):
    """Speichert keep_alive_minutes in config.yaml (wirkt nach Neustart)."""
    data = await request.json()
    minutes = int(data.get("keep_alive_minutes", 120))
    if minutes < 1 or minutes > 480:
        raise HTTPException(400, "keep_alive_minutes muss zwischen 1 und 480 liegen.")

    if not CONFIG_YAML_PATH.exists():
        raise HTTPException(500, "config.yaml nicht gefunden.")

    with open(CONFIG_YAML_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    cfg.setdefault("legacy", {}).setdefault("pacemaker", {})
    cfg["legacy"]["pacemaker"]["keep_alive_minutes"] = minutes

    temp_fd, temp_path = tempfile.mkstemp(dir=CONFIG_YAML_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        os.replace(temp_path, CONFIG_YAML_PATH)
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise HTTPException(500, f"Fehler beim Schreiben: {e}")

    return {"status": "ok", "keep_alive_minutes": minutes}


# ============================================================
# Metrics History mit BERT-Sentiment (Patch 57)
# ============================================================

@router.get("/metrics/history")
async def metrics_history(limit: int = 50):
    """
    Gibt die letzten N ausgewerteten Messages zurück inkl. aller Chart-Metriken:
    word_count, ttr, shannon_entropy, vader_compound, bert_sentiment_score.
    Spalten bert_sentiment_* werden graceful behandelt – falls noch nicht angelegt,
    wird null zurückgegeben.
    """
    from zerberus.core.database import _async_session_maker
    from sqlalchemy import text

    async with _async_session_maker() as session:
        # Spaltenliste dynamisch prüfen
        col_result = await session.execute(text("PRAGMA table_info(message_metrics)"))
        cols = {row[1] for row in col_result.fetchall()}
        has_bert = "bert_sentiment_label" in cols and "bert_sentiment_score" in cols

        bert_cols = (
            ", mm.bert_sentiment_label, mm.bert_sentiment_score"
            if has_bert
            else ", NULL as bert_sentiment_label, NULL as bert_sentiment_score"
        )

        try:
            result = await session.execute(text(f"""
                SELECT i.id, i.timestamp, i.role, i.session_id,
                       mm.word_count, mm.ttr, mm.shannon_entropy, mm.vader_compound
                       {bert_cols}
                FROM interactions i
                JOIN message_metrics mm ON i.id = mm.message_id
                ORDER BY i.timestamp DESC
                LIMIT :limit
            """), {"limit": limit})
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]
        except Exception as e:
            logger.warning(f"[metrics/history] Fehler: {e}")
            return []