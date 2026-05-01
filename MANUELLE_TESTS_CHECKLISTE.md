# MANUELLE_TESTS_CHECKLISTE.md — Zerberus Pro 4.0
*Stand: Patch 191 (2026-05-01) — Patches 120–191 abgedeckt*
*Teststand: ~1264 Tests passed (Default-Run, P188-Baseline 1181 + 34 P189 + 24 P190 + 25 P191)*

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

- [ ] Login frisch (Cache-Löschen oder Inkognito) → Bubble-Farben sind NICHT schwarz, sondern Pink/Dunkelblau-Default (P153/P169/P183)
- [ ] Alten Favoriten mit korruptem `#000000`-BG laden → wird beim Boot bereinigt, Favorit ist persistent gefixt
- [ ] Hamburger-Menü öffnet auf Mobile, alle Buttons reagieren auf `:active`
- [ ] Theme-Picker (HSL-Slider): Farb-Wechsel sofort sichtbar, Reload erhält Farben (P183 — Sanitizer + Sweep, B-024 / L8 erledigt)

## Modell-Selbstkenntnis (P185)

- [ ] Huginn (Telegram): "Welches Modell nutzt du?" → Antwort nennt korrektes Modell (z.B. deepseek-v3.2)
- [ ] Huginn: "Ist RAG aktiv?" → Antwort matcht config.yaml (`modules.rag.enabled`)
- [ ] Huginn: "Welches Guard-Modell?" → Antwort nennt mistral-small-24b-instruct-2501
- [ ] Nala-Frontend: "Was für ein Modell bist du?" → korrektes Modell
- [ ] Hel → Modell ändern → Server-Reload → nächste Frage zeigt neues Modell ohne Code-Änderung
- [ ] Server-Log: System-Prompt enthält den Block `[Aktuelle System-Informationen — automatisch generiert]`

## Nala-Persona / Mein Ton (P184)

- [ ] Settings → Tab Ausdruck → "Mein Ton" → Persona eingeben (z.B. Mutzenbacher) → Speichern → Status `✅ Gespeichert` erscheint
- [ ] Nachricht senden ("wie geht's?") → Antwort spiegelt den Ton wider (Wiener Schmäh, Persönlichkeit) — KEIN generisches "Alles gut hier"
- [ ] Server-Log: `[PERSONA-184]` zeigt `persona_active=True`, `sys_prompt_len > 1000`, `first200` enthält Persona-Phrase
- [ ] Browser neu starten → Login → Settings → Ton-Text ist noch da (atomares File-Write hält)
- [ ] Ton komplett leeren → Speichern → Antworten kehren auf Default-Nala zurück (warm/direkt aus `system_prompt.json`)
- [ ] User OHNE eigenen Ton (z.B. neues Profil) → Antworten nutzen Default, Server-Log zeigt `persona_active=False`

## Black-Bug Forensik (P183 — vierter Anlauf)

- [ ] Browser komplett schließen → Nala öffnen → einloggen → Bubbles NICHT schwarz, normale Pink/Dunkelblau-Defaults
- [ ] Abmelden → Browser-Cache löschen → neu einloggen → Bubbles NICHT schwarz
- [ ] Favorit speichern (mit normalen Farben) → abmelden → einloggen → Favorit laden → Bubbles NICHT schwarz
- [ ] Color-Picker auf Schwarz (#000000) stellen → Sanitizer greift, Bubble fällt auf CSS-Default zurück, Konsole zeigt `[BLACK-183]` Warning
- [ ] HSL-Slider auf Extrem (H=0, S=0, L=10 — minimum) → Sanitizer-Test: L<5% wird abgefangen, sonst durchgelassen
- [ ] Browser-Konsole bei normalem Login: KEIN `[BLACK-183]` Warning (= keine korrupten Werte vorhanden)
- [ ] DevTools → Application → localStorage: `nala_bubble_*_bg` enthält nie `#000000`/`hsl(0,0%,0%)`/etc. nach einem Sweep
- [ ] Alten Pre-P183-Favoriten mit `userText: '#000000'` laden → wird beim Sweep bereinigt (`[BLACK-183] Favorit X bereinigt` im Log)
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

## Guard + RAG-Kontext (P180)

- [ ] Huginn: „Was ist Zerberus?" → Antwort kommt, Log zeigt KEIN `[GUARD-120] WARNUNG`
- [ ] Huginn: Echte Halluzination provozieren („Erzähl mir über Zerberus Version 9.0") → Guard `WARNUNG` im Log
- [ ] Log: `tail logs/zerberus.log | grep "GUARD-120"` zeigt Guard-Calls; Persona-Auszug bis zu 800 Zeichen
- [ ] Huginn-Persona in Hel auf einen 600-Zeichen-Text setzen → erste 800 Zeichen erreichen Guard (vorher 300)

## Telegram Allowlist (P181)

- [ ] Default (Hel-Dropdown „Offen") → fremder User kann schreiben, Antwort kommt (Backward-Compat)
- [ ] Mode `admin_only` setzen → fremder User schreibt → bekommt einmalige Absage „🚫 nicht freigeschaltet", kein LLM-Call
- [ ] Mode `admin_only` → eigene Telegram-User-ID (Admin) → Antwort kommt normal
- [ ] Mode `allowlist` + eigene ID drin → Antwort kommt; ID weglassen → Block
- [ ] Hel-UI: Allowlist-Dropdown wechselt sichtbar zwischen Modi; User-IDs-Feld nur im Modus „allowlist" sichtbar
- [ ] Log: `tail logs/zerberus.log | grep ALLOWLIST-181` zeigt User-Block + Modus
- [ ] Spam-Test: gesperrter User schickt 10× → genau 1 Absage in der Stunde (Rate-Limit)

## ADMIN-Schwelle + Medien-Handler (P182)

- [ ] Huginn: Harmlose Frage („Wie geht's, Rabe?") → KEIN HitL-Button, normale Chat-Antwort (Plausi-Heuristik downgraded ADMIN→CHAT)
- [ ] Huginn: `/status` oder „Mach mal Restart" → HitL-Button erscheint (echter Admin-Befehl)
- [ ] Huginn: Sprachnachricht senden → „🎤 Sprachnachrichten kann ich noch nicht verarbeiten" + kein LLM-Call im Log
- [ ] Huginn: Sticker senden → „🎨 Sticker kann ich noch nicht verarbeiten"
- [ ] Huginn: Dokument (PDF) senden → „📎 Dokumente kann ich noch nicht verarbeiten"
- [ ] Huginn: Bild senden → läuft normal über Vision (NICHT als „unsupported")
- [ ] Log: `grep "HUGINN-182\|HITL-POLICY-182" logs/zerberus.log` zeigt Media-Skips + ADMIN-Downgrades

## Sandbox (P171)

- [ ] `docker images | grep -E "python:3.12-slim|node:20-slim"` → beide vorhanden (P176)
- [ ] CODE-Block (Python) im Telegram-Bot → Sandbox-Result als Reply unter der Datei
- [ ] Code mit `import os` → blockiert mit Pattern-Error (Belt+Suspenders)
- [ ] Sandbox-Container nach Crash/Timeout: `docker ps -a | grep zerberus-sandbox` → leer (force-rm)

## Auto-TTS (P186)

- [ ] Nala öffnen → Settings → Tab „Ausdruck" → Toggle „Antworten automatisch vorlesen" sichtbar UNTER Stimmen-Dropdown + Rate-Slider
- [ ] Toggle einmal an/aus klicken → reload Seite → Status persistiert (localStorage `nala_auto_tts`)
- [ ] Toggle AN, neue Frage stellen → nach Bot-Antwort startet TTS automatisch (gleiche Stimme wie Probe-Hören)
- [ ] Während TTS läuft, neue Frage senden → vorheriges Audio stoppt, neue Antwort wird vorgelesen
- [ ] Während TTS läuft, andere Session aus Sidebar laden → Audio stoppt
- [ ] Während TTS läuft, ausloggen → Audio stoppt
- [ ] Toggle AUS während Wiedergabe → laufendes Audio stoppt sofort, nächste Antwort nicht mehr vorgelesen
- [ ] DevTools Console → bei TTS-Fehler `[AUTO-TTS-186]`-warn statt Error-Popup
- [ ] Toggle AUS, neue Frage → KEINE TTS-Wiedergabe (Regression: 🔊-Button funktioniert weiterhin)

## FAISS-Migration / Dual-Embedder (P187)

- [ ] `ls data/vectors/` → `de.index`, `de_meta.json`, `faiss.index` (Legacy), `metadata.json` (Legacy) alle vorhanden
- [ ] Server-Restart → Startup-Log zeigt `[DUAL-187] Dual-Embedder aktiv. DE-Index: N Vektoren`
- [ ] Hel → RAG-Status → Index zeigt korrekte Anzahl Vektoren
- [ ] Nala-Query auf Deutsch („Was sind die Rosendornen?") → Antwort enthält Doku-Inhalt
- [ ] Hel → RAG-Search-Test → top-3-Treffer haben sinnvolle Distanzen (< 2.0 für relevante Queries)
- [ ] Reranker bleibt aktiv (Log: `[RERANK-89]` o.ä. bei jedem Search-Call)
- [ ] Optional: `config.yaml` → `use_dual_embedder: false` setzen → Server-Restart → Legacy-MiniLM-Pfad → Logs zeigen kein `[DUAL-187]` mehr (Backward-Compat-Verifikation)

## Prosodie-Foundation (P188)

- [ ] Server-Restart → Startup-Banner zeigt `⏭️  Prosodie         deaktiviert (modules.prosody.enabled=false)` (Default)
- [ ] `config.yaml` → `modules.prosody.enabled: true` + `model_path: ""` → Restart → Banner zeigt `⏭️  Prosodie         model_path leer — Modell nicht geladen`
- [ ] `model_path: "/nonexistent.gguf"` → Restart → Banner zeigt `❌ Prosodie         Modell nicht gefunden: /nonexistent.gguf`
- [ ] Voice-Endpunkt aufrufen (Audio aus Nala-UI senden) → KEIN Crash trotz aktivierter Prosody-Config (Pipeline-Anker ist Kommentar — kein aktiver Pfad)
- [ ] `venv\Scripts\python -c "from zerberus.modules.prosody.manager import ProsodyManager; import asyncio; print(asyncio.run(ProsodyManager().analyze(b'')))"` → Stub-Dict mit `source: "stub"`

## Prosodie-Pipeline (P189-191)

### Gemma-Client (P189)
- [ ] Modelle vorhanden: `dir C:\Users\chris\models\gemma4-e2b\` zeigt `google_gemma-4-E2B-it-Q4_K_M.gguf` (~3.4 GB) + `mmproj-google_gemma-4-E2B-it-bf16.gguf` (~940 MB)
- [ ] `config.yaml` → `modules.prosody.enabled: true`, `model_path: "C:/Users/chris/models/gemma4-e2b/google_gemma-4-E2B-it-Q4_K_M.gguf"`, `mmproj_path: "C:/Users/chris/models/gemma4-e2b/mmproj-google_gemma-4-E2B-it-bf16.gguf"`
- [ ] Server-Start: Banner zeigt `✅ Prosodie ok (Gemma E2B, Stub (Modell nicht geladen), …GB frei)` (CLI-Modus lädt erst pro Call)
- [ ] `llama-mtmd-cli` im PATH oder voller Pfad in `llama_cli_path` gesetzt — sonst Log `[PROSODY-189-CLI] Binary nicht gefunden`
- [ ] Audio-Nachricht an Nala (Mikrofon-Knopf) → Log zeigt `[PROSODY-190] Whisper+Gemma parallel (Consent gegeben)` (NUR wenn Consent aktiv) und `[PROSODY-189-CLI] Analyse OK`
- [ ] Bei deaktiviertem `enabled` → Log zeigt KEIN `[PROSODY-189]`, nur Whisper-Pfad

### Pipeline (P190)
- [ ] Audio-Nachricht mit Consent → Log zeigt Whisper-Ergebnis UND Prosodie-Ergebnis (`mood=… confidence=… source=gemma_e2b`)
- [ ] Prosodie-Ergebnis in /nala/voice-Response: Feld `prosody` mit `mood`, `tempo`, `confidence`, `valence`, `arousal`, `dominance`, `source`
- [ ] Anschließender Chat-Call: System-Prompt enthält `[Prosodie-Hinweis (Confidence: NN%): Stimmung=…, Tempo=…, Valenz=…, Arousal=…]`
- [ ] Bei `valence < -0.3` zusätzlich `[Hinweis: Stimme klingt anders als Text vermuten lässt — mögliche Ironie oder verdeckter Stress]`
- [ ] Text-Nachricht (kein Audio) → KEIN Prosodie-Block im Prompt
- [ ] Gemma-Fehler (z.B. CLI-Timeout) → Whisper-Transkription funktioniert trotzdem, Log `[PROSODY-190] Analyse fehlgeschlagen: …`

### Consent (P191)
- [ ] Nala → Einstellungen → Tab „Ausdruck" → Toggle „Sprachstimmung analysieren (Prosodie)" vorhanden, UNTER dem Auto-TTS-Toggle
- [ ] Default: AUS (localStorage `nala_prosody_consent` ist nicht gesetzt oder `"false"`)
- [ ] Toggle AN → Mikrofon-Button-Bereich zeigt 🎭-Indikator
- [ ] Toggle AN → Audio-Nachricht → Prosodie-Analyse läuft (Backend-Logs)
- [ ] Toggle AUS → Audio-Nachricht → NUR Whisper, KEINE Prosodie (Backend prüft `X-Prosody-Consent`)
- [ ] Reload Seite → Toggle-Status persistiert (localStorage)
- [ ] DevTools → Network → Audio-Request: Header `X-Prosody-Consent: true` (nur bei Consent)
- [ ] DevTools → Network → Folge-Chat-Request: Header `X-Prosody-Context: {"mood":…}` (nur bei Consent UND vorheriger Prosodie-Analyse)
- [ ] Hel → `GET /hel/admin/prosody/status` (curl mit ADMIN_USER/PASSWORD) → JSON mit `enabled`, `mode`, `success_count`, `error_count`, `last_success_ts`
- [ ] Hel-Status enthält KEINE Felder `mood`, `valence`, `arousal`, `dominance`, `tempo` (Worker-Protection)
- [ ] **Jojo (iPhone):** Prosodie-Toggle vorhanden und bedienbar (Settings → Ausdruck-Tab)
- [ ] DB-Check: `sqlite3 bunker_memory.db ".schema interactions"` → KEINE Spalte `prosody_*` (Worker-Protection)

## Cross-Cutting

- [ ] `pytest zerberus/tests/ -v --tb=short` → ~1181 passed, 114 deselected, 4 xfailed (P186-188-Baseline)
- [ ] `sync_repos.ps1` + `verify_sync.ps1` → alle 3 Repos clean
- [ ] Server überlebt Tailscale-Reconnect ohne Crash
- [ ] Pacemaker läuft (1× Puls/Minute im Log) — Watchdog ohne Restart-Loop

---

## Bekannte offene Themen (siehe `BACKLOG_ZERBERUS.md`)

- **B-001..B-005** — ✅ ERLEDIGT durch P180/P181/P182 (Guard+RAG, Persona-Cap, Allowlist, ADMIN-Plausi, Media-Handler)
- **B-024 / L8** — HSL-Farbfehler bei Browser-Kaltstart (Dauerläufer seit P153)
- **B-072** — Voice→Whisper-Pipeline für Huginn (P182 hat Voice nur abgewiesen, B-072 wäre echte Transkription)
