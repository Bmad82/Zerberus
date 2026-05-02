# Zerberus / Nala – Dein persönlicher KI-Assistent

---

## Was ist das?

Zerberus ist ein persönlicher KI-Assistent, der auf deinem eigenen Computer läuft.
Du sprichst mit ihm – per Sprache oder per Text – und er antwortet dir auf Deutsch.

Der Assistent heißt **Nala** (manchmal auch „Rosa"). Du erreichst ihn über deinen Browser,
egal ob du zu Hause bist oder von unterwegs via Tailscale zugreifst.

**Was Nala kann:**
- Gesprochene Fragen verstehen (Mikrofon → Whisper → Antwort)
- Sich an frühere Gespräche erinnern (semantisches Gedächtnis)
- Verschiedene Dialekte und Stile (Berlinerisch, Schwäbisch, Emoji-Modus)
- Auf dich zugeschnittene Antworten je nach Profil und System-Prompt
- Code in einer abgesicherten Umgebung ausführen (Docker-Sandbox)

Das Besondere: Deine Daten bleiben bei dir. Kein Cloud-Speicher, kein Tracking –
nur dein lokaler Computer und eine KI-API deiner Wahl.

---

## Was brauchst du?

Bevor du anfängst, besorg dir folgende Dinge (alle kostenlos oder mit kostenlosem Einstieg):

- **OpenRouter-Account + API-Key** – Das ist die KI-Schnittstelle, über die Nala denkt.
  Kostenlos registrieren unter: https://openrouter.ai
  Nach der Registrierung: API-Key erstellen und aufschreiben.

- **Tailscale** – Damit kannst du Nala sicher von unterwegs erreichen (Handy, anderer PC),
  als wärst du im Heimnetz. Kostenlos unter: https://tailscale.com
  Nach der Installation: Gerät hinzufügen, Tailscale-Domain deines PCs notieren
  (sieht so aus: `mein-pc.tail12345.ts.net`).

- **Docker Desktop** – Für erweiterte Funktionen (Code-Ausführung in der Sandbox).
  Kostenlos unter: https://docker.com
  Einfach installieren, danach starten.

- **BIOS: Virtualisierung aktivieren** – Einmalig nötig für Docker.
  Beim PC-Start BIOS öffnen (meist F2 oder Entf-Taste), dann
  „Intel VT-x" (Intel-PC) oder „AMD-V" (AMD-PC) aktivieren.
  Einmal gemacht, nie wieder nötig.

- **Python 3.11 oder neuer** – Muss auf deinem PC installiert sein.

---

## Installation

### Schritt 1 – Repository herunterladen

Lade das Projekt herunter (grüner „Code"-Button → „Download ZIP") und entpacke es,
oder klone es mit Git:

```
git clone <repository-url>
cd Zerberus
```

### Schritt 2 – Konfiguration einrichten

Im Projektordner findest du die Datei `config.yaml.example`.
Kopiere sie und benenne die Kopie um in `config.yaml`:

```
copy config.yaml.example config.yaml
```

Öffne `config.yaml` mit einem Texteditor (z.B. Notepad++ oder VS Code) und fülle aus:

- **`cloud_api_url`** – bleibt wie es ist (OpenRouter-Adresse)
- **`whisper_url`** – ersetze `YOUR_TAILSCALE_DOMAIN` durch deinen PC-Namen in Tailscale,
  z.B. `http://mein-pc.tail12345.ts.net:8002/v1/audio/transcriptions`
- **`profiles`** → `password_hash` – leer lassen für kein Passwort,
  oder später über das Admin-Dashboard setzen

Erstelle außerdem eine Datei namens `.env` im Projektordner mit diesem Inhalt:

```
OPENROUTER_API_KEY=sk-or-v1-dein-key-hier
```

### Schritt 3 – Virtuelle Umgebung einrichten

Öffne ein Terminal (cmd oder PowerShell) im Projektordner:

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Das lädt alle nötigen Bibliotheken herunter. Nur einmal nötig.

### Schritt 4 – Starten

Doppelklicke auf **`start.bat`** im Projektordner – oder starte im Terminal:

```
start.bat
```

Nala ist jetzt erreichbar unter: **http://localhost:5000/nala**

---

## Für Entwickler: GitHub & eigene Instanz

1. Repository klonen: `git clone https://github.com/DEIN_USERNAME/zerberus.git`
2. `config.yaml.example` → `config.yaml` kopieren und ausfüllen
3. `.env` anlegen mit: `OPENROUTER_API_KEY=...` und `ADMIN_PASSWORD=...`
4. JWT-Secret generieren: `python -c "import secrets; print(secrets.token_hex(32))"`
   → In `config.yaml` unter `auth.token_secret` eintragen
5. `venv` einrichten: `python -m venv venv`
6. Dependencies: `venv\Scripts\activate` dann `pip install -r requirements.txt`
7. `start.bat` starten

**Wichtig:** Niemals `config.yaml` oder `.env` committen — beide sind in `.gitignore`.

---

## Die Nala-Oberfläche

Öffne im Browser: `http://localhost:5000/nala`

### Login
Wähle dein Profil aus der Liste und gib dein Passwort ein (falls eines gesetzt ist).
Ohne Passwort einfach auf „Einloggen" klicken.

### Chat
Tippe deine Nachricht in das Eingabefeld unten und drücke Enter oder den Senden-Button.
Nala antwortet als Textnachricht.

### Spracheingabe
Klicke auf das Mikrofon-Symbol und sprich. Whisper wandelt deine Sprache in Text um,
Nala versteht und antwortet. Kurze Sprechpause = Aufnahme endet automatisch.

### Sessions
Links in der Seitenleiste siehst du deine bisherigen Gespräche.
Klicke auf eine Session, um sie weiterzuführen oder nochmal nachzulesen.
„Neue Session" startet ein frisches Gespräch.

---

## Das Hel-Dashboard (Admin)

Das Admin-Dashboard erreichst du unter: `http://localhost:5000/hel`

Hier steuerst du alles, was Nala ausmacht. Eine Übersicht der wichtigsten Bereiche:

### Modell wählen
Unter „Konfiguration" kannst du das KI-Modell wechseln (z.B. von Llama auf GPT-4o).
OpenRouter stellt eine Liste verfügbarer Modelle bereit.

### Temperatur
Ebenfalls in der Konfiguration: Je höher die Temperatur (0–2), desto kreativer und
freier antwortet Nala. Für sachliche Antworten eher 0.3–0.5, für Gespräche 0.7–1.0.

### Guthaben
Zeigt deinen aktuellen OpenRouter-Kontostand. So siehst du, wie viel du bisher verbraucht hast.

### Cleaner
Hier kannst du Korrekturen für Whisper-Transkriptionen einstellen.
Zum Beispiel: „wispa" → „Whisper", Füllwörter wie „ähm" entfernen.

### Fuzzy-Dictionary
Eine Liste von Wörtern, die Nala kennen soll (z.B. Namen, Fachbegriffe).
Wenn Whisper ein Wort falsch erkennt, sucht der Fuzzy-Matcher das ähnlichste Wort aus dieser Liste.

### Dialekte
Hier kannst du vordefinierte Antwort-Muster für Dialekte aktivieren oder anpassen.
Ein Emoji-Marker am Anfang deiner Nachricht aktiviert den jeweiligen Modus.

### System-Prompt
Der System-Prompt gibt Nala ihre Persönlichkeit. Hier kannst du einstellen,
wie Nala redet, was sie weiß und wie sie sich verhält. Pro Profil einstellbar.

### Metriken
Statistiken über deine Gespräche: Wortanzahl, Stimmung (Sentiment), Kosten pro Anfrage.
Gut um zu sehen, was das System alles verarbeitet hat.

### Sessions
Liste aller gespeicherten Gespräche. Du kannst Sessions exportieren (als JSON) oder löschen.

### Debug
Für technisch Interessierte: Zeigt den internen Systemzustand, aktive Module,
den letzten Pipeline-Durchlauf und mehr.

---

## Das Gedächtnis von Nala (RAG)

Stell dir Nalas Gedächtnis wie einen Zettelkasten vor: Für jedes Gespräch legt sie
einen kleinen Zettel an – und wenn du sie etwas fragst, durchsucht sie den ganzen
Zettelkasten nach dem passendsten Zettel.

Das Gleiche funktioniert jetzt auch mit eigenen Dokumenten. Du kannst eine Textdatei
oder ein Word-Dokument hochladen – zum Beispiel Notizen, ein Protokoll oder einen Artikel –
und Nala wird den Inhalt "gelesen" haben. Beim nächsten Gespräch kann sie daraus zitieren
oder darauf aufbauen.

### Wie lädst du ein Dokument hoch?

1. Öffne das Hel-Dashboard unter `http://localhost:5000/hel`
2. Klicke auf den Reiter **„Gedächtnis / RAG"**
3. Klicke auf „Datei auswählen" und wähle eine `.txt`- oder `.docx`-Datei aus
4. Klicke auf „Hochladen & Indizieren"
5. Nach kurzer Zeit erscheint eine Bestätigung, z.B.: *„23 Chunks indiziert aus Rosendornen.txt"*

Das war's. Nala kennt jetzt den Inhalt deines Dokuments.

### Was passiert dabei?

Das Dokument wird in kleine Abschnitte von etwa 300 Wörtern zerlegt (mit etwas Überlapp,
damit keine Sätze an Abschnittsgrenzen zerrissen werden). Jeder Abschnitt wird in eine
mathematische „Bedeutungs-Darstellung" umgewandelt und im Gedächtnis-Index gespeichert.

Beim nächsten Gespräch vergleicht Nala deine Frage mit allen gespeicherten Abschnitten
und zieht die passendsten als Kontext heran – bevor sie antwortet.

### Wie teste ich, ob es funktioniert hat?

Einfach Nala fragen! Wenn du z.B. eine Datei mit Gartennotizen hochgeladen hast,
frag: *„Was weißt du über Rosendornen?"* oder *„Was steht in meinen Notizen über…?"*

In der Index-Übersicht siehst du außerdem die Anzahl der gespeicherten Abschnitte
und welche Dateien bereits indiziert wurden.

### Index leeren

Wenn du alle hochgeladenen Dokumente aus dem Gedächtnis entfernen möchtest,
klicke im Reiter „Gedächtnis / RAG" auf **„Index leeren"**.
Die automatisch gespeicherten Gesprächserinnerungen bleiben davon unberührt.

---

## Der Pacemaker – Warum gibt es ihn?

Whisper (das Spracherkennungs-Programm) läuft in einem Docker-Container.
Wenn du eine Weile nicht mit Nala gesprochen hast, „schläft" der Container ein –
das erste Mal Sprechen dauert dann deutlich länger, weil der Container erst wieder
aufgeweckt werden muss.

Der **Pacemaker** verhindert das: Er schickt alle 4 Minuten ein kurzes, stilles
Signal an Whisper – quasi ein leises „Bist du noch da?" – damit der Container
wach bleibt.

### Wie lange läuft der Pacemaker?

Sobald du das erste Mal mit Nala gesprochen hast, startet der Pacemaker automatisch.
Er läuft standardmäßig **2 Stunden (120 Minuten)** – danach stoppt er, wenn nichts
mehr passiert ist. Sobald du wieder sprichst, startet er erneut.

### Laufzeit einstellen

Wenn du möchtest, dass der Pacemaker kürzer oder länger läuft:

1. Öffne das Hel-Dashboard unter `http://localhost:5000/hel`
2. Klicke auf den Reiter **„Systemsteuerung"**
3. Ändere den Wert im Feld „Pacemaker-Laufzeit (Minuten)"
4. Klicke auf „Speichern"

Die Änderung gilt ab dem nächsten Neustart des Servers.

---

## Zugriff von unterwegs (Tailscale)

Mit Tailscale kannst du Nala auch vom Handy oder einem anderen Computer aus erreichen,
ohne deinen Router konfigurieren zu müssen.

### Einrichtung
1. Tailscale auf deinem Hauptcomputer (dem mit Zerberus) installieren und starten.
2. Tailscale auch auf deinem Handy oder Laptop installieren, mit demselben Account einloggen.
3. In Tailscale siehst du nun die IP oder den Namen deines Hauptcomputers.

### Nala von unterwegs
Öffne im Browser deines Handys oder Laptops:
`http://DEIN-TAILSCALE-GERÄTENAME:5000/nala`

Stelle sicher, dass Zerberus auf dem Hauptcomputer läuft (start.bat aktiv).

---

## Häufige Fragen / Probleme

**Nala antwortet nicht auf Spracheingabe**
→ Läuft Whisper? Whisper ist ein separates Programm auf Port 8002.
  Prüfe die `whisper_url` in deiner `config.yaml`.

**„OPENROUTER_API_KEY not found"**
→ Hast du die `.env`-Datei im Projektordner angelegt?
  Inhalt: `OPENROUTER_API_KEY=sk-or-v1-dein-key-hier`

**Docker-Sandbox funktioniert nicht**
→ Läuft Docker Desktop? Starte es und warte, bis das Symbol in der Taskleiste grün zeigt.
  Falls Fehler wegen Virtualisierung: BIOS prüfen (Intel VT-x / AMD-V aktiviert?).

**Ich komme nicht ins Hel-Dashboard**
→ Das Passwort für Hel steht in deiner `.env`-Datei als `HEL_PASSWORD=...`.
  Falls nicht gesetzt, ist das Default-Passwort leer (einfach Enter drücken).

**Ich habe mein Profil-Passwort vergessen**
→ Öffne `config.yaml` und setze `password_hash: ''` für dein Profil.
  Damit kannst du dich wieder ohne Passwort einloggen und ein neues setzen.

**Der Server startet nicht**
→ Prüfe, ob die venv aktiv ist (`venv\Scripts\activate`) und ob alle Pakete installiert sind
  (`pip install -r requirements.txt`). Detailliertere Fehler erscheinen im Terminal-Fenster.

---

---

## Sentiment-Analyse

Nala analysiert die Stimmung jeder Nachricht automatisch – mit einem **deutschen BERT-Modell**
(`oliverguhr/german-sentiment-bert`), das speziell für deutsche Texte trainiert wurde.

**Das Modell erkennt drei Klassen:**
- `positive` – Freude, Lob, Zustimmung
- `negative` – Frust, Kritik, Unmut
- `neutral` – sachliche oder gemischte Aussagen

**GPU-Support:** Falls eine NVIDIA-GPU vorhanden ist, läuft das Modell auf CUDA – sonst auf der CPU.
Das Modell wird beim ersten Serverstart geladen und für alle weiteren Anfragen gecacht.
Sollte `torch` oder `transformers` nicht installiert sein, gibt Nala graceful `neutral (0.5)` zurück.

**Overnight-Auswertung:** Täglich um 04:30 wertet ein Hintergrund-Job alle Nachrichten der
letzten 24 Stunden aus und schreibt das BERT-Sentiment in die Datenbank. Die Ergebnisse sind
im Hel-Dashboard unter „Metriken" sichtbar (Toggle „BERT Sentiment" im Graph).

---

## Testing (Loki & Fenrir — Patch 93)

Zerberus hat zwei Test-Suiten auf Playwright-Basis:

- **Loki** (`zerberus/tests/test_loki.py`) — methodische E2E-Tests: Login, Chat-Input,
  Navigation, Hel-Dashboard, neuer Patch-91-Metrics-API-Envelope.
- **Fenrir** (`zerberus/tests/test_fenrir.py`) — Chaos-Agent: Prompt-Injection,
  XSS, SQL, Viewport-Wechsel, Rapid-Click.

**Voraussetzung:** Server läuft lokal auf `https://127.0.0.1:5000`, `loki` und
`fenrir` sind in `config.yaml` als Test-Profile angelegt (Patch 93).

```bash
# Beide Suiten mit HTML-Report
venv\Scripts\activate
pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html

# Nur Loki
pytest zerberus/tests/test_loki.py -v

# Nur Fenrir (Chaos)
pytest zerberus/tests/test_fenrir.py -v
```

Reports landen in `zerberus/tests/report/`. Patch 94 hat den Erstlauf gegen den
live Server gefahren — **32 passed, 0 skipped** in 47 s, kein 500er auf den 14
Chaos-Payloads. Patch 100 ergänzt `TestJavaScriptIntegrity` (Playwright
`pageerror`-Listener gegen Hel/Nala): **34 passed** in 54 s.

---

*Zerberus Pro 4.0 – Stand: 2026-04-28, Patch 177 (**Pipeline-Cutover (Feature-Flag)**: `process_update` ist eine 6-Zeilen-Weiche, der bisherige Body lebt als `_legacy_process_update` weiter. Neuer Config-Key `modules.pipeline.use_message_bus` (Default `false`) entscheidet pro Aufruf zwischen Legacy und `handle_telegram_update`. Letztere delegiert komplexe Pfade (Callbacks, Photos, Edited/Channel-Posts, Gruppen-Chats) zurück an Legacy — nur DM-Text läuft durch Adapter + Pipeline. Live-Switch via `--reload` ohne Server-Neustart. **965 passed, 114 deselected, 0 failed in 52s** (P176-Baseline 954 + 11 neue Cutover-Tests).)*

*Zerberus Pro 4.0 – Stand: 2026-04-29, Patch 178 (**Huginn-Selbstwissen + RAG-Integration**: Huginn ruft vor jedem LLM-Call den RAG-Index ab und reichert den System-Prompt mit System-Doku-Chunks an — aber nur welche der Kategorie `system` (Default `modules.telegram.rag_allowed_categories=["system"]`). Persönliche/narrative/Lore-Dokumente werden hart gefiltert, damit ein Fremder über Huginn keine privaten Inhalte zieht. Neue `system`-Kategorie in `_RAG_CATEGORIES` + `CHUNK_CONFIGS`. `docs/huginn_kennt_zerberus.md` indexiert (8 Chunks). Graceful Degradation: RAG-Exception → Fastlane-Fallback. **981 passed, 114 deselected, 0 failed** (P177-Baseline 965 + 16 neue Huginn-RAG-Tests).)*

*Zerberus Pro 4.0 – Stand: 2026-05-01, Patch 188 (**Auto-TTS + FAISS-Migration + Prosodie-Foundation** — Patch 186, Patch 187, Patch 188 zusammen ausgeliefert. Patch 186 — neuer Toggle „Antworten automatisch vorlesen" im Settings-Tab „Ausdruck", localStorage-Key `nala_auto_tts`, Default OFF, kein Backend-Change (edge-tts seit P143). Patch 187 — `scripts/migrate_embedder.py --execute` lokal gefahren, lokale config.yaml auf `use_dual_embedder: true`, Retriever-Code mit sprach-spezifischer Index-Auswahl (`_select_index_and_meta`, `_encode(text, language)`), Backward-Compat über Flag. Patch 188 — neues Modul `zerberus/modules/prosody/` mit `ProsodyConfig` + `ProsodyManager` (Stub bis P189+), Healthcheck im Startup-Banner, auskommentierter Pipeline-Anker in nala.py + legacy.py. Teststand 1119 → 1182 (+63), 0 Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-01, Patch 193 (**Sentiment-Triptychon + Whisper-Enrichment — Phase 4 ABGESCHLOSSEN** — Patch 192 + Patch 193 zusammen ausgeliefert. Patch 192 — Sentiment-Triptychon-UI: drei Chips pro Bubble (BERT 📝 + Prosodie 🎙️ + Konsens 🎯), Sichtbarkeit per Hover/`:active` analog Toolbar-Pattern. Neue Utility `zerberus/utils/sentiment_display.py` mit Mehrabian-Logik und Inkongruenz-Erkennung (🤔 wenn Text positiv aber Stimme negativ). Backend liefert `sentiment: {user, bot}` ADDITIV in `/v1/chat/completions`-Response — OpenAI-Schema bleibt formal kompatibel. 22 Tests. Patch 193 — Whisper-Endpoint Enrichment: `/v1/audio/transcriptions`-Response erweitert (`text` bleibt IMMER für Backward-Compat, optional `prosody` + `sentiment.bert` + `sentiment.consensus`). `/nala/voice` identisch erweitert + zusätzlich named SSE-Events `event: prosody` und `event: sentiment` über `/nala/events`. Fail-open bei BERT-Fehlern. 16 Tests. Teststand 1260 → **1316 passed**, 0 neue Failures, 2 pre-existing unrelated. Phase 4 ist damit komplett — Phase 5 (Nala-Projekte) startet ab P194, siehe `docs/HANDOVER_PHASE_5.md`.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 194 (**Phase 5a #1: Projekte als Entität — Backend** — Tabellen `projects` + `project_files` in `bunker_memory.db` (Decision 1, 2026-05-01: keine separate SQLite-DB), Repo `zerberus/core/projects_repo.py` mit async Pure-Functions, Hel-CRUD-Endpoints `/hel/admin/projects/*` (admin-only via Basic Auth — bewusst nicht unter `/v1/`, das ist Dictate-App-Lane). Soft-Delete via `is_archived`, Cascade per Repo (Models bleiben dependency-frei, keine ORM-Relations). Persona-Overlay als JSON für Decision 3 (Merge-Layer System → User → Projekt). Storage-Konvention `data/projects/<slug>/<sha[:2]>/<sha>` mit Sha-Prefix-Sub-Verzeichnis gegen Hotspot-Ordner. Slug-Generator mit Kollisions-Suffix. Alembic-Migration `b03fbb0bd5e3` idempotent. Lesson dokumentiert: Composite-UNIQUE muss als `__table_args__` im Model stehen, nicht nur als raw `CREATE INDEX` in `init_db`. UI-Tab in Hel folgt in P195. Teststand 1316 → **1365 passed** (+49: 28 Repo + 18 Endpoints + 3 weitere), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 195 (**Phase 5a #1: Hel-UI-Tab "Projekte" — Ziel #1 ABGESCHLOSSEN** — Neuer Tab `📁 Projekte` zwischen Huginn und Links. Liste mit Slug/Name/Updated/Status/Aktionen, Anlegen-Form als Overlay (statt extra Modal-Lib), Edit/Archive/Unarchive/Delete inline, Datei-Liste read-only (Upload kommt P196). Persona-Overlay-Editor: `system_addendum` (Textarea) + `tone_hints` (Komma-Liste, beim Submit zu Array). Slug-Override nur beim Anlegen aktiv (Slug ist immutable per Repo-Vertrag). Lazy-Load via `activateTab('projects') → loadProjects()`. Mobile-first 44px Touch-Targets durchgehend, scrollbare Tabelle mit `overflow-x:auto`-Wrapper. Lösch-Bestätigung mit Wort "UNWIDERRUFLICH" und Hinweis dass Bytes im Storage bleiben. 20 Source-Inspection-Tests in `test_projects_ui.py` (Pattern wie `test_patch170_hel_kosmetik.py`). Teststand 1365 → **1382 passed** (+17 — 20 neue UI-Tests minus 3 in P194 schon mitgezählte), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 197 (**Phase 5a Decision 3: Persona-Merge-Layer aktiviert** — Schließt die Lücke zwischen P194/P195 (Persona-Overlay-Schema + Hel-UI) und der eigentlichen Wirkung im LLM-Call. Aktivierung über Header `X-Active-Project-Id: <int>` am `POST /v1/chat/completions`-Request — keine Schema-Änderung, persistente Auswahl-Spalte ist später trivial nachrüstbar. Neuer Helper `zerberus/core/persona_merge.py` mit `merge_persona(base, overlay, project_slug=None)` als Pure-Function (synchron testbar), `read_active_project_id(headers)` mit Lowercase-Fallback (FastAPI-Headers vs. dict-Case-Sensitivity-Falle), `resolve_project_overlay(project_id, *, skip_archived=True)` als async DB-Schicht. Merge-Reihenfolge laut Decision 3 (System → User → Projekt) — die ersten beiden stecken bereits in `system_prompt_<profile>.json`, P197 hängt nur den Projekt-Block als markierten `[PROJEKT-KONTEXT — verbindlich für diese Session]` an. Verdrahtung VOR `_wrap_persona` (P184), damit der `# AKTIVE PERSONA — VERBINDLICH`-Marker auch das Overlay umschließt — Source-Audit-Test verifiziert die Reihenfolge per Substring-Position. `tone_hints` werden case-insensitive dedupliziert + Whitespace/Nicht-Strings gefiltert. Defensive Behaviors: archivierte Projekte → Overlay übersprungen aber Slug geloggt, unbekannte ID → kein Crash, kaputter Header → ignoriert, leerer Overlay → kein Block, Doppel-Injection-Schutz via Marker-Substring-Check. Telegram bewusst ausgeklammert (Huginn hat eigene Persona-Welt ohne User-Profile). Logging-Tag `[PERSONA-197]` mit `project_id`/`slug`/`base_len`/`project_block_len`. 33 neue Tests in `test_persona_merge.py` (12 Pure-Function-Edge-Cases + 7 Header-Reader + 5 async DB + 4 End-to-End mit Mock-LLM + 5 Source-Audit). Teststand 1431 → **1464 passed**, 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 196 (**Phase 5a #4: Datei-Upload-Endpoint + UI** — Multipart-Upload `POST /hel/admin/projects/{id}/files` und `DELETE /hel/admin/projects/{id}/files/{file_id}` mit SHA-Dedup-Schutz (Bytes bleiben liegen, wenn der `sha256` woanders noch referenziert wird — Schutz vor versehentlichem Cross-Project-Delete). Validierung an drei Achsen: Filename-Sanitize (Path-Traversal `..` strippen, Backslash → `/`, leading-slash weg), Extension-Blacklist (`.exe`, `.bat`, `.cmd`, `.com`, `.msi`, `.dll`, `.scr`, `.sh`, `.ps1`, `.vbs`, `.jar`), 50 MB Default-Limit. Atomic Write via `tempfile.mkstemp` im Ziel-Ordner + `os.replace` verhindert halbe Files nach Server-Kill. UI: Drop-Zone in der Detail-Card ersetzt den P195-Platzhalter, Drag-and-Drop UND klickbarer File-Picker (multiple), pro Datei eigene Progress-Zeile per `XMLHttpRequest.upload.progress` (sequenzieller Upload, nicht `Promise.all`), Lösch-Button pro Listeneintrag mit Confirm. Neue `ProjectsConfig` in `core/config.py` mit Defaults im Pydantic-Modell (config.yaml ist gitignored). Drei neue Repo-Helper (`count_sha_references`, `is_extension_blocked`, `sanitize_relative_path`). 49 neue Tests verteilt auf `test_projects_files_upload.py` (17 Endpoint-Tests) + `test_projects_repo.py` (21 Helper-Tests) + `test_projects_ui.py` (11 UI-Source-Tests). Teststand 1382 → **1431 passed** (+49), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 199 (**Phase 5a #3: Projekt-RAG-Index — Ziel #3 ABGESCHLOSSEN** — Jedes Projekt bekommt einen eigenen, isolierten Vektor-Index unter `data/projects/<slug>/_rag/{vectors.npy, meta.json}` — der globale RAG-Index in `modules/rag/router.py` bleibt unberührt. Damit kann das LLM beim aktiven Projekt (P197 `X-Active-Project-Id`-Header) auf Inhalte aus den Projektdateien zugreifen, ohne dass projekt-spezifische Chunks den globalen Memory-Index verschmutzen. **Pure-Numpy-Linearscan statt FAISS** — Per-Projekt-Indizes sind klein (~10-2000 Chunks), `argpartition` auf einem `(N, 384)`-Array ist auf der Größenordnung schneller als FAISS-Setup-Overhead und macht Tests dependency-frei. Embedder: MiniLM-L6-v2 (384 dim) als Default, lazy-loaded; Tests monkeypatchen `_embed_text` mit einem Hash-basierten 8-dim-Pseudo-Embedder. Chunker-Reuse: Code-Files via `code_chunker.chunk_code` (P122), Prosa via lokalem Para-Splitter (max 1500 Zeichen, snap an Doppel-Newline). Idempotenz: pro `file_id` höchstens ein Chunk-Set im Index — Re-Index entfernt alten Block zuerst. Trigger-Punkte: Upload-Endpoint, `materialize_template` (Templates sind sofort retrievbar), Delete-File, Delete-Project (löscht ganzen `_rag/`-Ordner) — alle best-effort, Indexing-Fehler brechen Hauptpfad NICHT ab. Wirkung im Chat: in `legacy.py::chat_completions` NACH P197/P184/P185/P118a/P190, aber VOR `messages.insert(0, system)` — Block beginnt mit `[PROJEKT-RAG — Kontext aus Projektdateien]`, Top-K Hits in Markdown-Sektionen mit `relevance=...`-Score. Feature-Flags `ProjectsConfig.rag_enabled` (Default `True`), `rag_top_k` (Default 5), `rag_max_file_bytes` (Default 5 MB — drüber: skip). Defensive Behaviors: leere/binäre/zu-große Dateien → skip mit `reason`-Code; inkonsistenter/kaputter Index → leere Basis, nächster Index-Call baut sauber auf; Dim-Mismatch → leeres Ergebnis statt Crash. Logging-Tag `[RAG-199]`. 46 neue Tests in `test_projects_rag.py` (5 Prose-Splitter + 5 Chunk-File + 5 Top-K + 4 Save-Load + 2 Remove-Index + 7 Index-File + 2 Remove-File + 4 Query + 2 Format-Block + 3 End-to-End + 1 Materialize-Indexes + 6 Source-Audit). Teststand 1487 → **1533 passed** (+46), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 198 (**Phase 5a #2: Template-Generierung beim Anlegen — Ziel #2 ABGESCHLOSSEN** — Ein neu angelegtes Projekt startet nicht mehr leer: `create_project_endpoint` materialisiert `ZERBERUS_<SLUG>.md` (Projekt-Bibel mit Sektionen "Ziel"/"Stack"/"Offene Entscheidungen"/"Dateien"/"Letzter Stand", analog ZERBERUS_MARATHON_WORKFLOW.md) plus `README.md` (kurze Prosa mit Name + Description). Inhalt rendert Project-Daten ein (Name, Slug, Description, Anlegedatum). Neuer Helper `zerberus/core/projects_template.py`: `render_project_bible` + `render_readme` als Pure-Functions (synchron, deterministisch via `now`-Parameter), `template_files_for` als Komposit, `materialize_template(project, base_dir, *, dry_run=False)` als async DB+Storage-Schicht. Templates landen im SHA-Storage (`<projects.data_dir>/projects/<slug>/<sha[:2]>/<sha>` — gleiche Konvention wie P196-Uploads), DB-Eintrag in `project_files` mit lesbarem `relative_path` — damit erscheinen Templates nahtlos in der Hel-Datei-Liste, im RAG-Index (P199) und in der Code-Execution-Pipeline (P200). Idempotenz via Existenz-Check vor Schreiben (User-Inhalte werden NICHT überschrieben — wenn der User schon eine eigene README hochgeladen hat, kommt nur die fehlende Bibel neu dazu). Best-Effort-Verdrahtung: Crash beim Materialisieren bricht das Anlegen NICHT ab (Projekt-Eintrag steht, Templates lassen sich notfalls nachgenerieren). Feature-Flag `ProjectsConfig.auto_template: bool = True` (Default `True`, Default IM Pydantic-Modell weil `config.yaml` gitignored). Git-Init bewusst weggelassen — SHA-Storage ist kein Working-Tree, `git init` ergibt erst Sinn mit echtem Workspace-Layout (P200). Pure-Python-String-Templates (kein Jinja, weil Bedarf trivial). Logging-Tag `[TEMPLATE-198]` mit `slug`/`path`/`size`/`sha[:8]`. 23 neue Tests in `test_projects_template.py` (6 Pure-Function-Edge-Cases + 6 Materialize-Cases inkl. Idempotenz/Dry-Run/User-Content-Schutz + 3 End-to-End inkl. Flag-on/off/Crash-Resilienz + 3 Source-Audit), `disable_auto_template`-Autouse-Fixture in `test_projects_endpoints.py` + `test_projects_files_upload.py` hält deren File-Counts stabil. Teststand 1464 → **1487 passed** (+23), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-01, Patch 191 (**Prosodie-Pipeline komplett** — Patch 189, Patch 190, Patch 191 zusammen ausgeliefert. Patch 189 — `GemmaAudioClient` mit Dual-Path (CLI via `llama-mtmd-cli` Subprocess JETZT, Server via `llama-server` `input_audio` sobald llama.cpp Issue #21868 gemergt), zentraler Prompt in `prompts.py`, `ProsodyConfig` erweitert um `mmproj_path`/`server_url`/`llama_cli_path`/`n_gpu_layers`/`timeout_seconds`, robustes JSON-Parsing mit Stub-Fallback. Patch 190 — `/nala/voice` und `/v1/audio/transcriptions` führen Whisper + Gemma parallel via `asyncio.gather(return_exceptions=True)` aus, neuer `injector.py` injiziert Prosodie-Block (Stimmung/Tempo/Valenz/Arousal + optional Inkongruenz-Warnung) hinter den System-Prompt von `/v1/chat/completions` über `X-Prosody-Context`-Header. Patch 191 — Frontend-Toggle „Sprachstimmung analysieren" im Settings-Tab „Ausdruck" + 🎭-Indikator neben Mikrofon, Header `X-Prosody-Consent: true`, neuer Hel-Admin-Endpoint `GET /hel/admin/prosody/status` mit reinen Aggregaten (Worker-Protection: keine individuellen Mood/Valence-Werte herausgegeben, Audio-Bytes leben nur im Request-Scope, KEIN Schreiben in `interactions`-Tabelle, tmp-Datei wird im `finally` per `unlink` entsorgt). Modelle: `google_gemma-4-E2B-it-Q4_K_M.gguf` (3.4 GB) + `mmproj-google_gemma-4-E2B-it-bf16.gguf` (940 MB) unter `C:\Users\chris\models\gemma4-e2b\`. Teststand ~1182 → ~1265 (+83: 34 P189 + 24 P190 + 25 P191), 0 Failures.)*
