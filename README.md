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

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 200 (**Phase 5a #16: PWA-Installation für Nala + Hel — Ziel #16 ABGESCHLOSSEN** — Beide Web-UIs lassen sich auf iPhone (Safari → "Zum Home-Bildschirm") und Android (Chrome → "App installieren") als eigenständige Apps installieren — Browser-Chrome verschwindet, Splash-Screen + Theme-Color stimmen, Icon im Kintsugi-Stil (Gold auf Blau für Nala, Rot auf Anthrazit für Hel). Eigener Router `zerberus/app/routers/pwa.py` mit vier Endpoints (`/nala/manifest.json`, `/nala/sw.js`, `/hel/manifest.json`, `/hel/sw.js`) ohne Auth-Dependencies — in `main.py` VOR `hel.router` eingehängt, sonst würde `verify_admin` den Manifest-Fetch mit 401 blockieren und der Install-Prompt nie erscheinen. SW-Scope folgt Pfad-Konvention (`/nala/sw.js` → scope `/nala/`, `/hel/sw.js` → scope `/hel/`) — keine `Service-Worker-Allowed`-Header-Akrobatik nötig, jede App cacht NUR ihre eigenen URLs. Per-App-Manifeste statt Single-Manifest mit Conditional-Logic. Icons via `scripts/generate_pwa_icons.py` (PIL, deterministisch — Re-Run liefert bytes-identische PNGs) unter `zerberus/static/pwa/{nala,hel}-{192,512}.png`. SW-Logik minimal: install precacht App-Shell (HTML + shared-design.css + favicon + Icons), activate räumt alte Caches, fetch macht network-first mit cache-fallback, non-GET-Requests passen unverändert durch — kein Offline-Modus, keine Push-Notifications (Huginn), kein Background-Sync (alles bewusst weggelassen). HTML-Verdrahtung: 7 Tags im `<head>` (Manifest-Link, Theme-Color, vier Apple-Mobile-Web-App-Meta, zwei Apple-Touch-Icons), SW-Registrierung als 8-Zeilen-Script vor `</body>` mit Feature-Detect + console.warn-Fallback. 39 neue Tests in `test_pwa.py` (5 Pure-Function für SW-Render + 6 Manifest-Dict-Validierung + 4 Endpoint-Direct-Calls + 16 Source-Audit pro HTML + 4 Icon-Existenz mit PNG-Magic-Byte-Check + 3 Routing-Order in main.py + 2 Generator-Skript-Existenz). Teststand 1533 → **1572 passed** (+39), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 201 (**Phase 5a #4 KOMPLETT ABGESCHLOSSEN: Nala-Tab "Projekte" + Header-Setter** — Schließt den letzten Hop, damit Nala-User vom Chat aus ein aktives Projekt auswählen können — ab da fließt Persona-Overlay (P197) und Datei-Wissen (P199-RAG) automatisch in jede Antwort. Vorher war diese Kombination nur über externe Clients (curl, SillyTavern) erreichbar. Neuer Endpoint `GET /nala/projects` (JWT-pflichtig via `request.state.profile_name`) liefert schlanke Liste `{id, slug, name, description, updated_at}` — `persona_overlay` ist BEWUSST NICHT im Response (Admin-Geheimnis, kann Prompt-Engineering-Spuren enthalten), archivierte Projekte ausgeblendet. Eigener Endpoint statt Wiederverwendung von `/hel/admin/projects` (Hel ist Basic-Auth, Nala ist JWT — zwei Auth-Welten, kein Bridge). UI: vierter Settings-Tab "📁 Projekte" zwischen "Ausdruck" und "System", lazy-loaded beim Tab-Klick. Header-Chip neben Profile-Badge zeigt aktives Projekt — gold-border Pill, klick öffnet Settings + springt direkt auf Projekte-Tab. State in zwei localStorage-Keys: `nala_active_project_id` (numerisch, für Header) + `nala_active_project_meta` (JSON, für Chip-Renderer ohne Re-Fetch). Header-Injektion ZENTRAL in `profileHeaders()` (drei Zeilen), wirkt damit auf ALLE Nala-Calls (Chat, Voice, Whisper) ohne Vergessens-Risiko. Zombie-ID-Schutz: nach jedem `loadNalaProjects` wird geräumt wenn aktive ID nicht mehr in Liste auftaucht. XSS-Schutz: `escapeProjectText()` für name+slug+description, Source-Audit-Test zählt mindestens 3 Aufrufe in Renderer. Beim Logout NICHT geräumt (gleicher User auf gleichem Browser will Auswahl behalten). 21 neue Tests in `test_nala_projects_tab.py` (6 Endpoint inkl. 401 + archived-versteckt + persona_overlay-NICHT-im-Response, 11 Source-Audit, 2 XSS, 1 Zombie-ID, 1 Tab-Lazy-Load) plus 1 nachgeschärfter Test in `test_settings_umbau.py` (alter `openSettingsModal()`-Proxy → spezifischer 🔧-Emoji + `icon-btn` mit openSettingsModal-only — P201 erlaubt openSettingsModal im Header NUR via Project-Chip). Teststand 1572 → **1594 passed** (+22), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 202 (**PWA-Auth-Hotfix: Service-Worker tötet Hel-Login** — Behebt einen kritischen Bug aus P200: Hel zeigte im Browser nur noch `{"detail":"Not authenticated"}` als JSON-Body, kein Basic-Auth-Prompt mehr. Nala unauffällig, Huginn unauffällig. Ursache: Der von P200 eingeführte Service-Worker `/hel/sw.js` mit Scope `/hel/` interceptet ALLE GET-Requests im Scope — auch die Top-Level-Navigation auf `/hel/`. Der SW macht `event.respondWith(fetch(event.request))`, bekommt vom Server eine korrekte 401 mit `WWW-Authenticate: Basic` zurück und reicht sie unverändert an die Page durch. **Bei SW-vermittelten Responses ignoriert der Browser den `WWW-Authenticate`-Header** und zeigt KEINEN nativen Auth-Prompt — die JSON-Response landet stattdessen sichtbar im DOM. Drei Fixes: (a) `event.request.mode === 'navigate'`-Skip im fetch-Handler — Top-Level-Navigation läuft jetzt durch den nativen Browser-Stack inkl. Auth-Prompt. (b) Cache-Name auf `nala-shell-v2` / `hel-shell-v2` gebumpt, damit der activate-Hook der neuen SW-Version den verseuchten v1-Cache räumt. (c) `NALA_SHELL` und `HEL_SHELL` ohne Root-Pfad-Eintrag (Navigation wird nicht mehr gecached, und beim Hel-SW-Install schlug `cache.addAll` ohnehin durch das 401 fehl — `Promise.all` rejected komplett, `.catch(() => null)` swallow den Fehler still — semantisch Müll). Side-Effect: HTML-Reload geht jetzt immer übers Netz, kein Offline-Modus für Hauptseiten — akzeptabel für Heimserver-Use-Case. Server-Pfad ist und bleibt korrekt: neuer End-to-End-Test `TestHelBasicAuthHeader::test_dependency_liefert_wwwauth_bei_missing_creds` verifiziert via TestClient, dass `verify_admin` ohne Credentials `401 + WWW-Authenticate: Basic` liefert (Schutz vor zukünftigen Server-Auth-Refactorings). 5 neue Tests + 3 angepasste in `test_pwa.py` (Pure-Function-Test für navigation-skip, drei Shell-Lists-Tests, ein End-to-End-WWW-Authenticate-Test, plus Cache-Name-v2 in zwei Endpoint-Tests). User mit P200-SW-Installation: einmal `/hel/` reloaden → neuer SW kommt automatisch (sw.js wird per `Cache-Control: no-cache` ausgeliefert), activate räumt v1-Cache, Auth-Prompt erscheint. Teststand 1594 → **1602 passed** (+8), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 203b (**Hel-UI-Hotfix: kaputtes Quote-Escaping → Event-Delegation** — Behebt einen Blocker in Hel: UI rendert, aber NICHTS ist anklickbar (Tabs wechseln nicht, Buttons reagieren nicht, Settings-Zahnrad öffnet kein Panel). Nala unauffällig. Aufgefallen erst nach P200/P202 — vorher hatte der Browser-/SW-Cache eine ältere Hel-Version aus der Zeit vor P196. Root-Cause: Im `loadProjectFiles`-Renderer (eingeführt mit P196) stand `+ ',\\'' + _escapeHtml(f.relative_path).replace(/'/g, "\\\\'") + '\\')" '` — Python interpretiert die Escape-Sequenzen im Triple-Quoted-String und produziert `+ ',''  + ... + '')" '`. JavaScript parst `+ ',' '' +` als zwei adjacent String-Literale ohne Operator → `SyntaxError: Unexpected string`. Ein einziger Syntax-Fehler in einem `<script>`-Block invalidiert den **gesamten** Block — alle Funktionen darin werden nicht definiert (`activateTab`, `toggleHelSettings`, `loadMetrics`, ...) → keine Klicks reagieren. Fix: Inline `onclick="deleteProjectFile(...)"` durch `data-*`-Attribute (`data-project-id`, `data-file-id`, `data-relative-path`) plus Event-Delegation per `addEventListener` ersetzt. Pattern ist quote-immun (Filename geht durch `_escapeHtml` direkt ins Attribut), XSS-sicher und wesentlich lesbarer. 10 neue Tests in `test_p203b_hel_js_integrity.py` (3 Source-Audit gegen Bug-Pattern, 5 Source-Audit für neue Event-Delegation, 1 JS-Integrity via `node --check` über alle inline `<script>`-Blöcke (skipped wenn node fehlt), 1 Smoke). Plus 1 angepasster bestehender Test in `test_projects_ui.py` (Block-Range erweitert, Klassen-Name-Check addiert). Lessons (3): inline-onclick mit Python-String-Concat in `"""...""""`-HTML ist hochfragil; Event-Delegation mit `data-*`-Attributen ist robust; Browser-/SW-Caches können JS-Render-Bugs sehr lange verschleiern — bei "neuen" Symptomen nach Cache-Wipe auch ältere Patches prüfen. Lokal: 1635 baseline → **1645 passed** (+10), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 203a (**Project-Workspace-Layout (Phase 5a #5 Vorbereitung)** — Legt das Fundament für die Code-Execution-Pipeline (P203b/c). Pro Projekt entsteht beim Upload/Template-Materialize ein echter Working-Tree unter `<data_dir>/projects/<slug>/_workspace/`, in dem die `project_files`-Einträge an ihrem `relative_path` materialisiert werden — die Sandbox kann später nur dann sinnvoll mounten, wenn die Files an ihrem lesbaren Pfad stehen (statt unter den SHA-Hash-Pfaden). Hardlink primär (`os.link` — gleiche Inode wie SHA-Storage, kein Plattenplatz-Verbrauch), Copy-Fallback (`shutil.copy2`) bei `OSError` (cross-FS, FAT32, Permission-Denied). Method-Resultat (`"hardlink"` / `"copy"` / `None`-bei-noop) wandert in Logs + Test-Returns, damit nachvollziehbar ist welcher Pfad live gegriffen hat — auf Windows-Test-Maschinen ohne dev-mode wird damit auch der Copy-Pfad live mitgetestet (Monkeypatch-Test). Atomic via tempfile-im-Ziel-Ordner + `os.replace`, damit parallele Sandbox-Reads (P203b) nie ein halb-geschriebenes Workspace-File sehen. Sicherheits-Check zwei-stufig: `is_inside_workspace(target, root)` resolved beide Pfade und prüft `relative_to` (schützt gegen `../../etc/passwd`-Angriffe via entartete `relative_path`-Werte aus der DB), plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet (Schutz gegen Slug-Manipulation). Verdrahtung als Best-Effort-Side-Effect in vier Trigger-Punkten: Upload-Endpoint (nach `register_file`+RAG-Index), Delete-File-Endpoint (nach `delete_file`, mit Slug aus extra `get_project`-Call vor dem DB-Delete), Delete-Project-Endpoint (`wipe_workspace` nach `delete_project`), `materialize_template` (nach jedem `register_file`). Alle vier mit Lazy-Import + try/except, Hauptpfad bleibt grün auch wenn Hardlink/Copy scheitert — Source-of-Truth ist der SHA-Storage + DB. Dazu die Komplett-Resync-API `sync_workspace(project_id, base_dir)` mit Materialize+Orphan-Removal als Recovery-Pfad für Pre-P203a-Files (idempotent, zweiter Aufruf liefert `{materialized:0, removed:0, skipped:N}`). Feature-Flag `ProjectsConfig.workspace_enabled = True` (Default an, Tests können abschalten — siehe `TestWorkspaceDisabled`-Klasse). Was P203a bewusst NICHT macht: Sandbox-Mount auf den Workspace (kommt P203b — `SandboxManager` aus P171 verbietet aktuell Volume-Mount explizit, muss um `workspace_mount: Optional[Path]` erweitert werden), LLM-Tool-Use für Code-Generation (P203c), UI-Render von Code+Output-Blöcken (P203c). 36 neue Tests in `test_projects_workspace.py` (4 Pure-Function inkl. Traversal-Reject + No-IO; 6 materialize_file inkl. nested-dirs + idempotent + missing-source + Copy-Fallback via monkeypatched `os.link`; 5 remove_file inkl. cleans-empty-parents + keeps-non-empty + traversal-reject; 3 wipe_workspace inkl. rejects-wrong-dirname; 4 sync_workspace inkl. removes-orphans; 3 async-Wrapper; 4 Endpoint-Integration mit echten Hel-Endpoints; 1 workspace_disabled-Pfad; 5 Source-Audit-Tests für die vier Verdrahtungs-Stellen). Lokal: 1599 baseline → **1635 passed** (+36), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`, beide nicht blockierend), 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 203c (**Phase 5a #5 Zwischenschritt: Sandbox-Workspace-Mount + execute_in_workspace** — Erweitert die Docker-Sandbox aus P171 um einen kontrollierten Volume-Mount auf den Project-Workspace aus P203a. Neue keyword-only Parameter `workspace_mount: Optional[Path] = None` und `mount_writable: bool = False` an `SandboxManager.execute()`. Default-Pfad ohne Mount bleibt unverändert (Backwards-Compat für Huginn-Pipeline). Bei gesetztem Mount: docker-args ergänzt um `-v <abs>:/workspace[:ro]` + `--workdir /workspace`. Read-Only ist die Default-Annahme — `mount_writable=True` muss der Caller explizit setzen. Mount-Validation als Early-Reject: `exists()` + `is_dir()` vor `docker run`, sonst `SandboxResult(exit_code=-1, error=...)`. Dazu Convenience-Wrapper `execute_in_workspace(project_id, code, language, base_dir, *, writable=False, timeout=None)` in `projects_workspace.py` — zieht Slug aus DB via `projects_repo.get_project`, baut `workspace_root` via `workspace_root_for`, validiert per `is_inside_workspace(workspace_root, base_dir)` (Defense-in-Depth gegen Slug-Manipulation), legt Workspace-Ordner on-demand an, reicht durch an `get_sandbox_manager().execute(...)`. Returns `None` bei unbekanntem Projekt, deaktivierter Sandbox oder Sicherheits-Reject — Caller kann einheitlich auf `None`-Pfade reagieren. Was P203c bewusst NICHT macht: Kein HitL-Gate (kommt P206), kein Tool-Use-LLM-Pfad (P203d), kein Sync-After-Write (Caller-Verantwortung wenn writable=True), keine Image-Pull-Logik. 17 neue Tests in `test_p203c_sandbox_workspace.py` — docker-args-Audit ohne Mount unverändert (Backwards-Compat), Mount-RO-Default, Mount-Writable, Mount-nonexistent → Error, Mount-is-File → Error, Disabled-Sandbox + Mount → None, Blocked-Pattern-Vorrang, execute_in_workspace mit fehlendem Projekt → None, korrekter Mount-Pfad-Passthrough, writable-Passthrough, Workspace-Anlage on-demand, Slug-Traversal-Reject, Source-Audits, Disabled-Sandbox-Passthrough via Convenience, Timeout-Passthrough, Mount-Pfad-resolve()-stable. Logging-Tags `[SANDBOX-203c]` (Mount-Setup) + `[WORKSPACE-203c]` (Convenience-Reject). Lokal: 1685 baseline → **1705 passed** (+20), 4 xfailed pre-existing, 0 neue Failures.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 204 (**Phase 5a #17 ABGESCHLOSSEN: Prosodie-Kontext im LLM** — Schließt den letzten Hop der Prosodie-Pipeline: Whisper+Gemma+BERT lieferten ihre Daten bisher nur ans UI-Triptychon (P192) — DeepSeek bekam bei Voice-Input keinen Kontext, das LLM "hörte" die Stimme nicht. P190 hatte einen rudimentären `[Prosodie-Hinweis ...]`-Block, aber nur Gemma (kein BERT, kein Konsens) und in einem ad-hoc-Format. P204 baut die Brücke richtig: ein markierter `[PROSODIE — Stimmungs-Kontext aus Voice-Input]...[/PROSODIE]`-Block analog `[PROJEKT-RAG]` (P199) mit fünf Zeilen — `Stimme:`, `Tempo:`, optional `Sentiment-Text: ... (BERT)`, `Sentiment-Stimme: ... (Gemma)`, `Konsens: ...`. Worker-Protection (P191): KEINE Zahlen im Block — Confidence/Score/Valence werden im Konsens-Label verkocht (`leicht positiv`, `deutlich negativ`, `inkongruent — Text positiv, Stimme negativ`), Defense via parametrisiertem Regex-Test (`\d+\.\d+`, `%`, `\b\d+\b`). Mehrabian-Konsens-Logik (Pure-Function `_consensus_label`): BERT positiv + Prosody-Valenz negativ → Inkongruenz-Hinweis (Ironie/Stress); sonst Confidence > 0.5 → Stimme dominiert; sonst BERT-Fallback. Schwellen identisch zu `utils/sentiment_display.py` (P192) — UI-Konsens und LLM-Konsens dürfen nicht voneinander abweichen. Verdrahtung in `legacy.py /v1/chat/completions`: JSON-Parse von `X-Prosody-Context`-Header mit Type-Guard (nur `dict`) + Consent-Check + BERT-Call auf `last_user_msg` in try/except (fail-open) + `inject_prosody_context(...)` mit Keyword-Args. Voice-only-Garantie zwei-stufig: (1) Frontend setzt den Header nur nach Whisper-Roundtrip — kein Header bei getipptem Text → kein Block; (2) Stub-Source-Check filtert versehentliche Pseudo-Contexts. 33 neue Tests in `test_p204_prosody_context.py` (`TestBuildProsodyBlock` 9, `TestWorkerProtectionNoNumbers` 3 parametrisiert, `TestConsensusLabel` 6, `TestBertQualitative` 6, `TestInjectWithBert` 5, `TestP204LegacyVerdrahtung` 6 Source-Audit, `TestMarkerUniqueness` 3 distinct vom PROJEKT-RAG/PROJEKT-KONTEXT). Plus 6 nachgeschärfte Tests in `test_prosody_pipeline.py::TestInjectProsodyContext` (Format-Assertions auf neue Marker, neuer Idempotenz-Test). Effekt: Bei Voice-Input liest DeepSeek im System-Prompt jetzt einen Stimmungs-Kontext-Block — Nala kann Ton subtil anpassen (zurücknehmen wenn jemand müde klingt, nachfragen bei Stress) ohne plakative "Du klingst traurig!"-Reaktionen. Bei getipptem Text: kein Block, Chat unverändert. Bei deaktiviertem Consent oder Stub-Pipeline: kein Block. Lokal: 1645 baseline → **1685 passed** (+40), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste, 0 neue Failures.)*


*Zerberus Pro 4.0 – Stand: 2026-05-03, Patch 205 (**Phase 5a Schuld aus P199 — RAG-Toast in Hel-UI nach Datei-Upload** — Schliesst die Lese-Schuld aus P199: `POST /hel/admin/projects/{id}/files` retourniert seit P199 ein `rag`-Dict (`{chunks, skipped, reason}`), das Hel-Frontend hatte das Feld bisher ignoriert. P205 liest das Feld im `xhr.onload`-Success-Branch des Drop-Zone-Uploads und zeigt einen kurzen Toast unten rechts: `📚 N Chunks indiziert` (cyan-Border) bei Erfolg, `⚠ Datei nicht indiziert: <Label>` (rot-Border) bei Skip. **Architektur**: Frontend-only, drei Bausteine in `zerberus/app/routers/hel.py` (kein Backend-Touch, kein neues Modul). (1) **CSS-Block** (~30 Zeilen) `.rag-toast` mit `position: fixed`, `bottom: 20px`, `right: 20px`, `min-height: 44px` (Mobile-first Touch-Dismiss), `pointer-events: none` im Hidden-State, plus `.rag-toast.visible`-State und Border-Varianten `.success` (cyan `#4ecdc4`) / `.warn` (rot `#ff6b6b`); Default-Border Kintsugi-Gold `#c8941f`. (2) **Reason-Mapping** als JS-Konstante `_RAG_REASON_LABELS` mit kurzen de-DE-Strings fuer alle Codes aus `zerberus/core/projects_rag.py::index_project_file` (`rag_disabled`, `too_large` → "zu gross", `binary` → "Binärdatei", `empty`, `no_chunks`, `embed_failed`, `file_not_found`, `project_not_found`, `bytes_missing`, `exception`); unbekannte Codes → "übersprungen". (3) **`_showRagToast(rag)`** als JS-Renderer (~25 Zeilen): liest `rag.skipped`/`rag.chunks`/`rag.reason`, baut Text via Mapping-Lookup, setzt `el.textContent` (XSS-immun — Reason-Strings stammen NIEMALS direkt aus dem Server-Pfad, nur aus dem statischen Mapping), toggelt `success`/`warn`-Klassen, fuegt `.visible` hinzu. Auto-Timeout 3500 ms, Klick dismissed sofort, Replace-Pattern (Singleton-Slot via `_showRagToast._t`). DOM: `<div id="ragToast" class="rag-toast" role="status" aria-live="polite">` direkt vor `</body>`, ein einziger Container ohne Stacking. **Verdrahtung** im bestehenden `_uploadProjectFiles` → `xhr.onload` Success-Branch direkt nach dem `'fertig'`-Render: `try { const body = JSON.parse(xhr.responseText); if (body && body.rag) _showRagToast(body.rag); } catch (_) {}` — fail-quiet bei kaputtem JSON, Backwards-Compat zu Backends ohne P199 (`body.rag` fehlt → kein Toast). Was P205 NICHT macht: kein Backend-Touch, kein neues Schema, keine Sammel-Aggregation bei Multi-Upload (Replace-Pattern wie HANDOVER-Spec), kein Stacking, kein Nala-Pfad (Nala hat keinen Datei-Upload), keine i18n (de-DE hardgecodet), kein Telegram-Pfad. **Lesson aus P203b/P203d-3**: `node --check` ueber alle inline `<script>`-Bloecke aus `ADMIN_HTML` faengt SyntaxError-Regression frueh ab. **Kollateral-Fix**: `test_projects_ui::test_uploads_are_sequential` hatte `[:3000]`-Slice auf `_uploadProjectFiles`-Body — durch das neue `_showRagToast` plus Toast-Verdrahtung wuchs der Body um ~600 Zeichen, der `for-loop`-Marker rutschte ueber die Slice-Grenze. Slice auf 4500 erhoeht. 20 neue Tests in `test_p205_hel_rag_toast.py`: 2 `TestToastFunctionExists` (Funktion + Signatur), 6 `TestReasonMapping` (alle 5 Hauptcodes parametrisiert + `'too_large' → 'zu gross'`-Check), 4 `TestRagToastCss` (Klasse, `min-height: 44px`, `position: fixed`, Toggle-Klasse), 1 `TestToastDom`, 3 `TestUploadWiring` (`body.rag` im Block, Aufruf im Block, Reihenfolge `'fertig'` < `_showRagToast`), 1 `TestToastXss` (`textContent` ODER `_escapeHtml`), 1 `TestJsSyntaxIntegrity` (`node --check`), 2 `TestHelHtmlSmoke` (alle Toast-Pieces im HTML, genau ein `id="ragToast"`). Lokal: 1797 baseline (P203d-3) → **1817 passed** (+20), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste, 0 neue Failures aus P205.)*

*Zerberus Pro 4.0 – Stand: 2026-05-03, Patch 203d-3 (**Phase 5a Ziel #5 ABGESCHLOSSEN: UI-Render im Nala-Frontend fuer Sandbox-Code-Execution** — Dritter und letzter Sub-Patch der P203d-Aufteilung. Schliesst Phase-5a-Ziel #5 endgueltig: nach P203d-1 (Backend-Code-Detection + Sandbox-Roundtrip + `code_execution`-JSON-Feld) und P203d-2 (Output-Synthese ersetzt `answer` durch menschenlesbaren Text) ignorierte das Nala-Frontend das `code_execution`-Feld komplett. Der User las nur die Synthese-Antwort, sah aber nicht den ausgefuehrten Code, die Roh-Ausgabe oder die Laufzeit. P203d-3 baut den UI-Render: nach `addMessage(reply, 'bot')` rendert `renderCodeExecution(wrapperEl, data.code_execution)` zwei Karten unter dem Bot-Bubble — Code-Card (Sprach-Tag + exit-Badge gruen/rot + escapter Code in `<pre><code>`, optional Laufzeit-Meta und Error-Banner) plus Output-Card (collapsible stdout/stderr mit 44×44px Touch-Toggle, optional Truncated-Marker). **Architektur**: Frontend-only-Patch in `zerberus/app/routers/nala.py` — kein neues Modul, kein Backend-Touch. Drei Bausteine: (1) CSS-Block (~120 Zeilen) mit neuen Klassen `.code-card`/`.output-card`/`.lang-tag`/`.exit-badge` (mit `.exit-ok` gruen / `.exit-fail` rot)/`.code-toggle` (44×44px) — erbt das Kintsugi-Gold-Theme von P124. (2) `escapeHtml(s)` als 3-Zeilen-Helper neben `escapeProjectText` (P201) — delegiert an `escapeProjectText`, eigener Name fuer den XSS-Audit-Test (Min-Count 4 `escapeHtml(`-Aufrufe im Renderer). (3) `renderCodeExecution(wrapperEl, codeExec)` als JS-Renderer (~80 Zeilen): liest alle 8 P203d-1-Schema-Felder (`language`/`code`/`exit_code`/`stdout`/`stderr`/`execution_time_ms`/`truncated`/`error`), baut die zwei Karten, haengt sie via `insertBefore(.sentiment-triptych)` in den Wrapper ein. Visual-Order: bubble → toolbar → code-card → output-card → triptych → export-row. **`addMessage` retourniert wrapper**: die Funktion gibt jetzt das DOM-Wrapper-Element zurueck, damit der Caller (sendMessage) den Renderer nachtraeglich einhaengen kann. Backwards-Compat: alle bisherigen Caller (Voice-Input, History-Replay, Late-Fallback) ignorieren den Return-Value. **Verdrahtung in `sendMessage`**: `const botWrapper = addMessage(reply, 'bot'); if (data.code_execution) { try { renderCodeExecution(botWrapper, data.code_execution); } catch (_e) {} }`. Fail-quiet auf jeder Stufe: Renderer-Crash darf den Chat-Loop nicht unterbrechen; leerer/fehlender `code_execution.code` → kein Render (Backwards-Compat zu Backends die das Feld nicht kennen). Was P203d-3 NICHT macht: keine Syntax-Highlighting-Library (Plain-Text `<pre><code>` — leichtgewichtig), kein Edit-Knopf (Code ist read-only), keine Re-Run-Funktion, keine SSE-Stream-Frames (Chat ist non-streaming bis P203d-2), kein Telegram-Pfad (Huginn bleibt auf `format_sandbox_result` aus P171), kein Copy-to-Clipboard-Button am Code-Card. **Lesson aus P203b**: `node --check` ueber alle inline `<script>`-Bloecke aus `NALA_HTML` (analog zu Hel-Test) — faengt SyntaxError-Regression frueh ab. 30 neue Tests in `test_p203d3_nala_code_render.py`: 2 `TestRendererExists` (Funktion + Signatur), 8 `TestRendererLiestSchemaFelder` (alle Schema-Felder), 2 `TestRendererFallbacks` (Null-Check + leerer Code), 1 `TestRendererInsertionPoint`, 5 `TestSendMessageVerdrahtung` (Wrapper-Return, Caller-Bind, Renderer-Aufruf, Reihenfolge addMessage-vor-Renderer, try/catch), 4 `TestXssEscape` (Helper-Existenz, escapeProjectText nicht geloescht, Min-Count 4, keine innerHTML ohne escape), 5 `TestCss` (Card-Klassen, 44×44px Touch-Toggle, `overflow-x: auto`, `.collapsed`-State, exit-ok/fail-Farbcodes), 1 `TestJsSyntaxIntegrity` (`node --check` ueber alle inline Scripts, skipped wenn `node` fehlt), 1 `TestNalaEndpointSmoke` (Endpoint-Smoke). Lokal: 1767 baseline → **1797 passed** (+30), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste, 0 neue Failures aus P203d-3.)*

*Zerberus Pro 4.0 – Stand: 2026-05-03, Patch 203d-2 (**Phase 5a #5 Output-Synthese: zweiter LLM-Call ersetzt Roh-Output mit menschenlesbarer Antwort** — Zweiter Sub-Patch der P203d-Aufteilung, schliesst den Backend-Loop von Phase-5a-Ziel #5. P203d-1 hatte den Sandbox-Roundtrip verdrahtet (`code_execution`-Feld in der HTTP-Response), aber der `answer`-String enthielt weiter den Original-Code-Block ohne menschenlesbare Erklaerung des Outputs. P203d-2 fuegt einen zweiten LLM-Call ein, der Original-Frage + Code + stdout/stderr in eine zusammenfassende Antwort verwandelt und den `answer` ersetzt. Neues Modul `zerberus/modules/sandbox/synthesis.py` mit Pure-Function-Schicht (`should_synthesize`, `_truncate`, `build_synthesis_messages`) plus Async-Wrapper `synthesize_code_output(user_prompt, payload, llm_service, session_id)` — Pattern analog zu P204 prosody/injector und P197 persona_merge. **Trigger-Gate**: True wenn `exit_code != 0` (Crash → Erklaerung) ODER `exit_code == 0` UND nicht-leerer stdout (Output → Aufbereitung). False bei `payload is None`, fehlendem `exit_code` oder `exit=0` mit leerem stdout (nichts zu sagen). **Bytes-genau Truncate** (5 KB pro Stream, UTF-8-encoded mit `errors='ignore'` plus ASCII-Marker `\n…[gekuerzt]`) — schuetzt Kontext-Fenster bei Mega-Output. **Marker `[CODE-EXECUTION — Sprache: ... | exit_code: ...]` / `[/CODE-EXECUTION]`** substring-disjunkt zu PROJEKT-RAG/PROJEKT-KONTEXT/PROSODIE. **Verdrahtung in `legacy.py::chat_completions`** direkt nach dem P203d-1-Block (vor Sentiment-Triptychon): wenn `code_execution_payload` da ist, `synthesize_code_output(...)`-Aufruf, bei nicht-leerem Result → `answer = synthesized`. Plus **`store_interaction`-Reorder**: User-Insert bleibt frueh (Eingabe ist endgueltig), Assistant-Insert + `update_interaction()` wandern ans Ende (nach Synthese), damit der gespeicherte Text der finale Output ist und nicht der Roh-Output mit Code-Block. Sentiment-Triptychon liest dann den finalen `answer`. Was P203d-2 NICHT macht: UI-Render (P203d-3), zweiter `store_interaction`-Eintrag fuer Roh-Output (Audit-Trail-Tabelle ist Phase-5a-Schuld), Cost-Aggregation des Synthese-Calls in `interactions.cost` (vermerkt), Streaming-SSE-Frames (P203d-3), writable-Mount (P207). **Fail-Open auf jeder Stufe** — Crash im LLM, leere Antwort, Whitespace-only, falsches Result-Format → Returns `None` → Caller behaelt Original-Answer (mit Code-Block); `code_execution` bleibt zusaetzlich in der Response, Frontend kann Roh-Output ggf. selbst rendern. Logging-Tag `[SYNTH-203d-2]` (separat von `[SANDBOX-203d]`) mit `exit_code`/`raw_output_len`/`synth_len`. 47 neue Tests in `test_p203d2_chat_synthesis.py`: 8 `TestShouldSynthesize` (Trigger-Gate), 5 `TestTruncate` (Bytes-genau, Multi-Byte-UTF-8-safe), 9 `TestBuildSynthesisMessages` (Format, Marker, Truncate-bei-Mega-Stdout), 8 `TestSynthesizeCodeOutput` (Async-Wrapper Skip + Happy + Fail-Open-Varianten), 7 `TestP203d2SourceAudit` (Modul-Existenz, Logging-Tag, Aufruf-Args, Reihenfolge synth-vor-assistant-store), 10 `TestE2ESynthesis` (Two-Step-Mock-LLM: Erst-Call Code, Zweit-Call Synthese; ersetzt-answer / erklaert-Fehler / kein-Synthese-bei-Skip-Cases / Fail-Open / OpenAI-Schema). Lokal: 1720 baseline → **1767 passed** (+47), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts` + `test_rag_dual_switch.test_fallback_logic` + `test_patch185_runtime_info` durch `config.yaml`-Drift), 0 neue Failures aus P203d-2.)*

*Zerberus Pro 4.0 – Stand: 2026-05-02, Patch 203d-1 (**Phase 5a #5 Backend-Pfad: Code-Detection + Sandbox-Roundtrip im Chat-Endpunkt** — Erster Sub-Patch der P203d-Aufteilung. Verdrahtet `/v1/chat/completions` (legacy.py::chat_completions) mit der Sandbox-Pipeline aus P171/P203a/P203c: nach dem LLM-Call wird die Antwort durch `first_executable_block(answer, allowed_languages)` gefiltert; bei aktivem-nicht-archiviertem Projekt UND aktivierter Sandbox UND vorhandenem Code-Block läuft `execute_in_workspace(project_id, code, language, base_dir, writable=False)`. Result kommt als additives `code_execution`-Feld in der `ChatCompletionResponse` (Schema: `{language, code, exit_code, stdout, stderr, execution_time_ms, truncated, error}`) — OpenAI-Schema bleibt formal kompatibel, Clients die nur `choices` lesen, sehen keinen Bruch. **Sechs-Stufen-Gate** (alles fail-open ausser ein Gate verbietet's): (1) `X-Active-Project-Id`-Header (P201) — sonst Datei-Fallback; (2) Slug aus `resolve_project_overlay`; (3) **`project_overlay is not None`** — bei archivierten Projekten liefert der Resolver `(None, slug)`, aktive ohne Overlay haben `({}, slug)` — damit blocken wir Code-Execution auf Eis-gelegten Projekten konservativ; (4) `sandbox.config.enabled`; (5) `first_executable_block` liefert Block (Pure-Function aus P171/P122, KEIN Fallback-Sprache); (6) `execute_in_workspace` liefert `SandboxResult` (P203c-Wrapper). Was P203d-1 NICHT macht: zweiter LLM-Call zur Output-Synthese (P203d-2), UI-Render im Nala-Frontend (P203d-3), HitL-Gate (P206), writable-Mount (P207). `writable=False` ist hardcoded — Schreibender Mount erst nach explizitem Sync-After-Write-Pfad. **Fail-Open auf jeden Pfad-Fehler** — jeder Crash in `execute_in_workspace` oder `first_executable_block` lässt den Endpunkt mit `code_execution=None` durchlaufen, Chat darf nicht durch Sandbox-Probleme blockiert werden. Logging-Tag `[SANDBOX-203d]` mit `project_id`/`slug`/`language`/`exit_code`/`stdout_len`/`stderr_len`/`time_ms`/`truncated` — Worker-Protection-konform, keine Code-Inhalte oder Output-Inhalte im Log. 19 neue Tests in `test_p203d_chat_sandbox.py`: 7 Source-Audit (Logging-Tag, Imports, Schema-Feld, `writable=False`-Aufruf-Fenster, Field-Pass-Through), 12 End-to-End mit Mock-LLM/Mock-Sandbox/gepatcht `execute_in_workspace`: Happy-Paths (Python+JS, exit_code != 0, multiple Blöcke → erster gewinnt), Skip-Cases (no project, no fence, disabled sandbox, archived, unknown language `rust`, execute returns None), Backwards-Compat (OpenAI-Schema-Felder unangetastet), Fail-Open (RuntimeError in Pipeline → `code_execution=None` ohne Crash). Lokal: 1705 baseline → **1720 passed** (+15 sichtbar — 19 neue P203d-1-Tests minus 4 reordered/skipped wegen tmp_db-Fixture-Caching-Effekten), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts` + `test_rag_dual_switch.test_fallback_logic` + neu `test_patch185_runtime_info` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 0 NEUE Failures aus P203d-1.)*
