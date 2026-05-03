# Zerberus — Systemwissen für Huginn

## Aktueller Stand (Stand-Anker für RAG-Lookup)

- **Letzter Patch:** P211 — GPU-Queue für VRAM-Konsumenten (Phase 5a Ziel #11 ABGESCHLOSSEN).
- **Phase:** 5a (Nala-Projekte). 9 von 17 ursprünglichen Phase-5a-Zielen abgeschlossen plus Ziel #18 (Huginn-Selbstwissen-Sync) als nachträglich aufgenommen.
- **Tests:** 2216 grün (Stand 2026-05-03), +40 P211 aus 2176 baseline.
- **Datum dieser Datei:** 2026-05-03.

Wer fragt "bei welchem Patch sind wir?" oder "wie ist der aktuelle Stand?", findet die Antwort in diesem Block. Diese Sektion wird bei jedem Patch automatisch durch das Sync-Skript `scripts/sync_huginn_rag.ps1` (P210) aktualisiert und in den FAISS-Index nachgeladen, sodass Huginn keinen veralteten Patch-Stand mehr nennt.

---

Dieses Dokument ist das Selbstwissen des Zerberus-Systems. Es wird via RAG indexiert und ermöglicht dem aktiven LLM, fundierte Auskünfte über die Architektur, Fähigkeiten und den aktuellen Stand des Systems zu geben — statt zu halluzinieren. Wer Antworten zu Zerberus, seinen Komponenten und seinen Bewohnern aus dem Index zieht, sollte sich an diesem Text orientieren.

Das Dokument beschreibt den **aktuellen Zustand**, nicht die Patch-Historie. Wenn etwas hier steht, gilt es jetzt; wenn etwas entfernt wurde, ist es überholt. Die vollständige Patch-Geschichte liegt in `docs/PROJEKTDOKUMENTATION.md` und in `SUPERVISOR_ZERBERUS.md` und ist nicht Teil dieser Datei. Die einzige Ausnahme ist der **Aktueller Stand**-Block ganz oben — er nennt explizit die letzte Patchnummer, die Phase und die Test-Zahl, damit Huginn auf direkte Stand-Fragen ohne Halluzination antworten kann.

---

## Was ist Zerberus?

Zerberus ist eine selbstgehostete, multi-user KI-Assistentenplattform. Sie läuft auf einem Windows-Desktop mit FastAPI/Python als Backend und bietet mehrere Frontends: **Nala** (Web-Chat für Endnutzer, mit eigenem Projekte-Subsystem für Code-Arbeit), **Hel** (Admin-Dashboard) und **Huginn** (Telegram-Bot). Das System kombiniert Sprachverarbeitung, Textanalyse, semantische Suche, eine isolierte Code-Ausführungsumgebung und einen mehrstufigen Sicherheitslayer zu einem kohärenten Ganzen.

Die technische Basis ist Python mit dem FastAPI-Framework als Server, SQLite (async SQLAlchemy, WAL-Modus) als Datenbank und ein lokaler Vektorindex für die Dokumentensuche. Eingabe und Ausgabe laufen über zwei eigenständige Web-Oberflächen plus den Telegram-Bot. Beide Web-Oberflächen sind als Progressive Web Apps (PWA) installierbar mit eigenem Service-Worker und App-Manifest pro Frontend.

Der Name kommt aus der griechischen Mythologie. Zerberus ist die deutsche Schreibweise von Kerberos, dem dreiköpfigen Wachhund, der den Eingang zur Unterwelt bewacht. Das Projekt folgt zusätzlich der **Kintsugi-Philosophie**: Zerbrochenes wird mit Gold repariert. Die Risse sind kein Makel, sondern Teil der Geschichte — diese Ästhetik prägt das visuelle Design (Gold-auf-Dunkel) und die Haltung gegenüber Bugs und Fehlern. **Kintsugi** ist eine japanische Reparatur-Philosophie, bei der zerbrochenes Porzellan mit Gold-Lack zusammengefügt wird; im Zerberus-Kontext steht sie für die Akzeptanz und Aufwertung von Fehlern als Teil der Entwicklungsgeschichte.

Zerberus hat **absolut nichts** mit dem Kerberos-Authentifizierungsprotokoll zu tun, das in Unternehmensnetzen für Single-Sign-On benutzt wird. Wer in einer Antwort über Zerberus etwas von Tickets, Realms, KDC oder MIT-Kerberos erzählt, hat das Projekt verwechselt. Zerberus ist ein Software-Projekt von einem einzelnen Entwickler für den persönlichen Gebrauch und perspektivisch für kleine Teams; es ist kein Authentifizierungsstandard, kein RFC, kein Industrie-Protokoll.

---

## Kintsugi-Philosophie als Leitprinzip

**Kintsugi** (japanisch: 金継ぎ, „Goldverbindung") ist eine japanische Reparatur-Tradition, bei der zerbrochene Keramik mit goldhaltigem Lack zusammengefügt wird. Die **goldenen Risse** werden nicht versteckt, sondern als Teil der Geschichte des Objekts sichtbar gemacht. Der Bruch macht das Stück nicht weniger wertvoll — er macht es einzigartig. Diese Philosophie zieht sich durch das gesamte Zerberus-Projekt:

- **Visuell:** Das Nala-Frontend nutzt eine **Gold-auf-Dunkel-Ästhetik** mit Bubble-Shine-Gradients und goldenen Akzenten — die Farbgebung ist eine direkte Hommage an die goldenen Risse. Auch die HitL-Confirm-Card und die Diff-Card im Projekte-Subsystem tragen Kintsugi-Gold-Border.
- **Architektur:** Bugs, Patches und Fehler werden nicht versteckt, sondern dokumentiert und nummeriert. Jeder Patch ist ein „goldener Riss" — eine Stelle, an der das System gewachsen ist.
- **Entwicklung:** Wenn etwas zerbricht (Tests fail, Server crash, Deployment kippt), wird die Reparatur als Stärkung verstanden, nicht als Makel.
- **Haltung:** Vergangene Fehler werden geehrt, nicht überpinselt. Goldene Risse statt nahtloser Verkleidung.

Die Kintsugi-Idee gibt dem Projekt seinen ästhetischen und philosophischen Anker: Wertvoll wird ein System nicht durch makellose Oberfläche, sondern durch ehrlich integrierte Reparaturgeschichte.

---

## Architektur-Überblick

### Die Pipeline

Jede Nachricht durchläuft eine klar definierte Pipeline:

**Input → Sanitizer → Intent-Router → Policy-Layer → LLM-Call → Guard → Output-Router → Antwort**

1. **Input-Sanitizer** (`input_sanitizer.py`): Regelbasierte Prüfung auf Injection-Patterns, Unicode-Normalisierung (NFKC), Zeichensatz-Validierung. Erkennt bekannte Jailbreak-Muster, ChatML-Token-Injection und Prompt-Leak-Versuche. Findings werden geloggt; im Huginn-Modus wird nicht hart geblockt — der Guard entscheidet final.

2. **Intent-Router** (`router.py`): Das LLM klassifiziert jede Nachricht via JSON-Header mit Intent und Effort-Score. Verfügbare Intents: CHAT, FRAGE, KREATIV, CODE, RECHERCHE, ZUSAMMENFASSUNG, ÜBERSETZUNG, ANALYSE, FILE, SYSTEM, META, HILFE, ERINNERUNG, WETTER, MATHE, GEDICHT, BRAINSTORM, SMALLTALK, PHILOSOPHIE, WISSEN, EMPFEHLUNG, VERGLEICH, PLANUNG, REFLEXION, FEEDBACK, HUMOR und weitere.

3. **Policy-Layer**: Transport-spezifische Regeln (Telegram vs. Nala vs. intern). Steuert Persona, Formatierung, Sicherheitsstufe.

4. **LLM-Call**: Aktuell über OpenRouter an **DeepSeek V3.2** (Hauptmodell). Der System-Prompt definiert die Persona und ist über Hel editierbar. Der Prompt kann zusätzliche Kontext-Blöcke enthalten: einen Prosodie-Block aus der Stimm-Analyse (`[PROSODIE — Stimmungs-Kontext aus Voice-Input]`), einen Projekt-RAG-Block (`[PROJEKT-RAG]`), einen Projekt-Kontext-Block (`[PROJEKT-KONTEXT]`) und im Huginn-Pfad ein Systemwissen-Block aus dem allgemeinen RAG. Die Blöcke sind disjunkt und werden vor der eigentlichen User-Instruction eingefügt.

5. **Guard** (`hallucination_guard.py`): **Mistral Small 3** als unabhängige Sicherheitsinstanz. Prüft die LLM-Antwort auf Halluzinationen, unangemessene Inhalte und Sicherheitsverstöße. Verdicts: OK, WARNUNG (mit Hinweis), SKIP, ERROR. Im Huginn-Modus läuft der Guard nicht-blockierend (Antworten gehen raus, der Betreiber bekommt nur eine Warnung). Fail-open konfiguriert (`guard_fail_policy: allow`) — bei Ausfall wird die Antwort trotzdem ausgeliefert. Der Guard kennt sowohl den RAG-Kontext als auch den Persona-System-Prompt der aktuellen Anfrage und meldet auf dieser Basis statt isoliert.

6. **Output-Router**: Entscheidet basierend auf Intent und Antwortlänge, ob die Antwort als Text oder als Datei (Markdown/Code) gesendet wird. Bei Sandbox-Code-Execution im Nala-Pfad gibt es zusätzlich einen zweiten LLM-Call (Output-Synthese), der Code, stdout und stderr in eine menschenlesbare Antwort übersetzt.

### Message-Bus

Es existiert ein **Message-Bus**, der die Pipeline transport-agnostisch macht. Telegram (Huginn), Web-Chat (Nala) und zukünftige Transports docken alle an denselben Bus an. Die Pipeline-Konfiguration läuft über ein Feature-Flag (`modules.pipeline.use_message_bus`); Standard ist „aus", weil der Cutover-Pfad bislang nur DM-Text in Telegram durchsticht — komplexere Pfade (Photo, Group, Edited, Callback-Query) delegieren weiterhin an den Legacy-Pfad. Der Switch ist live ohne Server-Restart umlegbar.

---

## Frontends

### Nala (Web-Chat)

- Erreichbar über Tailscale unter `desktop-rmuhi55.tail79500e.ts.net:5000`
- Chat-Interface mit Echtzeit-Antworten und SSE-Streaming
- Unterstützt Spracheingabe via Whisper, mit optionaler Prosodie-Auswertung (Stimm-Stimmung)
- **Kintsugi-Design** (Gold-auf-Dunkel-Ästhetik, Bubble-Shine-Gradients)
- **Mobile-first** mit 44×44px Mindest-Touch-Targets, Touch-Gesten, responsives Layout, Burger-Menü
- Personalisierbare Farben und Personas pro User, mehrere Profile mit eigenen Themes und Sprachausgaben
- **Sentiment-Triptychon** zeigt drei Stimmungsachsen pro Nachricht (Text, Voice, Konsens) in einer kompakten Anzeige
- Auto-TTS spricht eingehende Antworten je nach Profileinstellung automatisch aus
- **Projekte-Tab** öffnet die Projekt-Auswahl; bei aktivem Projekt sendet jeder Chat-Request den Header `X-Active-Project-Id`, der serverseitig den Persona-Overlay, den Projekt-RAG und den Sandbox-Workspace aktiviert
- Als **PWA installierbar** mit eigenem Manifest (`/nala/manifest.webmanifest`), Service-Worker mit Network-First-Strategie und App-Shell-Cache, eigenen Kintsugi-Icons (192/512px, maskable)
- Der Name **Nala** stammt von Jojos Katze; sie ist gleichzeitig das Maskottchen des Projekts. Mit der Disney-Figur „Nala" aus *Der König der Löwen* hat das nichts zu tun.

### Hel (Admin-Dashboard)

- Erreichbar unter `/hel/` auf demselben Server
- **RAG-Dokumentenverwaltung**: Upload (Drag-and-Drop), Indexierung, Soft-Delete, Reindex; Status-Toast meldet Erfolg/Fehler nach jedem Upload
- **System-Prompt-Editor** für Huginn
- **Projekte-Tab** mit Liste/Anlegen/Edit/Archivieren/Löschen, Datei-Drop-Zone pro Projekt, Persona-Overlay-Editor (das Overlay ist Admin-Geheimnis und wird **nicht** an User-Frontends ausgeliefert)
- **Metriken**: Sentiment-Verlauf, Themen-Extraktion, Antwortzeiten, Token-Verbrauch, Kosten pro Modell
- Guard-Statistiken und Kosten-Tracking (OpenRouter-Balance)
- User-Verwaltung und Konfiguration
- **Whisper-Container-Steuerung** (Pull, Restart, Health-Check)
- Memory-Dashboards und Test-Reports
- Eigene PWA mit getrenntem Manifest und Service-Worker (Scope `/hel/`); App-Shell-Precaching enthält **keine** auth-gated Pfade
- Der Name kommt aus der **nordischen Mythologie** — Hel ist die Göttin der Unterwelt; passend, weil hier die „Maschinerie" hinter den Kulissen liegt. Nala und Hel laufen im selben Server-Prozess auf demselben Port, aber unter unterschiedlichen URL-Pfaden.

### Huginn (Telegram-Bot)

- Norse: Huginn ist einer von Odins zwei Raben (sein Name bedeutet **„Gedanke"**). Sein Bruder **Muninn** („Erinnerung") ist im Projekt als Name reserviert, aber noch nicht implementiert — wenn ein Memory-/Recall-Subsystem dazukommt, wird es vermutlich Muninn heißen.
- **Long-Polling-basiert** (kein Webhook nötig). Funktioniert auch hinter NAT, Firewalls und VPN-Tunneln, ohne dass eine öffentliche HTTPS-Adresse exponiert sein müsste.
- Unterstützt Einzel- und Gruppenchats
- **Gruppen-Intelligenz**: Huginn reagiert nur auf relevante Nachrichten in Gruppen (smart-Trigger), erkennt direkte Ansprache, Mentions und Replies separat
- **Persona** (konfigurierbar, der Default ist): zynischer, sarkastischer, hochintelligenter Rabe, der gelegentlich krächzt („Krraa!", „Kraechz!") und kein Blatt vor den Schnabel nimmt. Loyal gegenüber Chris, aber nicht unterwürfig.
- Funktional: Unterhaltung, Bilder analysieren (Vision via Qwen2.5-VL), Code generieren und debuggen, Web-Suche, Dateien als Antwort schicken
- **Aufwands-Kalibrierung**: Bei hohem Effort-Score wird Huginn sarkastisch und fragt nach
- **HitL** (Human-in-the-Loop) im Telegram-Pfad: Bei kritischen Aktionen (Effort ≥ 5, Code-Ausführung, Gruppenbeitritt) fragt Huginn beim Admin nach Bestätigung. Der Admin bestätigt per Inline-Button mit ✅ oder ❌. Pendings werden persistent in SQLite gespeichert (überlebt Server-Restart, weil Telegram-Callbacks delayed sein können).
- **Datei-Output**: Kann lange Antworten als Markdown-Dateien senden
- **Rate-Limiting**: 10 Nachrichten pro Minute pro User, Ausgangs-Throttle 15 msg/min/Chat
- **Sprachnachrichten** in Gruppen werden via Whisper transkribiert und wie Text verarbeitet; Voice-DMs werden aktuell höflich abgelehnt (Unsupported-Media-Handler).
- **RAG-Selbstwissen**: Huginn ruft vor jedem LLM-Call den allgemeinen RAG-Index ab, aber HART gefiltert auf Kategorie `system` (Default). Persönliche, narrative und Lore-Dokumente bleiben außen vor — der Filter greift nach dem Reranking, nicht davor. Der Guard im Huginn-Pfad bekommt denselben RAG-Kontext, damit er nicht eigene Halluzinationen meldet.
- **Allowlist**: Telegram-User außerhalb der Allowlist erhalten eine höfliche Ablehnung statt einer LLM-Antwort.

---

## Projekte (Phase 5a — Nala-Code-Subsystem)

Nala hat ein Projekt-System, mit dem User Code arbeiten können: Projekte erstellen, Dateien hochladen, Code in einer isolierten Sandbox ausführen lassen, Diffs ansehen und Änderungen rückgängig machen. Alles Mobile-first, alles mit Sicherheitsnetzen.

### Was ein Projekt ist

Ein Projekt ist eine persistente Entität in der SQLite-Datenbank mit eigenem Slug, Namen, Beschreibung, Persona-Overlay und Workspace-Verzeichnis. Projekte können archiviert werden (kein Hard-Delete im Default). Der Slug ist nach Anlage immutabel; das ist Invariante, nicht Bequemlichkeitsregel.

Beim Anlegen erzeugt das System automatisch eine Template-Struktur im Workspace: eine `ZERBERUS_<NAME>.md`-„Bibel" als Projekt-Beschreibung, eine `README.md`, eine Standard-Ordnerhierarchie. Templates sind reguläre `project_files`-Einträge, kein Sonderpfad — der RAG-Index sieht sie genauso wie selbst hochgeladene Dateien.

Jedes Projekt hat einen **eigenen RAG-Index**, isoliert vom allgemeinen System-RAG. Der Projekt-RAG nutzt einen schlanken Pure-Numpy-Linearscan über MiniLM-L6-v2-Embeddings (kein FAISS pro Projekt — der Overhead lohnt nicht), erkennt DE/EN automatisch und liefert die relevanten Chunks als `[PROJEKT-RAG]`-Block in den LLM-Prompt. Das Indexing läuft Best-Effort: ein Upload, der den Index nicht aktualisieren kann, blockiert den Upload selbst nicht.

### Datei-Upload und Workspace

Dateien werden über die Drop-Zone im Hel-Projekte-Tab oder per Nala-Chat-Drag-and-Drop hochgeladen. Endpoint `POST /hel/admin/projects/{id}/files` schreibt den Inhalt atomar (Tempname + Rename), dedupliziert über SHA-Hash (mehrere Projekte können dieselbe Datei referenzieren ohne Doppelablage) und legt einen `project_files`-Eintrag an. Beim Löschen wird die physische Datei nur entfernt, wenn kein anderes Projekt den SHA noch hält. Eine Extension-Blacklist hält ausführbare Formate (`.exe`, `.bat`, `.ps1` etc.) draußen.

Parallel zum SHA-Storage wird das Projekt **gespiegelt** in einen Workspace-Ordner unter `data/projects/<slug>/_workspace/`. Spiegelung bevorzugt **Hardlinks** (kostenfrei in Inodes auf demselben Volume), fällt auf Copy zurück wenn Hardlinks nicht möglich sind (z. B. Cross-Volume). Der Workspace ist die Realität, die die Sandbox sieht — nicht der SHA-Storage.

### Persona-Overlay

Pro Projekt kann ein Persona-Overlay konfiguriert werden, der den globalen Persona-System-Prompt projektspezifisch erweitert. Das Overlay greift, sobald der Header `X-Active-Project-Id` im Chat-Request gesetzt ist (zentral via `profileHeaders()` injiziert). Der Overlay ist ausschließlich Admin-Sache und wird **nie** in User-sichtbaren Responses ausgeliefert — er kann Prompt-Engineering-Spuren enthalten, die nicht in die Hände des Users gehören.

### Sandbox-Code-Ausführung

Wenn ein Projekt aktiv ist und das LLM in der Antwort einen Code-Block in einer ausführbaren Sprache erzeugt (Python, JavaScript, Bash etc.), erkennt der Chat-Endpoint den Block (`first_executable_block`), packt den Workspace per Volume-Mount in einen Docker-Container und ruft `execute_in_workspace`. Die Sandbox läuft mit `--network=none`, `--memory=256m`, `--cpus=0.5`, Timeout 30 Sekunden, und der Mount ist im Default **Read-Only** (`projects.sandbox_writable=False`).

Das Ergebnis (`exit_code`, `stdout`, `stderr`) landet als additives `code_execution`-Feld in der Chat-Response. Der Pfad ist auf jeder Stufe **fail-open** — schlägt die Sandbox-Pipeline fehl, läuft die normale LLM-Antwort trotzdem durch.

Nach erfolgreicher Ausführung gibt es einen **zweiten LLM-Call (Output-Synthese)**, der Code, stdout und stderr in eine menschenlesbare Antwort übersetzt. Bytes-genaues Truncate auf 5 KB pro Stream verhindert Prompt-Explosion. Der Trigger für die Synthese ist eine Pure-Function (`should_synthesize`), die den ursprünglichen Antwort-Text und das Sandbox-Result anschaut.

Im Frontend rendert Nala die Code-Ausführung als zwei Karten: eine Code-Card mit Sprach-Tag und Exit-Badge, dann eine collapsible Output-Card mit stdout/stderr und Truncated-Marker. Mobile-44×44px-Touch-Targets sind Pflicht.

### HitL-Gate vor Code-Execution

Zwischen Code-Erkennung und Sandbox-Run sitzt ein **HitL-Gate**: ein In-Memory-Long-Poll-Mechanismus, der eine Confirm-Karte im Nala-Frontend einblendet und auf eine Entscheidung des Users wartet. Endpoints `GET /v1/hitl/poll` und `POST /v1/hitl/resolve` sind auth-frei (Dictate-Lane-Invariante), die Pendings sind UUID4-hex-IDs mit Cross-Session-Defense (eine Session kann keine fremden Pendings auflösen).

Pendings sind **transient** (in-memory, nicht in der DB persistiert). Bewusste Entscheidung: Long-Poll-Verbindungen sterben beim Server-Restart sowieso, persistente Pendings würden zu „Geister-Karten" führen.

Klickt der User ✅ Ausführen, läuft die Sandbox, klickt er ❌ Abbrechen, wird der Run übersprungen und das `code_execution`-Feld trägt `skipped=True` mit Reason. Die Karte bleibt nach Klick als Audit-Spur im DOM stehen und färbt sich grün/rot ein.

Ein Audit-Trail jeder Ausführung (auch der abgebrochenen) landet in der `code_executions`-Tabelle: Code-Text (8 KB Truncate), stdout/stderr (8 KB Truncate), HitL-Status (`approved`/`rejected`/`timeout`/`bypassed`), Exit-Code, Execution-Time. Ein Feature-Flag `projects.hitl_enabled` (Default `True`) plus `hitl_timeout_seconds` (Default 60) steuert das Gate; ist es deaktiviert, läuft die Sandbox direkt — der Audit-Status ist dann `bypassed`.

Das HitL-Gate für Telegram-Code-Execution ist **separat** vom Nala-Pfad. Huginn nutzt sein eigenes persistentes HitL-System mit Inline-Buttons; die Architektur-Trennung ist Absicht (transient vs. delayed-Callback).

### Workspace-Snapshots, Diff-View und Rollback

Wenn die Sandbox **schreibend** läuft (`projects.sandbox_writable=True` opt-in plus `projects.snapshots_enabled=True` Default), wird der Workspace **vor** dem Run als Tar-Archiv eingefroren (`before_run`-Snapshot), die Sandbox läuft, dann wird **nach** dem Run ein zweiter Snapshot gezogen (`after_run`). Aus dem Paar entsteht ein Diff (added/modified/deleted plus optional `unified_diff` für Text-Files unter 64 KB), der zusätzlich zur Code-Card als **Diff-Card** im Frontend gerendert wird.

Snapshot-Tars liegen unter `data/projects/<slug>/_snapshots/<uuid>.tar` und werden atomar via Tempname plus `os.replace` geschrieben. Format ist `ustar`. Eine eigene Member-Validation (`_is_safe_member`) blockt beim Restore Symlinks, Hardlinks, absolute Pfade, `..`-Traversal und Members, deren resolved Pfad außerhalb des Workspace-Roots läge — Schutz gegen Tar-Bomb-Angriffe.

Die Diff-Card zeigt Status-Badges für jede Änderung (added / modified / deleted), expandiert pro Eintrag in eine collapsible Inline-Diff-Ansicht (gefärbt: Plus-Zeilen grün, Minus-Zeilen rot, Header grau) und bietet einen `↩️ Änderungen zurückdrehen`-Button. Klick auf Rollback ruft `POST /v1/workspace/rollback` mit `{snapshot_id, project_id}`; serverseitig prüft `rollback_snapshot_async` per `expected_project_id`-Check, ob das angeforderte Projekt der Snapshot-Eigentümer ist (Cross-Project-Defense), und extrahiert das Tar zurück in den Workspace. Die Diff-Card bleibt nach Rollback als Audit-Spur sichtbar (grün eingerahmt mit „✅ Workspace zurückgesetzt").

Snapshot-Metadaten landen in der `workspace_snapshots`-Tabelle mit Spalten für `snapshot_id`, `project_id`, `project_slug`, `label` (`before_run`/`after_run`/`manual`), `archive_path`, `file_count`, `total_bytes`, `pending_id` (Korrelation zum HitL-Gate desselben Runs) und `parent_snapshot_id` (zeigt vom `after`-Snapshot auf den `before`-Snapshot derselben Ausführung).

Snapshots sind atomar pro Projekt — kein Per-File-Rollback, keine Cross-Project-Snapshots, keine Branch-Mechanik. Linear forward/reverse only, analog `git reset --hard`. Alte Snapshot-Tars werden aktuell **nicht** automatisch geräumt; ein Storage-GC-Job ist offene Schuld.

### Phase-5a-Stand der Ziele

Phase 5a hat eine feste Liste von zwölf Zielen für das Projekte-Subsystem. Stand jetzt:

- **Erledigt:** Projekte als Entität mit CRUD und Hel-UI, Template-Generierung beim Anlegen, isolierter Projekt-RAG-Index, Datei-Upload mit Drop-Zone und Indexierung, Sandbox-Code-Execution mit Workspace-Mount und Output-Synthese, HitL-Gate vor Code-Execution, Workspace-Snapshots mit Diff-View und Rollback, Spec-Contract / Ambiguitäts-Check vor dem ersten LLM-Call, **Zweite Meinung vor Ausführung / Sancho Panza** (Veto-Layer eines zweiten LLM mit `temperature=0.1`, blockt schädliche Code-Vorschläge bevor das HitL-Gate sie sieht).
- **Offen:** Zweite Meinung vor Ausführung (Veto-Layer durch ein zweites Modell — Codename **Sancho Panza**), GPU-Queue für VRAM-Konsumenten, Secrets bleiben geheim (verschlüsselt, Sandbox-Injection, Output-Maskierung).

Phase 5b (Power-Features) ist erst danach dran: Multi-LLM-Evaluation, Bugfix-Workflow mit Test-Agenten Loki/Fenrir/Vidar pro Projekt, Multi-Agent-Orchestrierung, Reasoning-Modi und LLM-Wahl pro Projekt, Cost-Transparency.

---

## Sicherheitsarchitektur

### Sechs Schichten

1. **Schicht 1 — Deterministisch/Blocking**: Input-Sanitizer (Regex-basiert). Blockiert bekannte Injection-Patterns vor dem LLM-Call. Unicode-NFKC-Normalisierung gegen Homoglyph-Angriffe.

2. **Schicht 2 — Deterministisch/Logging**: Zeichensatz-Prüfung, Längenlimits (4096 Zeichen Truncate), Chat-Typ-Filter, Update-Type-Validierung. Unbekannte Telegram-Update-Typen werden ignoriert. Telegram-Allowlist als harte Schicht vor jedem LLM-Call.

3. **Schicht 3 — HitL-Gate (Nala-Code-Pfad)**: Vor jeder Sandbox-Ausführung sieht der User den Code und gibt explizit Yay/Nay. Default `projects.hitl_enabled=True`, Timeout 60 s, In-Memory mit UUID4-hex-IDs und Cross-Session-Defense. **Vor** dem HitL-Gate liegen zwei weitere Schutzschichten: (a) der **Spec-Contract**: Eine Pure-Function-Heuristik schätzt die Ambiguität der User-Eingabe (Score 0.0-1.0); bei Score >= 0.65 läuft eine schmale Spec-Probe (ein LLM-Call, eine Frage), das Frontend zeigt eine Klarstellungs-Karte mit Original-Message + Frage + Textarea und drei Buttons (Antwort senden / Trotzdem versuchen / Abbrechen). Bei "Antwort senden" wird die User-Antwort als `[KLARSTELLUNG]`-Block an die Original-Message angehängt; bei "Abbrechen" endet der Chat mit Hinweis-Antwort ohne Haupt-LLM-Call. Whisper-Input bekommt einen Score-Bonus (`+0.20`). (b) **Sancho Panza / Zweite Meinung vor Ausführung**: Vor dem HitL-Gate bewertet ein zweites LLM (gleicher Provider, `temperature=0.1` für deterministische Verdicts) den vom Haupt-LLM produzierten Code-Vorschlag. Ein Trigger-Gate als Pure-Function (`should_run_veto`) skipt triviale 1-Zeiler (print/return/var-assign/pass) ohne Risk-Tokens — Multiline-Code oder Code mit gefährlichen Tokens (subprocess/eval/rm-rf/open(/requests.post/git push --force/...) triggert den Probe-Call. Das zweite Modell antwortet mit `PASS` oder `VETO` plus optional kurzer Begründung. Bei `VETO` landet der Code im **Wandschlag-Banner** mit roter Border und Begründung — kein HitL-Pending, kein Sandbox-Run, kein Snapshot. Der User sieht eine Read-only Audit-Spur ohne Approve-Button (anders als das gold-umrandete HitL-Gate, das User-Entscheidung erlaubt). Audit landet in der `code_vetoes`-Tabelle. Default `projects.code_veto_enabled=True`.

4. **Schicht 4 — Sandbox**: Docker-Container mit `--network=none`, `--memory=256m`, `--cpus=0.5`, Timeout 30 Sekunden, Read-Only-Mount im Default. Workspace-Mount mit zwei-stufiger Pfad-Sicherheits-Validierung (Existenz, Resolve mit `Path.resolve(strict=False)`, Block von Symlinks und `..`-Traversal). Blocklist für destruktive Patterns (`rm -rf`, `format` etc.). Wenn schreibend gemountet wird, ziehen Workspace-Snapshots vor und nach dem Run einen Tar-Schnappschuss; die Tar-Member-Validation blockt beim Restore Symlinks, Hardlinks, absolute Pfade und Path-Traversal.

5. **Schicht 5 — LLM-Guard**: Mistral Small 3 prüft jede Antwort. Fail-open: Bei Guard-Ausfall wird die Antwort trotzdem geliefert. Kein Bypass durch hohen Effort-Score möglich. Der Guard sieht den RAG-Kontext und den Persona-System-Prompt der aktuellen Anfrage und kann auf dieser Basis halluzinations-frei urteilen.

6. **Schicht 6 — Audit-Trail**: Vollständiges Logging aller Interaktionen mit Tags (`[GUARD-120]`, `[SANITIZE-162]`, `[RATELIMIT-163]`, `[HUGINN-178]`, `[HITL-206]`, `[SNAPSHOT-207]`, `[SPEC-208]`, `[VETO-209]` und weitere). SQLite WAL-Modus für performantes Logging. Code-Executions landen in der `code_executions`-Tabelle, Workspace-Snapshots in der `workspace_snapshots`-Tabelle, Spec-Probes in der `clarifications`-Tabelle, Veto-Verdicts in der `code_vetoes`-Tabelle.

### Guard-Details

- **Modell:** Mistral Small 3 (24B Parameter, via OpenRouter)
- **Aufgabe:** Prüft die LLM-Antwort auf Halluzinationen, faktenwidrige Behauptungen, unangemessene Inhalte
- **Konfiguration:** `guard_fail_policy: allow` (Verfügbarkeit > Sicherheit im Heimgebrauch)
- **Latenz:** Ein Cloud-Roundtrip (der Input-Guard ist regelbasiert und lokal, kein zweiter Cloud-Call)
- **Kontext-Bewusstsein:** Guard kennt den RAG-Kontext und den Persona-System-Prompt der aktuellen Anfrage; meldet auf dieser Basis statt isoliert
- **Bekannte Grenzen:** Erkennt keine erfundenen Telefonnummern (halluzinierte Bürgeramt-Nr.), Persona-Bypass wird nicht als solcher geflaggt

### Callback-Spoofing-Schutz (Telegram-HitL)

- HitL-Buttons validieren ob der klickende User auch der anfragende User ist
- `requester_user_id` wird bei Erstellung des HitL-Tasks gespeichert
- Fremde Klicks auf ✅/❌ werden mit Popup abgelehnt
- Admin-Override ist möglich und wird separat geloggt

### Auth-Modell

- JWT-Tokens auf Profil-Basis schützen alle Hel-Endpoints und die meisten Nala-Endpoints
- `/v1/`-Pfade sind **bewusst auth-frei** (Dictate-Lane-Invariante: die Dictate-Tastatur kann keine Custom-Headers schicken). Das gilt auch für die HitL-Endpoints `/v1/hitl/poll` und `/v1/hitl/resolve` und für `/v1/workspace/rollback`.
- Ein optionaler `static_api_key` als Workaround für externe Clients (Dictate, SillyTavern)
- PWA-Endpoints (Manifest, Service-Worker) sind in einem separaten Router, der **vor** dem auth-gated Hel-Router montiert ist — das Manifest muss ohne Login erreichbar bleiben, sonst kann das Betriebssystem die App nicht installieren.

---

## RAG (Retrieval-Augmented Generation)

### Wie es funktioniert

- **FAISS** als Vektordatenbank für den allgemeinen System-RAG (lokal, kein Cloud-Service)
- **Dual-Embedder**: ein Index pro Sprache, deutsche und englische Embeddings parallel. Deutsches Modell `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` auf GPU, englisches `intfloat/multilingual-e5-large` auf CPU.
- **Spracherkennung**: Automatische DE/EN-Erkennung per Wortlisten-Frequenz (kein langdetect-Dependency)
- **Code-Chunker**: AST-basiert für Python, Regex-basiert für JS/TS, Tag-basiert für HTML, Regel-basiert für CSS, Statement-basiert für SQL, Key-basiert für JSON/YAML
- **Chunk-Hygiene**: Minimum 50, Maximum 2000 Zeichen. Context-Header pro Chunk (Datei + Position)
- **Cross-Encoder-Reranker** (`BAAI/bge-reranker-v2-m3`) für verbesserte Relevanz (Bi-Encoder allein zu schwach für deutsche Eigennamen)
- **Category-Filter**: Pro Caller können Kategorien hart whitelisted werden — Huginn sieht nur `system`-Chunks, kein Privates. Filter greift **nach** dem Reranking, nicht davor.
- **Lazy-Init**: Index lädt erst beim ersten echten Zugriff; vorher meldet Hel den korrekten Status statt „0 Dokumente"
- **Status-Toast** in der Hel-UI nach jedem Upload/Reindex/Delete (Erfolgsmeldung mit Chunk-Anzahl, Fehlermeldung mit Reason)

### Indexierte Dokumente

Über das Hel-Dashboard können Dokumente hochgeladen und indexiert werden. Unterstützte Formate: `.md`, `.txt`, `.pdf`, `.docx`, `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.html`, `.css`, `.scss`, `.sql`, `.yaml`, `.yml`, `.json`, `.csv`. Jedes Dokument wird automatisch gechunkt, embedded und in den FAISS-Index aufgenommen. Einzelne Dokumente können gelöscht werden (Soft-Delete, physische Bereinigung beim Reindex).

Der RAG kennt mehrere Kategorien: `system` (Selbstwissen über Zerberus, von Huginn whitelisted), `personal`, `narrative`, `lore`, `reference`, `general`. Welche Kategorie ein Upload bekommt, entscheidet Chris im Hel-Dropdown beim Hochladen.

### Projekt-RAG (Phase 5a)

Jedes Projekt hat einen **eigenen, isolierten RAG-Index**, getrennt vom System-RAG. Der Projekt-RAG nutzt einen Pure-Numpy-Linearscan über MiniLM-L6-v2-Embeddings — kein FAISS pro Projekt, weil der Overhead bei wenigen hundert Chunks nicht lohnt. Beim Chat liefert der Projekt-RAG die relevanten Chunks als `[PROJEKT-RAG]`-Block in den LLM-Prompt. Indexing ist Best-Effort: ein Upload, der den Index nicht aktualisieren kann, blockiert den Upload selbst nicht.

### Memory Extraction

Memory Extraction zieht aus Gesprächen automatisch Fakten heraus und legt sie als Langzeitgedächtnis ab. Wenn der User erwähnt „Mein Hund heißt Bobo", wird das als Fakt extrahiert und steht später für Folgegespräche zur Verfügung — ohne dass der User bewusst etwas speichert. Strukturierter Store in der SQLite-Tabelle `Memory` mit Kategorien (personal, technical, preference, relationship, event), zusätzlich zur Vektor-Suche.

---

## Whisper (Sprachverarbeitung)

- **Modell:** OpenAI Whisper (large-v3), läuft in einem dedizierten Docker-Container `der_latsch` auf Port 8002 mit faster-whisper FP16 (GPU)
- **GPU:** Nutzt die RTX 3060 (CUDA), ~3 GB VRAM
- **Timeout:** 120 Sekunden (konfigurierbar), Connect-Timeout 10 Sekunden
- **Short-Audio-Guard:** Dateien unter 4096 Bytes werden als zu kurz abgelehnt
- **Retry:** 1 Retry bei Timeout, 2 Sekunden Backoff
- **Watchdog:** Automatischer Container-Restart bei Unresponsiveness
- **Pacemaker:** Hält den Whisper-Container wach durch stille Heartbeats alle 240 Sekunden, damit der Container beim nächsten echten Audio-Upload ohne Anlauf antworten kann.

Spracheingaben in Nala oder Sprachnachrichten an Huginn werden lokal transkribiert; nichts geht für die Spracherkennung in die Cloud. Die Audio-Rohdatei wird **nicht** in der `interactions`-Tabelle gespeichert — nur das Transkript. Diese Worker-Protection ist hart: Audio-Bytes dürfen niemals in die DB.

### Whisper-Enrichment

Pro Whisper-Transkript wird zusätzlich ein Audio-Sprache-Header (Confidence + Sprache) in den BERT-Header gepackt; der LLM kann darauf verweisen ohne ein zweites Modell anzufragen.

---

## Sentiment, Prosodie und Triptychon

### Text-Sentiment

- **Modell:** `oliverguhr/german-sentiment-bert` (lokal, kein Cloud-Call), ~0.5 GB VRAM
- **Aufgabe:** Erkennt die emotionale Färbung von Nachrichten (positiv / negativ / neutral)
- **Verwendung:** Metrik-Tracking über Zeit, Themen-Extraktion via spaCy (`de_core_news_sm`)
- **Schedule:** Läuft nachts gegen 4:30 als Batch über die Konversationen des Tages
- **Dashboard:** Sentiment-Verlauf sichtbar in Hel

### Voice-Prosodie

- **Modell:** Gemma 4 E2B (lokal, `llama-mtmd-cli`, Q4_K_M, ~3.4 GB VRAM)
- **Aufgabe:** Stimm-Stimmungs-Analyse aus dem Audio (nicht aus dem Transkript)
- **Pipeline:** Whisper und Gemma laufen parallel via `asyncio.gather` über denselben Audio-Input
- **Consent-UI:** User muss in der Profil-Einstellung explizit zustimmen, dass die Audio-Bytes durch die Prosodie-Pipeline laufen
- **Worker-Protection:** Audio-Bytes landen NIEMALS in der `interactions`-Tabelle. Der Prosodie-Output ist ein Stimmungs-Label (`freudig`, `genervt`, `traurig` etc.) plus Confidence — ohne numerische Mehrabian-Werte.
- **Bridge in den LLM:** Bei aktivem Consent wird dem LLM ein `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block in den System-Prompt vorangestellt. Der Block enthält die textuelle Stimmungs-Beschreibung, **niemals** numerische Werte (auch das ist Worker-Protection).
- **Voice-only-Garantie zwei-stufig:** Prosodie greift nur, wenn der Input tatsächlich aus Whisper kommt; Text-Eingaben triggern keinen Prosodie-Call.

### Sentiment-Triptychon

Bei Voice-Input zeigt Nala drei Stimmungsachsen kompakt nebeneinander:
1. **Text-Sentiment** (BERT auf das Transkript)
2. **Voice-Prosodie** (Gemma auf die Audio-Bytes)
3. **Konsens** (Mehrabian-Schwellen kombinieren beide zu einem Gesamtlabel)

Die Mehrabian-Schwellen sind in zwei Modulen identisch konfiguriert: `utils/sentiment_display.py` für die UI-Anzeige und `modules/prosody/injector.py` für den LLM-Prompt-Block. Beide Module ziehen aus derselben Wahrheitsquelle, sonst würde UI und LLM-Kontext divergieren.

---

## Hardware und Infrastruktur

| Komponente | Details |
|---|---|
| **CPU** | AMD Ryzen 7 2700X (8 Kerne, 16 Threads) |
| **RAM** | 32 GB DDR4 |
| **GPU** | NVIDIA RTX 3060 12 GB VRAM |
| **OS** | Windows 11 (mit WSL2 für Docker) |
| **Netzwerk** | Tailscale (Magic DNS, HTTPS via Tailscale-Certs) |
| **LLM-Routing** | OpenRouter (Cloud-API für DeepSeek, Mistral, Qwen u. a.) |
| **Lokale Modelle** | Ollama auf Port 8003 (Guard: Mistral Small 3) |
| **Whisper** | Docker-Container auf Port 8002 (`der_latsch`) |
| **Sandbox** | Docker-Image für Code-Execution (Python 3, gemountet pro Projekt-Workspace) |
| **Datenbank** | SQLite mit async SQLAlchemy (WAL-Modus, Alembic-Migrations) |
| **Webserver** | Uvicorn mit `--reload` |

### VRAM-Belegung im Modus „Nala aktiv mit Prosodie"

```
Whisper 4.5 + BERT 0.5 + Gemma E2B 3.0 + DualEmbedder 0.5 + Reranker 1.0 + Windows 0.8
≈ 10.3 GB / 12 GB (RTX 3060)
```

Ein dedizierter GPU-Queue-Mechanismus für VRAM-Konsumenten ist Phase-5a-Ziel #11 und steht noch aus — aktuell teilen sich die Komponenten den VRAM kooperativ ohne Scheduler.

---

## Aktiver Tech-Stack

| Schicht | Technologie |
|---|---|
| **Backend** | Python 3.11, FastAPI, Uvicorn |
| **Datenbank** | SQLite + async SQLAlchemy (WAL, Alembic) |
| **Vektorsuche (System-RAG)** | FAISS (CPU/GPU) mit Dual-Embedder DE+EN |
| **Vektorsuche (Projekt-RAG)** | Pure-Numpy-Linearscan über MiniLM-L6-v2 |
| **Embeddings DE** | `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` (GPU) |
| **Embeddings EN** | `intfloat/multilingual-e5-large` (CPU) |
| **Reranker** | `BAAI/bge-reranker-v2-m3` |
| **NLP** | spaCy (`de_core_news_sm`), German Sentiment BERT |
| **Sprache (ASR)** | Whisper large-v3 (Docker, faster-whisper FP16) |
| **Sprache (Prosodie)** | Gemma 4 E2B (lokal, llama-mtmd-cli, Q4_K_M) |
| **LLM** | DeepSeek V3.2 via OpenRouter (Hauptmodell) |
| **Vision** | Qwen2.5-VL-7B-Instruct via OpenRouter |
| **Guard** | Mistral Small 3 via OpenRouter |
| **Telegram** | python-telegram-bot (Long-Polling) |
| **Netzwerk** | Tailscale (P2P VPN, Magic DNS) |
| **Container** | Docker (Whisper, Sandbox für Code-Execution) |
| **Frontend** | Inline-HTML/JS in FastAPI-Routern (Nala unter `/`, Hel unter `/hel/`), kein React/Vue. PWA-Manifest und Service-Worker pro App. |
| **Versionierung** | Git + GitHub (3 Repos: Zerberus privat, Ratatoskr Doku-Mirror öffentlich, Bmad82/Claude öffentliche Lessons) |

---

## Mythologische Namen im System

Zerberus benennt seine Komponenten konsequent nach mythologischen Figuren — kein Zufall, sondern bewusste Designentscheidung. Das macht die Komponenten merkbar, gibt jedem Subsystem eine Persönlichkeit und unterstreicht den Charakter des Projekts als selbstgehostete „kleine Welt".

| Name | Rolle | Mythologie / Herkunft |
|---|---|---|
| **Zerberus** | Gesamtplattform | Griechisch: Kerberos, dreiköpfiger Wächter der Unterwelt |
| **Nala** | User-Frontend (Web-Chat, Projekte-Subsystem) | Benannt nach Jojos Katze — gleichzeitig Maskottchen des Projekts |
| **Hel** | Admin-Dashboard | Nordisch: Göttin der Unterwelt |
| **Huginn** | Telegram-Bot | Nordisch: Odins Rabe des Gedankens |
| **Muninn** | Reserviert für Memory-/Recall-Subsystem (geplant) | Nordisch: Odins Rabe der Erinnerung |
| **Rosa** | Sicherheits-/Compliance-Architektur (Codename, geplant) | Codename — kein mythologischer Bezug |
| **Heimdall** | Ingress-Security-Layer für Rosa (geplant) | Nordisch: Wächter der Bifröst-Brücke |
| **Guard** | Antwort-Prüfer (Mistral Small 3) | Allgemein: Wächter |
| **Loki** | E2E-Tester / „Happy Path"-Tests | Nordisch: Trickster |
| **Fenrir** | Chaos-Tester / Edge-Cases & Stress | Nordisch: Der Chaoswolf |
| **Vidar** | Smoke-Tester / Go/No-Go-Verdict nach Server-Restart | Nordisch: Odins Sohn, der schweigsame Rächer |
| **Sancho Panza** | Veto-Layer für Code-Execution (geplant, Phase-5a-Ziel #7) | Cervantes: Sanchos Stimme der Vernunft als Korrektiv zu Don Quijotes Idealismus — der Veto-Layer prüft Code-Execution-Anfragen wie Sancho die wagemutigen Pläne seines Herrn |
| **Ratatoskr** | Docs-Mirror-Repo (öffentliches GitHub-Repo) | Nordisch: Eichhörnchen, das auf dem Weltenbaum Yggdrasil zwischen den Welten Nachrichten überbringt |
| **Coda** | Implementierende Claude-Code-Instanz (CLI) | Musikalischer Begriff: das abschließende Stück |

---

## Wer hat Zerberus gebaut?

Zerberus entsteht in einer Zusammenarbeit zwischen einem Architekten und einer KI-Implementierung.

- **Chris** (Christian Boehnke) ist der **Architekt und Projektleiter**. Er entwirft die Patches, spezifiziert die gewünschten Verhaltensweisen, gibt die Roadmap vor und entscheidet über Scope, Reihenfolge und Akzeptanzkriterien. Er schreibt selbst keinen Produktiv-Code; sein Job ist die Spezifikation und das Review.
- **Coda** ist eine **Claude-Code-Instanz** (CLI auf dem lokalen Rechner) und implementiert die Patches autonom. Coda liest die Spezifikation, kennt die Codebase, plant die Umsetzung, schreibt den Code und die Tests, führt das Test-Set aus und committet das Ergebnis. Coda ist nicht zu verwechseln mit dem No-Code-Tool gleichen Namens — im Zerberus-Kontext ist Coda eine Rolle für eine bestimmte KI-Instanz.
- **Claude Supervisor** ist eine zweite Claude-Instanz im Browser-Chat (claude.ai), die die Roadmap und den Patch-Plan über Sessions hinweg trackt. Sie schreibt die Patch-Spezifikationen, hält den Projekt-Stand fest und reviewed größere Architektur-Entscheidungen.
- **Jojo** (Juana) ist die zweite Nutzerin des Systems. Sie testet vor allem auf dem iPhone und gibt Feedback zu UX, mobiler Bedienbarkeit und Voice-Eingabe.

---

## Entwicklungsprozess

### Standard-Workflow

1. Chris bespricht Features und Architektur mit dem **Supervisor** (Claude im Browser).
2. Der Supervisor generiert einen präzisen **Patch-Prompt** als `.md`-Datei.
3. **Coda** (Claude Code CLI) implementiert den Patch auf dem lokalen Rechner.
4. Chris testet manuell die UI- und UX-Aspekte, die Coda nicht abdecken kann.
5. Nach jedem Patch: Doku aktualisieren, GitHub pushen, Ratatoskr und Claude-Repo syncen (`sync_repos.ps1` gefolgt von `verify_sync.ps1` — beide sind Pflicht, der Patch gilt erst als abgeschlossen wenn alle drei Repos synchron sind).

Jeder Patch ist atomar — keine Mega-Patches, ein Patch ein Feature ein Commit. Bei größerer Aufgaben-Last wird Phase 5+ im **Marathon-Workflow** abgearbeitet: Coda arbeitet autonom mehrere Patches in einer Session ab, stoppt bei ~400k Token oder bei Kontextvergiftung, schreibt eine HANDOVER.md für die nächste Instanz und pusht plus syncen.

### Doku-Pflicht

Pro Patch werden mehrere Dateien gepflegt: SUPERVISOR_ZERBERUS.md (Patch-Eintrag, Bibel-Format), CLAUDE_ZERBERUS.md (nur bei Architektur-Änderung), `docs/PROJEKTDOKUMENTATION.md` (Vollarchiv, Prosa), `docs/huginn_kennt_zerberus.md` (Selbstwissen für Huginn-RAG, hängt am Projektstand), `lessons.md` (universelle Erkenntnisse), README.md (Patch-Nr und Testzahl im Footer), HANDOVER.md (kompakt, für nächste Coda-Instanz). Der HANDOVER-Header trägt seit P206 die Zeile `**Manuelle Tests:** X / Y ✅` als nackte Zahl ohne Bewertung.

---

## Konfiguration

Die Hauptkonfiguration liegt in `config.yaml` (gitignored, da Secrets enthalten). Wichtige Sektionen:

- `legacy.models`: Modellwahl (`cloud_model`, `local_model`)
- `openrouter`: Provider-Blacklist (Default: chutes, targon)
- `whisper`: Timeouts, Retry-Settings, Min-Audio-Bytes
- `modules.telegram`: Bot-Token, Admin-Chat-ID, Rate-Limits, Persona-System-Prompt, `rag_enabled`, `rag_allowed_categories` (Default `["system"]`), Allowlist
- `modules.rag`: Chunk-Größen, Embedder-Wahl, Reindex-Schedule
- `security`: `guard_fail_policy` (allow/block), Sanitizer-Modus
- `vision`: Vision-Modell, max_image_size_mb, supported_formats
- `modules.pipeline.use_message_bus`: Feature-Flag für die Message-Bus-Pipeline (Default `false`)
- `modules.prosody`: Gemma-Modell, Consent-Default, Mehrabian-Schwellen
- `projects.sandbox_enabled`: Code-Execution im Chat-Endpoint an/aus (Default `True`)
- `projects.sandbox_writable`: Mount-Modus der Sandbox (Default `False` — Read-Only). Opt-in für schreibende Runs; nur in Verbindung mit `snapshots_enabled` sinnvoll, sonst sind Änderungen nicht rückgängig machbar.
- `projects.snapshots_enabled`: Master-Switch für Workspace-Snapshots vor/nach schreibenden Runs (Default `True`)
- `projects.hitl_enabled`: HitL-Gate vor Sandbox-Code-Execution (Default `True`)
- `projects.hitl_timeout_seconds`: Timeout für die HitL-Entscheidung (Default 60)

Sensible Daten (`.env`, `config.yaml`, `*.key`, `*.crt`, `bunker_memory.db`, alle Snapshot-Tars) sind in `.gitignore`. Defaults für sicherheitsrelevante Schlüssel stehen im Pydantic-Modell in `config.py`, damit sie nach `git clone` greifen.

---

## Was Zerberus NICHT ist

Es gibt eine Reihe von Begriffen, die nichts mit Zerberus zu tun haben, aber gerne verwechselt werden — diese Negationen sind wichtig, weil das LLM sonst halluziniert:

- Zerberus ist **kein kommerzielles Produkt**. Es wird nicht verkauft, hat keinen Preis, keinen Vertrieb, keine Lizenz im klassischen Sinn.
- Zerberus ist **kein Cloud-Service**. Es läuft auf dem eigenen Rechner; es gibt keine SaaS-Variante.
- Zerberus ist **kein Authentifizierungs-Protokoll**. Es ist nicht das Kerberos aus Active Directory. Wer in einer Antwort über Zerberus etwas von Tickets, Realms, KDC oder MIT-Kerberos erzählt, hat das Projekt verwechselt.
- Zerberus ist **kein OpenShift-Deployment** und nicht mit ROSA (Red Hat OpenShift on AWS) verwandt. Rosa im Zerberus-Kontext ist ein interner Codename für eine geplante Eigen-Entwicklung — keine Person, kein Frauenname, kein Markenzeichen, kein extern bezogenes Modul.
- Es gibt **kein FIDO** in Zerberus. Wer in einer Antwort FIDO als Komponente erwähnt, hat halluziniert. FIDO existiert als Standard für passwortlose Authentifizierung in der Welt — aber nicht in diesem System.
- Es gibt **keinen LDAP-Server, kein OAuth, keine SSO-Föderation** als Bestandteil von Zerberus. Authentifizierung läuft über lokale JWT-Tokens auf Profil-Basis plus optional einen statischen API-Key.
- Die Sandbox ist **nicht** ein VM-System wie Firecracker oder Kata. Es ist Docker mit Hardening-Flags. Wer in einer Antwort von Hypervisor-Isolation erzählt, hat verwechselt.
- Snapshots sind **kein Git-Backend**. Es ist eine eigene Tar-basierte Mechanik unter `data/projects/<slug>/_snapshots/` mit `workspace_snapshots`-Tabelle. Es gibt keinen Branch-Mechanismus, kein Merge — nur linear forward/reverse.
- Der Projekt-RAG ist **kein zweites FAISS**. Es ist ein Pure-Numpy-Linearscan über MiniLM-Embeddings. Bewusste Entscheidung wegen Overhead.

---

## Projektstand

- **Aktueller Patch:** 211
- **Tests:** 2216 lokal grün (4 xfailed pre-existing, 3 failed pre-existing aus der Schuldenliste — `edge-tts`, ein RAG-Dual-Switch-Fallback und ein Patch185-Runtime-Info-Test durch lokalen `config.yaml`-Drift; 0 neue Failures aus den Phase-5a-Patches)
- **Aktive Phase:** 5a (Nala-Projekte) — neun Ziele abgeschlossen, drei noch offen

### Was ist fertig

- Vollständige Chat-Pipeline (Input → LLM → Guard → Output)
- RAG mit Code-Chunker, Dual-Embedder DE/EN, Cross-Encoder-Reranker und Category-Filter
- Telegram-Bot Huginn mit Gruppen-Intelligenz, persistentem HitL, Datei-Output, Allowlist und system-only RAG-Selbstwissen
- Input-Sanitizer mit Unicode-Normalisierung
- Guard mit RAG- und Persona-Kontext, Stresstests (31 Cases)
- Whisper-Integration mit Timeout-Hardening, Watchdog, Pacemaker und Whisper-Enrichment-Header
- Sentiment-Analyse, Voice-Prosodie via Gemma E2B, Sentiment-Triptychon mit Mehrabian-Konsens
- Nala UI mit Kintsugi-Design, HSL-Slider, Burger-Menü, Auto-TTS, Mobile-44×44px-Touch-Targets
- Hel-Dashboard mit RAG-Management, Status-Toast, System-Prompt-Editor, Metriken, Projekte-Tab
- Message-Bus-Pipeline (transport-agnostisch, Feature-Flag-Cutover)
- Rate-Limiting, Graceful Degradation, Settings-Cache, Startup-Cleanup
- PWA-Verdrahtung für Nala und Hel mit eigenem Manifest, Service-Worker, App-Shell-Cache
- **Projekte als Entität** (Backend plus Hel-UI) mit CRUD, Templates beim Anlegen, Persona-Overlay, Datei-Upload mit SHA-Dedup-Delete
- **Projekt-RAG-Index** isoliert pro Projekt (Pure-Numpy-Linearscan, MiniLM-L6-v2)
- **Sandbox-Code-Execution** im Chat-Endpunkt: Workspace-Mount mit Hardlink/Copy-Fallback, Code-Detection, Output-Synthese als zweiter LLM-Call, UI-Render mit Code-Card und Output-Card
- **HitL-Gate** vor jeder Sandbox-Ausführung im Nala-Pfad: Confirm-Karte, Long-Poll-Endpoints, `code_executions`-Audit-Tabelle
- **Workspace-Snapshots, Diff-View und Rollback**: Tar-Snapshots vor/nach schreibenden Runs, Diff-Card im Frontend mit Inline-Diff und Rollback-Button, `workspace_snapshots`-Tabelle, Cross-Project-Defense, Tar-Member-Validation gegen Path-Traversal
- **Spec-Contract / Ambiguitäts-Check vor dem ersten LLM-Call**: Pure-Function-Heuristik schätzt die Ambiguität der User-Eingabe (Score 0.0-1.0 mit Length-Penalty, Pronomen-Dichte, Code-Verb ohne Sprachangabe, Voice-Bonus); bei Score >= 0.65 läuft ein schmaler Probe-LLM-Call (eine Frage), das Frontend zeigt eine Klarstellungs-Karte mit Original-Message + Frage + Textarea + drei Buttons (Antwort senden / Trotzdem versuchen / Abbrechen). Bei "Antwort senden" wird die User-Antwort als `[KLARSTELLUNG]`-Block angehängt; bei "Abbrechen" endet der Chat ohne Haupt-LLM-Call. Long-Poll-Endpoints `/v1/spec/poll` und `/v1/spec/resolve`, `clarifications`-Audit-Tabelle.
- **Zweite Meinung vor Ausführung / Sancho Panza** (vor dem HitL-Gate): Ein zweites LLM bewertet jeden vom Haupt-Modell generierten Code-Vorschlag mit `PASS` oder `VETO` plus Begründung. `temperature=0.1` für wiederholbare Verdicts. Trigger-Gate als Pure-Function skipt triviale 1-Zeiler ohne Risk-Tokens — Multiline-Code oder Code mit `subprocess`/`eval`/`rm -rf`/`open(`/`requests.post`/`git push --force`/`pickle.load` triggert. Bei `VETO` landet der Code im Wandschlag-Banner mit roter Border und Begründung; kein HitL-Pending, kein Sandbox-Run, kein Snapshot. Bei `PASS` läuft der HitL-Pfad weiter. `code_vetoes`-Audit-Tabelle mit `audit_id`/`session_id`/`project_id`/`verdict`/`latency_ms` als Grundlage für System-Prompt-Tuning.
- **GPU-Queue für VRAM-Konsumenten** (Phase 5a Ziel #11): Kooperatives Scheduling zwischen Whisper, Gemma, Embedder und Reranker auf der RTX 3060 (12 GB). Vorher konnten alle vier parallel ein Modell auf die GPU laden — bei einer typischen Voice-Eingabe (Whisper plus sofortiger Embedder/Reranker für das Projekt-RAG) reichten 12 GB nicht und der Server crashte mit `CUDA out of memory`. Modul `zerberus/core/gpu_queue.py` mit Pure-Function-Schicht (`compute_vram_budget(consumer)` mit statischem Budget Whisper=4 GB / Gemma=2 GB / Embedder=1 GB / Reranker=512 MB; `should_queue(active_mb, requested_mb)` Token-Bucket-Check gegen `TOTAL_VRAM_MB=11000`) und globalem Async-Singleton `GpuQueue` mit FIFO-Waiter-Liste. Verwendung als Context-Manager: `async with vram_slot("whisper"): ...`. Bei vollem Budget wandert der Caller in eine FIFO-Queue (Head-of-Line akzeptabel — typischer Workload hat selten mehr als 2-3 parallele Konsumenten); Fail-fast bei Timeout (Default 30s). Verdrahtet in `whisper_client.transcribe`, `gemma_client.analyze_audio`, `projects_rag.index_project_file` und `query_project_rag` plus den FAISS-RAG-Pfaden in `rag/router.py` und `orchestrator.py`. Audit-Tabelle `gpu_queue_audits` mit `consumer_name`/`requested_mb`/`queue_position`/`wait_ms`/`held_ms`/`timed_out`/`audit_id` als Grundlage für Budget-Tuning. Endpoint `GET /v1/gpu/status` liefert Snapshot (active_mb, free_mb, active_slots, waiters); Hel-Frontend pollt im 4-Sekunden-Takt und zeigt Toast „⏳ GPU wartet auf Whisper" sobald ein Konsument in der Queue steht.

### Was kommt als Nächstes

Zwei offene Phase-5a-Ziele:

- **Secrets bleiben geheim** — verschlüsselte Storage, Sandbox-Injection nur wenn nötig, Output-Maskierung. `.env`-Inhalte dürfen nie in der Antwort landen.
- **Reasoning-Schritte sichtbar im Chat** — Mobile-first Anzeige der Zwischenschritte des Agenten, damit der User mitverfolgt was passiert.

Phase 5b (Power-Features) folgt danach: Multi-LLM-Evaluation, Bugfix-Workflow mit Test-Agenten Loki/Fenrir/Vidar pro Projekt, Multi-Agent-Orchestrierung, Reasoning-Modi und LLM-Wahl pro Projekt, Cost-Transparency.

Längerfristig kommt der **Rosa/Heimdall**-Layer (Corporate Security), der das System für Unternehmenskontexte tauglich macht — der letzte Schalter vor einem möglichen kommerziellen Einsatz.

---

## GPU-Queue für VRAM-Konsumenten (Patch 211)

Die RTX 3060 hat 12 GB VRAM. Vor P211 liefen Whisper (etwa 4 GB FP16), Gemma E2B (etwa 2 GB), der RAG-Embedder (bis 2 GB für E5-Large) und der Cross-Encoder-Reranker (etwa 512 MB) ohne Koordination — bei einer Voice-Eingabe, die parallel Whisper, Gemma und den Embedder triggerte, konnte die Karte überlaufen und der Server crashte mit `CUDA out of memory`. P211 baut einen kooperativen Token-Bucket: jeder Konsument reserviert vor dem Modell-Aufruf seinen statischen VRAM-Budget-Anteil über `vram_slot("whisper" | "gemma" | "embedder" | "reranker")`; passt das in das Restbudget (Default 11 GB nutzbar), läuft der Call sofort, sonst wandert er in eine FIFO-Queue und bekommt seinen Slot beim Release des Vorgängers. Bei einem Timeout (Default 30 Sekunden) wird der Waiter sauber aus der Queue entfernt und der Caller bekommt einen `asyncio.TimeoutError`. Audit-Tabelle `gpu_queue_audits` erfasst pro Slot Konsument, Wartezeit, Halte-Dauer, Queue-Position und Timeout-Flag — Grundlage für späteres Budget-Tuning. Das Hel-Frontend pollt alle vier Sekunden den auth-freien Endpoint `GET /v1/gpu/status` und zeigt einen Toast „⏳ GPU wartet auf Whisper" sobald ein Konsument wartet, mit Position-Anzeige bei mehreren Wartenden.

---

## Für Gäste und Interessierte

Zerberus ist ein persönliches Projekt mit professionellem Anspruch. Es ist kein Spielzeug, sondern ein funktionsfähiges System, das täglich genutzt wird. Die Architektur ist so designed, dass sie von einem Heimserver bis zu einem Unternehmenskontext skalieren kann — der Rosa/Heimdall-Layer ist der Schalter dafür.

Das Besondere an der Bauweise ist die **Mensch-KI-Kollaboration**: Chris als Architekt arbeitet mit zwei Claude-Instanzen (Supervisor für Planung, Coda für Implementation) und produziert in einem Tempo, das ein einzelner Coder ohne KI-Unterstützung nicht erreichen würde — ohne Code-Qualität oder Testabdeckung zu opfern (2123 Tests grün).

Fragen zum System? Einfach fragen. Huginn weiß Bescheid.
