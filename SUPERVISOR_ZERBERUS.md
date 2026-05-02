# SUPERVISOR_ZERBERUS.md – Zerberus Pro 4.0
*Strategischer Stand für die Supervisor-Instanz (claude.ai Chat)*
*Letzte Aktualisierung: Patch 204 (2026-05-02) – Prosodie-Kontext im LLM (Phase 5a #17)*

---

## Aktueller Patch

**Patch 204** — Prosodie-Kontext im LLM (Phase 5a #17, unabhängig einschiebbar) (2026-05-02)

Schließt Phase-5a-Ziel #17: Die Prosodie-Pipeline (P189-193) lieferte ihre Daten bisher nur ans UI-Triptychon (P192) — DeepSeek bekam beim Voice-Input keinen Kontext, das LLM "hörte" die Stimme nicht. P190 hatte zwar einen rudimentären `[Prosodie-Hinweis ...]`-Block hinzugefügt, aber nur Gemma (kein BERT, kein Konsens-Label) und in einem ad-hoc-Format. P204 baut die Brücke richtig: ein markierter `[PROSODIE]...[/PROSODIE]`-Block analog `[PROJEKT-RAG]` (P199), mit BERT-Sentiment und Mehrabian-Konsens.

**Format-Entscheidung: qualitative Labels, keine Zahlen (Worker-Protection P191).** Confidence/Score/Valence/Arousal werden im Konsens-Label verkocht — das LLM bekommt nur menschenlesbare Beschreibungen wie "leicht positiv", "ruhig", "deutlich negativ", "inkongruent — Text positiv, Stimme negativ". Damit kann das Modell die Daten nicht zu Performance-Bewertungen aus Stimmungsdaten missbrauchen. Tests verifizieren die Number-Free-Property mit einem Regex (`\d+\.\d+`, `%`, `\b\d+\b`).

**Architektur: Pure-Function `build_prosody_block` plus Wrapper `inject_prosody_context`.** Pure-Schicht baut den Block-String (lookup-table-based mood/tempo-Übersetzung de, Mehrabian-Konsens-Logik aus `utils.sentiment_display` repliziert), Wrapper hängt am System-Prompt an mit Idempotenz-Check (Marker-Substring im Prompt → kein zweiter Block). BERT-Parameter sind keyword-only und additiv — bestehende P190-Aufrufer ohne BERT bekommen einen Block ohne Sentiment-Text-Zeile (Stimm-only-Pfad).

**Verdrahtung in `legacy.py /v1/chat/completions`.** Der `X-Prosody-Context`-Header (vom Whisper-Endpoint übers Frontend durchgereicht) plus `X-Prosody-Consent: true` schaltet den Block frei. Server-seitig wird BERT auf der letzten User-Message berechnet (fail-open: BERT-Fehler → kein Sentiment-Text-Zeile, Block läuft ohne) und an `inject_prosody_context` durchgereicht. Voice-only-Garantie: der Header existiert nur nach Whisper-Roundtrip (Frontend setzt ihn nicht bei getipptem Text) — defense-in-depth über Stub-Source-Check filtert versehentliche Pseudo-Contexts.

**Mehrabian-Konsens (Pure):** BERT positiv + Prosody-Valenz negativ → `"inkongruent — ..."`. Sonst: Confidence > 0.5 → Stimme dominiert (Stimm-Mood gewinnt), Confidence ≤ 0.5 → BERT-Fallback (`"deutlich/leicht positiv/negativ"` oder `"neutral"`). Schwellen identisch zu `utils/sentiment_display.py` (P192) — UI-Konsens und LLM-Konsens dürfen nicht voneinander abweichen.

- **Pure-Schicht:** [`zerberus/modules/prosody/injector.py::build_prosody_block`](zerberus/modules/prosody/injector.py) plus Helper `_consensus_label`, `_bert_qualitative` und Marker-Konstanten `PROSODY_BLOCK_MARKER` / `PROSODY_BLOCK_CLOSE`. Lookup-Tables `_BERT_LABEL_DE`, `_PROSODY_MOOD_DE`, `_PROSODY_TEMPO_DE`. Schwellen `_BERT_HIGH=0.7`, `_PROSODY_DOMINATES_CONFIDENCE=0.5`, `_MIN_CONFIDENCE_FOR_BLOCK=0.3`.
- **Wrapper:** `inject_prosody_context(system_prompt, prosody_result, *, bert_label=None, bert_score=None)` — Backward-Compat-Signatur (keyword-only-Parameter additiv). Idempotent. Logging `[PROSODY-204]` wenn BERT mitgegeben, `[PROSODY-190]` ohne.
- **Verdrahtung:** [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) — JSON-Parse + Type-Guard (nur `dict`), BERT-Call in try/except (fail-open), Aufruf mit Keyword-Args.
- **Tests:** 33 in [`test_p204_prosody_context.py`](zerberus/tests/test_p204_prosody_context.py) (`TestBuildProsodyBlock` 9: Marker, Stimme/Tempo, Konsens-mit-BERT, Konsens-ohne-BERT, Stub-Reject, Low-Conf-Reject, None/Wrong-Type, Invalid-Conf, Unknown-Mood-Fallback; `TestWorkerProtectionNoNumbers` 3 parametrisiert: Regex-Check `%`/`\d+\.\d+`/`\b\d+\b`; `TestConsensusLabel` 6: Inkongruenz, Voice-dominiert, BERT-Fallback, Neutral, ohne-BERT, Invalid-Inputs; `TestBertQualitative` 6: positive/negative high/low, neutral, invalid; `TestInjectWithBert` 5: append-mit-BERT, Stub-skip, Low-Conf-skip, leerer-Base, Idempotenz; `TestP204LegacyVerdrahtung` 6 Source-Audit; `TestMarkerUniqueness` 3: Format, Distinct-vom-PROJEKT-RAG/PROJEKT-KONTEXT). Plus 6 nachgeschärfte Tests in [`test_prosody_pipeline.py::TestInjectProsodyContext`](zerberus/tests/test_prosody_pipeline.py) — Format-Assertions auf neue Marker (`PROSODY_BLOCK_MARKER`, `PROSODY_BLOCK_CLOSE`, qualitative Labels statt Zahlen, Konsens-Zeile, Inkongruenz-via-BERT, neuer Idempotenz-Test).
- **Logging-Tag:** `[PROSODY-204]` für BERT-erweiterten Block, `[PROSODY-190]` bleibt für Stimm-only-Pfad.
- **Teststand:** 1645 baseline (P203b) → **1685 passed** (+40), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`, beide nicht blockierend).
- **Effekt für den User:** Bei Voice-Input liest DeepSeek im System-Prompt jetzt einen Block wie `[PROSODIE — Stimmungs-Kontext aus Voice-Input]\nStimme: muede\nTempo: langsam\nSentiment-Text: leicht positiv (BERT)\nSentiment-Stimme: muede (Gemma)\nKonsens: muede\n[/PROSODIE]`. Nala kann den Ton subtil anpassen (zurücknehmen wenn jemand müde klingt, nachfragen bei Stress) ohne plakative "Du klingst traurig!"-Reaktionen. Bei getipptem Text: kein Block, Chat unverändert. Bei deaktiviertem Consent: kein Block. Bei Stub-Pipeline (kein Modell geladen): kein Block.
- **Was P204 bewusst NICHT macht:** Persistierung der Prosodie-Daten in der DB (Worker-Protection — Daten sind one-shot pro Request). Kein neues UI (Triptychon P192 zeigt schon). Keine Pipeline-Änderung (Brücke zum LLM, nicht Refactor von Whisper/Gemma/BERT). Kein Voice-Indicator-Header über `X-Voice-Input` o.ä. — der bestehende `X-Prosody-Context`-Header IST der Voice-Indikator (Frontend setzt ihn nur nach Whisper-Roundtrip), Stub-Source-Check + Consent-Header sind defense-in-depth.

---

**Patch 203b** — Hel-UI-Hotfix (BLOCKER, Chris-Bugmeldung) (2026-05-02)

Behebt einen Blocker in Hel: Die UI rendert, aber NICHTS ist anklickbar — Tabs wechseln nicht, Buttons reagieren nicht, Formulare nicht bedienbar. Nala lief unauffällig. Auffällig erst nach P200/P202 (PWA-Roll-Out + Cache-Wipe), Symptom verschleiert vorher durch Browser-Cache.

Root-Cause: Im `loadProjectFiles`-Renderer (eingeführt mit P196) stand fehlerhaftes Python-Quote-Escaping. Im Python-Source: `+ ',\'' + _escapeHtml(f.relative_path).replace(/'/g, "\\'") + '\')" '` — Python evaluiert die Escape-Sequenzen im Triple-Quoted-String und produziert in der ausgelieferten HTML/JS-Zeile: `+ ',''  + _escapeHtml(...).replace(/'/g, "\'") + '')" '`. JavaScript parst das als `+ ',' '' +` (zwei adjacent String-Literale ohne Operator) und wirft `SyntaxError: Unexpected string`. Ein einziger Syntax-Fehler in einem `<script>`-Block invalidiert den **gesamten** Block — alle Funktionen darin werden nicht definiert, inkl. `activateTab`, `toggleHelSettings`, `loadProjects`, `loadMetrics`. Damit: keine Klicks, keine Tabs, kein nichts. Nala unbetroffen, weil eigener Renderer.

Fix: Inline `onclick="deleteProjectFile(...)"` durch `data-*`-Attribute (`data-project-id`, `data-file-id`, `data-relative-path`) plus Event-Delegation per `addEventListener` ersetzt. Pattern ist immun gegen Quote-Escape-Probleme (Filename geht durch `_escapeHtml` direkt ins Attribut, statt durch eine fragile JS-String-Concat-Kette mit `replace(/'/g, ...)`) und gleichzeitig XSS-sicher.

Warum erst nach P200/P202 sichtbar: Bug existiert seit P196. Bis P200 hatte der Browser eine ältere Hel-Version aus dem HTTP-Cache. Mit P200 (SW-Roll-Out + Cache-v1) wechselte der Cache, mit P202 (SW-v2-Activate + Wipe) wurde alles geräumt — der Browser holte die echte aktuelle Hel-Seite mit dem P196-Bug. Chris' manuelles Unregister + Cache-Wipe + Hard-Refresh hat das Symptom dann deutlich gemacht.

- **Fix:** [`hel.py::loadProjectFiles`](zerberus/app/routers/hel.py) — `<button class="proj-file-delete-btn" data-project-id="..." data-file-id="..." data-relative-path="...">` plus `list.querySelectorAll('.proj-file-delete-btn').forEach(btn => btn.addEventListener('click', () => deleteProjectFile(...)))`. Kein `onclick`-Attribut, kein `replace(/'/g, "\\'")` mehr.
- **Tests:** 10 neue in [`test_p203b_hel_js_integrity.py`](zerberus/tests/test_p203b_hel_js_integrity.py) — drei Source-Audit-Tests gegen das alte Bug-Pattern (`+ ',''`, `onclick="deleteProjectFile(`, `replace(/'/g, "\\'")`), fünf Source-Audit-Tests für die neue Event-Delegation (Klassen-Name + drei data-Attribute + addEventListener-Nähe), ein Smoke-Test gegen den Endpunkt-Output, und ein **JS-Integrity-Test** der ALLE inline `<script>`-Blöcke aus `ADMIN_HTML` extrahiert und mit `node --check` validiert (skipped wenn `node` nicht im PATH). Letzterer hätte den Bug bei P196 sofort gefangen — nachträglich eingebaut als Schutz vor Wiederholung. Plus 1 angepasster bestehender Test (`test_projects_ui::test_file_list_has_delete_button` — Block-Range erweitert + Klassen-Name-Check, weil event-delegation den Block länger macht).
- **Logging:** Kein neuer Tag — Hotfix bleibt unter `[PWA-200]`/`[PWA-202]`-Doku-Klammer im Quelltext.
- **Teststand:** lokal 1635 baseline (P203a) → **1645 passed** (+10), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste.
- **Effekt für den User:** Hel wieder voll bedienbar — Tabs wechseln, Buttons funktionieren, Forms gehen. Beim Laden der Projekte-Tab werden Files mit Lösch-Button korrekt gerendert, Klick auf Lösch-Button feuert die Delete-Bestätigung. Kein Browser-Cache-Reset mehr nötig (Bug-Pattern aus dem HTML raus).
- **Lessons:** (a) JS-Syntax-Errors in inline `<script>`-Blöcken sind silent-killers für die ganze Page — ein Test der `node --check` über alle Inline-Scripts laufen lässt fängt das früh. (b) Inline `onclick` mit String-Concat über benutzergenerierte Daten ist immer fragil — Event-Delegation mit `data-*`-Attributen ist robust und XSS-sicher. (c) Browser-/SW-Caches können Bugs monatelang verschleiern — bei "neuen" Symptomen nach Cache-Wipe immer auch ältere Patches auf rendering-Probleme prüfen.

---

**Patch 203a** — Project-Workspace-Layout (Phase 5a #5, Vorbereitung) (2026-05-02)

Eigenständige Aufteilung von P203 (Code-Execution-Pipeline, Phase-5a-Ziel #5) durch Coda: Das Original-Ziel ist groß (Workspace + Sandbox-Mount + Tool-Use-LLM + UI-Synthese), wird in drei Sub-Patches zerlegt. P203a legt heute das Workspace-Layout — die Sandbox kann Dateien später nur dann sinnvoll mounten, wenn sie an ihrem `relative_path` (statt unter den SHA-Hash-Pfaden) im Filesystem stehen. Pro Projekt entsteht beim Upload/Template-Materialize ein echter Working-Tree unter `<data_dir>/projects/<slug>/_workspace/`.

Architektur-Entscheidung: **Hardlink primär (`os.link`), Copy als Fallback (`shutil.copy2`).** Hardlinks haben gleiche Inode wie der SHA-Storage — kein Plattenplatz-Verbrauch, instantan, atomar. Bei `OSError` (cross-FS, NTFS-without-dev-mode, FAT32, Permission) fällt der Helper auf `shutil.copy2`. Die gewählte Methode wird im Return ausgewiesen (`"hardlink"` / `"copy"` / `None`-bei-noop) und geloggt — auf Windows-Test-Maschinen ohne dev-mode wird damit auch der Copy-Pfad live mitgetestet (Monkeypatch-Test simuliert `os.link`-Failure).

**Atomic via Tempfile + os.replace.** Auch im Workspace, nicht nur im SHA-Storage. Grund: parallele Sandbox-Reads (P203b) dürfen nie ein halb-geschriebenes Workspace-File sehen. Pattern dupliziert (statt Import aus hel.py), weil das Workspace-Modul auch ohne FastAPI-Stack importierbar bleiben muss (Tests, künftige CLI).

**Pfad-Sicherheit zwei-stufig.** `is_inside_workspace(target, root)` resolved beide Pfade und prüft `relative_to` — schützt gegen `../../etc/passwd`-style relative_paths aus alten Datenbanken oder Migrations. Plus: `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet — verhindert ein versehentliches `wipe_workspace(Path("/"))` bei Slug-Manipulation.

**Best-Effort-Verdrahtung in vier Trigger-Punkten.** Upload-Endpoint (nach `register_file` + nach RAG-Index), Delete-File-Endpoint (nach `delete_file`, mit Slug aus extra `get_project`-Call vor dem DB-Delete), Delete-Project-Endpoint (`wipe_workspace` nach `delete_project`), `materialize_template` (nach jedem `register_file`). Alle vier wickeln den Workspace-Call in try/except — Hauptpfad bleibt grün auch wenn Hardlink/Copy scheitert. Lazy-Import (`from zerberus.core import projects_workspace` im try-Block) wie bei RAG, damit der Helper nicht beim Import-Time geladen wird.

**`sync_workspace` als Komplett-Resync.** Materialisiert alle DB-Files, entfernt Orphans (Files im Workspace, die nicht mehr in der DB sind). Idempotent. Nicht in den Endpoints verdrahtet (Single-File-Trigger reichen), aber als Recovery-API für künftige CLI/Reindex-Endpoint vorhanden — Pre-P203a-Files können damit in den Workspace nachgezogen werden.

**Was P203a bewusst NICHT macht** (kommt mit P203b/c):

- Sandbox-Mount auf den Workspace-Ordner — der bestehende `SandboxManager` (P171) verbietet Volume-Mount explizit ("Kein Volume-Mount vom Host"). P203b muss entweder eine Erweiterung (`workspace_mount: Optional[Path]`) oder eine Schwester-Klasse bauen. Empfehlung: Erweiterung mit Read-Only-Mount per Default, Read-Write nur explizit.
- LLM-Tool-Use-Pfad für Code-Generation — kommt mit P203c.
- Frontend-Render von Code+Output-Blöcken — kommt mit P203c.

- **Pure-Function-Schicht:** [`projects_workspace.py::workspace_root_for`](zerberus/core/projects_workspace.py) + `is_inside_workspace` — keine I/O, deterministisch, Pfad-Sicherheits-Check. Verwendbar in P203b für Mount-Source-Validation.
- **Sync-FS-Schicht:** `materialize_file` (mit Hardlink-primary + Copy-Fallback + Idempotenz via Inode/Size-Check), `remove_file` (räumt leere Eltern bis Workspace-Root), `wipe_workspace` (Sicherheits-Reject auf Wrong-Dirname). Pure-Aber-Mit-FS-I/O.
- **Async DB-Schicht:** `materialize_file_async`, `remove_file_async`, `sync_workspace` — DB-Lookup + Schicht-Wrapper.
- **Verdrahtung:** [`hel.py::upload_project_file_endpoint`](zerberus/app/routers/hel.py), [`hel.py::delete_project_file_endpoint`](zerberus/app/routers/hel.py), [`hel.py::delete_project_endpoint`](zerberus/app/routers/hel.py), [`projects_template.py::materialize_template`](zerberus/core/projects_template.py) — alle Best-Effort mit Lazy-Import.
- **Config-Flag:** [`config.py::ProjectsConfig.workspace_enabled`](zerberus/core/config.py) — Default `True`. Tests können abschalten (siehe `TestWorkspaceDisabled`-Klasse).
- **Logging-Tag:** `[WORKSPACE-203]` — Materialize, Wipe, Sync.
- **Tests:** 36 in [`test_projects_workspace.py`](zerberus/tests/test_projects_workspace.py) (4 Pure-Function inkl. Traversal-Reject + No-IO; 6 materialize_file inkl. nested-dirs + idempotent + missing-source + Copy-Fallback via monkeypatched `os.link`; 5 remove_file inkl. cleans-empty-parents + keeps-non-empty + traversal-reject; 3 wipe_workspace inkl. rejects-wrong-dirname; 4 sync_workspace inkl. removes-orphans; 3 async-Wrapper; 4 Endpoint-Integration mit echten Hel-Endpoints; 1 workspace_disabled-Pfad; 5 Source-Audit-Tests für die vier Verdrahtungs-Stellen).
- **Teststand:** lokal 1599 baseline → **1635 passed** (+36), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`, beide nicht blockierend).
- **Effekt für die nächste Coda-Session:** P203b kann sofort starten — `workspace_root_for(slug, base_dir)` liefert den Mount-Pfad, `is_inside_workspace` validiert User-Inputs, `sync_workspace` ist Recovery-API. P203b's Job ist nur noch: `SandboxManager.execute` um `workspace_mount` erweitern + `execute_in_workspace`-Convenience + Tests.

---

**Patch 202** — PWA-Auth-Hotfix: SW-Navigation-Skip + Cache-v2 (2026-05-02)

Behebt einen kritischen Bug aus P200: Hel war im Browser nur noch als JSON-Antwort `{"detail":"Not authenticated"}` sichtbar — kein Basic-Auth-Prompt mehr. Nala und Huginn unauffällig. Ursache: Der von P200 eingeführte Service-Worker fängt im Scope `/hel/` ALLE GET-Requests ab und macht `event.respondWith(fetch(event.request))`. Bei navigierenden Top-Level-Anfragen liefert der SW damit die 401-Response unverändert an die Page zurück — und der Browser ignoriert in diesem Fall den `WWW-Authenticate: Basic`-Header, der normalerweise den nativen Auth-Prompt triggert. Ergebnis: User sah JSON statt Login-Dialog.

Architektur-Entscheidung: **Navigation-Requests gar nicht erst abfangen.** Statt nachträglich auf 401 zu reagieren oder Auth-Header zu injecten, returnt der SW jetzt früh wenn `event.request.mode === 'navigate'`. Die Navigation läuft durch den nativen Browser-Stack — inklusive Auth-Prompt, HTTPS-Indikator, Mixed-Content-Warnung, etc. Das ist ohnehin sauberere PWA-Hygiene: SWs cachen statische Assets, nicht HTML-Pages.

**Cache-Versions-Bump auf -v2.** Damit der activate-Hook der neuen SW-Version den verseuchten v1-Cache räumt, der noch /hel/-Navigations-Antworten enthalten könnte. Da der SW selbst per `Cache-Control: no-cache` ausgeliefert wird, bekommt jeder Browser den neuen SW beim nächsten Reload, ohne manuelles Eingreifen — der activate räumt dann automatisch.

**APP_SHELL ohne Root-Pfad.** Vorher enthielt `HEL_SHELL` den Eintrag `"/hel/"` — beim install-Hook versuchte der SW per `cache.addAll(APP_SHELL)` den Hel-Root zu cachen, was wegen Basic-Auth mit 401 fehlschlug. `Promise.all` rejected dann komplett, die `.catch(() => null)`-Klausel im Code swallow den Fehler still, aber semantisch falsch. Jetzt sind die SHELL-Listen rein statische Assets, die alle 200 liefern — der precache läuft sauber durch.

**Side-Effect:** HTML-Reload geht jetzt immer übers Netz (kein Offline-Modus für die Hauptseite). Für den Heimserver-Use-Case akzeptabel — ohne laufenden Server gibt es ohnehin keinen sinnvollen Betrieb (Chat-Endpoints, Whisper, Sandbox sind alle online-only). Wenn später ein echter Offline-Modus gewünscht ist, müsste man den Approach umkrempeln (cache-first für Navigation, mit eigenem Auth-Handling) — aktuell nicht relevant.

- **SW-Template:** [`pwa.py::SW_TEMPLATE`](zerberus/app/routers/pwa.py) — drei Zeilen früh-return für `event.request.mode === 'navigate'`, vor dem `respondWith`-Block.
- **Cache-Names:** `nala-shell-v1` → `nala-shell-v2`, `hel-shell-v1` → `hel-shell-v2` in den Endpoint-Funktionen `nala_service_worker` + `hel_service_worker`.
- **APP_SHELL:** `NALA_SHELL` und `HEL_SHELL` ohne Root-Pfad-Eintrag — nur statische Assets (`/static/css/...`, `/static/favicon.ico`, `/static/pwa/*.png`).
- **Tests:** 5 neue in [`test_pwa.py`](zerberus/tests/test_pwa.py) — Pure-Function-Test für navigation-skip im SW-Body, drei Shell-Lists-Tests (kein Root-Pfad in Nala/Hel), und ein End-to-End-Test mit `TestClient` der `verify_admin` ohne Credentials anpingt und den `WWW-Authenticate: Basic`-Header verifiziert. Plus 3 angepasste bestehende Tests (Cache-Name v2 statt v1, Asset-Audit auf Static-Assets statt Root-Pfad).
- **Logging:** Kein neuer Tag — die SW-Korrektur ist klein-genug für `[PWA-200]`/`[PWA-202]` als Doku-Hinweis im Quelltext-Header. Kein Server-seitiger Log-Output.
- **Teststand:** 1594 → **1602 passed** (+8), 4 xfailed pre-existing.
- **Effekt für den User:** `/hel/` zeigt wieder den nativen Browser-Auth-Prompt. Nach Login funktioniert Hel-UI komplett wie vor P200. Bestehende SW-v1-Installationen werden automatisch auf v2 hochgezogen, sobald der User `/hel/` neu lädt.

---

**Patch 201** — Phase 5a #4: Nala-Tab "Projekte" + Header-Setter (2026-05-02)

Schließt Phase-5a-Ziel #4 ("Dateien kommen ins Projekt") komplett ab — der Backend-Teil war seit P196 da, der Indexer seit P199, jetzt verdrahtet P201 das letzte Stück: Nala-User können vom Chat aus ein aktives Projekt auswählen, und ab da fließt der Persona-Overlay (P197) und das Datei-Wissen (P199-RAG) automatisch in jede Antwort. Vorher war diese Kombination nur über externe Clients (curl, SillyTavern, eigene Skripte) erreichbar — die Hel-CRUD-UI (P195) konnte zwar Projekte anlegen und Dateien hochladen, aber kein User in Nala konnte sie aktivieren.

Architektur-Entscheidung: **Eigener `/nala/projects`-Endpoint statt Wiederverwendung von `/hel/admin/projects`.** Drei Gründe: (a) Hel-CRUD ist Basic-Auth-gated, Nala-User haben aber JWT (zwei Auth-Welten, kein Bridge nötig). (b) Nala-User darf NIEMALS `persona_overlay` sehen — das ist Admin-Geheimnis, kann Prompt-Engineering-Spuren oder interne Tonfall-Hints enthalten, die der User nicht beeinflussen können soll. Der Endpoint slimmed das Response-Dict auf `{id, slug, name, description, updated_at}`. (c) Archivierte Projekte werden hier per Default ausgeblendet — der User soll keine "alten" Projekte sehen, die er nicht mehr nutzen soll.

**Header-Injektion zentral in `profileHeaders()`.** Statt nur den Chat-Fetch zu modifizieren, hängt P201 den `X-Active-Project-Id`-Header in die zentrale Helper-Funktion, die ALLE Nala-Calls verwenden (Chat, Voice, Whisper, Profile-Endpoints). Damit ist garantiert, dass der Projekt-Kontext konsistent gegated ist — keine Möglichkeit, dass ein neuer Endpoint den Header "vergisst".

**State in zwei localStorage-Keys.** `nala_active_project_id` (numerisch) für die Header-Injektion, `nala_active_project_meta` (JSON: id+slug+name) für den Header-Chip-Renderer ohne Re-Fetch. Beim Logout (handle401) wird beides bewusst NICHT gelöscht — der nächste Login bekommt das Projekt automatisch zurück, was für den typischen Use-Case (gleicher User, gleicher Browser) genau richtig ist.

**Zombie-ID-Schutz.** Wenn `loadNalaProjects` ein Projekt nicht mehr in der Liste findet (gelöscht oder archiviert in der Zwischenzeit), wird die aktive Auswahl automatisch geräumt. Sonst hängt der Header-Chip an einer Zombie-ID und das Backend würde einen non-existing-project-Header bekommen (was P197 zwar gracefully ignoriert, aber sauberer ist client-seitig zu räumen).

**UI-Verdrahtung minimal-invasiv.** Neuer Settings-Tab "📁 Projekte" zwischen "Ausdruck" und "System" — bestehende Tab-Mechanik wird wiederverwendet, kein neues Modal, kein Sidebar-Tab. Lazy-Loading: `loadNalaProjects()` läuft erst, wenn der User auf den Tab klickt, nicht beim Modal-Öffnen — spart Roundtrip wenn der User nur Theme ändern will. Active-Project-Chip im Chat-Header, klick öffnet Settings + springt direkt auf den Projekte-Tab. Chip nur sichtbar wenn ein Projekt aktiv ist.

**XSS-Schutz im Renderer.** Der Listen-Renderer rendert User-eingegebene Felder (name, slug, description) — alle drei laufen durch `escapeProjectText()` (wandelt `&`, `<`, `>`, `"`, `'` in HTML-Entities). Source-Audit-Test zählt mindestens drei `escapeProjectText`-Aufrufe in `renderNalaProjectsList`, damit ein vergessener Aufruf in zukünftigen Refactorings sofort auffällt.

- **Backend-Endpoint:** [`nala.py::nala_projects_list`](zerberus/app/routers/nala.py) — `GET /nala/projects`, JWT-pflichtig via `request.state.profile_name`, ruft `projects_repo.list_projects(include_archived=False)` und slimmed das Response-Dict.
- **UI-Tab:** Settings-Modal um vierten Tab "📁 Projekte" erweitert. Panel mit Aktiv-Anzeige, Aktualisieren-Button, Auswahl-löschen-Button, Listen-Container.
- **Header-Chip:** [`nala.py`](zerberus/app/routers/nala.py) — `<span id="active-project-chip" class="active-project-chip">` neben dem Profile-Badge im main-header. Goldener Pill-Border, klick öffnet Settings + Projects-Tab.
- **JS-Funktionen:** `getActiveProjectId`, `getActiveProjectMeta`, `setActiveProject`, `clearActiveProject`, `selectActiveProjectById`, `renderActiveProjectChip`, `renderNalaProjectsActive`, `renderNalaProjectsList`, `escapeProjectText`, `loadNalaProjects` — kompletter Lifecycle.
- **Header-Injektion:** [`nala.py::profileHeaders`](zerberus/app/routers/nala.py) — drei zusätzliche Zeilen, die `X-Active-Project-Id` setzen wenn aktiv. Damit wirkt es auf ALLE Nala-Calls, nicht nur Chat.
- **Lazy-Load:** [`switchSettingsTab`](zerberus/app/routers/nala.py) ruft `loadNalaProjects()` wenn der Projects-Tab aktiviert wird.
- **CSS:** `.active-project-chip` mit gold-border, transparent-Hintergrund, hover-Glow, max-width + ellipsis für lange Slugs.
- **Tests:** 21 neue in [`test_nala_projects_tab.py`](zerberus/tests/test_nala_projects_tab.py) (6 Endpoint inkl. 401, archived-versteckt, persona_overlay-NICHT-im-Response, minimal-Felder; 11 Source-Audit für Tab/Panel/Chip/JS/Header-Injektion/Lazy-Load; 2 XSS-Schutz inkl. Min-Count escapeProjectText; 1 Zombie-ID-Schutz; 1 Tab-Lazy-Load). Plus 1 nachgeschärfter Test in `test_settings_umbau.py` (alter `openSettingsModal()`-Proxy-Test → spezifischer 🔧-Emoji + icon-btn-Pattern-Check, weil P201 erlaubt openSettingsModal im Header NUR via Project-Chip).
- **Teststand:** 1572 → **1594 passed** (+22), 4 xfailed pre-existing, 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #4:** ✅ Dateien kommen ins Projekt (komplett: P196 Upload + P199 Index + P201 Nala-Auswahl). Damit sind die Ziele #1, #2, #3, #4, #16 durch. Nächste sinnvolle Patches: #5 (Code-Execution P202).

**Patch 200** — Phase 5a #16: PWA für Nala + Hel (2026-05-02)

Schließt Phase-5a-Ziel #16 ("Nala + Hel als PWA") ab. Beide Web-UIs lassen sich auf iPhone (Safari → "Zum Home-Bildschirm") und Android (Chrome → "App installieren") als eigenständige Apps installieren — Browser-Chrome verschwindet, Splash-Screen + Theme-Color stimmen, Icon im Kintsugi-Stil (Gold auf Blau für Nala, Rot auf Anthrazit für Hel). Kein Offline-Modus, keine Push-Notifications, kein Background-Sync — alles bewusst weggelassen, weil der Heimserver eh laufen muss und Huginn die Push-Schiene besetzt.

Architektur-Entscheidung: **Eigener Router `pwa.py` statt Erweiterung des Hel/Nala-Routers.** Hintergrund: `hel.router` hat router-weite Basic-Auth-Dependency (`dependencies=[Depends(verify_admin)]`), die jeden Endpoint dort gated. Würde man `/hel/manifest.json` und `/hel/sw.js` in `hel.router` definieren, würde der Browser beim Manifest-Fetch eine Auth-Challenge bekommen und der Install-Prompt nie erscheinen. Lösung: separater `pwa.router` ohne Dependencies, in `main.py` VOR `hel.router` eingehängt — gleiche URL-Pfade, aber andere Auth-Policy. FastAPI matcht die URL-Pfade global: weil `hel.router` keine Route für `/manifest.json` oder `/sw.js` definiert, gibt es keinen Konflikt; `pwa.router` gewinnt durch frühere Registrierung trotzdem die Race-Condition für eventuell hinzukommende Routes.

**Service-Worker-Scope-Trick:** Der Browser begrenzt den SW-Scope per Default auf den Pfad, von dem die SW-Datei ausgeliefert wird. `/nala/sw.js` → scope `/nala/`, `/hel/sw.js` → scope `/hel/`. Damit braucht es KEINEN `Service-Worker-Allowed`-Header und keine Root-Position für die SW-Datei. Genau die richtige Granularität: die Nala-PWA cacht nur Nala-URLs, die Hel-PWA nur Hel-URLs.

**Manifeste pro App, nicht ein gemeinsames:** Beide Apps brauchen separate Icons, Theme-Colors, Namen — zwei `manifest.json`-Endpoints sind sauberer als Conditional-Logic in einem Manifest. Konstanten `NALA_MANIFEST` und `HEL_MANIFEST` in `pwa.py` sind Pure-Python-Dicts, direkt als Tests ohne Server konsumierbar.

**Icon-Generierung deterministisch via PIL.** Skript `scripts/generate_pwa_icons.py` zeichnet 4 PNGs (Nala 192/512, Hel 192/512) — dunkler Hintergrund, großer goldener/roter Initial-Buchstabe, drei dünne Kintsugi-Adern in der Akzentfarbe als Bruchnaht-Anspielung. Deterministisch (kein RNG), damit Re-Runs bytes-identische PNGs erzeugen und der Git-Diff sauber bleibt. Fonts werden aus systemweiten Serif-Kandidaten (Georgia/Times/DejaVuSerif/Arial) gesucht, mit PIL-Default-Font als Fallback.

**Service-Worker-Logik minimal:** Install precacht App-Shell-Liste (HTML-Page + shared-design.css + favicon + Icons), Activate räumt alte Caches, Fetch macht network-first mit cache-fallback (damit Updates direkt durchgehen). Non-GET-Requests passen unverändert durch. Kein Background-Sync, kein Push-Listener.

**Wirkung im HTML:** `<head>` beider Templates erweitert um `<link rel="manifest">`, `<meta name="theme-color">`, vier Apple-Mobile-Web-App-Meta-Tags, zwei Apple-Touch-Icons (192/512). Service-Worker-Registrierung als kleines `<script>` vor `</body>`: Feature-Detection, `load`-Listener, `register('/nala/sw.js', { scope: '/nala/' })`, Catch loggt nach Console (kein UI-Block beim SW-Fail).

- **Neuer Router:** [`zerberus/app/routers/pwa.py`](zerberus/app/routers/pwa.py) — vier Endpoints (`/nala/manifest.json`, `/nala/sw.js`, `/hel/manifest.json`, `/hel/sw.js`), keine Auth-Dependencies. Pure-Function-Schicht: `render_service_worker(cache_name, shell)` rendert SW-JS aus Template + Cache-Name + Shell-URL-Liste. Konstanten `NALA_MANIFEST`, `HEL_MANIFEST`, `NALA_SHELL`, `HEL_SHELL`.
- **Verdrahtung in [`main.py`](zerberus/main.py):** `from zerberus.app.routers import legacy, nala, orchestrator, hel, archive, pwa`, dann `app.include_router(pwa.router)` als ERSTER `include_router`-Call (vor allen anderen). Kommentar erklärt warum die Reihenfolge zwingend ist.
- **HTML-Verdrahtung Nala:** [`nala.py::NALA_HTML`](zerberus/app/routers/nala.py) `<head>` um sieben Tags erweitert (Manifest-Link, Theme-Color #0a1628, Apple-Capable yes, Apple-Status-Bar black-translucent, Apple-Title "Nala", zwei Apple-Touch-Icons). SW-Registrierung als 8-Zeilen-Script vor `</body>`.
- **HTML-Verdrahtung Hel:** [`hel.py::ADMIN_HTML`](zerberus/app/routers/hel.py) analog mit Theme-Color #1a1a1a, Apple-Title "Hel", Hel-Icons. SW-Registrierung mit Scope `/hel/`.
- **Icons:** vier PNGs unter `zerberus/static/pwa/{nala,hel}-{192,512}.png`, generiert via `scripts/generate_pwa_icons.py`. Skript ist Repo-Bestandteil, damit Icons reproduzierbar sind und das Theme später zentral angepasst werden kann.
- **Logging:** Kein eigener Tag — SW-Fehler landen browser-seitig in `console.warn` mit Tag `[PWA-200]`. Server-seitig sind die Endpoints stumm (Standard-Access-Log reicht).
- **Tests:** 39 neue Tests in [`test_pwa.py`](zerberus/tests/test_pwa.py) — 5 Pure-Function-Tests für `render_service_worker` (Cache-Name, Shell-URLs, alle drei Event-Listener, skipWaiting/clients.claim, GET-only-Caching), 6 Manifest-Dict-Tests (Pflichtfelder, 192+512-Icons pro App, Themes unterscheiden sich, JSON-serialisierbar), 4 Endpoint-Tests (Status 200, korrekte Media-Types, Body-Inhalte, Cache-Control), 8 Source-Audit-Tests pro HTML (Manifest-Link, Theme-Color, alle Apple-Tags, SW-Registrierung mit korrektem Scope), 4 Icon-Existenz-Tests (PNG-Magic-Bytes), 3 Routing-Order-Tests (pwa-Import, pwa-Include, pwa-VOR-hel), 2 Generator-Skript-Tests.
- **Teststand:** 1533 → **1572 passed** (+39), 4 xfailed pre-existing, 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #16:** ✅ Nala + Hel als PWA. Unabhängiges Ziel, blockiert nichts. Damit sind die Ziele #1, #2, #3 und #16 durch. Nächste sinnvolle Patches: #5 (Code-Execution P201) oder #4 abschließen via Nala-Tab "Projekte" (P202).

**Patch 199** — Phase 5a #3: Projekt-RAG-Index (2026-05-02)

Schließt Phase-5a-Ziel #3 ("Projekte haben eigenes Wissen") ab. Jedes Projekt bekommt einen eigenen, isolierten Vektor-Index unter `data/projects/<slug>/_rag/{vectors.npy, meta.json}` — der globale RAG-Index in `modules/rag/router.py` bleibt unberührt. Damit kann das LLM beim aktiven Projekt (P197 `X-Active-Project-Id`-Header) auf Inhalte aus den Projektdateien zugreifen, ohne dass projekt-spezifische Chunks den globalen Memory-Index verschmutzen.

Architektur-Entscheidung: **Pure-Numpy-Linearscan statt FAISS.** Per-Projekt-Indizes sind klein (typisch 10–2000 Chunks). Ein `argpartition` auf einem `(N, 384)`-Array ist auf der Größenordnung schneller als FAISS-Setup-Overhead und macht Tests dependency-frei (kein faiss-Mock nötig). Persistenz als `vectors.npy` (float32) + `meta.json` (Liste, gleiche Reihenfolge). Atomare Writes via `tempfile.mkstemp` + `os.replace`. Eine FAISS-Migration ist trivial nachrüstbar, falls Projekte signifikant >10k Chunks bekommen — aber das ist heute nicht der Bottleneck.

Embedder: **MiniLM-L6-v2 (384 dim)** als Default, lazy-loaded — kompatibel mit dem Legacy-Globalpfad und ohne sprach-spezifisches Setup. Tests monkeypatchen `_embed_text` mit einem Hash-basierten 8-dim-Pseudo-Embedder; der echte SentenceTransformer wird in Unit-Tests nie geladen. Wenn der globale Index irgendwann komplett auf Dual umsteigt, kann man den Per-Projekt-Pfad mit demselben Modell betreiben — das ist eine reine Konfig-Änderung im Wrapper.

Chunker-Reuse: Code-Files (.py/.js/.ts/.html/.css/.json/.yaml/.sql) gehen durch den existierenden `code_chunker.chunk_code` (P122 — AST/Regex-basiert). Prosa (.md/.txt/Default) durch einen neuen lokalen Para-Splitter mit weichen Absatz-Grenzen (max 1500 Zeichen, snap an Doppel-Newline, Sentence-Split-Fallback für überlange Absätze). Bei Python-SyntaxError im Code-Pfad: Fallback auf Prose, damit kaputte Dateien trotzdem indexiert werden.

Idempotenz: Pro `file_id` höchstens ein Chunk-Set im Index. Beim Re-Index wird der alte Block für die file_id zuerst entfernt — gleicher `sha256` ergibt funktional dasselbe Ergebnis (Hash-Embedder ist deterministisch), anderer `sha256` ersetzt den alten Block. Das vermeidet Doubletten beim Re-Upload mit gleichem `relative_path`.

Trigger-Punkte: (a) `upload_project_file_endpoint` NACH `register_file` — neuer File wandert direkt in den Index. (b) `materialize_template` ruft am Ende `index_project_file` für jede neu angelegte Skelett-Datei — Template-Inhalte sind sofort retrievbar. (c) `delete_project_file_endpoint` ruft `remove_file_from_index` NACH dem DB-Delete — keine stale Treffer mehr. (d) `delete_project_endpoint` löscht den ganzen `_rag/`-Ordner. Alle Trigger sind Best-Effort: Indexing-Fehler brechen den Hauptpfad NICHT ab; der Eintrag steht in der DB, der Index lässt sich später nachziehen.

Wirkung im Chat: Nach P197 (Persona-Merge), nach P184 (`_wrap_persona`), nach P185 (Runtime-Info), nach P118a (Decision-Box) und nach P190 (Prosodie), aber VOR `messages.insert(0, system)`. Der Block beginnt mit `[PROJEKT-RAG — Kontext aus Projektdateien]` als eindeutigem Marker, gefolgt von einer kurzen Anweisung an das LLM und Top-K Hits in Markdown-Sektionen pro File. Best-Effort: jeder Fehler (Embedder fehlt, Index kaputt) → kein Block, Chat läuft normal weiter. Logging-Tag `[RAG-199]` mit `project_id`/`slug`/`chunks_used`. Pro Chat-Request höchstens ein Embed-Call (User-Query) + ein Linearscan über den Projekt-Index — Latenz ~10ms typisch.

Feature-Flags: `ProjectsConfig.rag_enabled: bool = True` (kann via config.yaml ausgeschaltet werden, nützlich für Tests/Setups ohne `sentence-transformers`). `ProjectsConfig.rag_top_k: int = 5` (max Anzahl Chunks pro Query, vom Chat-Endpoint genutzt). `ProjectsConfig.rag_max_file_bytes: int = 5 * 1024 * 1024` (5 MB — drüber: skip beim Indexen, weil's typisch Bilder/Archive sind).

Defensive Behaviors: leere Datei → skip mit `reason="empty"`, binäre Datei (UTF-8-Decode-Fehler) → skip mit `reason="binary"`, Datei zu groß → skip mit `reason="too_large"`, Bytes nicht im Storage → skip mit `reason="bytes_missing"`, Embed-Fehler → skip mit `reason="embed_failed"`, Embedder-Dim-Wechsel zwischen Sessions → Index wird komplett neu aufgebaut (Dim-Mismatch im `top_k_indices` liefert leere Ergebnisliste statt Crash). Inkonsistenter Index (nur eine der zwei Dateien existiert) → leere Basis, der nächste `index_project_file`-Call baut sauber auf.

- **Neuer Helper:** [`zerberus/core/projects_rag.py`](zerberus/core/projects_rag.py) — Pure-Functions `_split_prose`, `chunk_file_content`, `top_k_indices`, `format_rag_block`. File-I/O `load_index`, `save_index`, `remove_project_index`, `index_paths_for`. Embedder-Wrapper `_embed_text` (lazy MiniLM-L6-v2). Async `index_project_file`, `remove_file_from_index`, `query_project_rag`. Konstanten `RAG_SUBDIR`, `VECTORS_FILENAME`, `META_FILENAME`, `PROJECT_RAG_BLOCK_MARKER`, `DEFAULT_EMBED_MODEL`, `DEFAULT_EMBED_DIM`.
- **Verdrahtung Hel:** [`hel.py::upload_project_file_endpoint`](zerberus/app/routers/hel.py) ruft NACH `register_file` `await projects_rag.index_project_file(project_id, file_id, base)` auf — Response erweitert um `rag: {chunks, skipped, reason}`. [`hel.py::delete_project_file_endpoint`](zerberus/app/routers/hel.py) ruft `remove_file_from_index` — Response erweitert um `rag_chunks_removed`. [`hel.py::delete_project_endpoint`](zerberus/app/routers/hel.py) merkt den Slug VOR dem Delete und ruft `remove_project_index` danach.
- **Verdrahtung Materialize:** [`projects_template.py::materialize_template`](zerberus/core/projects_template.py) ruft am Ende JEDES erfolgreichen `register_file` `await projects_rag.index_project_file(project_id, registered_id, base_dir)` auf — frisch materialisierte Skelett-Files sind sofort im Index.
- **Verdrahtung Chat:** [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) — nach P190 (Prosodie), VOR `messages.insert(0, system)`: wenn `active_project_id` + `project_slug` + `last_user_msg` + `rag_enabled`, dann `query_project_rag` und `format_rag_block` an den `sys_prompt` anhängen.
- **Feature-Flags:** `ProjectsConfig.rag_enabled: bool = True`, `rag_top_k: int = 5`, `rag_max_file_bytes: int = 5 * 1024 * 1024` — alle Defaults im Pydantic-Modell (config.yaml gitignored).
- **Logging:** Neuer `[RAG-199]`-Tag mit `slug`/`file_id`/`path`/`chunks`/`total` beim Indexen, `chunks_used` beim Chat-Query, `chunks_removed` beim Delete. Inkonsistenter/kaputter Index → WARN; Embed/Query-Fehler → WARN mit Exception-Text.
- **Tests:** 46 neue Tests in [`test_projects_rag.py`](zerberus/tests/test_projects_rag.py) — 5 Prose-Splitter-Edge-Cases, 5 Chunk-File-Cases (Code/Markdown/Unknown-Extension/SyntaxError-Fallback/Empty), 5 Top-K-Cases (leer/k=0/sortiert/cap-at-size/Dim-Mismatch), 4 Save-Load-Roundtrip + Inconsistent + Corrupted, 2 Remove-Index, 7 Index-File-Cases (Markdown/Idempotent/Empty/Binary/Too-Large/Bytes-Missing/Rag-Disabled), 2 Remove-File + Drop-Empty-Index, 4 Query-Cases (Hit/Empty-Query/Missing-Project/Missing-Index), 2 Format-Block, 3 End-to-End via Upload/Delete-File/Delete-Project, 1 Materialize-Indexes-Templates, 6 Source-Audit. `fake_embedder`-Fixture mit Hash-basiertem 8-dim-Pseudo-Embedder verhindert das Laden des echten SentenceTransformer in Unit-Tests.
- **Teststand:** 1487 → **1533 passed** (+46), 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #3:** ✅ Projekte haben eigenes Wissen. Damit sind die Ziele #1 (Backend P194 + UI P195), #2 (Templates P198) und #3 (RAG-Index) durch. Decision 3 (Persona-Merge-Layer) seit P197 aktiv. Datei-Upload aus Hel-UI (P196) seit dem auch indexiert. Nächste sinnvolle Patches: #5 (Code-Execution P200) oder #4 abschließen via Nala-Tab "Projekte" (P201).

**Patch 198** — Phase 5a #2: Template-Generierung beim Anlegen (2026-05-02)

Schließt Phase-5a-Ziel #2 ("Projekte haben Struktur") ab. Ein neu angelegtes Projekt startete bisher leer — der User mußte selbst eine Struktur hochladen, bevor das LLM überhaupt etwas zu lesen hatte. P198 generiert beim Anlegen zwei Skelett-Dateien: `ZERBERUS_<SLUG>.md` als Projekt-Bibel (analog zu `ZERBERUS_MARATHON_WORKFLOW.md` mit Sektionen "Ziel", "Stack", "Offene Entscheidungen", "Dateien", "Letzter Stand") und ein kurzes `README.md`. Inhalt rendert die Project-Daten ein (Name, Slug, Description, Anlegedatum) — der User hat sofort einen sinnvollen Ausgangspunkt.

Architektur-Entscheidung: Templates landen im SHA-Storage (`data/projects/<slug>/<sha[:2]>/<sha>` — gleiche Konvention wie P196-Uploads), DB-Eintrag in `project_files` mit lesbarem `relative_path`. Damit erscheinen Templates nahtlos in der Hel-Datei-Liste, sind im RAG-Index (P199) indexierbar und in der Code-Execution-Pipeline (P200) sichtbar — ohne Sonderpfad. Pure-Python-String-Templates (kein Jinja, weil das den Stack nicht rechtfertigt). Render-Funktionen sind synchron + I/O-frei und unit-bar; Persistenz liegt separat in der async `materialize_template`.

Idempotenz: Existierende `relative_path`-Einträge werden NICHT überschrieben. Wenn der User in einer früheren Session schon eigene Inhalte angelegt hat, bleiben die unangetastet. Helper liefert nur die TATSÄCHLICH neu angelegten Files zurück — leer, wenn alles schon existiert. Die UNIQUE-Constraint `(project_id, relative_path)` aus P194 wäre der zweite Fallback, aber wir prüfen vorher explizit über `list_files`.

Best-Effort-Verdrahtung: Wenn `materialize_template` crasht (Disk-Full, DB-Lock, was auch immer), bricht das Anlegen NICHT ab. Der Projekt-Eintrag steht, der User sieht eine 200-Antwort, Templates lassen sich notfalls nachgenerieren oder per Hand anlegen. Crash-Path mit Source-Audit-Test verifiziert.

Git-Init bewusst weggelassen: Der SHA-Storage ist kein Working-Tree (Bytes liegen unter Hash-Pfaden, nicht unter `relative_path`). `git init` ergibt erst Sinn mit einem echten `_workspace/`-Layout, das mit der Code-Execution-Pipeline (P200, Phase 5a #5) kommt. Bis dahin: kein halbgares Git-Init, das später wieder umgebogen werden müßte.

- **Neuer Helper:** [`zerberus/core/projects_template.py`](zerberus/core/projects_template.py) — `render_project_bible(project, *, now=None)` + `render_readme(project)` als Pure-Functions, `template_files_for(project, *, now=None)` als Komposit, `materialize_template(project, base_dir, *, dry_run=False, now=None)` als async DB+Storage-Schicht, `_write_atomic()` als lokale Kopie aus `hel._store_uploaded_bytes` (Helper soll auch ohne FastAPI-Stack laufen können). Konstanten `PROJECT_BIBLE_FILENAME_TEMPLATE`, `README_FILENAME` exportiert.
- **Verdrahtung:** [`hel.py::create_project_endpoint`](zerberus/app/routers/hel.py) ruft NACH `projects_repo.create_project()` `materialize_template(project, _projects_storage_base())` auf; Response-Feld `template_files` listet die neu angelegten Einträge.
- **Feature-Flag:** `ProjectsConfig.auto_template: bool = True` (Default `True`, Default in `config.py` weil `config.yaml` gitignored). Kann für Migrations-Tests/Bulk-Imports abgeschaltet werden.
- **Logging:** Neuer `[TEMPLATE-198]`-INFO-Log mit `slug`/`path`/`size`/`sha[:8]` pro neu angelegter Datei + `skip slug=... path=... (already exists)` bei Idempotenz-Skip. Bei Crash WARNING via `logger.exception` mit Slug.
- **Tests:** 23 neue Tests in [`test_projects_template.py`](zerberus/tests/test_projects_template.py) — 6 Pure-Function-Cases (Slug-Uppercase, Datum, Sektionen, Description-Block, Empty-Description-Placeholder, Missing-Keys-Defaults), 6 Materialize-Cases (Two-Files, SHA-Storage-Pfad, Idempotenz, User-Content-Schutz, Dry-Run-no-side-effect, Content-Render), 3 End-to-End (Flag-on, Flag-off, Crash-Resilienz), 3 Source-Audit (Imports, Flag-Honor, Konstanten). `disable_auto_template`-Autouse-Fixture in `test_projects_endpoints.py` + `test_projects_files_upload.py` hält deren File-Counts stabil.
- **Teststand:** 1464 → **1487 passed** (+23), 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #2:** ✅ Projekte haben Struktur. Damit sind die Ziele #1 (Backend+UI) + #2 (Struktur) abgeschlossen — als nächstes #3 (Projekt-RAG-Index, P199) oder #5 (Code-Execution, P200).

**Patch 197** — Phase 5a Decision 3: Persona-Merge-Layer aktiviert (2026-05-02)

Schließt die Lücke zwischen P194 (Persona-Overlay-JSON in der DB) / P195 (Hel-UI-Editor dafür) und der eigentlichen Wirkung im LLM-Call. Bisher konnte Chris in der Hel-UI ein `system_addendum` und `tone_hints` pro Projekt pflegen — aber das landete nirgends im System-Prompt. P197 verdrahtet das.

Aktivierungs-Mechanismus: Header `X-Active-Project-Id: <int>` am `POST /v1/chat/completions`-Request. Header-basierte Auswahl (statt persistenter Spalte) gewinnt im ersten Schritt — keine Schema-Änderung, kein Migration-Risiko, der Frontend-Caller (Nala-Tab "Projekte" sobald gebaut, oder externe Clients) entscheidet pro Request. Persistente Auswahl (Spalte `active_project_id` an `chat_sessions`) ist später trivial nachrüstbar — der Reader `read_active_project_id` ist die einzige Stelle, die geändert werden muss.

Merge-Reihenfolge laut Decision 3 (2026-05-01): System-Default → User-Persona ("Mein Ton") → Projekt-Overlay. Die ersten beiden stecken bereits zusammen in `system_prompt_<profile>.json` (eine Datei pro Profil); P197 hängt nur den Projekt-Layer als markierten Block hinten dran. Der Block beginnt mit `[PROJEKT-KONTEXT — verbindlich für diese Session]` als eindeutigem Marker (Substring-Check für Tests/Logs + Schutz gegen Doppel-Injection in derselben Pipeline). Optional ist eine `Projekt: <slug>`-Zeile drin, damit das LLM beim Self-Talk korrekt referenziert.

Position der Verdrahtung: VOR `_wrap_persona` (P184), damit der `# AKTIVE PERSONA — VERBINDLICH`-Marker auch das Projekt-Overlay umschließt — sonst stünde der Projekt-Block AUSSERHALB der "verbindlichen Persona" und das LLM könnte ihn entwerten. Die Reihenfolge ist explizit per Source-Audit-Test verifiziert (`test_persona_merge.TestSourceAudit.test_merge_runs_before_wrap`).

Defensive Behaviors: archivierte Projekte → kein Overlay (Slug wird trotzdem geloggt, damit Bug-Reports diagnostizierbar bleiben); unbekannte ID → kein Crash, einfach kein Overlay; kaputter Header (Buchstaben, negative Zahl) → ignoriert; leerer Overlay (kein `system_addendum`, leere `tone_hints`) → kein Block; `tone_hints` mit Duplikaten/Leer-Strings → bereinigt (case-insensitive Dedupe, erstes Vorkommen gewinnt). Die DB-Auflösung `resolve_project_overlay` ist von der Pure-Function `merge_persona` getrennt — der Helper bleibt I/O-frei und damit synchron testbar.

Telegram bewusst aus P197 ausgeklammert: Huginn hat eine eigene Persona-Welt (zynischer Rabe) ohne User-Profile und ohne Verbindung zu Nala-Projekten. Project-Awareness in Telegram bräuchte eigene UX (`/project <slug>`-Befehl oder persistente Bind-Tabelle) — eigener Patch wenn der Bedarf entsteht.

- **Neuer Helper:** [`zerberus/core/persona_merge.py`](zerberus/core/persona_merge.py) — `merge_persona(base_prompt, overlay, project_slug=None)` als Pure-Function, `read_active_project_id(headers)` mit Lowercase-Fallback (FastAPI-`Headers` ist case-insensitive, ein Test-`dict` nicht), `resolve_project_overlay(project_id, *, skip_archived=True)` als async DB-Schnittstelle (lazy-Import von `projects_repo` gegen Zirkular-Importe). Konstanten `ACTIVE_PROJECT_HEADER` und `PROJECT_BLOCK_MARKER` exportiert.
- **Verdrahtung:** [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) — Reihenfolge jetzt `load_system_prompt` → **`merge_persona` (P197, NEU)** → `_wrap_persona` (P184) → `append_runtime_info` (P185) → `append_decision_box_hint` (P118a) → Prosodie-Inject (P190) → `messages.insert(0, system)`.
- **Logging:** Neuer `[PERSONA-197]`-INFO-Log mit `project_id`, `slug`, `base_len`, `project_block_len`. Bei Lookup-Fehlern WARNING mit Exception-Text. Bei archiviertem Projekt INFO mit Slug.
- **Tests:** 33 neue Tests in [`test_persona_merge.py`](zerberus/tests/test_persona_merge.py) — 12 Edge-Cases für `merge_persona` (kein Overlay/leeres Overlay/nur Addendum/nur Hints/beide/Dedupe case-insensitive/leere Strings strippen/Doppel-Injection-Schutz/leerer Base-Prompt/Slug-Anzeige/unerwartete Typen/Separator-Format), 7 Header-Reader-Cases (fehlt/None/leer/valid/Lowercase-Fallback/Non-Numeric/negativ), 5 async DB-Cases via `tmp_db`-Fixture (None-ID/Unknown-ID/Existing/Archived/Archived-with-Skip-False/ohne Overlay), 4 End-to-End über `chat_completions` mit Mock-LLM (Overlay erscheint im messages[0], kein Header → kein Overlay, unbekannte ID → kein Crash, archiviert → übersprungen), 5 Source-Audit-Cases (Log-Marker, Imports, Reihenfolge merge-vor-wrap).
- **Teststand:** 1431 → **1464 passed** (+33), 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Decision 3:** ✅ Persona-Merge-Layer aktiv. Die in P194/P195 vorbereitete Persona-Overlay-Pflege wirkt jetzt im LLM-Call.

**Patch 196** — Phase 5a #4: Datei-Upload-Endpoint + UI (2026-05-02)

Erster Schritt für Phase-5a-Ziel #4 ("Dateien kommen ins Projekt"). `POST /hel/admin/projects/{id}/files` (multipart) und `DELETE /hel/admin/projects/{id}/files/{file_id}`. Bytes liegen weiter unter `data/projects/<slug>/<sha[:2]>/<sha>` (P194-Konvention) — kein Doppel-Schreiben, wenn der SHA im Projekt-Slug-Pfad schon existiert. Validierung: Filename-Sanitize (`..`/Backslashes/leere Segmente raus), Extension-Blacklist (`.exe`, `.bat`, `.sh`, ...), 50 MB Default-Limit. Delete-Logik: wenn der `sha256` woanders noch referenziert wird, bleibt die Storage-Datei liegen (Schutz vor versehentlichem Cross-Project-Delete); sonst wird sie atomar entfernt + leere Parent-Ordner aufgeräumt. Atomic Write per `tempfile.mkstemp` + `os.replace` verhindert halbe Dateien nach Server-Kill.

UI-seitig ersetzt eine Drop-Zone in der Detail-Card den P195-Platzhalter ("Upload kommt in P196"). Drag-and-drop UND klickbarer File-Picker (multiple), pro Datei eigene Progress-Zeile per `XMLHttpRequest.upload.progress`-Event (sequenzielle Uploads, damit der Server bei Massen-Drops nicht mit parallelen Streams überrannt wird). Datei-Liste bekommt einen Lösch-Button mit Confirm-Dialog; Hinweistext klärt, dass die Bytes nur entfernt werden, wenn sie nirgends sonst referenziert sind.

- **Schema:** Neue `ProjectsConfig` in `core/config.py` (`data_dir`, `max_upload_bytes`, `blocked_extensions`) — Defaults im Pydantic-Modell, weil `config.yaml` gitignored ist (sonst fehlt der Schutz nach `git clone`). Neue Repo-Helper `count_sha_references()`, `is_extension_blocked()`, `sanitize_relative_path()` in `core/projects_repo.py`.
- **Endpoints:** `_projects_storage_base()` als Indirektion in `hel.py` — Tests können den Storage-Pfad per Monkeypatch umbiegen, ohne die globalen Settings anzufassen. `_store_uploaded_bytes()` schreibt atomar; `_cleanup_storage_path()` räumt Datei + leere Parent-Ordner bis zum `data_dir`-Anker auf (best-effort).
- **Tests:** 49 neue Tests — 17 Upload-Endpoint (`test_projects_files_upload.py`: Happy-Path, Subdir, Dedup, Extension-Block, Path-Traversal, Empty-Filename, Empty-Data, Too-Large, 409-Dup, Delete-Unique, Delete-Shared, Cross-Project-404, Storage-Cleanup), 21 Repo-Helper (`test_projects_repo.py`: Sanitize, Extension-Block, count-sha-references), 11 UI-Source-Inspection (`test_projects_ui.py`: Drop-Zone, Progress, Delete-Button, Drag-and-Drop-Events).
- **Teststand:** 1382 → **1431 passed** (+49). 0 neue Failures, 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts — bekannte Schulden).
- **Phase 5a Ziel #4:** ✅ Datei-Upload geöffnet. Indexierung in projekt-spezifischen RAG (Ziel #3) folgt mit P199.

**Patch 195** — Phase 5a #1: Hel-UI-Tab "Projekte" — schließt Ziel #1 ab (2026-05-02)

UI-Hülle über das P194-Backend. Neuer Tab `📁 Projekte` im Hel-Dashboard zwischen Huginn und Links. Liste, Anlegen-Modal (Form-Overlay statt extra CSS-Lib), Edit/Archive/Unarchive/Delete inline, Datei-Liste read-only (Upload kommt P196). Persona-Overlay-Editor: `system_addendum` (Textarea) + `tone_hints` (Komma-Liste). Mobile-first: 44px Touch-Targets durchgehend, scrollbare Tab-Nav, Form-Overlay mit `flex-start`-Top für kleine Screens. Slug-Override nur beim Anlegen editierbar (Slug ist immutable per Repo-Vertrag P194). Lazy-Load via `activateTab('projects')`.

- **Tests:** 20 Source-Inspection-Tests in [`test_projects_ui.py`](zerberus/tests/test_projects_ui.py) (Pattern wie `test_patch170_hel_kosmetik.py`). Decken Tab-Reihenfolge, Section-Markup, JS-Funktionen (`loadProjects`, `saveProjectForm`, `archive/unarchive/delete`, `loadProjectFiles`), 44px-Touch-Targets, Lazy-Load-Verdrahtung.
- **Teststand:** 1365 → **1382 passed** (+17, da `test_projects_endpoints.py` schon mitgezählt war), 0 neue Failures, 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — nicht blockierend).
- **Phase 5a Ziel #1:** ✅ vollständig (Backend P194 + UI P195). Nächste Patches greifen sukzessive Ziele #2 (Templates), #3 (RAG-Index pro Projekt), #4 (Datei-Upload-Endpoint).

**Patch 194** — Phase 5a #1: Projekte als Entität, Backend-Layer (2026-05-02)

Erster Patch der Phase 5a. Tabellen `projects` + `project_files` in `bunker_memory.db` (Decision 1, 2026-05-01), Repo + Hel-CRUD-Endpoints + 46 neue Tests. UI-Tab folgt in P195. Teststand 1316 → **1365 passed** (+49: 28 Repo + 18 Endpoints + 3 weitere), 0 neue Failures, 4 xfailed (pre-existing).

- **Schema:** `projects(id, slug UNIQUE, name, description, persona_overlay JSON, is_archived, created_at, updated_at)` + `project_files(id, project_id, relative_path, sha256, size_bytes, mime_type, storage_path, uploaded_at, UNIQUE(project_id, relative_path))`. Soft-Delete via `is_archived`. Cascade per Repo (`delete_project` ⇒ `DELETE FROM project_files WHERE project_id = ?`), nicht per FK — Models bleiben dependency-frei (keine ORM-Relations). Persona-Overlay als JSON-TEXT für Decision 3 (Merge-Layer System → User → Projekt).
- **Storage-Konvention:** `data/projects/<slug>/<sha[:2]>/<sha>` — Sha-Prefix als Sub-Verzeichnis, damit kein Hotspot-Ordner entsteht. Bytes liegen NICHT in der DB. Helper `storage_path_for()` + `compute_sha256()` in `projects_repo.py`.
- **Endpoints:** `/hel/admin/projects` (list, create) + `/hel/admin/projects/{id}` (get, patch, delete) + `/admin/projects/{id}/archive` + `/unarchive` + `/files`. Admin-only via Hel `verify_admin` (Basic Auth). Bewusst nicht unter `/v1/` — `/v1/` ist exklusiv Dictate-App-Lane (Hotfix 103a, lessons.md Regel 8).
- **Slug-Generator:** Lower-case, special-chars → `-`, max 64 Zeichen, Kollisions-Suffix `-2`/`-3`/.... `slugify("AI Research v2")` → `"ai-research-v2"`.
- **Migration:** Alembic-Revision `b03fbb0bd5e3` (down_revision `7feab49e6afe`), idempotent via `_has_table()`-Guard. Indexe: `uq_projects_slug`, `idx_projects_is_archived`, `idx_project_files_project`, `idx_project_files_sha`. UNIQUE-Constraint `(project_id, relative_path)` direkt in `op.create_table()` über `sa.UniqueConstraint`.
- **Lesson dokumentiert:** Composite-UNIQUE-Constraints MÜSSEN im Model (`__table_args__`) deklariert werden, nicht nur als raw `CREATE UNIQUE INDEX` in `init_db` — sonst greift der Constraint nicht in Test-Fixtures, die nur `Base.metadata.create_all` aufrufen.

- **P192 — Sentiment-Triptychon UI:** Drei Chips (BERT 📝 + Prosodie 🎙️ + Konsens 🎯) an jeder Chat-Bubble, Sichtbarkeit per Hover/`:active` analog Toolbar-Pattern (P139). Neue Utility [`zerberus/utils/sentiment_display.py`](zerberus/utils/sentiment_display.py) mit `bert_emoji()`, `prosody_emoji()`, `consensus_emoji()`, `compute_consensus()`, `build_sentiment_payload()`. Backend liefert `sentiment: {user, bot}` ADDITIV in `/v1/chat/completions`-Response — OpenAI-Schema bleibt formal kompatibel. Mehrabian-Regel: bei `confidence > 0.5` dominiert die Prosodie, sonst Fallback auf BERT. Inkongruenz 🤔 wenn BERT positiv und Prosodie-Valenz < -0.2. 22 Tests in [`test_sentiment_triptych.py`](zerberus/tests/test_sentiment_triptych.py).
- **P193 — Whisper-Endpoint Prosodie/Sentiment-Enrichment:** `/v1/audio/transcriptions` Response erweitert: `text` bleibt IMMER (Backward-Compat für Dictate / SillyTavern / Generic-Clients), zusätzlich optional `prosody` (P190) + neu `sentiment.bert` + `sentiment.consensus`. `/nala/voice` identisch erweitert + zusätzlich named SSE-Events `event: prosody` und `event: sentiment` über `/nala/events` — Triptychon-Frontend kann sync (JSON) oder async (SSE) konsumieren. Fail-open: BERT-Fehler erzeugt nur Logger-Warnung, Endpoint läuft sauber durch. 16 Tests in [`test_whisper_enrichment.py`](zerberus/tests/test_whisper_enrichment.py). Logging-Tag `[ENRICHMENT-193]`.

### Doku-Konsolidierung (Phase-4-Abschluss)

- `lessons.md` aktualisiert: neue Sektionen Prosodie/Audio (P188-191), RAG/FAISS (P187+), Frontend (P186+P192), Whisper-Enrichment (P193). Veraltete Hinweise auf MiniLM als „aktiver Embedder" durch DualEmbedder-Beschreibung ersetzt (in CLAUDE_ZERBERUS.md).
- `CLAUDE_ZERBERUS.md` aktualisiert: neue Top-Sektionen Sentiment-Triptychon (P192) + Whisper-Endpoint Enrichment (P193).
- `SUPERVISOR_ZERBERUS.md` (diese Datei) auf < 400 Zeilen gestrafft. Patch-Details vor P177 leben jetzt in [`docs/PROJEKTDOKUMENTATION.md`](docs/PROJEKTDOKUMENTATION.md).
- Neues Übergabedokument [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) für die nächste Supervisor-Session (Phase 5 / Nala-Projekte).

---

## Phase 4 — ABGESCHLOSSEN ✅ (P119–P193)

Vollständige Patch-Historie in [`docs/PROJEKTDOKUMENTATION.md`](docs/PROJEKTDOKUMENTATION.md). Hier nur die letzten Meilensteine ab P186:

| Patch | Datum | Zusammenfassung |
|-------|-------|-----------------|
| **P197** | 2026-05-02 | Phase 5a Decision 3: Persona-Merge-Layer aktiviert — Header `X-Active-Project-Id` + `merge_persona`-Helper + 33 Tests |
| **P196** | 2026-05-02 | Phase 5a #4: Datei-Upload-Endpoint + Drop-Zone-UI (öffnet Ziel #4) — SHA-Dedup-Delete, Extension-Blacklist, atomic write + 49 Tests |
| **P195** | 2026-05-02 | Phase 5a #1: Hel-UI-Tab "Projekte" (schließt Ziel #1 ab) — Liste/Form/Persona-Overlay + 20 Tests |
| **P194** | 2026-05-02 | Phase 5a #1: Projekte als Entität (Backend) — Schema + Repo + Hel-CRUD + 46 Tests |
| P192–P193 | 2026-05-01 | Sentiment-Triptychon + Whisper-Enrichment + Phase-4-Abschluss + Doku-Konsolidierung |
| P189–P191 | 2026-05-01 | Prosodie-Pipeline komplett: Gemma-Client + Pipeline + Consent-UI + Worker-Protection |
| P186–P188 | 2026-05-01 | Auto-TTS + FAISS-Migration (DualEmbedder DE/EN) + Prosodie-Foundation |
| P183–P185 | 2026-05-01 | Black-Bug VIERTER (endgültig) + Persona-Wrap + Runtime-Info-Block |
| P180–P182 | 2026-04-30 | Live-Findings L-178: Guard+RAG-Kontext, Telegram-Allowlist, ADMIN-Plausi, Unsupported-Media |
| P178–P179 | 2026-04-29 | Huginn-RAG-Selbstwissen (system-Kategorie, Default-Whitelist, Backlog-Konsolidierung) |
| P177 | 2026-04-29 | Pipeline-Cutover-Feature-Flag (`use_message_bus`, default false, live-switch) |
| P173–P176 | 2026-04-28 | Phase-E-Skelett (Message-Bus, Adapter, Pipeline, Sandbox-Live, Coda-Autonomie) |
| P119–P172 | 2026-04-09…04-28 | Aufbau Phase 4: RAG-Pipeline, Guard, Sentiment, Memory-Extraction, Telegram-Bot, HitL, Sandbox |

### Phase-4-Bilanz

| Bereich | Highlights |
|---------|-----------|
| **Guard** | Mistral Small 3 (P120/P180), `caller_context` + `rag_context` ohne Halluzinations-Risiko, fail-open |
| **Huginn (Telegram)** | Long-Polling, Allowlist, Intent-Router via JSON-Header, HitL persistent (SQLite), RAG via system-Kategorie, Sandbox-Hook |
| **Nala UI** | Mobile-first Bubbles, HSL-Slider, TTS, Auto-TTS, Katzenpfoten, Feuerwerk, Triptychon |
| **RAG** | Code-Chunker, DualEmbedder (DE GPU + EN CPU), FAISS-Migration, Soft-Delete, Category-Boost |
| **Prosodie** | Gemma 4 E2B (lokal, Q4_K_M), Whisper+Gemma parallel via `asyncio.gather`, Consent-UI, Triptychon |
| **Pipeline** | Message-Bus + Telegram/Nala/Rosa-Adapter, DI-only Pipeline, Feature-Flag-Cutover |
| **Security** | Input-Sanitizer (NFKC + Patterns), Callback-Spoofing-Schutz, Allowlist, Worker-Protection (Audio nicht in DB) |
| **Infrastruktur** | Docker-Sandbox (`--network none --read-only`), Pacemaker, pytest-Marker (e2e/guard_live/docker), Coda-Autonomie |
| **Bugs** | 🪦 Black Bug (4 Anläufe — P183 hat ihn endgültig getötet), HSL-Parse, Config-Save, Terminal-Hygiene |

---

## Phase 5 — Nala-Projekte (Roadmap)

**Referenz-Dokumente:**
- [`nala-projekte-features.md`](nala-projekte-features.md) — 100+ Features, konsolidiert
- [`NALA_PROJEKTE_PRIORISIERUNG.md`](NALA_PROJEKTE_PRIORISIERUNG.md) — Priorisierung für Zerberus-Kontext
- [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) — Übergabedokument mit Tech-Stack, VRAM, offenen Items

### Phase 5a — Grundgerüst (erste 10 Patches)

| # | Feature | Beschreibung |
|---|---------|-------------|
| P194 | Projekt-DB (Backend) | SQLite-Schema, Repo, Hel-CRUD-Endpoints ✅ |
| P195 | Hel-UI-Tab Projekte | Liste + Anlegen/Edit/Archive/Delete + Persona-Overlay-Editor ✅ |
| P196 | Datei-Upload-Endpoint + UI | `POST /hel/admin/projects/{id}/files` + Drop-Zone + SHA-Dedup-Delete ✅ |
| P197 | Persona-Merge-Layer | System → User → Projekt-Overlay im LLM-Prompt aktivieren |
| P198 | Template-Generierung | `ZERBERUS_X.md`, Ordnerstruktur, Git-Init |
| P199 | Projekt-RAG-Index | Isolierter FAISS pro Projekt |
| P200 | Code-Execution-Pipeline | Intent `PROJECT_CODE` → LLM → Sandbox |
| P201 | HitL-Gate für Code | Sicherheit vor Ausführung (Default-ON) |
| P202 | Snapshot/Backup-System | `.bak` + Rollback vor jeder Dateiänderung |
| P203 | Diff-View | User sieht jede Änderung vor Bestätigung |

### Phase 5b — Power-Features (danach)

- Multi-LLM Evaluation (Bench-Mode pro Projekt-Aufgabe)
- Bugfix-Workflow + Test-Agenten (Loki/Fenrir/Vidar Per-Projekt)
- Multi-Agent Orchestrierung, Debugging-Konsole
- Reasoning-Modi, LLM-Wahl, Cost-Transparency
- Agent-Observability (Chain-of-Thought Inspector)

### Abhängigkeiten / Was steht schon

- Docker-Sandbox (P171) existiert + Images gezogen (P176)
- HitL-Mechanismus (P167) existiert, SQLite-persistent
- Pipeline-Cutover (P177) existiert, Feature-Flag bereit
- Guard (P120/P180) funktioniert mit RAG- und Persona-Kontext
- Prosodie (P189–P191) funktioniert — kann in Projekt-Kontext integriert werden (z. B. Stimmung als Code-Quality-Indikator)

---

## Architektur-Referenz

### Aktiver Tech-Stack

| Komponente | Modell / Technologie |
|------------|---------------------|
| **Cloud-LLM** | DeepSeek V3.2 (OpenRouter) |
| **Guard** | Mistral Small 3 (OpenRouter, `mistralai/mistral-small-24b-instruct-2501`) |
| **Prosodie** | Gemma 4 E2B (lokal, `llama-mtmd-cli`, Q4_K_M, ~3.4 GB) |
| **ASR / Whisper** | faster-whisper large-v3 (FP16, Docker Port 8002) |
| **Sentiment (Text)** | `oliverguhr/german-sentiment-bert` (lokal) |
| **Embeddings DE** | `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` (GPU) |
| **Embeddings EN** | `intfloat/multilingual-e5-large` (CPU, optional Index) |
| **Reranker** | `BAAI/bge-reranker-v2-m3` |
| **DB** | SQLite (`bunker_memory.db`, WAL-Modus, Alembic-Migrations seit P92) |
| **Frontend** | Nala (Mobile-first), Hel (Admin-Dashboard) |
| **Bot** | Huginn (Telegram, Long-Polling, Tailscale-intern) |

### VRAM-Belegung (Modus „Nala aktiv" mit Prosodie)

```
Whisper 4.5 + BERT 0.5 + Gemma E2B 3.0 + DualEmbedder 0.5 + Reranker 1.0 + Windows 0.8
= ~10.3 GB / 12 GB (RTX 3060)
```

### Repos (alle 3 müssen synchron sein)

- **Zerberus** (Code, lokal): `C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus`
- **Ratatoskr** (Doku-Sync, GitHub): `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr`
- **Claude** (universelle Lessons, GitHub): `C:\Users\chris\Python\Claude`

---

## Offene Items / Bekannte Schulden

→ Konsolidiert in [`BACKLOG_ZERBERUS.md`](BACKLOG_ZERBERUS.md) (seit Patch 179). Hier nur strukturelle Schulden:

- **Persona-Hierarchie** (Hel vs. Nala „Mein Ton") — löst sich mit SillyTavern/ChatML-Wrapper (B-071)
- **`interactions`-Tabelle ohne User-Spalte** — Per-User-Metriken erst nach Alembic-Schema-Fix vertrauenswürdig
- **`scripts/verify_sync.ps1`** existiert nicht — `sync_repos.ps1` Output muss manuell geprüft werden
- **`system_prompt_chris.json` Trailing-Newline-Diff** — `git checkout` zum Bereinigen, kein echter Bug
- **Voice-Messages in Telegram-DM** funktionieren nicht (P182 Unsupported-Media-Handler antwortet höflich) — B-072 für echte Whisper-Pipeline

## Architektur-Warnungen

- **Rosa Security Layer:** NICHT implementiert — Dateien im Projektordner sind nur Vorbereitung
- **JWT** blockiert externe Clients komplett — `static_api_key` ist der einzige Workaround (Dictate, SillyTavern)
- **/v1/-Endpoints** MÜSSEN auth-frei bleiben (Dictate-Tastatur kann keine Custom-Headers) — Bypass via `_JWT_EXCLUDED_PREFIXES`
- **Chart.js / zoom-plugin / hammer.js** via CDN — bei Air-Gap ist das Metriken-Dashboard tot
- **Prosodie-Audio-Bytes** dürfen NICHT in `interactions`-Tabelle landen (Worker-Protection P191)

---

## Sync-Pflicht (Patch 164+)

`sync_repos.ps1` muss nach jedem `git push` ausgeführt werden — der Patch gilt erst als abgeschlossen, wenn Zerberus, Ratatoskr und Claude-Repo synchron sind. Coda pusht zuverlässig nach Zerberus, vergisst aber den Sync regelmäßig, sodass Ratatoskr und Claude-Repo driften.

Falls Claude Code den Sync nicht selbst ausführen kann (z. B. PowerShell nicht verfügbar oder Skript wirft Fehler), MUSS er das explizit melden — etwa mit „⚠️ sync_repos.ps1 nicht ausgeführt — bitte manuell nachholen". Stillschweigendes Überspringen ist nicht zulässig. Die durable Regel steht in `CLAUDE_ZERBERUS.md` unter „Repo-Sync-Pflicht".

## Langfrist-Vision

- **Phase 5 — Nala-Projekte:** Zerberus wird zur persönlichen Code-Werkstatt, Nala vermittelt zwischen Chris und LLMs/Sandboxes
- **Metric Engine** = kognitives Tagebuch + Frühwarnsystem für Denkmuster-Drift
- **Rosa Corporate Security Layer** = letzter Baustein vor kommerziellem Einsatz
- **Telegram-Bot** als Zero-Friction-Frontend für Dritte (keine Tailscale-Installation nötig)

## Don'ts für Supervisor

- **PROJEKTDOKUMENTATION.md NICHT vollständig laden** (5000+ Zeilen = Kontextverschwendung) — nur gezielt nach Patch-Nummern grep'en
- **Memory-Edits max 500 Zeichen** pro Eintrag
- **Session-ID ≠ User-Trennung** — Metriken pro User erst nach DB-Architektur-Fix vertrauenswürdig
- **Patch-Prompts IMMER als `.md`-Datei** — NIE inline im Chat. Claude Code erhält den Inhalt per Copy-Paste aus der Datei (Patch 101)
- **Dateinamen `CLAUDE_ZERBERUS.md` und `SUPERVISOR_ZERBERUS.md` sind FINAL** — in Patch-Prompts nie mit alten Namen (`CLAUDE.md`, `HYPERVISOR.md`) referenzieren (Patch 100/101)
- **Lokale Pfade:** Ratatoskr liegt unter `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr\` (nicht `Rosa\Ratatoskr\`), Bmad82/Claude unter `C:\Users\chris\Python\Claude\` (nicht `Rosa\Claude\`). Patch-Prompts mit falschen Pfaden → immer erst verifizieren, nicht raten

---

## Für die nächste Supervisor-Session (Phase 5 Start)

1. `SUPERVISOR_ZERBERUS.md` von GitHub fetchen (frisch konsolidiert in P192–P193)
2. [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) lesen — Tech-Stack, VRAM, offene Items, Phase-5-Roadmap stehen dort vollständig
3. Phase-5a mit P194 (Projekt-DB + Workspace) starten — Doppel- oder Dreiergruppen-Rhythmus beibehalten
4. Prosodie-Live-Test steht noch aus — `llama-mtmd-cli` muss im PATH sein, sonst läuft Pfad A nicht
5. Pipeline-Feature-Flag (`use_message_bus`) ist bereit für Live-Switch-Tests, aber Default-OFF bis Chris explizit umstellt
