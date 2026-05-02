# ZERBERUS_MARATHON_WORKFLOW.md

**Phase:** 5a — Nala-Projekte
**Letzter Patch:** P203b | **Tests:** 1645 passed (4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste)

---

## Philosophie

Du bist Architekt mit vollem Werkzeugkasten. Diese Datei gibt dir Ziele, nicht Rezepte. Wie du dort hinkommst — welche Reihenfolge, welche Gruppierung, welche Abstraktionen — ist deine Entscheidung. Nutze dein Reasoning. Denk nach bevor du tippst. 5 Minuten besseres Design schlägt 2 Stunden Refactoring.

Wenn du einen besseren Weg siehst als hier beschrieben: nimm ihn. Dokumentiere die Abweichung und warum.

---

## Session-Zyklus

1. Lies HANDOVER.md → weißt wo du stehst
2. Lies diese Datei → weißt was offen ist
3. Wähle deine nächsten Patches (Abhängigkeiten beachten, sonst frei)
4. Implementiere. Teste. Committe.
5. Aktualisiere diese Datei (Patch-Status, offene Fragen, manuelle Tests)
6. Schreibe HANDOVER.md neu (für die nächste Instanz)
7. Pflege alle Doku-Dateien (siehe Doku-Pflicht)
8. Push + sync_repos.ps1 + scripts/verify_sync.ps1

Chris sagt nur: "Lies HANDOVER.md und ZERBERUS_MARATHON_WORKFLOW.md. Mach weiter."

---

## Stopp-Regeln

Du entscheidest selbst wann du aufhörst. Orientierung:

- ~400k Token verbraucht → aktuellen Patch sauber fertigmachen, dann Doku + Handover + STOPP
- Antworten werden unschärfer, du vergisst Details → gleiche Reaktion
- Lieber 2 saubere Patches als 3 mit dem dritten halb fertig
- Test-Failures → fixen BEVOR nächster Patch. Nicht aufschieben
- Blockiert? Frage in DECISIONS_PENDING parken → weiter zum nächsten unabhängigen Patch

---

## Doku-Pflicht (nach jedem Patch)

| Datei | Format | Was |
|-------|--------|-----|
| CLAUDE_ZERBERUS.md | Bibel (Pipes) | Nur bei Architektur-Änderung |
| SUPERVISOR_ZERBERUS.md | Bibel (Pipes) | Patch-Eintrag. <400 Zeilen halten |
| lessons.md | Bibel (Pipes) | Universelle Erkenntnisse |
| docs/PROJEKTDOKUMENTATION.md | Prosa | Vollständiger Eintrag. NIE kürzen |
| README.md | Prosa | Footer: Patch-Nr + Testzahl |
| HANDOVER.md | Kompakt | Überschreiben. Für nächste Instanz |
| Diese Datei | Mixed | Patch-Status, manuelle Tests, offene Fragen |

Cleanup: CLAUDE_ZERBERUS.md <150 Zeilen, SUPERVISOR <400. Erledigtes raus, Lessons nach lessons.md.

---

## Phase 5a — Ziele

Nala bekommt ein Projekt-System. User erstellt Projekte, lädt Dateien hoch, lässt Code in der Sandbox ausführen, sieht Diffs, bestätigt Änderungen. Alles Mobile-first, alles mit Sicherheitsnetz.

Die folgende Liste beschreibt WAS, nicht WIE. Die Architektur ist deine Sache.

| # | Ziel | Kontext | Braucht | Status |
|---|------|---------|---------|--------|
| 1 | **Projekte existieren als Entität** — Persistenz, CRUD, sichtbar in Hel | Fundament für alles | — | ✅ (Backend P194, Hel-UI P195) |
| 2 | **Projekte haben Struktur** — Template-Dateien, Ordner, optional Git | Damit Projekte nicht leer starten | #1 | ✅ (Templates P198 — Git-Init verschoben auf P200/Workspace) |
| 3 | **Projekte haben eigenes Wissen** — isolierter RAG-Index pro Projekt | Code-LLM braucht Projektkontext | #1 | ✅ (Projekt-RAG-Index P199 — Pure-Numpy-Linearscan + MiniLM + Best-Effort-Triggers) |
| 4 | **Dateien kommen ins Projekt** — Upload in Nala-Chat, Indexierung, Auswahl im Chat | Dateien müssen rein | #1 | ✅ (Hel-Upload P196 + Indexierung P199 + Nala-Tab "Projekte" P201 — Header-Setter `X-Active-Project-Id` aktiviert P197+P199 vom Chat aus) |
| 5 | **Code wird ausgeführt** — vom Chat zur Docker-Sandbox und zurück | Kernfeature | #1, #3 | 🚧 (P203a Workspace-Layout DA — P203b Sandbox-Mount + P203c Tool-Use+UI offen) |
| 6 | **Mensch bestätigt vor Ausführung** — HitL-Gate, One-Click | Sicherheit | #5 | ⬜ |
| 7 | **Zweite Meinung vor Ausführung** — Veto-Logik, Wandschlag-Erkennung | Schutzschicht | #5 | ⬜ |
| 8 | **Erst verstehen, dann coden** — Ambiguitäts-Check, Spec-Contract | Whisper-Input = Ambiguität | #1 | ⬜ |
| 9 | **Änderungen sind rückgängig machbar** — Snapshots, Rollback | Bevor Code Dateien ändert | #5 | ⬜ |
| 10 | **User sieht was passiert** — Diff-View, atomare Change-Sets | Transparenz | #9 | ⬜ |
| 11 | **GPU teilen ohne Crash** — Queue/Scheduling für VRAM-Konsumenten | RTX 3060 12GB = Goldstaub | — | ⬜ |
| 12 | **Secrets bleiben geheim** — verschlüsselt, Sandbox-Injection, Output-Maskierung | .env darf nie leaken | #5 | ⬜ |
| 13 | **Sehen was der Agent denkt** — Reasoning-Schritte sichtbar im Chat | Mobile = muss sichtbar sein | #5 | ⬜ |
| 14 | **Wiederkehrende Jobs** — Scheduler für Projekt-Tasks | "Teste jede Nacht" | #1 | ⬜ |
| 15 | **Billige Fehler billig fangen** — Validierung vor teuren LLM-Calls | Token sparen | #1 | ⬜ |
| 16 | **Nala + Hel als PWA** — Manifest, Service Worker, Icons, "Add to Home Screen" auf iPhone + Android | App-Feeling ohne Browser-Chrome (Jojo iPhone, Chris Android) | — | ✅ (PWA-Patch P200 — pwa.router + Per-App-Manifeste + SW-Scope-via-Pfad + Kintsugi-Icons) |

Abhängigkeits-Kurzform:
- #1 ← fast alles
- #5 ← #1, #3
- #6, #7 ← #5
- #10 ← #9 ← #5
- #11, #15, #16 ← unabhängig (jederzeit einschiebbar)

---

## Vorhandene Bausteine (nicht nochmal bauen)

Docker-Sandbox (P171) + Images (P176) ✅
HitL-Mechanismus (P167) + SQLite-persistent ✅
Pipeline + Message-Bus (P174/P177) + Feature-Flag ✅
Guard Mistral Small 3 (P120/P180) ✅
Prosodie Gemma E2B (P189-191) ✅
Sentiment-Triptychon (P192) + Whisper-Enrichment (P193) ✅
**Projekte-Backend (P194):** Tabellen `projects` + `project_files` in `bunker_memory.db`, Repo `zerberus/core/projects_repo.py`, Hel-CRUD `/hel/admin/projects/*` (Basic-Auth) ✅
**Projekte-UI (P195):** Hel-Tab `📁 Projekte` mit Liste/Form/Persona-Overlay-Editor ✅
**Projekt-Datei-Upload (P196):** `POST /hel/admin/projects/{id}/files` (Multipart) + `DELETE /hel/admin/projects/{id}/files/{file_id}` mit SHA-Dedup-Schutz, Extension-Blacklist, 50 MB Limit, atomic write via `tempfile + os.replace`, Drop-Zone-UI mit XHR-Progress + Lösch-Button ✅
**Persona-Merge-Layer (P197):** Header `X-Active-Project-Id: <int>` am `POST /v1/chat/completions` aktiviert das Projekt-Overlay. `zerberus/core/persona_merge.py` mit `merge_persona` (Pure-Function), `read_active_project_id` (Header-Reader mit Lowercase-Fallback), `resolve_project_overlay` (async DB-Schicht). Merge-Reihenfolge System → User → Projekt-Overlay (Decision 3, 2026-05-01). Verdrahtung VOR `_wrap_persona`-Marker, damit AKTIVE-PERSONA-Wrap auch das Overlay umschließt. `[PERSONA-197]`-Logging-Tag. Telegram bewusst ausgeklammert. ✅
**Projekt-Templates (P198):** `create_project_endpoint` materialisiert `ZERBERUS_<SLUG>.md` (Projekt-Bibel, 5 Sektionen) + `README.md` (kurze Prosa) beim Anlegen. `zerberus/core/projects_template.py` mit Pure-Function-Render-Schicht (`render_project_bible`/`render_readme`/`template_files_for`) und async Materialisierungs-Schicht (`materialize_template`). SHA-Storage wie Uploads, DB-Eintrag in `project_files` mit lesbarem `relative_path`. Idempotenz via Existenz-Check (User-Inhalte werden NICHT überschrieben). Best-Effort: Crash bricht Anlegen nicht ab. Feature-Flag `ProjectsConfig.auto_template: bool = True`. Git-Init bewusst weggelassen (kommt mit Workspace-Layout in P200). `[TEMPLATE-198]`-Logging-Tag. ✅
**Projekt-RAG-Index (P199):** Per-Projekt-Vektor-Store unter `data/projects/<slug>/_rag/{vectors.npy, meta.json}`, isoliert vom globalen RAG. `zerberus/core/projects_rag.py` mit Pure-Function-Schicht (Splitter, Chunker-Dispatcher, Top-K, Format-Block), File-I/O-Schicht (load/save/remove + atomic write), Embedder-Wrapper (`_embed_text` lazy MiniLM-L6-v2, monkeypatchbar) und async DB-Schicht (`index_project_file`, `remove_file_from_index`, `query_project_rag`). Pure-Numpy-Linearscan via `argpartition` statt FAISS, weil Per-Projekt-Indizes klein sind (~10-2000 Chunks) und Tests dependency-frei bleiben. Code-Files via `code_chunker.chunk_code` (P122), Prosa via lokalem Para-Splitter mit Sentence-Fallback. Idempotenz pro `file_id`. Trigger: Upload-Endpoint, `materialize_template`, Delete-File, Delete-Projekt — alle Best-Effort. Wirkung im Chat NACH P197/P184/P185/P118a/P190, VOR `messages.insert`, mit Marker `[PROJEKT-RAG — Kontext aus Projektdateien]`. Feature-Flags `rag_enabled`, `rag_top_k`, `rag_max_file_bytes`. `[RAG-199]`-Logging-Tag. ✅
**PWA-Verdrahtung Nala + Hel (P200):** Eigener Router `zerberus/app/routers/pwa.py` mit vier Endpoints (`/nala/manifest.json`, `/nala/sw.js`, `/hel/manifest.json`, `/hel/sw.js`) ohne Auth-Dependencies. In `main.py` VOR `hel.router` via `include_router(pwa.router)` eingehängt — sonst gated `verify_admin` den Manifest-Fetch und der Install-Prompt erscheint nie. SW-Scope folgt dem Pfad der SW-Datei (`/nala/sw.js` → scope `/nala/`, `/hel/sw.js` → scope `/hel/`) — kein `Service-Worker-Allowed`-Header nötig, jede App cacht NUR ihre eigenen URLs. Per-App-Manifeste mit Theme-Color, Apple-Mobile-Web-App-Meta, zwei Icons (192/512 px, `purpose: "any maskable"` für Android-Adaptive). Icons via `scripts/generate_pwa_icons.py` (PIL, deterministisch) im Kintsugi-Stil. SW-Logik: install precacht App-Shell, activate räumt alte Caches, fetch macht network-first mit cache-fallback, non-GET passt durch. `[PWA-200]`-Logging-Tag (browser-seitig in `console.warn`). ✅
**Nala-Tab "Projekte" + Header-Setter (P201):** Neuer Endpoint `GET /nala/projects` (JWT-pflichtig, eigener `nala.router` statt Wiederverwendung von `/hel/admin/projects` — zwei Auth-Welten + Persona-Overlay-Schutz + Archived-Default unterscheiden sich). Response slimmed auf `{id, slug, name, description, updated_at}` ohne `persona_overlay`. UI: vierter Settings-Tab "📁 Projekte" zwischen "Ausdruck" und "System", lazy-loaded beim Tab-Klick. Active-Project-Chip im Chat-Header neben Profile-Badge (gold-border Pill, klick öffnet Settings + Projects-Tab). State in zwei localStorage-Keys (`nala_active_project_id` numerisch + `nala_active_project_meta` JSON für Renderer ohne Re-Fetch). Header-Injektion ZENTRAL in `profileHeaders()` (drei Zeilen) → wirkt auf ALLE Nala-Calls. Zombie-ID-Schutz: nach jedem `loadNalaProjects` wird geräumt wenn aktive ID nicht mehr in Liste. XSS-Schutz via `escapeProjectText()` für name+slug+description, Source-Audit-Test zählt mind. 3 Aufrufe. ✅
**PWA-Auth-Hotfix (P202):** Service-Worker fängt nicht mehr Top-Level-Navigation-Requests ab — `event.request.mode === 'navigate'` returnt früh ohne respondWith, Browser-nativer Stack übernimmt (inkl. Basic-Auth-Prompt + WWW-Authenticate-Auswertung). Cache-Name auf `nala-shell-v2`/`hel-shell-v2` gebumpt, damit der activate-Hook der neuen SW-Version den verseuchten v1-Cache räumt. APP_SHELL-Listen ohne den Root-Pfad (Navigation wird sowieso nicht mehr gecached, und beim Hel-SW-Install rejected `cache.addAll` sonst durch das 401 vom auth-gated `/hel/`). Side-Effect: HTML-Reload geht jetzt immer übers Netz — akzeptabel, weil ohne Server eh kein sinnvoller Betrieb. `[PWA-202]`-Doku-Tag im SW-Quelltext. ✅
**Hel-UI-Hotfix (P203b, BLOCKER aus Chris-Bugmeldung 2026-05-02):** Hel war nicht mehr bedienbar — UI rendert, aber keine Klicks gehen durch. Root-Cause: `loadProjectFiles`-Renderer (P196) hatte fehlerhaftes Python-Quote-Escaping in einer inline-`onclick`-String-Concat-Kette. Python interpretierte `\\'` und `\'` zur selben Ausgabe (`'`); rendered JS hatte `+ ',''  +` (drei adjacent quotes) → `SyntaxError: Unexpected string` → gesamter `<script>`-Block tot → alle Hel-Funktionen (Tabs, Settings, Buttons) undefined. Verschleiert vor P200 durch Browser-Cache. Fix: Inline-`onclick` durch `data-*`-Attribute + Event-Delegation per `addEventListener` ersetzt — quote-immun, XSS-sicher. 10 neue Tests in `test_p203b_hel_js_integrity.py` (3 Source-Audit gegen Bug-Pattern, 5 Source-Audit für Event-Delegation, 1 `node --check`-Pass über ALLE inline `<script>`-Blöcke aus `ADMIN_HTML` (skipped wenn node fehlt), 1 Smoke). Plus 1 angepasster Test (`test_projects_ui::test_file_list_has_delete_button`). `[PWA-200]`/`[PWA-202]`-Doku-Klammer reicht — kein neuer Logging-Tag. ✅
**Project-Workspace-Layout (P203a, Vorbereitung Phase 5a #5):** Neues Modul `zerberus/core/projects_workspace.py` mit Pure-Function-Schicht (`workspace_root_for(slug, base_dir)` → `<base>/projects/<slug>/_workspace/`, `is_inside_workspace(target, root)` für Pfad-Sicherheit), Sync-FS-Schicht (`materialize_file` mit Hardlink-primary + Copy-Fallback bei `OSError`, atomic via tempfile + `os.replace`; `remove_file` räumt leere Eltern bis Workspace-Root weg; `wipe_workspace` mit Sicherheits-Reject auf Pfade die nicht auf `_workspace` enden) und async DB-Schicht (`materialize_file_async`, `remove_file_async`, `sync_workspace` für Komplett-Resync mit Materialize+Orphan-Removal). Verdrahtung als Best-Effort-Side-Effect in vier Trigger-Punkten: `upload_project_file_endpoint` (nach `register_file`+RAG-Index), `delete_project_file_endpoint` (nach `delete_file`, mit Slug aus extra `get_project`-Call vor dem DB-Delete), `delete_project_endpoint` (`wipe_workspace` nach `delete_project`), `materialize_template` (nach jedem `register_file`). Alle vier wickeln den Workspace-Call in try/except — Hauptpfad bleibt grün. Lazy-Import des Moduls im try-Block (analog RAG-Pattern aus P199). Feature-Flag `ProjectsConfig.workspace_enabled = True`. `[WORKSPACE-203]`-Logging-Tag. Was P203a bewusst NICHT macht: Sandbox-Mount auf den Workspace (kommt P203b — `SandboxManager` muss `workspace_mount: Optional[Path]` bekommen, aktuell verbietet er Volume-Mount explizit), LLM-Tool-Use für Code-Generation (P203c), UI-Render von Code+Output-Blöcken (P203c). ✅

---

## Manuelle Tests (Chris)

> Coda: Trage hier ein was Chris auf echten Geräten testen muss.
> Chris: Hake ab (⬜→✅) und schreib das Datum. Nächste Instanz sieht den Stand.

| # | Was testen | Patch | Status | Datum |
|---|-----------|-------|--------|-------|
| 1 | git push + sync_repos.ps1 für P192/P193 | P193 | ✅ | 2026-05-01 (Bootstrap-Session) |
| 2 | llama-mtmd-cli im PATH für Prosodie-Live-Test | P191 | ⬜ | — |
| 3 | Sentiment-Triptychon auf iPhone + Android visuell prüfen | P192 | ⬜ | — |
| 4 | Spracheingabe → Triptychon aktualisiert sich live | P193 | ⬜ | — |
| 5 | git push + sync_repos.ps1 für P194 | P194 | ⬜ | — |
| 6 | Server-Restart: `init_db`-Bootstrap für `projects` + `project_files` ohne Fehler im Log (`[PATCH-92]`/`✅ Datenbank bereit`) | P194 | ⬜ | — |
| 7 | `curl -u admin:<pw> https://localhost:5000/hel/admin/projects` → `{"projects":[],"count":0}` | P194 | ⬜ | — |
| 8 | Hel öffnen → Tab `📁 Projekte` sichtbar, Liste lädt, "+ Projekt anlegen" funktioniert (Anlegen + Edit + Archive + Delete) | P195 | ⬜ | — |
| 9 | Hel-Tab `📁 Projekte` auf iPhone: Tabelle scrollbar, Form-Overlay nicht abgeschnitten, Touch-Targets klickbar | P195 | ⬜ | — |
| 10 | Persona-Overlay-Form: `tone_hints` als Komma-Liste eingeben, Speichern, Edit öffnen → Werte korrekt deserialisiert (kein doppeltes Komma, keine leeren Strings) | P195 | ⬜ | — |
| 11 | git push + sync_repos.ps1 für P196 | P196 | ⬜ | — |
| 12 | Projekt anlegen, Detail-Card öffnen → Drop-Zone sichtbar mit Hinweistext "Max. 50 MB". Datei per Drag&Drop + per Klick-Auswahl hochladen → beide Wege funktionieren, Progress läuft, Liste reloaded mit Datei | P196 | ⬜ | — |
| 13 | Reject-Pfade: `.exe`-Datei droppen → 400 mit "blockiert", 60-MB-Datei droppen → 413 "zu gross". Im Browser-Devtools-Network-Tab Status prüfen | P196 | ⬜ | — |
| 14 | Datei löschen → Confirm-Dialog. Bestätigen → Datei verschwindet aus Liste. Storage-Ordner `data/projects/<slug>/` prüfen: Datei + leere Sha-Prefix-Ordner sind weg | P196 | ⬜ | — |
| 15 | SHA-Dedup-Schutz: Dieselbe Datei in zwei Projekten hochladen, in Projekt A löschen → Datei in Projekt B bleibt erhalten (Test deckt das schon, hier nur sanity-check über Hel-UI) | P196 | ⬜ | — |
| 16 | iPhone: Drop-Zone und Datei-Liste auf 390x844-Viewport — Drop-Zone ist tap-bar (öffnet File-Picker), Lösch-Button hat 36px+ Touch-Target | P196 | ⬜ | — |
| 17 | git push + sync_repos.ps1 für P197 | P197 | ⬜ | — |
| 18 | Header-Test: Projekt mit Persona-Overlay anlegen (Hel-UI), dann `curl -H "Authorization: Bearer <token>" -H "X-Active-Project-Id: <id>" -X POST -d '{"messages":[{"role":"user","content":"hi"}]}' .../v1/chat/completions` → Server-Log nach `[PERSONA-197]` greppen, prüfen dass `project_block_len > 0`. LLM-Antwort sollte den Tonfall aus `tone_hints` widerspiegeln (z.B. wenn `tone_hints=["foermlich"]` → Sie statt du). Optional Kontroll-Test ohne Header → `[PERSONA-197]`-Zeile fehlt im Log | P197 | ⬜ | — |
| 19 | git push + sync_repos.ps1 für P198 | P198 | ⬜ | — |
| 20 | Hel-UI: Projekt anlegen → Detail-Card öffnen → Datei-Liste zeigt automatisch `ZERBERUS_<SLUG>.md` + `README.md`. Beide Files anklicken/herunterladen → Bibel hat alle 5 Sektionen mit eingerendertem Slug/Name/Datum, README hat Name + Description. Server-Log nach `[TEMPLATE-198]` greppen → zwei `created`-Zeilen | P198 | ⬜ | — |
| 21 | Idempotenz-Check: Im Storage-Ordner `data/projects/<slug>/` sind die Bibel-Bytes vorhanden. Manueller Re-Run: in `python -c "import asyncio; from zerberus.core.projects_template import materialize_template; from zerberus.core.projects_repo import get_project; import asyncio; ..."` (oder kurzer CLI-Aufruf) — `materialize_template` ein zweites Mal aufrufen → Rückgabe-Liste ist leer, keine Doubletten in `project_files`-Tabelle. Optional: README löschen, Materialize nochmal → README kommt zurück, Bibel bleibt unverändert | P198 | ⬜ | — |
| 22 | git push + sync_repos.ps1 für P199 | P199 | ⬜ | — |
| 23 | Hel-UI: Projekt anlegen → in `data/projects/<slug>/_rag/` müssen `vectors.npy` + `meta.json` liegen (durch P199-Verdrahtung in `materialize_template`). Eine Datei via Drop-Zone hochladen → Index wächst (vor/nach mit `python -c "from zerberus.core.projects_rag import load_index; v,m=load_index('<slug>', __import__('pathlib').Path('data')); print(v.shape if v is not None else None, len(m))"` prüfen). Server-Log nach `[RAG-199]` greppen → "indexed slug=... file_id=... chunks=N total=M" | P199 | ⬜ | — |
| 24 | Datei in der Hel-UI löschen → Index schrumpft (gleicher `load_index`-Check). Server-Log: `[RAG-199] removed slug=... chunks_removed=N`. Dann das ganze Projekt löschen → `data/projects/<slug>/_rag/` Ordner ist weg (`ls data/projects/<slug>/_rag` schlägt fehl). | P199 | ⬜ | — |
| 25 | Chat-Wirkung: Projekt mit indexierter Datei (Inhalt z.B. ein Markdown mit "Mein Geheimrezept ist Banane mit Senf"). `curl -H "Authorization: Bearer <token>" -H "X-Active-Project-Id: <id>" -X POST -d '{"messages":[{"role":"user","content":"Was ist mein Geheimrezept?"}]}' .../v1/chat/completions` → Antwort sollte "Banane mit Senf" enthalten. Server-Log: `[RAG-199] project_id=... slug=... chunks_used=N` mit N>0. Kontroll-Test ohne `X-Active-Project-Id` → keine `[RAG-199]`-Zeile, LLM kennt das Rezept nicht. | P199 | ⬜ | — |
| 26 | git push + sync_repos.ps1 für P200 | P200 | ⬜ | — |
| 27 | iPhone Safari → Nala öffnen → Teilen-Menü → "Zum Home-Bildschirm" → App startet ohne Browser-Leiste (standalone), Icon auf Homescreen ist Kintsugi-Gold-auf-Dunkel, Theme-Color/Statusbar passt | P200 | ⬜ | — |
| 28 | iPhone Safari → Hel öffnen → "Zum Home-Bildschirm" (separates Icon!) → öffnet standalone, Icon unterscheidbar von Nala | P200 | ⬜ | — |
| 29 | Android Chrome → Nala öffnen → "App installieren"-Banner oder Menü-Eintrag → installiert standalone, Splash-Screen zeigt Icon + Theme-Color, App im App-Drawer | P200 | ⬜ | — |
| 30 | Android Chrome → Hel öffnen → "App installieren" → installiert standalone, separates Icon vom Nala-PWA | P200 | ⬜ | — |
| 31 | Beide Geräte: Service-Worker-Registrierung im DevTools-Application-Tab sichtbar, App-Shell wird gecached, Reload aus dem Cache funktioniert wenn Server steht | P200 | ⬜ | — |
| 32 | Hel-PWA-Auth: nach Install im standalone-Mode öffnen → Basic-Auth-Prompt erscheint (Hel-Routes sind weiterhin authentifiziert, nur Manifest+SW gehen public). Manifest-Fetch via DevTools-Network: 200 OHNE Auth-Header | P200 | ⬜ | — |
| 33 | git push + sync_repos.ps1 für P201 | P201 | ⬜ | — |
| 34 | Nala-Tab "Projekte": Login → Settings (⚙ in Sidebar-Footer) → Tab `📁 Projekte` sichtbar zwischen "Ausdruck" und "System". Klick → Liste lädt. Projekt auswählen → Header-Chip `📁 <slug>` erscheint neben Profile-Badge. Reload (F5) → Chip bleibt sichtbar (localStorage). Klick auf Chip → Settings öffnet automatisch auf Projects-Tab. Auswahl löschen → Chip verschwindet | P201 | ⬜ | — |
| 35 | End-to-End P197+P199 von Nala aus: Projekt mit indexierter Datei (Inhalt z.B. "Geheimrezept Banane mit Senf"), in Nala aktivieren, normale Chat-Frage "Was ist mein Geheimrezept?" → Antwort enthält "Banane mit Senf". Server-Log: `[PERSONA-197]` mit project_block_len > 0 + `[RAG-199]` mit chunks_used > 0. Auswahl löschen, gleiche Frage erneut → keine `[PERSONA-197]`/`[RAG-199]`-Zeile, LLM kennt das Rezept nicht | P201 | ⬜ | — |
| 36 | Zombie-ID-Schutz: Projekt in Nala aktivieren, dann in Hel löschen (oder archivieren), dann in Nala Settings → Projekte-Tab öffnen → Liste lädt OHNE das gelöschte Projekt, Header-Chip ist automatisch verschwunden, localStorage geräumt | P201 | ⬜ | — |
| 37 | XSS-Sanity: in Hel ein Projekt mit name `<script>alert(1)</script>` anlegen, in Nala Settings → Projekte-Tab → Name wird als TEXT angezeigt (escaped: `&lt;script&gt;...`), KEIN Alert-Popup. Sanity-Check in DevTools → DOM enthält die Entitäten, nicht das Tag | P201 | ⬜ | — |
| 38 | git push + sync_repos.ps1 für P202 | P202 | ⬜ | — |
| 39 | **Hel-Auth-Recovery:** Browser → `https://localhost:5000/hel/` → Basic-Auth-Prompt erscheint NATIVELY (kein JSON-Body sichtbar). Wenn vorher P200 lief: DevTools → Application → Service Workers → `/hel/sw.js` → Unregister → F5 → neuer SW (v2) installiert sich → Auth-Prompt erscheint. Login mit admin-creds → Hel-UI lädt | P202 | ⬜ | — |
| 40 | **PWA-Roll-Out beider Apps:** Auf iPhone + Android beide installierten PWAs (Nala + Hel) öffnen → SW-Update läuft im Hintergrund (DevTools-Application zeigt Cache `nala-shell-v2`/`hel-shell-v2`, alter v1-Cache ist weg). Hel-PWA: nach erstem Open im Standalone-Mode kommt nativer Auth-Prompt (vorher: JSON-Fehler). Nala-PWA: weiter unverändert. | P202 | ⬜ | — |
| 41 | **SW-Navigation-Skip live verifizieren:** DevTools → Application → Service Workers → `/hel/sw.js` → Source ansehen → muss `request.mode === 'navigate'` enthalten und früh return-en. Network-Tab beim `/hel/`-Reload: Request muss als "Navigation" mode (nicht via SW) geloggt sein | P202 | ⬜ | — |
| 42 | git push + sync_repos.ps1 für P203a | P203a | ⬜ | — |
| 43 | **Workspace-Materialize live:** Hel → Projekt anlegen → in Datei-Drop-Zone eine Datei hochladen → in `data/projects/<slug>/_workspace/` muss die Datei mit dem `relative_path` als Name liegen. `ls -la` (oder `Get-Item`) prüfen ob es ein Hardlink ist (`st_nlink` ≥ 2 vs. SHA-Storage). Server-Log nach `[WORKSPACE-203]` greppen → "materialized path=... via=hardlink|copy" | P203a | ⬜ | — |
| 44 | **Hardlink-vs-Copy auf Chris' FS:** Auf der echten Workstation (NTFS) sollten `hardlink`-Logs erscheinen. Bei Volumen-Check: `du -sh data/projects/<slug>/_workspace/` sollte ~0 sein, weil Hardlinks keinen Plattenplatz brauchen. Bei `via=copy`-Logs ist das ein Indiz für cross-fs (data_dir auf anderer Partition als SHA-Storage?) — nachdenken ob das gewollt ist | P203a | ⬜ | — |
| 45 | **Wipe-bei-Delete-Project verifizieren:** Projekt mit Dateien anlegen, in `data/projects/<slug>/_workspace/` mehrere Files präsent, Hel → Projekt löschen → Ordner `_workspace` ist weg. Server-Log: `[WORKSPACE-203] wiped workspace_root=...`. Plus: Delete-Single-File: Datei löschen → einzelner Eintrag aus dem Workspace verschwindet, leere Eltern-Ordner werden mit aufgeräumt, andere Files im Workspace bleiben | P203a | ⬜ | — |
| 46 | git push + sync_repos.ps1 für P203b | P203b | ⬜ | — |
| 47 | **Hel-UI wieder bedienbar:** Browser-Cache leeren (DevTools → Application → Clear Storage), Hel öffnen, Login durchführen → Tabs sind klickbar (Metriken/LLM/Prompt/RAG/Cleaner/User/Tests/Dialekte/System/Provider/Huginn/**Projekte**/Links/About). Alle Tabs durchklicken → Inhalt wechselt. Settings-Zahnrad → Panel öffnet. Browser-Konsole offen lassen → KEIN ReferenceError, KEIN SyntaxError beim Laden. | P203b | ⬜ | — |
| 48 | **Datei-Lösch-Button funktioniert (Event-Delegation):** Hel → Projekte-Tab → Projekt auswählen → Datei-Liste im Detail-Panel → Lösch-Button neben einem File klicken → Confirm-Dialog erscheint → Bestätigen → Datei verschwindet aus Liste. DevTools-Network: DELETE-Call gegen `/hel/admin/projects/<id>/files/<file_id>` mit 200. Plus: Datei mit Apostroph im Namen hochladen (z.B. `chris's notes.md`) → Lösch-Button funktioniert auch dort sauber (war vor P203b XSS-Risiko + Quote-Escape-Bruch). | P203b | ⬜ | — |

---

## Offene Fragen (DECISIONS_PENDING)

> Coda: Parke hier was du nicht allein entscheiden kannst oder willst.
> Chris: Beantworte und setz Status auf BEANTWORTET. Coda liest es im nächsten Fenster.

| # | Frage | Kontext | Antwort Chris | Status |
|---|-------|---------|---------------|--------|
| 1 | Projekt-DB: eigene SQLite oder Tabellen in bunker_memory.db? | Isolation vs. Einfachheit | **bunker_memory.db** mit eigenen Tabellen (projects, project_files). Vermeidet zwei Connections/WAL-Configs/Backup-Pfade und ATTACH bei Joins. Isolation via Foreign Keys + Namespaces. | BEANTWORTET 2026-05-01, UMGESETZT P194 |
| 2 | Projekt-UI zuerst in Hel oder auch in Nala? | Admin-first vs. Mobile-first | **Hel-first.** Projekt-Verwaltung (anlegen/konfigurieren/Dateien) = Admin-Arbeit, Desktop-Kontext. Nala-Integration zweiter Schritt: im Chat "Wechsel zu Projekt X", Projektkontext fließt in Antworten. | BEANTWORTET 2026-05-01, BACKEND P194, UI offen → P195 |
| 3 | Persona-Hierarchie: Projekt überschreibt User-Persona? | "Mein Ton" vs. Projekt-Ton | **Merge, nicht Override.** Layer-Order: System-Default → User-Persona ("Mein Ton") → Projekt-Persona. Projekt darf Fachsprache und Kontext-Regeln hinzufügen, Grundton bleibt erhalten. | BEANTWORTET 2026-05-01, SCHEMA-FELD `persona_overlay` IN P194, MERGE-LAYER AKTIV in P197 ✅ |

---

## Bekannte Schulden

| Item | Status |
|------|--------|
| system_prompt_chris.json Mutzenbacher-Persona-Experiment | gedroppt 2026-05-01 (Chris-Entscheidung) |
| interactions-Tabelle ohne User-Spalte | Alembic nötig vor Per-User-Metriken |
| 2 pre-existing Test-Failures (SentenceTransformer-Mock, edge-tts-Install) | Nicht blockierend |
