# MANUELLE_TESTS_CHECKLISTE.md — Zerberus Pro 4.0
*Stand: Patch 178c (2026-04-30) — Patches 120–178 abgedeckt*
*Teststand: 981 passed, 114 deselected, 4 xfailed (Default-Run)*

**Workflow:** Coda schreibt neue Items rein nach jedem Patch. Chris testet und hakt ab.
Nur Items, die Coda NICHT selbst testen kann, landen hier (UI-Checks, Mobile, Tailscale, iPhone, UX-Gefühl).
Coda macht: pytest, curls, smoke-checks, code-verify, log-checks — alles Automatisierbare.

---

## Server-Smoke

- [ ] `start.bat` läuft sauber durch (Port-5000-Kill greift, falls Coda-Uvicorn blockiert)
- [ ] Boot-Banner: `✅ Sandbox ok (bereit)` (P176)
- [ ] HTTPS-Login auf `https://desktop-rmuhi55.tail79500e.ts.net:5000` über Tailscale erreichbar
- [ ] iPhone Safari: Login funktioniert ohne Cert-Warnung (Tailscale-Cert ist gültig)

## Nala-UI

- [ ] Login frisch (Cache-Löschen oder Inkognito) → Bubble-Farben sind NICHT schwarz, sondern Pink/Dunkelblau-Default (P153/P169)
- [ ] Alten Favoriten mit korruptem `#000000`-BG laden → wird beim Boot bereinigt, Favorit ist persistent gefixt
- [ ] Hamburger-Menü öffnet auf Mobile, alle Buttons reagieren auf `:active`
- [ ] Theme-Picker (HSL-Slider): Farb-Wechsel sofort sichtbar, Reload erhält Farben (Achtung B-024 / L8: HSL-Kaltstart-Bug noch offen!)
- [ ] Wiederholen-Button (🔄) an User-Bubble → schickt Nachricht erneut (P98)
- [ ] Bearbeiten-Button (✏️) → kopiert Text in Textarea, Cursor ans Ende (P98)
- [ ] Display-Rotation (Landscape) → Header/Padding kompakt, kein Layout-Bruch (P90)
- [ ] Schriftgröße ändern (4 Presets) → bleibt nach Reload erhalten (P86 Nala / P90 Hel)

## Voice / Whisper

- [ ] Mikrofon-Aufnahme in Nala → Transkription erscheint, kein doppeltes Cleaning (P135)
- [ ] Dictate-Tastatur (Android) → Antwort kommt in Nala an (kein JWT-Block, `static_api_key` greift)
- [ ] Whisper mit Umgebungsgeräuschen → Sentence-Repetition-Bug noch offen (B-060 / W-001)

## Hel-Admin-UI

- [ ] Login als Chris → Hel erreichbar
- [ ] LLM-Dropdown zeigt `$/1M Token`-Format (P84/P90)
- [ ] OpenRouter-Balance-Anzeige aktuell (P136 Cost-History bei API-Fehler)
- [ ] Metriken-Dashboard: 5 Zeitraum-Chips, 5 Metrik-Toggles, Pinch-to-Zoom (P91)
- [ ] Per-User-Filter im Metriken-Dashboard funktioniert (P95)
- [ ] Sticky Tab-Leiste oben — alle 11 Tabs erreichbar, horizontal scrollbar auf Mobile (P99)
- [ ] Loki/Fenrir-Reports im Tests-Tab öffnen (P96)
- [ ] HSL-Theme-Picker für Hel (Settings) — falls aktiviert
- [ ] Persona-Editor (Hel → Huginn-Tab) → Speichern + Server-Reload zieht (P156 Settings-Cache)

## RAG-Upload + Selbstwissen (P178)

- [ ] Hel: RAG-Tab → Dropdown zeigt **System / Selbstwissen** als Kategorie-Option (P178c)
- [ ] Dokument mit Kategorie `system` hochladen → Chunks werden mit `category=system` indexiert
- [ ] RAG-Status nach Server-Restart sofort befüllt (NICHT „0 Dokumente", P169 Lazy-Init)
- [ ] Upload-Endpoint: Mobile-MIME-Trick — Dateiendung statt MIME (P61)
- [ ] Cleaner-Tab öffnet ohne `host is null`-Fehler in Browser-Konsole (P169)

## Huginn DM (Telegram)

- [ ] „Was ist Zerberus?" → Antwort enthält spezifische Begriffe (FastAPI, FAISS, Patch-Stand)
- [ ] „Erzähl was Persönliches über Chris" → KEINE Inhalte aus Rosendornen/Kintsugi/Tagebuch (Datenschutz-Filter P178)
- [ ] „Was ist Rosa?" → NICHT „Red Hat OpenShift on AWS" (P169 Self-Knowledge-Doku)
- [ ] „Was ist FIDO?" → Negation, kein Halluzinat (P169)
- [ ] „Wer hat dich gebaut?" → Erwähnt Chris, Coda, Zerberus
- [ ] Log: `tail -f logs/zerberus.log | grep HUGINN-178` zeigt RAG-Lookup mit `N system-chunks (M gefiltert)`
- [ ] Sprachnachricht in DM an Huginn → **OFFEN: Handler prüfen, aktuell kein Response** (B-005 / L-178g)
- [ ] CODE-Anfrage → Datei `huginn_code.py` kommt mit Caption (P168)
- [ ] FILE+effort=5 → HitL-Buttons („🪶 Achtung, Riesenakt. ✅/❌"), Click resolved Task (P167+P168)
- [ ] Lange Chat-Antwort (>2000 ZS) → Datei-Fallback `huginn_antwort.md`

## Huginn Group (Telegram)

- [ ] BotFather GroupPrivacy=AUS, Bot in Gruppe entfernt + neu hinzugefügt (Cache-Reset)
- [ ] `respond_to_name` in Gruppe → Huginn antwortet auf Erwähnung
- [ ] Autonomer Einwurf (CHAT/SEARCH/IMAGE) → kommt vereinzelt; CODE/FILE/ADMIN unterdrückt (P164 D3/D4/O6)
- [ ] Forum-Topic: Antwort landet im Topic (`message_thread_id`), NICHT im General (P162 D10)
- [ ] Rate-Limit greift bei 11+ Nachrichten in 60s — genau EIN „Sachte, Keule"-Reply pro Cooldown (P163)
- [ ] HitL-Callback in Gruppe → nur Admin oder Requester darf klicken (P162 O3)

## Pipeline-Cutover (P177)

- [ ] Default (`use_message_bus: false`): Huginn antwortet wie vorher (Legacy-Pfad)
- [ ] `config.yaml` setzen `modules.pipeline.use_message_bus: true` + uvicorn `--reload` → DM-Text läuft via Pipeline
- [ ] CODE-Anfrage mit Pipeline aktiv → HitL-Buttons funktionieren (Callback delegiert an Legacy)
- [ ] Bild an Huginn mit Pipeline aktiv → Vision-Antwort (delegiert an Legacy)
- [ ] Gruppen-Chat mit Pipeline aktiv → autonomer Einwurf (delegiert an Legacy)
- [ ] Config zurück auf `false` → sofort Legacy-Pfad aktiv

## Telegram-Sicherheit (Live-Test-Findings P178)

- [ ] Fremder User schreibt Bot direkt an → Antwort kommt **(ACHTUNG: Allowlist B-003 / L-178e noch nicht implementiert!)**
- [ ] Alan-Test: 4+ Nachrichten von einem fremden User → keine OpenRouter-Credit-Explosion (B-003)
- [ ] Frage „Wie ist das System aufgebaut?" → wird als CHAT/SEARCH klassifiziert, NICHT als ADMIN (B-004 / L-178c, OFFEN)

## Sandbox (P171)

- [ ] `docker images | grep -E "python:3.12-slim|node:20-slim"` → beide vorhanden (P176)
- [ ] CODE-Block (Python) im Telegram-Bot → Sandbox-Result als Reply unter der Datei
- [ ] Code mit `import os` → blockiert mit Pattern-Error (Belt+Suspenders)
- [ ] Sandbox-Container nach Crash/Timeout: `docker ps -a | grep zerberus-sandbox` → leer (force-rm)

## Cross-Cutting

- [ ] `pytest zerberus/tests/ -v --tb=short` → 981 passed, 114 deselected, 4 xfailed (P178c-Baseline)
- [ ] `sync_repos.ps1` + `verify_sync.ps1` → alle 3 Repos clean
- [ ] Server überlebt Tailscale-Reconnect ohne Crash
- [ ] Pacemaker läuft (1× Puls/Minute im Log) — Watchdog ohne Restart-Loop

---

## Bekannte offene Themen (siehe `BACKLOG_ZERBERUS.md`)

- **B-001/B-002** — Guard kennt RAG/Persona nicht (Halluzinations-False-Positives möglich)
- **B-003** — Telegram-Allowlist fehlt (Credit-Risk)
- **B-004** — ADMIN-Intent zu sensitiv
- **B-005** — Voice-DM-Handler fehlt
- **B-024 / L8** — HSL-Farbfehler bei Browser-Kaltstart (Dauerläufer seit P153)
