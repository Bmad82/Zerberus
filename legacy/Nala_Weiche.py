#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nala Weiche V4.0 – stabilisiert & vorbereitet für Rosa-Zerberus
"""

import asyncio
import re
import json
import os
import math
import logging
import struct
import time
import wave
import io
from collections import Counter
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

import httpx
import uvicorn
import aiosqlite
from fastapi import FastAPI, Request, HTTPException, Form, UploadFile, File, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from deepmultilingualpunctuation import PunctuationModel

# ==================== KONFIGURATION ====================
PORT = 8051
WHISPER_URL = "http://127.0.0.1:8002/v1/audio/transcriptions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# API-Key muss in der Umgebung gesetzt sein – kein Fallback!
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
if not OPENROUTER_KEY:
    raise RuntimeError("❌ OPENROUTER_KEY nicht gesetzt. Start abgebrochen.")

DB_PATH = "nala_memory.db"
CONFIG_PATH = Path("config.json")
WHISPER_CLEANER_PATH = Path("whisper_cleaner.json")
DIALECT_PATH = Path("dialect.json")

# ==================== LOGGING (früh initialisieren) ====================
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler("nala.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("nala")

# ==================== PACEMAKER ====================
PACEMAKER_INTERVAL = 240          # 4 Minuten
PACEMAKER_KEEP_ALIVE = 180000     # 30 Minuten
last_interaction_time = 0.0
pacemaker_task = None
pacemaker_running = False
_pacemaker_lock = asyncio.Lock()

def create_silent_wav(duration_sec: int = 1) -> bytes:
    sample_rate = 16000
    num_samples = sample_rate * duration_sec
    num_channels = 1
    bits_per_sample = 16
    bytes_per_sample = bits_per_sample // 8

    riff_header = b'RIFF'
    file_size = 36 + num_samples * num_channels * bytes_per_sample
    riff_header += struct.pack('<I', file_size)
    riff_header += b'WAVE'

    fmt_chunk = b'fmt '
    fmt_chunk += struct.pack('<I', 16)
    fmt_chunk += struct.pack('<H', 1)
    fmt_chunk += struct.pack('<H', num_channels)
    fmt_chunk += struct.pack('<I', sample_rate)
    fmt_chunk += struct.pack('<I', sample_rate * num_channels * bytes_per_sample)
    fmt_chunk += struct.pack('<H', num_channels * bytes_per_sample)
    fmt_chunk += struct.pack('<H', bits_per_sample)

    data_chunk = b'data'
    data_size = num_samples * num_channels * bytes_per_sample
    data_chunk += struct.pack('<I', data_size)
    silence = bytes([0] * data_size)

    return riff_header + fmt_chunk + data_chunk + silence

async def pacemaker_worker():
    global pacemaker_running
    logger_pace = logging.getLogger("nala.pacemaker")
    logger_pace.info("💓 Pacemaker-Worker gestartet")
    while pacemaker_running:
        await asyncio.sleep(PACEMAKER_INTERVAL)
        now = time.time()
        if now - last_interaction_time > PACEMAKER_KEEP_ALIVE:
            logger_pace.info("⏸️ Keine Interaktion für %d Minuten – Pacemaker stoppt", PACEMAKER_KEEP_ALIVE//60)
            async with _pacemaker_lock:
                pacemaker_running = False
            break
        try:
            wav_data = create_silent_wav()
            files = {"file": ("silence.wav", wav_data, "audio/wav")}
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(WHISPER_URL, files=files)
            logger_pace.debug("💓 Pacemaker-Puls gesendet")
        except Exception as e:
            logger_pace.error(f"❌ Pacemaker-Fehler: {e}")

async def update_interaction():
    global last_interaction_time, pacemaker_running, pacemaker_task
    last_interaction_time = time.time()
    async with _pacemaker_lock:
        if not pacemaker_running:
            logger.info("▶️ Pacemaker wird gestartet (erste Interaktion)")
            pacemaker_running = True
            pacemaker_task = asyncio.create_task(pacemaker_worker())

# ==================== LIFESPAN ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    load_configs()
    get_punctuation_model()
    logger.info("Nala Weiche V4.0 gestartet – Hel-Frontend unter /hel")
    yield
    global pacemaker_running, pacemaker_task
    pacemaker_running = False
    if pacemaker_task:
        pacemaker_task.cancel()
        try:
            await pacemaker_task
        except asyncio.CancelledError:
            pass
    logger.info("Pacemaker gestoppt")

# ==================== GLOBALE VARIABLEN ====================
analyzer = SentimentIntensityAnalyzer()
stimmungs_durchschnitt = 0.0
ALPHA = 0.2
_stimmung_lock = asyncio.Lock()

whisper_cleaner_rules = []
dialect_rules = {}

# ==================== KOMMASETZUNG (deepmultilingualpunctuation) ====================
_punctuation_model = None

def get_punctuation_model():
    global _punctuation_model
    if _punctuation_model is None:
        try:
            _punctuation_model = PunctuationModel()
            logger.info("✅ Kommasetzungs-Modell geladen (deepmultilingualpunctuation)")
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden: {e}")
            _punctuation_model = None
    return _punctuation_model

async def apply_punctuation_async(text: str) -> str:
    if not text:
        return text
    model = get_punctuation_model()
    if model is None:
        return text
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, model.restore_punctuation, text)
    except Exception as e:
        logger.error(f"Fehler bei Kommasetzung: {e}")
        return text

# ==================== KONFIGURATION LADEN MIT CACHE ====================
class ConfigManager:
    _cache = {}
    _mtime = {}

    @classmethod
    def _load_json(cls, path: Path, default=None):
        try:
            current_mtime = path.stat().st_mtime
            if path not in cls._mtime or cls._mtime[path] != current_mtime:
                with open(path, "r", encoding="utf-8") as f:
                    cls._cache[path] = json.load(f)
                cls._mtime[path] = current_mtime
            return cls._cache.get(path, default)
        except FileNotFoundError:
            cls._cache[path] = default if default is not None else {}
            cls._mtime[path] = 0
            return cls._cache[path]

    @classmethod
    def get_whisper_cleaner(cls):
        return cls._load_json(WHISPER_CLEANER_PATH, [])

    @classmethod
    def get_dialect(cls):
        return cls._load_json(DIALECT_PATH, {})

    @classmethod
    def get_config(cls):
        return cls._load_json(CONFIG_PATH, {})

def load_configs():
    global whisper_cleaner_rules, dialect_rules
    whisper_cleaner_rules = ConfigManager.get_whisper_cleaner()
    dialect_rules = ConfigManager.get_dialect()
    logger.info(f"Whisper-Cleaner geladen: {len(whisper_cleaner_rules)} Regeln")
    logger.info(f"Dialekt-Regeln geladen: {list(dialect_rules.keys())}")

# ==================== DATENBANK ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                word_count INTEGER,
                sentence_count INTEGER,
                character_count INTEGER,
                avg_word_length REAL,
                unique_word_count INTEGER,
                ttr REAL,
                hapax_count INTEGER,
                yule_k REAL,
                shannon_entropy REAL,
                vader_compound REAL,
                FOREIGN KEY (message_id) REFERENCES interactions (id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cost REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_interactions_session ON interactions(session_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_metrics_message ON message_metrics(message_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_costs_session ON costs(session_id)")
        await db.commit()

async def save_interaction(role: str, content: str, session_id: str = None) -> int:
    metrics = compute_metrics(content)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "INSERT INTO interactions (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content)
            )
            message_id = cursor.lastrowid
            await db.execute("""
                INSERT INTO message_metrics (
                    message_id, word_count, sentence_count, character_count,
                    avg_word_length, unique_word_count, ttr, hapax_count,
                    yule_k, shannon_entropy, vader_compound
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id,
                metrics["word_count"],
                metrics["sentence_count"],
                metrics["character_count"],
                metrics["avg_word_length"],
                metrics["unique_word_count"],
                metrics["ttr"],
                metrics["hapax_count"],
                metrics["yule_k"],
                metrics["shannon_entropy"],
                metrics["vader_compound"]
            ))
            await db.commit()
            return message_id
        except Exception:
            await db.rollback()
            raise

async def save_cost(session_id: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO costs (session_id, model, prompt_tokens, completion_tokens, total_tokens, cost)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, model, prompt_tokens, completion_tokens, prompt_tokens+completion_tokens, cost))
        await db.commit()

async def get_mem(limit: int = 10, session_id: str = None) -> List[Dict[str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT role, content FROM interactions"
        params = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

# ==================== METRIKEN ====================
def compute_metrics(text: str) -> Dict[str, Any]:
    if not text:
        return {k:0 for k in ["word_count","sentence_count","character_count","avg_word_length",
                "unique_word_count","ttr","hapax_count","yule_k","shannon_entropy","vader_compound"]}
    words = re.findall(r'\w+', text.lower())
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    word_count = len(words)
    sentence_count = len(sentences)
    character_count = len(text)
    avg_word_length = sum(len(w) for w in words) / word_count if word_count else 0
    freq = Counter(words)
    unique_word_count = len(freq)
    ttr = unique_word_count / word_count if word_count else 0
    hapax_count = sum(1 for v in freq.values() if v == 1)
    if word_count > 0:
        v_i = {}
        for f in freq.values():
            v_i[f] = v_i.get(f, 0) + 1
        sum_i2_v = sum((i ** 2) * cnt for i, cnt in v_i.items())
        yule_k = 10000 * (sum_i2_v - word_count) / (word_count ** 2) if word_count > 0 else 0
    else:
        yule_k = 0
    entropy = 0.0
    if word_count > 0:
        for f in freq.values():
            p = f / word_count
            entropy -= p * math.log2(p)
    vader_scores = analyzer.polarity_scores(text)
    vader_compound = vader_scores['compound']
    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "character_count": character_count,
        "avg_word_length": round(avg_word_length, 2),
        "unique_word_count": unique_word_count,
        "ttr": round(ttr, 3),
        "hapax_count": hapax_count,
        "yule_k": round(yule_k, 2),
        "shannon_entropy": round(entropy, 3),
        "vader_compound": round(vader_compound, 3)
    }

# ==================== CLEANER & DIALEKT ====================
def apply_whisper_cleaner(text: str) -> str:
    if not text:
        return text
    for rule in whisper_cleaner_rules:
        try:
            pattern = rule["pattern"]
            replacement = rule.get("replacement", "")
            text = re.sub(pattern, replacement, text)
        except Exception as e:
            logger.error(f"Fehler bei Cleaner-Regel {rule}: {e}")
    return text.strip()

def apply_dialect(text: str, dialect_name: str) -> str:
    if dialect_name not in dialect_rules:
        return text
    rules = dialect_rules[dialect_name]
    words = text.split()
    new_words = []
    for w in words:
        w_lower = w.lower()
        if w_lower in rules:
            new_words.append(rules[w_lower])
        else:
            new_words.append(w)
    return " ".join(new_words)

def detect_dialect_marker(text: str) -> Tuple[Optional[str], str]:
    markers = {
        "🐻": "berlin",
        "🥨": "schwaebisch",
        "✨": "emojis"
    }
    stripped = text.lstrip()
    for marker, dialect in markers.items():
        if stripped.startswith(marker * 5):
            rest = stripped[5:].lstrip()
            logger.info(f"✅ Dialekt erkannt: {dialect} (Marker {marker*5})")
            return dialect, rest
    return None, text

# ==================== ZENTRALE TEXT-PIPELINE ====================
async def process_text_pipeline(raw: str) -> Tuple[str, Optional[str], str]:
    cleaned = apply_whisper_cleaner(raw)
    cleaned = await apply_punctuation_async(cleaned)
    dialect, rest = detect_dialect_marker(cleaned)
    return cleaned, dialect, rest

# ==================== LLM-AUFRUF MIT DSGVO-FALLBACK ====================
async def call_llm(prompt: str, session_id: str = None) -> Tuple[str, str, int, int, float]:
    settings = ConfigManager.get_config()
    model = settings.get("llm", {}).get("cloud_model", "deepseek/deepseek-chat")
    temperature = settings.get("llm", {}).get("temperature", 0.7)
    key_env = settings.get("llm", {}).get("openrouter_key_env", "OPENROUTER_KEY")
    api_key = os.getenv(key_env, OPENROUTER_KEY)

    history = await get_mem(limit=10, session_id=session_id)

    system_msg = {
        "role": "system",
        "content": """Du bist Nala, Jojos treue, kluge und charakterstarke Katze. Antworte kurz, prägnant und mit einem Augenzwinkern. Du hilfst Jojo beim Formulieren und Denken. Sei ehrlich, direkt und manchmal frech, aber immer liebevoll."""
    }

    messages = [system_msg] + history + [{"role": "user", "content": prompt}]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 800,
        "provider": {
            "data_collection": "deny"
        }
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            if resp.status_code == 401:
                return ("[FEHLER] OpenRouter API Key ungültig!", model, 0, 0, 0.0)
            resp.raise_for_status()
            data = resp.json()
            answer = data['choices'][0]['message']['content']
            usage = data.get('usage', {})
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)
            cost = 0.0
            return answer, model, prompt_tokens, completion_tokens, cost
    except Exception as e:
        logger.exception("Fehler beim LLM-Aufruf")
        return (f"Miau... mein Kopf brummt. (Fehler: {e})", model, 0, 0, 0.0)

# ==================== AUDIO-DAUER PRÜFEN ====================
def get_audio_duration_sec(audio_bytes: bytes, filename: str) -> float:
    if not filename.lower().endswith(".wav"):
        return 999.0
    try:
        with wave.open(io.BytesIO(audio_bytes)) as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate == 0:
                return 0.0
            return frames / rate
    except Exception:
        return 999.0

# ==================== FRONTEND JOJO ====================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>Nala</title><meta charset="UTF-8"></head>
<body style="font-family:sans-serif; margin:2em;">
<h1>🐱 Nala – Jojos Assistentin</h1>
<form method="post" action="/voice" enctype="multipart/form-data">
    <input type="file" name="file" accept="audio/*" />
    <button type="submit">Sprachaufnahme senden</button>
</form>
<form method="post" action="/chat" style="margin-top:1em;">
    <textarea name="text" rows="4" cols="60" placeholder="Deine Nachricht..."></textarea><br/>
    <button type="submit">Senden</button>
</form>
</body>
</html>
"""

# ==================== ADMIN-HEL HTML ====================
HEL_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hel – Nala Admin</title>
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
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Hel – Admin-Konsole</h1>

        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('llm')">LLM & Guthaben</button>
            <button class="tab-btn" onclick="switchTab('cleaner')">Whisper-Cleaner</button>
            <button class="tab-btn" onclick="switchTab('dialect')">Dialekte</button>
            <button class="tab-btn" onclick="switchTab('metrics')">Metriken</button>
        </div>

        <!-- Tab LLM -->
        <div id="tab-llm" class="tab-content active">
            <div class="card">
                <h2>OpenRouter Modell & Guthaben</h2>
                <label>Modell auswählen:</label>
                <select id="modelSelect" onchange="changeModel()"></select>
                <div class="balance" id="balanceDisplay">Guthaben wird geladen...</div>
            </div>
        </div>

        <!-- Tab Cleaner -->
        <div id="tab-cleaner" class="tab-content">
            <div class="card">
                <h2>Whisper-Cleaner JSON bearbeiten</h2>
                <textarea id="cleanerEditor" rows="20" style="font-family: monospace;"></textarea>
                <button onclick="saveCleaner()">Speichern</button>
                <div id="cleanerStatus"></div>
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

        <!-- Tab Metriken -->
        <div id="tab-metrics" class="tab-content">
            <div class="card">
                <h2>Letzte Nachrichten</h2>
                <table id="messagesTable">
                    <thead> <tr><th>Zeit</th><th>Rolle</th><th>Inhalt</th><th>Wörter</th><th>Sentiment</th></tr> </thead>
                    <tbody></tbody>
                </table>
            </div>
            <div class="card">
                <h2>Sentiment-Verlauf</h2>
                <div id="chart-container"><canvas id="sentimentChart"></canvas></div>
            </div>
            <div class="card">
                <h2>Gespeicherte Sessions</h2>
                <div id="sessionList"></div>
            </div>
        </div>
    </div>

    <script>
        let currentModel = '';
        let chart;

        function switchTab(name) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab-btn[onclick*="${name}"]`).classList.add('active');
            document.getElementById(`tab-${name}`).classList.add('active');
            if (name === 'metrics') loadMetrics();
        }

        async function loadModelsAndBalance() {
            try {
                const [modelsRes, balanceRes] = await Promise.all([
                    fetch('/admin/models'),
                    fetch('/admin/balance')
                ]);
                const modelsData = await modelsRes.json();
                const models = modelsData.data || [];
                const balance = await balanceRes.json();
                const select = document.getElementById('modelSelect');
                select.innerHTML = '';
                models.forEach(m => {
                    const option = document.createElement('option');
                    option.value = m.id;
                    option.textContent = `${m.name} (${m.pricing?.prompt?.toFixed(6) ?? '?'} $/1k)`;
                    select.appendChild(option);
                });
                const configRes = await fetch('/admin/config');
                const config = await configRes.json();
                currentModel = config.llm?.cloud_model || '';
                if (currentModel && select.querySelector(`option[value="${currentModel}"]`)) {
                    select.value = currentModel;
                }
                document.getElementById('balanceDisplay').innerText =
                    `Guthaben: ${balance.data?.credits ?? '?'} USD`;
            } catch (e) {
                document.getElementById('balanceDisplay').innerText = 'Fehler beim Laden';
            }
        }

        async function changeModel() {
            const model = document.getElementById('modelSelect').value;
            await fetch('/admin/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ llm: { cloud_model: model } })
            });
            alert('Modell gespeichert');
        }

        async function loadCleaner() {
            const res = await fetch('/admin/whisper_cleaner');
            const data = await res.json();
            document.getElementById('cleanerEditor').value = JSON.stringify(data, null, 2);
        }
        async function saveCleaner() {
            const raw = document.getElementById('cleanerEditor').value;
            try { JSON.parse(raw); } catch (e) { alert('Ungültiges JSON'); return; }
            const res = await fetch('/admin/whisper_cleaner', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: raw
            });
            if (res.ok) document.getElementById('cleanerStatus').innerText = '✅ Gespeichert';
            else document.getElementById('cleanerStatus').innerText = '❌ Fehler';
        }

        async function loadDialect() {
            const res = await fetch('/admin/dialect');
            const data = await res.json();
            document.getElementById('dialectEditor').value = JSON.stringify(data, null, 2);
        }
        async function saveDialect() {
            const raw = document.getElementById('dialectEditor').value;
            try { JSON.parse(raw); } catch (e) { alert('Ungültiges JSON'); return; }
            const res = await fetch('/admin/dialect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: raw
            });
            if (res.ok) document.getElementById('dialectStatus').innerText = '✅ Gespeichert';
            else document.getElementById('dialectStatus').innerText = '❌ Fehler';
        }

        async function loadMetrics() {
            const res = await fetch('/metrics/latest?limit=20');
            const data = await res.json();
            const tbody = document.querySelector('#messagesTable tbody');
            tbody.innerHTML = '';
            const chartData = { labels: [], sentiments: [] };
            data.forEach((msg, i) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td>${new Date(msg.timestamp).toLocaleString()}</td>
                                <td>${msg.role}</td>
                                <td>${msg.content.substring(0,50)}...</td>
                                <td>${msg.word_count ?? ''}</td>
                                <td>${msg.vader_compound ?? ''}</td>`;
                tbody.appendChild(tr);
                if (msg.vader_compound !== null) {
                    chartData.labels.push(i);
                    chartData.sentiments.push(msg.vader_compound);
                }
            });
            if (chart) chart.destroy();
            const ctx = document.getElementById('sentimentChart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: chartData.labels,
                    datasets: [{
                        label: 'Sentiment',
                        data: chartData.sentiments,
                        borderColor: '#ff6b6b',
                        backgroundColor: 'rgba(255,107,107,0.1)'
                    }]
                },
                options: { responsive: true, maintainAspectRatio: false }
            });

            const sessionsRes = await fetch('/admin/sessions');
            const sessions = await sessionsRes.json();
            const sessionDiv = document.getElementById('sessionList');
            sessionDiv.innerHTML = sessions.map(s => `
                <div class="session-item" onclick="exportSession('${s.session_id}')">
                    <span>${s.first_message || 'Neuer Chat'}</span>
                    <span>${new Date(s.last_time).toLocaleString()}</span>
                </div>
            `).join('');
        }

        async function exportSession(sessionId) {
            window.location.href = `/admin/export/session/${sessionId}`;
        }

        loadModelsAndBalance();
        loadCleaner();
        loadDialect();
    </script>
</body>
</html>
"""

# ==================== PYDANTIC MODELLE ====================
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]

# ==================== FASTAPI APP ====================
app = FastAPI(title="Nala Weiche V4.0", lifespan=lifespan)

@app.get("/favicon.ico", include_in_schema=False)
async def fav(): return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def index(): return HTML_TEMPLATE

@app.get("/hel", response_class=HTMLResponse)
async def hel_interface(): return HEL_HTML

# ---------- Web-Frontend Endpunkte ----------
@app.post("/voice")
async def voice(file: UploadFile = File(...), request: Request = None):
    session_id = request.headers.get("X-Session-ID") if request else None
    await update_interaction()
    try:
        audio_data = await file.read()
        duration = get_audio_duration_sec(audio_data, file.filename)
        if duration < 0.5:
            return {"text": "", "error": "Audio zu kurz (<0.5s) – übersprungen"}
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(WHISPER_URL, files={'file': (file.filename, audio_data, file.content_type)})
            resp.raise_for_status()
            whisper_text = resp.json().get('text', '')
    except Exception as e:
        logger.exception("Fehler bei Whisper-Transkription")
        return {"text": "", "error": str(e)}
    cleaned, dialect, rest = await process_text_pipeline(whisper_text)
    if dialect:
        final_text = apply_dialect(rest, dialect)
        await save_interaction("user", cleaned, session_id=session_id)
        await save_interaction("assistant", final_text, session_id=session_id)
        vader = analyzer.polarity_scores(final_text)['compound']
        async with _stimmung_lock:
            global stimmungs_durchschnitt
            stimmungs_durchschnitt = (vader * ALPHA) + (stimmungs_durchschnitt * (1 - ALPHA))
        return {"text": final_text, "vader": stimmungs_durchschnitt}
    answer, model, pt, ct, cost = await call_llm(rest, session_id)
    await save_interaction("user", cleaned, session_id=session_id)
    await save_interaction("assistant", answer, session_id=session_id)
    await save_cost(session_id, model, pt, ct, cost)
    vader = analyzer.polarity_scores(answer)['compound']
    async with _stimmung_lock:
        stimmungs_durchschnitt = (vader * ALPHA) + (stimmungs_durchschnitt * (1 - ALPHA))
    return {"text": answer, "vader": stimmungs_durchschnitt}

@app.post("/chat")
async def chat(text: str = Form(...), request: Request = None):
    session_id = request.headers.get("X-Session-ID") if request else None
    await update_interaction()
    cleaned, dialect, rest = await process_text_pipeline(text)
    if dialect:
        final_text = apply_dialect(rest, dialect)
        await save_interaction("user", cleaned, session_id=session_id)
        await save_interaction("assistant", final_text, session_id=session_id)
        vader = analyzer.polarity_scores(final_text)['compound']
        async with _stimmung_lock:
            global stimmungs_durchschnitt
            stimmungs_durchschnitt = (vader * ALPHA) + (stimmungs_durchschnitt * (1 - ALPHA))
        return {"text": final_text, "vader": stimmungs_durchschnitt}
    answer, model, pt, ct, cost = await call_llm(rest, session_id)
    await save_interaction("user", cleaned, session_id=session_id)
    await save_interaction("assistant", answer, session_id=session_id)
    await save_cost(session_id, model, pt, ct, cost)
    vader = analyzer.polarity_scores(answer)['compound']
    async with _stimmung_lock:
        stimmungs_durchschnitt = (vader * ALPHA) + (stimmungs_durchschnitt * (1 - ALPHA))
    return {"text": answer, "vader": stimmungs_durchschnitt}

# ---------- OpenAI-kompatible Endpunkte ----------
@app.post("/v1/audio/transcriptions")
async def v1_audio_transcriptions(file: UploadFile = File(...), request: Request = None):
    logger.info(f"🎤 Audio-Request empfangen: {file.filename}")
    await update_interaction()
    try:
        audio_data = await file.read()
        duration = get_audio_duration_sec(audio_data, file.filename)
        if duration < 0.5:
            return {"text": "", "error": "Audio zu kurz (<0.5s) – übersprungen"}
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(WHISPER_URL, files={'file': (file.filename, audio_data, file.content_type)})
            resp.raise_for_status()
            whisper_text = resp.json().get('text', '')
    except Exception as e:
        logger.exception("Fehler bei Whisper")
        raise HTTPException(500, detail=str(e))
    cleaned, _, _ = await process_text_pipeline(whisper_text)
    return {"text": cleaned}

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def v1_chat_completions(request: Request):
    body_bytes = await request.body()
    try:
        req = ChatCompletionRequest.parse_raw(body_bytes)
    except Exception as e:
        logger.error(f"❌ Parse-Fehler: {e}")
        raise HTTPException(400, detail="Invalid request format")
    session_id = request.headers.get("X-Session-ID")
    await update_interaction()
    user_message = None
    for msg in reversed(req.messages):
        if msg.role == "user":
            user_message = msg.content
            break
    if not user_message:
        raise HTTPException(400, detail="Keine User-Nachricht")
    cleaned, dialect, rest = await process_text_pipeline(user_message)
    if dialect:
        final_text = apply_dialect(rest, dialect)
        await save_interaction("user", cleaned, session_id=session_id)
        await save_interaction("assistant", final_text, session_id=session_id)
        vader = analyzer.polarity_scores(final_text)['compound']
        async with _stimmung_lock:
            global stimmungs_durchschnitt
            stimmungs_durchschnitt = (vader * ALPHA) + (stimmungs_durchschnitt * (1 - ALPHA))
        return ChatCompletionResponse(
            id="...", created=int(time.time()), model="dialect",
            choices=[ChatCompletionResponseChoice(index=0, message=ChatMessage(role="assistant", content=final_text), finish_reason="stop")]
        )
    answer, model, pt, ct, cost = await call_llm(rest, session_id)
    await save_interaction("user", cleaned, session_id=session_id)
    await save_interaction("assistant", answer, session_id=session_id)
    await save_cost(session_id, model, pt, ct, cost)
    vader = analyzer.polarity_scores(answer)['compound']
    async with _stimmung_lock:
        stimmungs_durchschnitt = (vader * ALPHA) + (stimmungs_durchschnitt * (1 - ALPHA))
    return ChatCompletionResponse(
        id="...", created=int(time.time()), model=model,
        choices=[ChatCompletionResponseChoice(index=0, message=ChatMessage(role="assistant", content=answer), finish_reason="stop")]
    )

# ---------- Workaround für doppelte Pfade (TODO: Client-Bug) ----------
@app.post("/v1/audio/transcriptions/audio/transcriptions")
async def v1_audio_transcriptions_double(file: UploadFile = File(...), request: Request = None):
    # TODO: Workaround für SillyTavern/Client der den Basispfad doppelt anhängt.
    return await v1_audio_transcriptions(file, request)

@app.post("/v1/chat/completions/chat/completions", response_model=ChatCompletionResponse)
async def v1_chat_completions_double(request: Request):
    # TODO: Workaround für SillyTavern/Client der den Basispfad doppelt anhängt.
    return await v1_chat_completions(request)

# ---------- Admin-Endpunkte ----------
@app.get("/admin/whisper_cleaner")
async def get_whisper_cleaner():
    return JSONResponse(content=ConfigManager.get_whisper_cleaner())

@app.post("/admin/whisper_cleaner")
async def post_whisper_cleaner(request: Request):
    data = await request.json()
    with open(WHISPER_CLEANER_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    load_configs()
    return {"status": "ok"}

@app.get("/admin/dialect")
async def get_dialect():
    return JSONResponse(content=ConfigManager.get_dialect())

@app.post("/admin/dialect")
async def post_dialect(request: Request):
    data = await request.json()
    with open(DIALECT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    load_configs()
    return {"status": "ok"}

@app.get("/admin/models")
async def get_models():
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://openrouter.ai/api/v1/models")
        return resp.json()

@app.get("/admin/balance")
async def get_balance():
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}
        )
        return resp.json()

@app.get("/admin/config")
async def get_config():
    return JSONResponse(content=ConfigManager.get_config())

@app.post("/admin/config")
async def post_config(request: Request):
    data = await request.json()
    current = ConfigManager.get_config()
    if "llm" in data:
        current["llm"] = {**current.get("llm", {}), **data["llm"]}
    with open(CONFIG_PATH, "w") as f:
        json.dump(current, f, indent=2)
    return {"status": "ok"}

@app.get("/admin/sessions")
async def get_sessions():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("""
            SELECT session_id, MIN(timestamp) as first_time, MAX(timestamp) as last_time,
                   (SELECT content FROM interactions i2 WHERE i2.session_id = i.session_id AND role='user' ORDER BY timestamp LIMIT 1) as first_message
            FROM interactions i
            WHERE session_id IS NOT NULL
            GROUP BY session_id
            ORDER BY last_time DESC
        """)
        return [dict(r) for r in rows]

@app.get("/admin/export/session/{session_id}")
async def export_session(session_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT role, content, timestamp FROM interactions WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        )
        return [dict(r) for r in rows]

@app.get("/metrics/latest")
async def get_latest_metrics(limit: int = 10, session_id: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT i.id, i.role, i.content, i.timestamp,
                   m.word_count, m.sentence_count, m.ttr, m.vader_compound
            FROM interactions i
            LEFT JOIN message_metrics m ON i.id = m.message_id
        """
        params = []
        if session_id:
            query += " WHERE i.session_id = ?"
            params.append(session_id)
        query += " ORDER BY i.timestamp DESC LIMIT ?"
        params.append(limit)
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

@app.get("/metrics/summary")
async def get_metrics_summary(session_id: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT
                AVG(m.word_count) as avg_word_count,
                AVG(m.sentence_count) as avg_sentence_count,
                AVG(m.ttr) as avg_ttr,
                AVG(m.vader_compound) as avg_sentiment,
                COUNT(*) as total_messages
            FROM interactions i
            LEFT JOIN message_metrics m ON i.id = m.message_id
        """
        params = []
        if session_id:
            query += " WHERE i.session_id = ?"
            params.append(session_id)
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}

if __name__ == "__main__":
    # Zertifikate: zuerst aus Umgebungsvariablen, dann lokale Dateien
    ssl_cert = os.getenv("NALA_SSL_CERT")
    ssl_key = os.getenv("NALA_SSL_KEY")
    if ssl_cert and ssl_key:
        ssl = {"ssl_certfile": ssl_cert, "ssl_keyfile": ssl_key}
        print("🔒 SSL über Umgebungsvariablen aktiv!")
    else:
        import glob
        certs = glob.glob("*.crt")
        keys = glob.glob("*.key")
        if certs and keys:
            ssl = {"ssl_certfile": certs[0], "ssl_keyfile": keys[0]}
            print("🔒 Tailscale SSL aktiv!")
        else:
            ssl = {}
            print("⚠️ Start ohne SSL (Mikrofon nur auf Localhost)")
    uvicorn.run(app, host="0.0.0.0", port=PORT, **ssl)