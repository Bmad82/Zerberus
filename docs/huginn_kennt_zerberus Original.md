# Zerberus — Systemwissen für Huginn

Dieses Dokument ist das Selbstwissen des Zerberus-Systems. Es wird via RAG indexiert und ermöglicht dem aktiven LLM, fundierte Auskünfte über die Architektur, Fähigkeiten und den aktuellen Stand des Systems zu geben.

---

## Was ist Zerberus?

Zerberus ist eine selbstgehostete, multi-user KI-Assistentenplattform. Sie läuft auf einem Windows-Desktop mit FastAPI/Python als Backend und bietet mehrere Frontends: **Nala** (Web-Chat für Endnutzer), **Hel** (Admin-Dashboard), und **Huginn** (Telegram-Bot). Das System kombiniert Sprachverarbeitung, Textanalyse, semantische Suche und einen mehrstufigen Sicherheitslayer zu einem kohärenten Ganzen.

Der Name stammt aus der nordischen Mythologie — Zerberus ist der Wächter. Das Projekt folgt der **Kintsugi-Philosophie**: Zerbrochenes wird mit Gold repariert. Die Risse sind kein Makel, sondern Teil der Geschichte.

---

## Architektur-Überblick

### Die Pipeline

Jede Nachricht durchläuft eine klar definierte Pipeline:

**Input → Sanitizer → Intent-Router → Policy-Layer → LLM-Call → Guard → Output-Router → Antwort**

1. **Input-Sanitizer** (`input_sanitizer.py`): Regelbasierte Prüfung auf Injection-Patterns, Unicode-Normalisierung (NFKC), Zeichensatz-Validierung. Erkennt bekannte Jailbreak-Muster, ChatML-Token-Injection, und Prompt-Leak-Versuche. Findings werden geloggt, nicht geblockt — der Guard entscheidet final.

2. **Intent-Router** (`router.py`): Das LLM klassifiziert jede Nachricht via JSON-Header mit Intent und Effort-Score. Verfügbare Intents: CHAT, FRAGE, KREATIV, CODE, RECHERCHE, ZUSAMMENFASSUNG, ÜBERSETZUNG, ANALYSE, FILE, SYSTEM, META, HILFE, ERINNERUNG, WETTER, MATHE, GEDICHT, BRAINSTORM, SMALLTALK, PHILOSOPHIE, WISSEN, EMPFEHLUNG, VERGLEICH, PLANUNG, REFLEXION, FEEDBACK, HUMOR, und weitere.

3. **Policy-Layer**: Transport-spezifische Regeln (Telegram vs. Nala vs. intern). Steuert Persona, Formatierung, Sicherheitsstufe.

4. **LLM-Call**: Aktuell über OpenRouter an **DeepSeek V3.2** (Hauptmodell). Der System-Prompt definiert die Persona und ist über Hel editierbar.

5. **Guard** (`hallucination_guard.py`): **Mistral Small 3** als unabhängige Sicherheitsinstanz. Prüft die LLM-Antwort auf Halluzinationen, unangemessene Inhalte, und Sicherheitsverstöße. Verdicts: OK, WARNUNG (mit Hinweis), SKIP, ERROR. Der Guard läuft asynchron und ist fail-open konfiguriert (bei Ausfall wird die Antwort trotzdem ausgeliefert, um Verfügbarkeit zu gewährleisten).

6. **Output-Router**: Entscheidet basierend auf Intent und Antwortlänge, ob die Antwort als Text oder als Datei (Markdown/Code) gesendet wird.

### Message-Bus

Seit Patch 173-177 existiert ein **Message-Bus** der die Pipeline transport-agnostisch macht. Telegram (Huginn), Web-Chat (Nala), und zukünftige Transports docken alle an denselben Bus an. Die Pipeline-Konfiguration läuft über Feature-Flags.

---

## Frontends

### Nala (Web-Chat)
- Erreichbar über Tailscale unter `desktop-rmuhi55.tail79500e.ts.net:5000`
- Chat-Interface mit Echtzeit-Antworten
- Unterstützt Spracheingabe via Whisper
- Kintsugi-Design (Gold-auf-Dunkel Ästhetik)
- Mobile-optimiert (Touch-Gesten, responsive Layout)
- Personalisierbare Farben und Personas pro User

### Hel (Admin-Dashboard)
- Erreichbar unter `/hel/` auf demselben Server
- RAG-Dokumentenverwaltung (Upload, Indexierung, Löschung)
- System-Prompt-Editor für Huginn
- Metriken: Sentiment-Verlauf, Themen-Extraktion, Antwortzeiten
- Guard-Statistiken und Kosten-Tracking (OpenRouter-Balance)
- User-Verwaltung und Konfiguration

### Huginn (Telegram-Bot)
- Norse: Huginn ist einer von Odins zwei Raben (Gedanke)
- Long-Polling-basiert (kein Webhook nötig)
- Unterstützt Einzel- und Gruppenchats
- Gruppen-Intelligenz: Huginn reagiert nur auf relevante Nachrichten in Gruppen
- Persona: Zynischer, allwissender Rabe mit trockenem Humor
- Aufwands-Kalibrierung: Bei hohem Effort-Score wird Huginn sarkastisch und fragt nach
- HitL (Human-in-the-Loop): Bei kritischen Aktionen (Effort ≥ 5) fragt Huginn beim Admin nach Bestätigung
- Datei-Output: Kann lange Antworten als Markdown-Dateien senden
- Rate-Limiting: 10 Nachrichten pro Minute pro User, Ausgangs-Throttle 15 msg/min/Chat
- Sprachnachrichten werden via Whisper transkribiert und wie Text verarbeitet

---

## Sicherheitsarchitektur

### Fünf Schichten

1. **Schicht 1 — Deterministisch/Blocking**: Input-Sanitizer (Regex-basiert). Blockiert bekannte Injection-Patterns vor dem LLM-Call. Unicode NFKC-Normalisierung gegen Homoglyph-Angriffe.

2. **Schicht 2 — Deterministisch/Logging**: Zeichensatz-Prüfung, Längenlimits, Chat-Typ-Filter, Update-Type-Validierung. Unbekannte Telegram-Update-Typen werden ignoriert.

3. **Schicht 3 — Sandbox**: Docker-Container mit `--network=none`, `--memory=256m`, `--cpus=0.5`, `--read-only`. Timeout nach 30 Sekunden. Blocklist für destruktive Patterns (`rm -rf`, `format`, etc.).

4. **Schicht 4 — LLM-Guard**: Mistral Small 3 prüft jede Antwort. Fail-open: Bei Guard-Ausfall wird die Antwort trotzdem geliefert. Kein Bypass durch hohen Effort-Score möglich — Guard ist unabhängig von der Persona.

5. **Schicht 5 — Audit-Trail**: Vollständiges Logging aller Interaktionen mit Tags (`[GUARD-120]`, `[SANITIZE-162]`, `[RATELIMIT-163]`, `[HUGINN-168]`). SQLite WAL-Mode für performantes Logging.

### Guard-Details
- **Modell:** Mistral Small 3 (24B Parameter, via OpenRouter)
- **Aufgabe:** Prüft die LLM-Antwort auf Halluzinationen, faktenwidrige Behauptungen, unangemessene Inhalte
- **Konfiguration:** `guard_fail_policy: allow` (Verfügbarkeit > Sicherheit im Heimgebrauch)
- **Latenz:** Ein Cloud-Roundtrip (der Input-Guard ist regelbasiert und lokal, kein zweiter Cloud-Call)
- **Stresstests:** 31 Cases in `test_guard_stress.py` (T01-T16 offline, T17-T25 live gegen Mistral)
- **Bekannte Grenzen:** Erkennt keine erfundenen Telefonnummern (halluzinierte Bürgeramt-Nr.), Persona-Bypass wird nicht als solcher geflaggt

### Callback-Spoofing-Schutz
- HitL-Buttons validieren ob der klickende User auch der anfragende User ist
- `requester_user_id` wird bei Erstellung des HitL-Tasks gespeichert
- Fremde Klicks auf ✅/❌ werden mit Popup abgelehnt

---

## RAG (Retrieval-Augmented Generation)

### Wie es funktioniert
- **FAISS** als Vektordatenbank (lokal, kein Cloud-Service)
- **Dual-Embedder**: Deutsche und englische Embeddings gleichzeitig
- **Spracherkennung**: Automatische DE/EN-Erkennung per Wortlisten-Frequenz (kein langdetect-Dependency)
- **Code-Chunker**: AST-basiert für Python, Regex-basiert für JS/TS, Tag-basiert für HTML, Regel-basiert für CSS, Statement-basiert für SQL, Key-basiert für JSON/YAML
- **Chunk-Hygiene**: Minimum 50, Maximum 2000 Zeichen. Context-Header pro Chunk (Datei + Position)
- **Cross-Encoder Reranker** für verbesserte Relevanz (MiniLM Bi-Encoder allein zu schwach für deutsche Eigennamen)

### Indexierte Dokumente
Über das Hel-Dashboard können Dokumente hochgeladen und indexiert werden. Unterstützte Formate: `.md`, `.txt`, `.pdf`, `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.html`, `.css`, `.scss`, `.sql`, `.yaml`, `.yml`, `.json`. Jedes Dokument wird automatisch gechunkt, embedded, und in den FAISS-Index aufgenommen. Einzelne Dokumente können gelöscht werden (Soft-Delete, physische Bereinigung beim Reindex).

---

## Whisper (Sprachverarbeitung)

- **Modell:** OpenAI Whisper, läuft in einem dedizierten Docker-Container auf Port 8002
- **GPU:** Nutzt die RTX 3060 (CUDA)
- **Timeout:** 120 Sekunden (konfigurierbar), Connect-Timeout 10 Sekunden
- **Short-Audio-Guard:** Dateien unter 4096 Bytes werden als zu kurz abgelehnt
- **Retry:** 1 Retry bei Timeout, 2 Sekunden Backoff
- **Watchdog:** Automatischer Container-Restart bei Unresponsiveness
- Whisper-Transkriptionen werden als Text weiterverarbeitet. Die Audio-Rohdatei wird NICHT gespeichert — nur das Transkript.

---

## Sentiment-Analyse

- **Modell:** `oliverguhr/german-sentiment-bert` (lokal, kein Cloud-Call)
- **Aufgabe:** Erkennt die emotionale Färbung von Nachrichten (positiv/negativ/neutral)
- **Verwendung:** Metrik-Tracking über Zeit, Themen-Extraktion via spaCy (`de_core_news_sm`)
- **Dashboard:** Sentiment-Verlauf sichtbar in Hel

---

## Hardware & Infrastruktur

| Komponente | Details |
|---|---|
| **CPU** | AMD Ryzen 7 2700X (8 Kerne, 16 Threads) |
| **RAM** | 32 GB DDR4 |
| **GPU** | NVIDIA RTX 3060 12GB VRAM |
| **OS** | Windows (mit WSL2 für Docker) |
| **Netzwerk** | Tailscale (Magic DNS, HTTPS via Tailscale-Certs) |
| **LLM-Routing** | OpenRouter (Cloud-API für DeepSeek, Mistral, Claude, etc.) |
| **Lokale Modelle** | Ollama auf Port 8003 (Guard: Mistral Small 3) |
| **Whisper** | Docker-Container auf Port 8002 |
| **Datenbank** | SQLite mit async SQLAlchemy (WAL-Mode) |
| **Webserver** | Uvicorn mit `--reload` |

### VRAM-Belegung (typisch)
- Whisper: ~3 GB
- Sentence-Transformers (Embeddings): ~0.5 GB
- Sentiment-BERT: ~0.5 GB
- Guard (Mistral Small 3 via Ollama): ~4 GB bei lokaler Ausführung
- Freier Headroom: ~3-4 GB

---

## Aktiver Tech-Stack

| Schicht | Technologie |
|---|---|
| **Backend** | Python 3.11, FastAPI, Uvicorn |
| **Datenbank** | SQLite + async SQLAlchemy |
| **Vektorsuche** | FAISS (CPU/GPU) |
| **Embeddings** | Sentence-Transformers (MiniLM + Cross-Encoder) |
| **NLP** | spaCy (`de_core_news_sm`), German Sentiment BERT |
| **Sprache** | Whisper (Docker) |
| **LLM** | DeepSeek V3.2 via OpenRouter (Hauptmodell) |
| **Guard** | Mistral Small 3 via OpenRouter |
| **Telegram** | python-telegram-bot (Long-Polling) |
| **Netzwerk** | Tailscale (P2P VPN, Magic DNS) |
| **Container** | Docker (Whisper, zukünftig Sandbox) |
| **Versionierung** | Git + GitHub |

---

## Mythologische Namen im System

| Name | Rolle | Mythologie |
|---|---|---|
| **Zerberus** | Gesamtplattform | Griechisch: Wächter der Unterwelt |
| **Nala** | User-Frontend (Web-Chat) | Benannt nach einer Katze — Jojos Katze |
| **Hel** | Admin-Dashboard | Nordisch: Herrscherin über die Unterwelt |
| **Huginn** | Telegram-Bot | Nordisch: Odins Rabe des Gedankens |
| **Rosa** | Sicherheitslayer (Corporate) | Codename, kein mythologischer Bezug |
| **Heimdall** | Ingress-Security für Rosa | Nordisch: Wächter der Bifröst-Brücke |
| **Guard** | Antwort-Prüfer (Mistral Small 3) | Allgemein: Wächter |
| **Loki** | E2E-Tester (geplant) | Nordisch: Trickster |
| **Fenrir** | Chaos-Tester (geplant) | Nordisch: Der Chaoswolf |
| **Vidar** | Validierungstester (geplant) | Nordisch: Der schweigsame Rächer |
| **Sancho Panza** | Veto-Layer für Code-Execution (geplant) | Cervantes: Sancho Panzas Stimme der Vernunft |
| **Ratatoskr** | Docs-Mirror-Repo | Nordisch: Das Eichhörnchen das Nachrichten überbringt |

---

## Projektstand

### Aktueller Patch: 177
### Tests: 965 grün
### Phase: 4 (Huginn-Roadmap v2 abgeschlossen)

### Was ist fertig (Auswahl wichtiger Meilensteine)
- Komplette Chat-Pipeline (Input → LLM → Guard → Output)
- RAG mit Code-Chunker und Dual-Embedder-Infrastruktur
- Telegram-Bot Huginn mit Gruppen-Intelligenz, HitL, Datei-Output
- Input-Sanitizer mit Unicode-Normalisierung und 12/16 Erkennungsrate
- Guard-Stresstests (31 Cases)
- Whisper-Integration mit Timeout-Hardening und Watchdog
- Sentiment-Analyse und Metriken
- Nala UI mit Kintsugi-Design, Bubble-Shine, Collapse-Funktion
- Hel Dashboard mit RAG-Management, System-Prompt-Editor, Metriken
- Message-Bus-Pipeline (transport-agnostisch)
- Rate-Limiting und Graceful Degradation
- Settings-Cache mit `@invalidates_settings`
- Startup-Cleanup (sauberes Terminal-Output mit 5 Sektionen)

### Was kommt als nächstes
- FAISS-Migration auf Dual-Embedder
- Prosodie (Audio-Sentiment via Gemma 4 E2B, lokal)
- Nala Projekte (Code-Execution-Plattform mit Docker-Sandbox, Multi-Agent, Sancho-Panza-Veto)
- Rosa/Heimdall (Corporate Security Layer — letzter Schritt)

---

## Entwicklungsprozess

Zerberus wird von **Chris** (Architekt, kein Coder) orchestriert. Die Implementierung erfolgt durch **Claude Code ("Coda")** auf dem lokalen Rechner. Der Workflow:

1. Chris bespricht Features und Architektur mit dem **Supervisor** (Claude, diese Instanz in Conversations)
2. Der Supervisor generiert einen präzisen **Patch-Prompt** als `.md`-Datei
3. **Coda** (Claude Code CLI) implementiert den Patch auf dem lokalen Rechner
4. Chris testet und meldet Ergebnisse
5. Nach jedem Patch: Docs aktualisieren, GitHub pushen, Ratatoskr syncen

Jeder Patch ist atomar — keine Mega-Patches. Ein Patch, ein Feature, ein Commit.

---

## Konfiguration

Die Hauptkonfiguration liegt in `config.yaml` (gitignored, da Secrets). Wichtige Sektionen:

- `llm`: Modellwahl, API-Keys, Temperatur
- `guard`: Guard-Modell, fail_policy, Schwellwerte
- `whisper`: Timeouts, Retry-Settings, Min-Audio-Bytes
- `telegram`: Bot-Token, Admin-Chat-ID, Rate-Limits
- `rag`: Chunk-Größen, Embedder-Wahl, Reindex-Schedule
- `security`: Input-Sanitizer-Modus, Guard-Fail-Policy

Sensible Daten (`.env`, `config.yaml`, `*.key`, `*.crt`, `bunker_memory.db`) sind alle in `.gitignore`.

---

## Für Gäste und Interessierte

Zerberus ist ein persönliches Projekt mit professionellem Anspruch. Es ist kein Spielzeug, sondern ein funktionsfähiges System das täglich genutzt wird. Die Architektur ist so designed, dass sie von einem Heimserver bis zu einem Unternehmenskontext skalieren kann — der Rosa/Heimdall-Layer ist der Schalter dafür.

Fragen zum System? Einfach fragen. Huginn weiß Bescheid.
