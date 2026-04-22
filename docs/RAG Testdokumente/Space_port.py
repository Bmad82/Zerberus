#!/usr/bin/env python3
# LICENSE: MIT (Do whatever you want, just don't blame me if it breaks)
"""
MISSION CONTROL SYSTEM V7.3 - FUSION EDITION
============================================
System:    Janome VR3000 / Industrie-Drucküberwachung
Fusion:    Backend (Python) + Frontend (HTML/JS) in einer Datei
Status:    ALL-IN-ONE SOLUTION
"""

from __future__ import annotations

import sys
import os
import time
import threading
import queue
import signal
import re
import json
import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
from http.server import BaseHTTPRequestHandler, HTTPServer

# ============================================================================
# DAS DASHBOARD (Frontend im Bauch des Walfischs)
# ============================================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MISSION CONTROL V7.3</title>
    <style>
        :root {
            --bg-color: #0d1117; --card-bg: #161b22; --text-color: #c9d1d9;
            --accent-color: #58a6ff; --success-color: #238636;
            --warning-color: #d29922; --danger-color: #f85149;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-color); color: var(--text-color);
            display: flex; flex-direction: column; align-items: center;
            margin: 0; padding: 20px;
        }
        .container { width: 100%; max-width: 800px; }
        header {
            text-align: center; margin-bottom: 30px;
            border-bottom: 2px solid var(--accent-color); padding-bottom: 10px;
        }
        .grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px; margin-bottom: 30px;
        }
        .card {
            background-color: var(--card-bg); padding: 20px; border-radius: 8px;
            border: 1px solid #30363d; text-align: center;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }
        .card h3 { margin: 0; font-size: 0.9rem; color: #8b949e; text-transform: uppercase; }
        .card div { font-size: 1.8rem; font-weight: bold; margin-top: 10px; }
        .status-running { color: var(--success-color); }
        .status-error { color: var(--danger-color); }
        .status-connecting { color: var(--warning-color); }
        .pressure-display { font-size: 4rem !important; color: var(--accent-color); }
        .raw-data {
            font-family: 'Courier New', monospace; background: #000; padding: 10px;
            border-radius: 4px; font-size: 0.8rem; color: #3fb950;
            margin-top: 20px; overflow: hidden; text-overflow: ellipsis;
        }
        .footer { margin-top: 50px; font-size: 0.7rem; color: #484f58; text-align: center; }
    </style>
</head>
<body>
<div class="container">
    <header>
        <h1>MISSION CONTROL V7.3</h1>
        <p>Janome VR3000 | Fusion Core</p>
    </header>
    <div class="grid">
        <div class="card">
            <h3>System-Zustand</h3>
            <div id="zustand" class="status-connecting">LÄDT...</div>
        </div>
        <div class="card">
            <h3>Laufzeit</h3>
            <div id="laufzeit">0s</div>
        </div>
        <div class="card">
            <h3>Fehler (Log)</h3>
            <div id="fehler" style="color: var(--danger-color);">0</div>
        </div>
    </div>
    <div class="card" style="grid-column: span 2;">
        <h3>Aktueller Druck</h3>
        <div id="druck" class="pressure-display">0.00</div>
        <div style="font-size: 1rem;">bar</div>
    </div>
    <div class="card" style="margin-top: 20px;">
        <h3>Letzter Rohwert (Telemetrie)</h3>
        <div id="rohwert" class="raw-data">Warte auf Daten...</div>
        <div id="zeitstempel" style="font-size: 0.8rem; margin-top: 5px; color: #8b949e;">--:--:--</div>
    </div>
    <div class="footer">
        Peters Bastelbude & The Architect | Status: Kintsugi-Soul-Driven
    </div>
</div>
<script>
    async function updateDashboard() {
        try {
            // Holt die Daten jetzt vom API-Endpunkt
            const response = await fetch('/api/status');
            const data = await response.json();

            const statusElem = document.getElementById('zustand');
            statusElem.innerText = data.zustand;
            statusElem.className = data.zustand === 'LAUFEND' ? 'status-running' : 'status-connecting';
            
            document.getElementById('laufzeit').innerText = data.laufzeit_sekunden + 's';
            document.getElementById('fehler').innerText = data.fehler_anzahl;
            document.getElementById('druck').innerText = data.letzter_wert.toFixed(2);
            document.getElementById('rohwert').innerText = data.letzter_rohwert;
            document.getElementById('zeitstempel').innerText = 'Letzter Check: ' + data.zeitstempel;
        } catch (error) {
            console.error('Verbindungsfehler:', error);
        }
    }
    setInterval(updateDashboard, 1000);
    updateDashboard();
</script>
</body>
</html>
"""

# ============================================================================
# KONFIGURATION & STATUS
# ============================================================================

@dataclass
class MissionConfig:
    port: str = os.getenv("ROBOT_PORT", "COM3")
    baudrate: int = 9600
    timeout: float = 0.5
    reconnect_delay: float = 2.0
    log_dir: str = "flugschreiber"
    db_file: str = "missions_telemetrie.db"
    jsonl_file: str = "audit_trail.jsonl"
    min_pressure: float = 0.0
    max_pressure: float = 10.0
    http_port: int = 8080

@dataclass
class SystemStatus:
    zustand: str = "INITIALISIERUNG"
    start_zeit: float = field(default_factory=time.time)
    letzter_wert: float = 0.0
    letzter_rohwert: str = "N/A"
    fehler_anzahl: int = 0
    nachrichten_gesamt: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "zustand": self.zustand,
                "laufzeit_sekunden": int(time.time() - self.start_zeit),
                "letzter_wert": self.letzter_wert,
                "letzter_rohwert": self.letzter_rohwert,
                "fehler_anzahl": self.fehler_anzahl,
                "nachrichten_gesamt": self.nachrichten_gesamt,
                "zeitstempel": datetime.now().strftime('%H:%M:%S')
            }

# ============================================================================
# LOGGING & SERVER
# ============================================================================

def setup_logger(cfg: MissionConfig) -> logging.Logger:
    os.makedirs(cfg.log_dir, exist_ok=True)
    log_file = os.path.join(cfg.log_dir, f"mission_{datetime.now().strftime('%Y%m%d')}.log")
    logger = logging.getLogger("MissionControl")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter('%(asctime)s | %(levelname)-8s | %(message)s', '%H:%M:%S')
    fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

class MonitoringHandler(BaseHTTPRequestHandler):
    """Der magische Türsteher: Entscheidet ob HTML oder JSON serviert wird."""
    
    def do_GET(self) -> None:
        if self.path == '/' or self.path == '/index.html':
            # Serviere die HTML Seite
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode('utf-8'))
        
        elif self.path == '/api/status':
            # Serviere die Rohdaten (JSON)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            status_json = json.dumps(self.server.system_status.to_dict())
            self.wfile.write(status_json.encode('utf-8'))
        
        else:
            self.send_error(404, "Hier gibts nischt.")
    
    def log_message(self, format, *args): pass

# ============================================================================
# LOGIK (Parser & Threads)
# ============================================================================

class PressureParser:
    def __init__(self, min_val: float, max_val: float) -> None:
        self.min_val = min_val
        self.max_val = max_val
        self.patterns = [
            re.compile(r'(?:pressure|druck|psi|bar|P)[\s:=]+([-+]?\d+[.,]?\d*)', re.IGNORECASE),
            re.compile(r'([-+]?\d+[.,]?\d*)\s*(?:bar|psi|mbar)', re.IGNORECASE),
            re.compile(r'^\s*([-+]?\d+[.,]?\d*)\s*$')
        ]
    
    def parse(self, raw_str: str) -> Optional[float]:
        normalized = raw_str.replace(',', '.')
        for pattern in self.patterns:
            match = pattern.search(normalized)
            if match:
                try:
                    value = float(match.group(1))
                    if self.min_val <= value <= self.max_val:
                        return value
                except (ValueError, IndexError):
                    continue
        return None

class MissionControl:
    def __init__(self, cfg: MissionConfig) -> None:
        self.cfg = cfg
        self.logger = setup_logger(cfg)
        self.status = SystemStatus()
        self._stop_event = threading.Event()
        self._data_queue: queue.Queue[str] = queue.Queue()
        self._serial = None
        self.parser = PressureParser(cfg.min_pressure, cfg.max_pressure)
        self._init_db()

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self.cfg.db_file) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        raw_data TEXT NOT NULL,
                        value REAL,
                        status TEXT NOT NULL
                    )
                """)
        except sqlite3.Error: pass

    def _reader(self) -> None:
        import serial
        while not self._stop_event.is_set():
            if not self._serial or not self._serial.is_open:
                try:
                    with self.status.lock: self.status.zustand = "VERBINDEN..."
                    self._serial = serial.Serial(self.cfg.port, self.cfg.baudrate, timeout=self.cfg.timeout)
                    self.logger.info(f"✅ Hardware an {self.cfg.port} gefunden.")
                    with self.status.lock: self.status.zustand = "LAUFEND"
                except Exception:
                    with self.status.lock: self.status.zustand = "SUCHE HARDWARE"
                    time.sleep(self.cfg.reconnect_delay)
                    continue
            try:
                line = self._serial.readline()
                if line:
                    decoded = line.decode('utf-8', errors='replace').strip()
                    if decoded: self._data_queue.put(decoded)
            except Exception:
                if self._serial: self._serial.close()
                self._serial = None

    def _processor(self) -> None:
        db_conn = sqlite3.connect(self.cfg.db_file, check_same_thread=False)
        while not self._stop_event.is_set():
            try:
                raw_data = self._data_queue.get(timeout=0.5)
                value = self.parser.parse(raw_data)
                ts = datetime.now().isoformat()

                with self.status.lock:
                    self.status.nachrichten_gesamt += 1
                    self.status.letzter_rohwert = raw_data
                    if value is not None:
                        self.status.letzter_wert = value
                        log_msg = f"📊 {value:.2f} bar"
                    else:
                        self.status.fehler_anzahl += 1
                        log_msg = f"⚠️ {raw_data}"

                try:
                    db_conn.execute("INSERT INTO telemetry (timestamp, raw_data, value, status) VALUES (?,?,?,?)",
                                  (ts, raw_data, value, "VALID" if value is not None else "INVALID"))
                    db_conn.commit()
                except sqlite3.Error: pass
                
                try:
                    with open(self.cfg.jsonl_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": ts, "raw": raw_data, "val": value}) + "\n")
                except OSError: pass

                self.logger.info(log_msg)
                self._data_queue.task_done()
            except queue.Empty: continue
        db_conn.close()

    def run(self) -> None:
        server = HTTPServer(('localhost', self.cfg.http_port), MonitoringHandler)
        server.system_status = self.status
        
        threads = [
            threading.Thread(target=self._reader, name="SerialReader", daemon=True),
            threading.Thread(target=self._processor, name="DataProcessor", daemon=True),
            threading.Thread(target=server.serve_forever, name="WebAPI", daemon=True)
        ]
        for t in threads: t.start()
        
        self.logger.info(f"🚀 Dashboard & API aktiv: http://localhost:{self.cfg.http_port}")
        
        signal.signal(signal.SIGINT, lambda s, f: self.shutdown())
        while not self._stop_event.is_set(): time.sleep(0.5)

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._serial: self._serial.close()
        sys.exit(0)

if __name__ == "__main__":
    try:
        import serial
    except ImportError:
        print("❌ 'pyserial' fehlt: pip install pyserial")
        sys.exit(1)
    MissionControl(MissionConfig()).run() Mio