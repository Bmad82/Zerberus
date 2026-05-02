## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P199 — Phase 5a #3: Projekt-RAG-Index
**Tests:** 1533 passed (+46), 4 xfailed (pre-existing), 2 pre-existing Failures (unrelated)
**Commit:** e5e89b1 — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert
P199 ausgeliefert: Phase 5a Ziel #3 ("Projekte haben eigenes Wissen") abgeschlossen. Jedes Projekt bekommt einen eigenen, isolierten Vektor-Index unter `data/projects/<slug>/_rag/{vectors.npy, meta.json}` — der globale RAG-Index in `modules/rag/router.py` bleibt unberührt. Damit kann das LLM beim aktiven Projekt (P197 `X-Active-Project-Id`-Header) auf Inhalte aus den Projektdateien zugreifen, ohne dass projekt-spezifische Chunks den globalen Memory-Index verschmutzen.

Neuer Helper [`zerberus/core/projects_rag.py`](zerberus/core/projects_rag.py) ist mehrschichtig: Pure-Functions (`_split_prose`, `chunk_file_content`, `top_k_indices`, `format_rag_block`) + File-I/O (`load_index`, `save_index`, `remove_project_index`) + Embedder-Wrapper (`_embed_text` lazy-loaded MiniLM-L6-v2) + async DB-Schicht (`index_project_file`, `remove_file_from_index`, `query_project_rag`). Strikte Trennung der Schichten macht jede einzeln testbar — der echte SentenceTransformer wird in Unit-Tests nie geladen, der `fake_embedder`-Fixture monkeypatcht `_embed_text` mit einem Hash-basierten 8-dim-Pseudo-Embedder.

**Architektur-Entscheidung Pure-Numpy statt FAISS.** Per-Projekt-Indizes sind klein (typisch 10–2000 Chunks). Ein `argpartition` auf `(N, 384)` schlägt den FAISS-Setup-Overhead, und Tests laufen dependency-frei. Persistierung als `vectors.npy` + `meta.json` mit identischer Reihenfolge, atomar via `tempfile.mkstemp` + `os.replace`. Falls Projekte später signifikant >10k Chunks bekommen: trivialer Tausch zu FAISS — nur `top_k_indices` + `load_index`/`save_index` ändern, Public API bleibt.

**Chunker-Reuse:** Code-Files (.py/.js/.ts/.html/.css/.json/.yaml/.sql) via `code_chunker.chunk_code` (P122 AST/Regex). Prosa (.md/.txt/Default) via lokalem Para-Splitter mit weichen Absatz-Grenzen (max 1500 Zeichen, snap an Doppel-Newline, Sentence-Split-Fallback für überlange Absätze, hartes Char-Slice als letzter Fallback). Bei Python-SyntaxError → automatischer Fallback auf Prose-Splitter, damit kaputte Dateien trotzdem indexiert werden.

**Idempotenz:** Pro `file_id` höchstens ein Chunk-Set im Index. Beim Re-Index wird der alte Block für die file_id zuerst entfernt. Gleicher `sha256` ergibt funktional dasselbe Ergebnis (deterministischer Embedder), anderer `sha256` ersetzt den alten Block — vermeidet Doubletten beim Re-Upload mit gleichem `relative_path`.

**Trigger-Punkte:** (a) [`hel.py::upload_project_file_endpoint`](zerberus/app/routers/hel.py) NACH `register_file` — Response erweitert um `rag: {chunks, skipped, reason}`. (b) [`projects_template.py::materialize_template`](zerberus/core/projects_template.py) ruft am Ende JEDES erfolgreichen `register_file` `index_project_file` auf — frisch materialisierte Skelett-Files sind sofort retrievbar. (c) [`hel.py::delete_project_file_endpoint`](zerberus/app/routers/hel.py) ruft `remove_file_from_index` NACH dem DB-Delete — Response erweitert um `rag_chunks_removed`. (d) [`hel.py::delete_project_endpoint`](zerberus/app/routers/hel.py) merkt den Slug VOR dem `delete_project()`-Call und ruft `remove_project_index(slug)` danach. Alle Trigger sind Best-Effort: Indexing-Fehler brechen den Hauptpfad NICHT ab; der Eintrag steht in der DB, der Index lässt sich später nachziehen.

**Wirkung im Chat:** [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) — Block hängt sich NACH P197 (`merge_persona`), NACH P184 (`_wrap_persona`), NACH P185 (`append_runtime_info`), NACH P118a (Decision-Box-Hint) und NACH P190 (Prosodie) an den `sys_prompt`, aber VOR `messages.insert(0, Message(role="system", ...))`. Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` + Anweisung an das LLM + Top-K Hits als Markdown-Sektionen pro File mit `relevance=<score>`. Source-Audit-Test verifiziert die Reihenfolge per Substring-Position (`merge_pos < rag_pos < insert_pos`). Pro Chat-Request höchstens ein Embed-Call (User-Query) plus ein Linearscan über den Projekt-Index — typisch ~10ms.

**Feature-Flags** in `ProjectsConfig` (alle Defaults im Pydantic-Modell, weil `config.yaml` gitignored): `rag_enabled: bool = True` (Tests + Setups ohne `sentence-transformers` schalten den Pfad ab), `rag_top_k: int = 5` (max Anzahl Chunks pro Chat-Query), `rag_max_file_bytes: int = 5 * 1024 * 1024` (drüber: skip beim Indexen, weil typisch Bilder/Archive).

**Defensive Behaviors:** leere Datei → `reason="empty"`, binäre Datei (UTF-8-Decode-Fehler) → `reason="binary"`, Datei zu groß → `reason="too_large"`, Bytes nicht im Storage → `reason="bytes_missing"`, Embed-Fehler → `reason="embed_failed"`, Embedder-Dim-Wechsel zwischen Sessions → Index wird beim nächsten Index-Call neu aufgebaut (kein Crash), inkonsistenter Index (nur eine der zwei Dateien existiert) → leere Basis, kaputtes meta.json → leere Basis, Dim-Mismatch im `top_k_indices` → leeres Ergebnis statt Crash.

46 neue Tests in [`test_projects_rag.py`](zerberus/tests/test_projects_rag.py): 5 Prose-Splitter (Empty/Single-Para/Multi-Para-Merge/Oversized-Split/Single-Para-Sentence-Split), 5 Chunk-File (Empty/Code-Path/Markdown-Prose/Unknown-Extension/SyntaxError-Fallback), 5 Top-K (Empty-Index/k=0/Sortiert/Cap-at-Size/Dim-Mismatch), 4 Save-Load (Missing/Roundtrip/Inconsistent/Corrupted), 2 Remove-Index, 7 Index-File (Markdown/Idempotent/Empty/Binary/Too-Large/Bytes-Missing/Rag-Disabled), 2 Remove-File (selektiv + Drop-Empty-Index), 4 Query (Hit/Empty-Query/Missing-Project/Missing-Index), 2 Format-Block, 3 End-to-End via Upload/Delete-File/Delete-Project, 1 Materialize-Indexes-Templates, 6 Source-Audit (hel.py × 3, projects_template.py, legacy.py-Reihenfolge, config.py). `fake_embedder`-Fixture (Hash-basiert, 8-dim) verhindert echtes Modell-Laden in Unit-Tests. Teststand 1487 → **1533 passed**.

Logging-Tag `[RAG-199]` zeigt `slug`/`file_id`/`path`/`chunks`/`total` beim Indexen, `chunks_used` beim Chat-Query, `chunks_removed` beim Delete. Inkonsistenter/kaputter Index → WARN; Embed/Query-Fehler → WARN mit Exception-Text.

**Phase 5a Ziel #3 ist damit abgeschlossen.** Ziele #1 (Backend P194 + UI P195 + Datei-Upload P196), #2 (Templates P198) und #3 (RAG-Index) sind durch. Decision 3 (Persona-Merge-Layer) seit P197 aktiv. Datei-Upload aus Hel-UI (P196) seit dem auch indexiert.

## Nächster Schritt — sofort starten
**P200: Code-Execution-Pipeline** — Phase 5a Ziel #5 ("Code wird ausgeführt"). Vom Chat aus per Intent `PROJECT_CODE` → LLM generiert Code → Docker-Sandbox (P171) führt aus → Ergebnis zurück in den Chat. Mit Workspace-Layout für Git (jetzt sinnvoll, weil P198 die Templates schon im SHA-Storage liegen hat).

Konkret (Coda darf abweichen):
1. **Workspace-Layout definieren.** Empfehlung: pro Projekt ein `data/projects/<slug>/_workspace/`-Ordner als echter Working-Tree, in den die `project_files`-Einträge per `relative_path` materialisiert werden (Symlink oder Hardlink auf den SHA-Storage, Linux-fähig; Windows-Fallback per Copy). `git init` erst hier sinnvoll. Bei jedem Upload/Delete/Index-Trigger den Workspace synchronisieren.
2. **Intent-Erweiterung.** Neuer Intent `PROJECT_CODE` in `intent_parser.py` — Trigger z.B. "lass laufen", "execute", "run python" + aktives Projekt-Header. Ohne `X-Active-Project-Id` → Intent zurückweisen mit "Bitte Projekt auswählen".
3. **LLM-Code-Generation-Pfad.** Neuer Endpoint oder Sub-Pipeline: Code-Generation-Prompt baut sich aus User-Frage + System-Prompt + RAG-Hits (P199 ist DA, perfekt) + aktuellem Workspace-Tree-Snapshot. LLM bekommt Tool-Use für `execute_python`/`execute_javascript`/`read_file`/`write_file`/`list_files` (alle scoped auf den Projekt-Workspace).
4. **Sandbox-Verdrahtung.** `modules/sandbox/` (P171) ist bereits da — Workspace als Volume mounten, Read-Write nur innerhalb des Workspaces. Output-Limit (Default 10000 Chars), Timeout (30s), pids/memory/cpu-Limits aus `SandboxConfig`.
5. **Antwort-Synthese.** LLM-Antwort enthält ggf. Sandbox-Output. Im Chat als spezielle Message-Type rendern (Code-Block + Output-Block, expandierbar).
6. **HitL-Gate (Phase 5a #6, kommt mit P202).** Vor JEDER Ausführung: Bestätigungs-UI im Chat ("Soll ich das wirklich ausführen?"). Aber das ist P202 — für P200 erstmal direkter Pfad mit Logging, HitL kommt drauf.
7. **Tests.** Pure-Function-Tests für Workspace-Sync (Files materialisieren, Git-Init), async Tests für Sandbox-Aufruf mit Mock-Subprocess, End-to-End mit Mock-LLM, Edge-Cases (Workspace existiert nicht, Code crasht in Sandbox, Timeout, Output zu groß).

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P200** Code-Execution-Pipeline — Ziel #5 (oben beschrieben)
- **P201** Nala-Tab "Projekte" + Header-Setter — verdrahtet das Nala-Frontend mit P197 (User wählt aktives Projekt im Chat-UI), damit P197+P199 endlich von Nala aus erreichbar werden
- **P202** HitL-Gate vor Code-Execution — Ziel #6
- **P203** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P204** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199: `projects_rag.py` mit Pure-Numpy-Linearscan + MiniLM-Embedder + Code-/Prose-Chunker-Reuse + Best-Effort-Verdrahtung in Hel/Materialize/Chat + `rag_enabled`-Flag)**.

Helper aus P199 die in P200+ direkt nutzbar sind:
- `projects_rag.query_project_rag(project_id, query, base_dir, *, k=5)` — Top-K-Hits für eine Query, fertig formatiert mit Score und Metadata. Nutzt P200 für den Code-Generation-Prompt (Codebase-Kontext aus dem Projekt) ohne neuen RAG-Layer
- `projects_rag.format_rag_block(hits, project_slug=None)` — markdown-formatierter Block für System-Prompt-Anhang. Wenn P200 einen separaten "Code-Kontext"-Block brauchen sollte, die gleiche Funktion mit anderem Marker forkbar
- `projects_rag.chunk_file_content(text, relative_path)` — Pure-Function-Chunker für ein einzelnes File. Wenn P200 das Workspace pro Re-Sync neu indexieren will, ohne über Storage zu gehen, ist das die richtige Schicht
- `projects_rag.index_project_file(project_id, file_id, base_dir)` — bequeme async-API für Trigger-Punkte. Wenn P200 nach Code-Execution geänderte Files in den Index reinrollt: einmal pro File aufrufen, ist idempotent
- `projects_rag.remove_file_from_index(project_id, file_id, base_dir)` — Inverse zu obigem; nötig wenn P200 Files aus dem Workspace löschen können soll
- `projects_rag.PROJECT_RAG_BLOCK_MARKER` + `RAG_SUBDIR` + `VECTORS_FILENAME` + `META_FILENAME` — Konstanten für andere Module die Index-Pfade ansprechen müssen (z.B. ein Reindex-CLI-Tool)

Projekt-Bausteine (P194-P199):
- `projects_repo.create_project/get_project/update_project/archive_project/delete_project` — CRUD
- `projects_repo.register_file/list_files/get_file/delete_file` — Datei-Metadaten
- `projects_repo.compute_sha256/storage_path_for/sanitize_relative_path/is_extension_blocked/count_sha_references` — Helper
- `persona_merge.merge_persona/read_active_project_id/resolve_project_overlay` — Persona-Layer (P197)
- `projects_template.template_files_for/materialize_template` — Skelett-Files (P198)
- `projects_rag.index_project_file/remove_file_from_index/query_project_rag/format_rag_block` — RAG-Layer (P199)
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können umbiegen)

Persona-Pipeline-Bausteine (P184/P185/P197/P199):
- `legacy.load_system_prompt(profile_name)` — User-Persona-Resolver
- `legacy._wrap_persona(prompt)` — AKTIVE-PERSONA-Marker davor
- `persona_merge.merge_persona(base, overlay, slug)` — Projekt-Overlay-Block hinten dran (P197)
- `runtime_info.append_runtime_info(prompt, settings)` — Live-Modell/Sandbox/RAG-Block
- `prompt_features.append_decision_box_hint(prompt, settings)` — Decision-Box-Syntax-Anweisung
- `projects_rag.query_project_rag + format_rag_block` — Projekt-RAG-Block hinten dran (P199)

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197), #19-21 (P198), **#22-25 (P199: Push, Upload-triggert-Index, Delete-räumt-Index, Chat-Wirkung mit aktivem Projekt)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (4 xfailed sichtbar — nicht blockierend).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete (P194 `delete_project` löscht aktuell nur DB-Cascade, Bytes können verwaisen wenn der SHA nirgends sonst referenziert ist) — low-prio bis Storage-Volumen relevant. P199 räumt den Projekt-Slug-spezifischen `_rag/`-Ordner auf, aber NICHT die `<sha[:2]>/<sha>`-Bytes — das ist immer noch der separate Cleanup-Job.
- **Nala-Frontend setzt `X-Active-Project-Id` noch nicht** — kommt mit dem Nala-Tab "Projekte" (P201-Vorschlag). Bis dahin ist die Kombination P197+P199 nur über externe Clients (`curl`, SillyTavern, eigene Skripte) nutzbar.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — Upload-Response enthält `rag.{chunks, skipped, reason}`, aber das Frontend ignoriert das Feld. Ein "Indexiert: 5 Chunks" / "Übersprungen (binär)" als Toast wäre nett. Low-prio.
- **Pre-existing Files ohne Index** — Files, die VOR P199 hochgeladen oder via P198 materialisiert wurden, sind nicht im Index. Wenn Chris die nachrüsten will: ein Mini-CLI-Skript, das `list_files` für jedes Projekt ruft und `index_project_file` pro Eintrag — `index_project_file` ist idempotent. Ein größerer Reindex-Endpoint (`POST /hel/admin/projects/{id}/reindex`) wäre sauberer, aber eigener Patch.
- **Lazy-Embedder-Latenz** — der erste `index_project_file`-Call lädt MiniLM-L6-v2 (~80 MB Download beim allerersten Mal, danach Cache). Während Tests via Monkeypatch abgefangen, in Produktion einmal pro Server-Start. Falls das stört: Eager-Init in `main.py` Startup-Hook hinter `rag_enabled`-Flag — kein eigener Patch nötig, ein 3-Zeilen-Diff.
- **Embedder-Modell hardcoded auf MiniLM-L6-v2** — wenn Chris später auf dual umsteigen will (DE/EN-Trennung wie der globale Index), muss `_embed_text` umgebaut werden. Aktuell ist das wartbar via einer einzigen Funktion — kein Architektur-Schmerz, nur ein Modell-Switch.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan)

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (Crash beim Materialisieren bricht Anlegen nicht ab — P198) | **Per-Projekt-RAG-Index ist isoliert vom globalen Index — projekt-spezifische Inhalte verschmutzen nie den globalen Memory-Layer (P199)** | **Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen — `_embed_text` ist die einzige Stelle, die Tests umlenken müssen (P199)** | **Best-Effort-Indexing: jeder Trigger-Punkt (Upload/Materialize/Delete/Chat-Query) toleriert RAG-Fehler — der Hauptpfad bleibt grün (P199)** | **RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig, damit Doppel-Injection-Schutz und Test-Substring-Checks greifen (P199)**
