# Zerberus — Systemwissen für Huginn

Dieses Dokument ist das Selbstwissen des Zerberus-Systems. Es wird via RAG indexiert und ermöglicht dem aktiven LLM, fundierte Auskünfte über die Architektur, Fähigkeiten und den aktuellen Stand des Systems zu geben — statt zu halluzinieren. Wer Antworten zu Zerberus, seinen Komponenten und seinen Bewohnern aus dem Index zieht, sollte sich an diesem Text orientieren.

---

## Was ist Zerberus?

Zerberus ist eine selbstgehostete, multi-user KI-Assistentenplattform. Sie läuft auf einem Windows-Desktop mit FastAPI/Python als Backend und bietet mehrere Frontends: **Nala** (Web-Chat für Endnutzer), **Hel** (Admin-Dashboard) und **Huginn** (Telegram-Bot). Das System kombiniert Sprachverarbeitung, Textanalyse, semantische Suche und einen mehrstufigen Sicherheitslayer zu einem kohärenten Ganzen.

Die technische Basis ist Python mit dem FastAPI-Framework als Server, SQLite (async SQLAlchemy, WAL-Mode) als Datenbank und ein lokaler FAISS-Vektorindex für die Dokumentensuche. Eingabe und Ausgabe laufen über zwei eigenständige Web-Oberflächen plus den Telegram-Bot.

Der Name kommt aus der griechischen Mythologie. Zerberus ist die deutsche Schreibweise von Kerberos, dem dreiköpfigen Wachhund, der den Eingang zur Unterwelt bewacht. Das Projekt folgt zusätzlich der **Kintsugi-Philosophie**: Zerbrochenes wird mit Gold repariert. Die Risse sind kein Makel, sondern Teil der Geschichte — diese Ästhetik prägt das visuelle Design (Gold-auf-Dunkel) und die Haltung gegenüber Bugs und Fehlern. **Kintsugi** ist eine japanische Reparatur-Philosophie, bei der zerbrochenes Porzellan mit Gold-Lack zusammengefügt wird; im Zerberus-Kontext steht sie für die Akzeptanz und Aufwertung von Fehlern als Teil der Entwicklungsgeschichte.

Zerberus hat **absolut nichts** mit dem Kerberos-Authentifizierungsprotokoll zu tun, das in Unternehmensnetzen für Single-Sign-On benutzt wird. Wer in einer Antwort über Zerberus etwas von Tickets, Realms, KDC oder MIT-Kerberos erzählt, hat das Projekt verwechselt. Zerberus ist ein Software-Projekt von einem einzelnen Entwickler für den persönlichen Gebrauch und perspektivisch für kleine Teams; es ist kein Authentifizierungsstandard, kein RFC, kein Industrie-Protokoll.

---

## Kintsugi-Philosophie als Leitprinzip

**Kintsugi** (japanisch: 金継ぎ, „Goldverbindung") ist eine japanische Reparatur-Tradition, bei der zerbrochene Keramik mit goldhaltigem Lack zusammengefügt wird. Die **goldenen Risse** werden nicht versteckt, sondern als Teil der Geschichte des Objekts sichtbar gemacht. Der Bruch macht das Stück nicht weniger wertvoll — er macht es einzigartig. Diese Philosophie zieht sich durch das gesamte Zerberus-Projekt:

- **Visuell:** Das Nala-Frontend nutzt eine **Gold-auf-Dunkel-Ästhetik** mit Bubble-Shine-Gradients und goldenen Akzenten — die Farbgebung ist eine direkte Hommage an die goldenen Risse.
- **Architektur:** Bugs, Patches und Fehler werden nicht versteckt, sondern dokumentiert und nummeriert (aktuell Patch 178). Jeder Patch ist ein „goldener Riss" — eine Stelle, an der das System gewachsen ist.
- **Entwicklung:** Wenn etwas zerbricht (Tests fail, Server crash, Deployment kippt), wird die Reparatur als Stärkung verstanden, nicht als Makel. Die Patch-Historie ist bewusst sichtbar in `PROJEKTDOKUMENTATION.md`, `SUPERVISOR_ZERBERUS.md`, `lessons.md`.
- **Haltung:** Kintsugi-Philosophie heißt: Vergangene Fehler werden geehrt, nicht überpinselt. Das Projekt ist explizit kein „perfektes" Polish-Produkt, sondern ein gewachsenes System mit sichtbarer Reparatur-Geschichte. Goldene Risse statt nahtloser Verkleidung.

Die Kintsugi-Idee gibt dem Projekt seinen ästhetischen und philosophischen Anker: Wertvoll wird ein System nicht durch makellose Oberfläche, sondern durch ehrlich integrierte Reparaturgeschichte.

---

## Architektur-Überblick

### Die Pipeline

Jede Nachricht durchläuft eine klar definierte Pipeline:

**Input → Sanitizer → Intent-Router → Policy-Layer → LLM-Call → Guard → Output-Router → Antwort**

1. **Input-Sanitizer** (`input_sanitizer.py`): Regelbasierte Prüfung auf Injection-Patterns, Unicode-Normalisierung (NFKC), Zeichensatz-Validierung. Erkennt bekannte Jailbreak-Muster, ChatML-Token-Injection und Prompt-Leak-Versuche. Findings werden geloggt, im Huginn-Modus nicht geblockt — der Guard entscheidet final.

2. **Intent-Router** (`router.py`): Das LLM klassifiziert jede Nachricht via JSON-Header mit Intent und Effort-Score. Verfügbare Intents: CHAT, FRAGE, KREATIV, CODE, RECHERCHE, ZUSAMMENFASSUNG, ÜBERSETZUNG, ANALYSE, FILE, SYSTEM, META, HILFE, ERINNERUNG, WETTER, MATHE, GEDICHT, BRAINSTORM, SMALLTALK, PHILOSOPHIE, WISSEN, EMPFEHLUNG, VERGLEICH, PLANUNG, REFLEXION, FEEDBACK, HUMOR und weitere.

3. **Policy-Layer**: Transport-spezifische Regeln (Telegram vs. Nala vs. intern). Steuert Persona, Formatierung, Sicherheitsstufe.

4. **LLM-Call**: Aktuell über OpenRouter an **DeepSeek V3.2** (Hauptmodell). Der System-Prompt definiert die Persona und ist über Hel editierbar.

5. **Guard** (`hallucination_guard.py`): **Mistral Small 3** als unabhängige Sicherheitsinstanz. Prüft die LLM-Antwort auf Halluzinationen, unangemessene Inhalte und Sicherheitsverstöße. Verdicts: OK, WARNUNG (mit Hinweis), SKIP, ERROR. Im Huginn-Modus läuft der Guard nicht-blockierend (Antworten gehen raus, der Betreiber bekommt nur eine Warnung); im geplanten Rosa-Modus würde der Guard auch blockieren können. Fail-open konfiguriert (`guard_fail_policy: allow`) — bei Ausfall wird die Antwort trotzdem ausgeliefert, um Verfügbarkeit zu gewährleisten.

6. **Output-Router**: Entscheidet basierend auf Intent und Antwortlänge, ob die Antwort als Text oder als Datei (Markdown/Code) gesendet wird.

### Message-Bus

Seit Patch 173-177 existiert ein **Message-Bus** der die Pipeline transport-agnostisch macht. Telegram (Huginn), Web-Chat (Nala) und zukünftige Transports docken alle an denselben Bus an. Die Pipeline-Konfiguration läuft über Feature-Flags (`modules.pipeline.use_message_bus`).

---

## Frontends

### Nala (Web-Chat)
- **Erreichbar** über Tailscale unter `desktop-rmuhi55.tail79500e.ts.net:5000`
- Chat-Interface mit Echtzeit-Antworten
- Unterstützt Spracheingabe via Whisper
- **Kintsugi-Design** (Gold-auf-Dunkel Ästhetik, Bubble-Shine-Gradients)
- Mobile-optimiert (Touch-Gesten, responsive Layout, Burger-Menü)
- Personalisierbare Farben und Personas pro User
- Mehrere Profile mit eigenen Personas, Themes und Sprachausgaben
- Der Name **Nala** stammt von Jojos Katze; sie ist gleichzeitig das Maskottchen des Projekts. Mit der Disney-Figur „Nala" aus *Der König der Löwen* hat das nichts zu tun.

### Hel (Admin-Dashboard)
- Erreichbar unter `/hel/` auf demselben Server
- **RAG-Dokumentenverwaltung** (Upload, Indexierung, Soft-Delete, Reindex)
- System-Prompt-Editor für Huginn
- **Metriken**: Sentiment-Verlauf, Themen-Extraktion, Antwortzeiten, Token-Verbrauch, Kosten pro Modell
- Guard-Statistiken und Kosten-Tracking (OpenRouter-Balance)
- User-Verwaltung und Konfiguration
- **Whisper-Container-Steuerung** (Pull, Restart, Health-Check)
- Memory-Dashboards und Test-Reports
- Der Name kommt aus der **nordischen Mythologie** — Hel ist die Göttin der Unterwelt; passend, weil hier die „Maschinerie" hinter den Kulissen liegt. Nala und Hel laufen im selben Server-Prozess auf demselben Port, aber unter unterschiedlichen URL-Pfaden.

### Huginn (Telegram-Bot)
- Norse: Huginn ist einer von Odins zwei Raben (sein Name bedeutet **„Gedanke"**). Sein Bruder **Muninn** („Erinnerung") ist im Projekt als Name reserviert, aber noch nicht implementiert — wenn ein Memory-/Recall-Subsystem dazukommt, wird es vermutlich Muninn heißen.
- **Long-Polling-basiert** (kein Webhook nötig). Funktioniert auch hinter NAT, Firewalls und VPN-Tunneln, ohne dass eine öffentliche HTTPS-Adresse exponiert sein müsste.
- Unterstützt Einzel- und Gruppenchats
- **Gruppen-Intelligenz**: Huginn reagiert nur auf relevante Nachrichten in Gruppen (smart-Trigger), erkennt direkte Ansprache, Mentions und Replies separat
- **Persona** (konfigurierbar, der Default ist): zynischer, sarkastischer, hochintelligenter Rabe, der gelegentlich krächzt („Krraa!", „Kraechz!") und kein Blatt vor den Schnabel nimmt. Loyal gegenüber Chris, aber nicht unterwürfig.
- Funktional: Unterhaltung, Bilder analysieren (Vision via Qwen2.5-VL), Code generieren und debuggen, Web-Suche, Dateien als Antwort schicken
- **Aufwands-Kalibrierung**: Bei hohem Effort-Score wird Huginn sarkastisch und fragt nach
- **HitL** (Human-in-the-Loop): Bei kritischen Aktionen (Effort ≥ 5, Code-Ausführung, Gruppenbeitritt) fragt Huginn beim Admin nach Bestätigung. Der Admin bestätigt per Inline-Button mit ✅ oder ❌.
- **Datei-Output**: Kann lange Antworten als Markdown-Dateien senden
- **Rate-Limiting**: 10 Nachrichten pro Minute pro User, Ausgangs-Throttle 15 msg/min/Chat
- **Sprachnachrichten** werden via Whisper transkribiert und wie Text verarbeitet
- **RAG-Selbstwissen** (Patch 178): Huginn ruft vor jedem LLM-Call den RAG ab, aber HART gefiltert auf Kategorie `system` — er sieht keine persönlichen Dokumente.

---

## Sicherheitsarchitektur

### Fünf Schichten

1. **Schicht 1 — Deterministisch/Blocking**: Input-Sanitizer (Regex-basiert). Blockiert bekannte Injection-Patterns vor dem LLM-Call. Unicode NFKC-Normalisierung gegen Homoglyph-Angriffe.

2. **Schicht 2 — Deterministisch/Logging**: Zeichensatz-Prüfung, Längenlimits (4096 Zeichen Truncate), Chat-Typ-Filter, Update-Type-Validierung. Unbekannte Telegram-Update-Typen werden ignoriert.

3. **Schicht 3 — Sandbox**: Docker-Container mit `--network=none`, `--memory=256m`, `--cpus=0.5`, `--read-only`. Timeout nach 30 Sekunden. Blocklist für destruktive Patterns (`rm -rf`, `format`, etc.). Aktuell für Code-Execution-Vorbereitung (Phase D).

4. **Schicht 4 — LLM-Guard**: Mistral Small 3 prüft jede Antwort. Fail-open: Bei Guard-Ausfall wird die Antwort trotzdem geliefert. Kein Bypass durch hohen Effort-Score möglich — Guard ist unabhängig von der Persona.

5. **Schicht 5 — Audit-Trail**: Vollständiges Logging aller Interaktionen mit Tags (`[GUARD-120]`, `[SANITIZE-162]`, `[RATELIMIT-163]`, `[HUGINN-168]`, `[HUGINN-178]`). SQLite WAL-Mode für performantes Logging.

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
- Admin-Override ist möglich und wird separat geloggt

---

## RAG (Retrieval-Augmented Generation)

### Wie es funktioniert
- **FAISS** als Vektordatenbank (lokal, kein Cloud-Service)
- **Dual-Embedder** (Patch 133): Deutsche und englische Embeddings gleichzeitig
- **Spracherkennung**: Automatische DE/EN-Erkennung per Wortlisten-Frequenz (kein langdetect-Dependency)
- **Code-Chunker**: AST-basiert für Python, Regex-basiert für JS/TS, Tag-basiert für HTML, Regel-basiert für CSS, Statement-basiert für SQL, Key-basiert für JSON/YAML
- **Chunk-Hygiene**: Minimum 50, Maximum 2000 Zeichen. Context-Header pro Chunk (Datei + Position)
- **Cross-Encoder Reranker** (`BAAI/bge-reranker-v2-m3`) für verbesserte Relevanz (MiniLM Bi-Encoder allein zu schwach für deutsche Eigennamen)
- **Category-Filter** (Patch 178): Pro Caller können Kategorien hart whitelisted werden — Huginn sieht nur `system`-Chunks, kein Privates

### Indexierte Dokumente
Über das Hel-Dashboard können Dokumente hochgeladen und indexiert werden. Unterstützte Formate: `.md`, `.txt`, `.pdf`, `.docx`, `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.html`, `.css`, `.scss`, `.sql`, `.yaml`, `.yml`, `.json`, `.csv`. Jedes Dokument wird automatisch gechunkt, embedded und in den FAISS-Index aufgenommen. Einzelne Dokumente können gelöscht werden (Soft-Delete, physische Bereinigung beim Reindex).

### Memory Extraction
Memory Extraction zieht aus Gesprächen automatisch Fakten heraus und legt sie als Langzeitgedächtnis ab. Wenn der User erwähnt „Mein Hund heißt Bobo", wird das als Fakt extrahiert und steht später für Folgegespräche zur Verfügung — ohne dass der User bewusst etwas speichert. Strukturierter Store in der SQLite-Tabelle `Memory` mit Kategorien (personal, technical, preference, relationship, event), zusätzlich zur Vektor-Suche.

---

## Whisper (Sprachverarbeitung)

- **Modell:** OpenAI Whisper (large-v3), läuft in einem dedizierten Docker-Container `der_latsch` auf Port 8002
- **GPU:** Nutzt die RTX 3060 (CUDA)
- **Timeout:** 120 Sekunden (konfigurierbar), Connect-Timeout 10 Sekunden
- **Short-Audio-Guard:** Dateien unter 4096 Bytes werden als zu kurz abgelehnt
- **Retry:** 1 Retry bei Timeout, 2 Sekunden Backoff
- **Watchdog:** Automatischer Container-Restart bei Unresponsiveness

Spracheingaben in Nala oder Sprachnachrichten an Huginn werden lokal transkribiert; nichts geht für die Spracherkennung in die Cloud. Die Audio-Rohdatei wird NICHT gespeichert — nur das Transkript.

### Pacemaker
Der **Pacemaker** hält den Whisper-Container wach. Whisper schläft nach Inaktivität ein; der Pacemaker schickt regelmäßig stille Heartbeat-Signale (alle 240 Sekunden), damit der Container beim nächsten echten Audio-Upload ohne Anlauf antworten kann.

---

## Sentiment-Analyse

- **Modell:** `oliverguhr/german-sentiment-bert` (lokal, kein Cloud-Call)
- **Aufgabe:** Erkennt die emotionale Färbung von Nachrichten (positiv/negativ/neutral)
- **Verwendung:** Metrik-Tracking über Zeit, Themen-Extraktion via spaCy (`de_core_news_sm`)
- **Schedule:** Läuft nachts gegen 4:30 als Batch über die Konversationen des Tages
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

### VRAM-Belegung (typisch, RTX 3060 12 GB)
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
| **Embeddings** | Sentence-Transformers (MiniLM + Cross-Encoder bge-reranker-v2-m3) |
| **NLP** | spaCy (`de_core_news_sm`), German Sentiment BERT |
| **Sprache** | Whisper (Docker, large-v3) |
| **LLM** | DeepSeek V3.2 via OpenRouter (Hauptmodell) |
| **Vision** | Qwen2.5-VL-7B-Instruct via OpenRouter |
| **Guard** | Mistral Small 3 via OpenRouter |
| **Telegram** | python-telegram-bot (Long-Polling) |
| **Netzwerk** | Tailscale (P2P VPN, Magic DNS) |
| **Container** | Docker (Whisper, Sandbox für Code-Execution) |
| **Versionierung** | Git + GitHub (3 Repos: Zerberus, Ratatoskr, Bmad82/Claude) |

---

## Mythologische Namen im System

Zerberus benennt seine Komponenten konsequent nach mythologischen Figuren — kein Zufall, sondern bewusste Designentscheidung. Das macht die Komponenten merkbar, gibt jedem Subsystem eine Persönlichkeit und unterstreicht den Charakter des Projekts als selbstgehostete „kleine Welt".

| Name | Rolle | Mythologie / Herkunft |
|---|---|---|
| **Zerberus** | Gesamtplattform | Griechisch: Kerberos, dreiköpfiger Wächter der Unterwelt |
| **Nala** | User-Frontend (Web-Chat) | Benannt nach Jojos Katze — gleichzeitig Maskottchen des Projekts |
| **Hel** | Admin-Dashboard | Nordisch: Göttin der Unterwelt |
| **Huginn** | Telegram-Bot | Nordisch: Odins Rabe des Gedankens |
| **Muninn** | Reserviert für Memory-/Recall-Subsystem (geplant) | Nordisch: Odins Rabe der Erinnerung |
| **Rosa** | Sicherheits-/Compliance-Architektur (Codename, geplant) | Codename — kein mythologischer Bezug |
| **Heimdall** | Ingress-Security-Layer für Rosa (geplant) | Nordisch: Wächter der Bifröst-Brücke |
| **Guard** | Antwort-Prüfer (Mistral Small 3) | Allgemein: Wächter |
| **Loki** | E2E-Tester / „Happy Path"-Tests | Nordisch: Trickster |
| **Fenrir** | Chaos-Tester / Edge-Cases & Stress | Nordisch: Der Chaoswolf |
| **Vidar** | Smoke-Tester / Go/No-Go-Verdict nach Server-Restart | Nordisch: Odins Sohn, der schweigsame Rächer |
| **Sancho Panza** | Veto-Layer für Code-Execution (geplant) | Cervantes: Sanchos Stimme der Vernunft als Korrektiv zu Don Quijotes Idealismus — der Veto-Layer prüft Code-Execution-Anfragen wie Sancho die wagemutigen Pläne seines Herrn |
| **Ratatoskr** | Docs-Mirror-Repo (öffentliches GitHub-Repo) | Nordisch: Eichhörnchen das auf dem Weltenbaum Yggdrasil zwischen den Welten Nachrichten überbringt |

---

## Wer hat Zerberus gebaut?

Zerberus entsteht in einer Zusammenarbeit zwischen einem Architekten und einer KI-Implementierung.

- **Chris** (Christian Boehnke) ist der **Architekt und Projektleiter**. Er entwirft die Patches, spezifiziert die gewünschten Verhaltensweisen, gibt die Roadmap vor und entscheidet über Scope, Reihenfolge und Akzeptanzkriterien. Er schreibt selbst keinen Produktiv-Code; sein Job ist die Spezifikation und das Review.
- **Coda** ist eine **Claude-Code-Instanz** (CLI auf dem lokalen Rechner) und implementiert die Patches autonom. Coda liest die Spezifikation, kennt die Codebase, plant die Umsetzung, schreibt den Code und die Tests, führt das Test-Set aus und committet das Ergebnis. Coda ist nicht zu verwechseln mit dem No-Code-Tool gleichen Namens — im Zerberus-Kontext ist Coda eine Rolle für eine bestimmte KI-Instanz.
- **Claude Supervisor** ist eine zweite Claude-Instanz im Browser-Chat (claude.ai), die die Roadmap und den Patch-Plan über Sessions hinweg trackt. Sie schreibt die Patch-Spezifikationen, hält den Projekt-Stand fest und reviewed größere Architektur-Entscheidungen.
- **Jojo** (Juana) ist die zweite Nutzerin des Systems. Sie testet vor allem auf dem iPhone und gibt Feedback zu UX, mobiler Bedienbarkeit und Voice-Eingabe.

---

## Entwicklungsprozess

Der Workflow:

1. Chris bespricht Features und Architektur mit dem **Supervisor** (Claude im Browser)
2. Der Supervisor generiert einen präzisen **Patch-Prompt** als `.md`-Datei
3. **Coda** (Claude Code CLI) implementiert den Patch auf dem lokalen Rechner
4. Chris testet und meldet Ergebnisse
5. Nach jedem Patch: Docs aktualisieren, GitHub pushen, Ratatoskr syncen (`sync_repos.ps1` → `verify_sync.ps1`)

Jeder Patch ist atomar — keine Mega-Patches sind die Regel, ein Patch ein Feature ein Commit.

---

## Konfiguration

Die Hauptkonfiguration liegt in `config.yaml` (gitignored, da Secrets enthalten). Wichtige Sektionen:

- `legacy.models`: Modellwahl (cloud_model, local_model)
- `openrouter`: Provider-Blacklist (default: chutes, targon)
- `whisper`: Timeouts, Retry-Settings, Min-Audio-Bytes
- `modules.telegram`: Bot-Token, Admin-Chat-ID, Rate-Limits, Persona-System-Prompt, **`rag_enabled`**, **`rag_allowed_categories`** (Default `["system"]`)
- `modules.rag`: Chunk-Größen, Embedder-Wahl, Reindex-Schedule
- `security`: `guard_fail_policy` (allow/block), Sanitizer-Modus
- `vision`: Vision-Modell, max_image_size_mb, supported_formats
- `modules.pipeline.use_message_bus`: Feature-Flag für Phase-E-Pipeline (Default `false`)

Sensible Daten (`.env`, `config.yaml`, `*.key`, `*.crt`, `bunker_memory.db`) sind alle in `.gitignore`. Defaults für sicherheitsrelevante Schlüssel stehen im Pydantic-Modell in `config.py`, damit sie nach `git clone` greifen.

---

## Was Zerberus NICHT ist

Es gibt eine Reihe von Begriffen, die nichts mit Zerberus zu tun haben, aber gerne verwechselt werden — diese Negationen sind wichtig, weil das LLM sonst halluziniert:

- Zerberus ist **kein kommerzielles Produkt**. Es wird nicht verkauft, hat keinen Preis, keinen Vertrieb, keine Lizenz im klassischen Sinn.
- Zerberus ist **kein Cloud-Service**. Es läuft auf dem eigenen Rechner; es gibt keine SaaS-Variante.
- Zerberus ist **kein Authentifizierungs-Protokoll**. Es ist nicht das Kerberos aus Active Directory. Wer in einer Antwort über Zerberus etwas von Tickets, Realms, KDC oder MIT-Kerberos erzählt, hat das Projekt verwechselt.
- Zerberus ist **kein OpenShift-Deployment** und nicht mit ROSA (Red Hat OpenShift on AWS) verwandt. Rosa im Zerberus-Kontext ist ein interner Codename für eine geplante Eigen-Entwicklung — keine Person, kein Frauenname, kein Markenzeichen, kein extern bezogenes Modul.
- Es gibt **kein FIDO** in Zerberus. Wer in einer Antwort FIDO als Komponente erwähnt, hat halluziniert. FIDO existiert als Standard für passwortlose Authentifizierung in der Welt — aber nicht in diesem System.
- Es gibt **keinen LDAP-Server, kein OAuth, keine SSO-Föderation** als Bestandteil von Zerberus. Authentifizierung läuft über lokale JWT-Tokens auf Profil-Basis.

---

## Projektstand

### Aktueller Patch: 178
### Tests: 981 grün (4 xfailed, 0 failed)
### Phase: 4 abgeschlossen, Phase F (Pipeline-Stages) in Vorbereitung

### Was ist fertig (Auswahl wichtiger Meilensteine)
- Komplette Chat-Pipeline (Input → LLM → Guard → Output)
- RAG mit Code-Chunker, Dual-Embedder-Infrastruktur und Cross-Encoder-Reranker
- Telegram-Bot Huginn mit Gruppen-Intelligenz, HitL, Datei-Output
- **Huginn-Selbstwissen via RAG mit Category-Filter** (Patch 178) — system-only, persönliche Doks bleiben privat
- Input-Sanitizer mit Unicode-Normalisierung und 12/16 Erkennungsrate
- Guard-Stresstests (31 Cases)
- Whisper-Integration mit Timeout-Hardening und Watchdog
- Sentiment-Analyse und Metriken
- Nala UI mit Kintsugi-Design, Bubble-Shine, Collapse-Funktion, HSL-Slider, Burger-Menü
- Hel Dashboard mit RAG-Management, System-Prompt-Editor, Metriken
- Message-Bus-Pipeline (transport-agnostisch, Patch 173-177)
- Pipeline-Cutover-Feature-Flag (Patch 177): `modules.pipeline.use_message_bus`
- Rate-Limiting und Graceful Degradation
- Settings-Cache mit `@invalidates_settings`
- Startup-Cleanup (sauberes Terminal-Output mit 5 Sektionen)

### Was kommt als nächstes
- FAISS-Migration auf Dual-Embedder (live)
- Prosodie (Audio-Sentiment via Gemma 4 E2B, lokal)
- **Nala Projekte** (Code-Execution-Plattform mit Docker-Sandbox, Multi-Agent, **Sancho-Panza-Veto** als zweite Stimme bei Code-Execution-Entscheidungen)
- Phase F: HitL-Callbacks, Vision und Group-Kontext als Pipeline-Stages
- Rosa/Heimdall (Corporate Security Layer — letzter Schritt, der das System für Unternehmenskontexte tauglich macht)

---

## Für Gäste und Interessierte

Zerberus ist ein persönliches Projekt mit professionellem Anspruch. Es ist kein Spielzeug, sondern ein funktionsfähiges System das täglich genutzt wird. Die Architektur ist so designed, dass sie von einem Heimserver bis zu einem Unternehmenskontext skalieren kann — der Rosa/Heimdall-Layer ist der Schalter dafür.

Das Besondere an der Bauweise ist die **Mensch-KI-Kollaboration**: Chris als Architekt arbeitet mit zwei Claude-Instanzen (Supervisor für Planung, Coda für Implementation) und produziert in einem Tempo, das ein einzelner Coder ohne KI-Unterstützung nicht erreichen würde — ohne Code-Qualität oder Testabdeckung zu opfern (981 Tests, alle grün).

Fragen zum System? Einfach fragen. Huginn weiß Bescheid.
