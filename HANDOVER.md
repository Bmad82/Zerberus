## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P204 — Prosodie-Kontext im LLM (Phase 5a #17 abgeschlossen)
**Tests:** 1685 passed (+40), 4 xfailed (pre-existing), 2 failed (pre-existing aus Schuldenliste: edge-tts + dual-rag)
**Commit:** (folgt nach Push)
**Repos synchron:** (folgt nach sync_repos.ps1 + verify_sync.ps1)

---

## Zuletzt passiert (1 Patch in dieser Session)

**P204 — Prosodie-Kontext im LLM (Phase 5a #17, unabhängig einschiebbar).** Der Feature-Request von Chris (siehe `FEATURE_REQUEST_PROSODIE_KONTEXT.md` aus dem Session-Eintrag, lokal gehalten) hatte als Ziel #17 in den Workflow eingetragen — danach autonom implementiert.

**Was vorher fehlte:** Die Whisper+Gemma+BERT-Pipeline lieferte ihre Daten ans UI-Triptychon (P192), aber DeepSeek bekam beim Voice-Input keinen Stimmungs-Kontext. P190 hatte einen rudimentären `[Prosodie-Hinweis ...]`-Block, der NUR Gemma transportierte (kein BERT, kein Konsens) und ein ad-hoc-Format hatte (`Stimmung=happy, Tempo=fast, Confidence: 85%`).

**Was P204 baut:**

Pure-Function-Schicht in [`zerberus/modules/prosody/injector.py`](zerberus/modules/prosody/injector.py):

```python
def build_prosody_block(
    prosody: Optional[dict],
    *,
    bert_label: Optional[str] = None,
    bert_score: Optional[float] = None,
) -> str:
    ...
```

Block-Format mit eigenem Marker analog `[PROJEKT-RAG]` (P199):

```
[PROSODIE — Stimmungs-Kontext aus Voice-Input]
Stimme: ruhig
Tempo: langsam
Sentiment-Text: leicht positiv (BERT)
Sentiment-Stimme: ruhig (Gemma)
Konsens: ruhig
[/PROSODIE]
```

Bei Inkongruenz (BERT positiv + Prosody-Valenz negativ): `Konsens: inkongruent — Text positiv, Stimme negativ (mögliche Ironie oder Stress)`.

**Worker-Protection (P191): keine Zahlen im Block.** Confidence/Score/Valence werden im Konsens-Label verkocht — qualitative Labels wie `leicht positiv`, `deutlich negativ`, `ruhig`, `müde`, `inkongruent` sind alles, was beim LLM ankommt. Defense via parametrisiertem Regex-Test (`TestWorkerProtectionNoNumbers`): drei Prosody+BERT-Szenarien werden geprüft, dass `\d+\.\d+`/`%`/`\b\d+\b` nirgends im Block vorkommt.

**Mehrabian-Konsens-Logik (Pure):** BERT positiv (`label=positive` UND `score>0.5`) + Prosody-Valenz negativ (`valence<-0.2`) → Inkongruenz-Pfad. Sonst Confidence > 0.5 → Stimme dominiert (Stimm-Mood gewinnt). Sonst BERT-Fallback (`deutlich/leicht positiv/negativ` oder `neutral`). Schwellen sind die GLEICHEN wie in [`utils/sentiment_display.py::consensus_emoji`](zerberus/utils/sentiment_display.py) — UI-Konsens und LLM-Konsens dürfen nicht divergieren. (Falls die Schwellen mal angepasst werden: BEIDE Stellen synchron halten, oder gemeinsamen Helper extrahieren.)

**Verdrahtung in `legacy.py /v1/chat/completions`:** Der bestehende P190-Block wurde umgebaut. JSON-Parse von `X-Prosody-Context`-Header mit Type-Guard (nur `dict`), `X-Prosody-Consent: true` als Gate, server-seitig `analyze_sentiment(last_user_msg)` aus `zerberus.modules.sentiment.router` als BERT-Quelle (try/except, fail-open: BERT-Fehler → kein Sentiment-Text-Zeile, Block läuft trotzdem), Aufruf von `inject_prosody_context(sys_prompt, _prosody_ctx, bert_label=..., bert_score=...)` mit Keyword-Args.

**Reihenfolge der Brücken-Blöcke im finalen System-Prompt** (von oben nach unten):

1. Base-Persona aus `system_prompt_<profile>.json` (P184)
2. Projekt-Persona-Overlay (P197 `[PROJEKT-KONTEXT — verbindlich für diese Session]`)
3. AKTIVE-PERSONA-Wrap (P184)
4. Runtime-Info (P185)
5. Decision-Box-Hint (P118a)
6. **Prosodie-Block (P190+204 `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`)**
7. Projekt-RAG-Block (P199 `[PROJEKT-RAG — Kontext aus Projektdateien]`)

**Voice-only-Garantie zwei-stufig:**

1. **Datenfluss:** `X-Prosody-Context`-Header wird vom Frontend NUR nach Whisper-Roundtrip gesetzt. Bei getipptem Text gibt's keinen Header → kein Block.
2. **Defense-in-depth:** Stub-Source-Check filtert versehentliche Pseudo-Contexts. Wenn Frontend-Bug einen alten Voice-Context bei einem getippten Turn mitsendet → `prosody.get("source") == "stub"` → leerer Block.

**Tests:** 33 neue in `test_p204_prosody_context.py` (`TestBuildProsodyBlock` 9, `TestWorkerProtectionNoNumbers` 3 parametrisiert, `TestConsensusLabel` 6, `TestBertQualitative` 6, `TestInjectWithBert` 5, `TestP204LegacyVerdrahtung` 6 Source-Audit, `TestMarkerUniqueness` 3 — distinct vom PROJEKT-RAG/PROJEKT-KONTEXT). Plus 6 nachgeschärfte Tests in `test_prosody_pipeline.py::TestInjectProsodyContext` (Format-Assertions umgestellt von `"[Prosodie-Hinweis"`/`"Stimmung=happy"`/`"Confidence: 85%"` auf `PROSODY_BLOCK_MARKER`/`"Stimme: fröhlich"`/qualitative Labels, plus neuer Idempotenz-Test).

**Was P204 bewusst NICHT macht:**
- **Keine Persistierung der Prosodie-Daten in der DB.** Worker-Protection — Daten sind one-shot pro Request.
- **Keine Triptychon-UI-Änderung.** P192 zeigt die Daten schon, P204 ist die Brücke zum LLM.
- **Keine Pipeline-Änderung.** Whisper/Gemma/BERT bleiben unverändert; P204 nutzt nur die Outputs.
- **Kein neuer `X-Voice-Input`-Header.** Der bestehende `X-Prosody-Context`-Header IST der Voice-Indikator, Stub-Source-Check ist defense-in-depth.
- **Kein BERT-Header-Reuse aus P193.** Server-seitig BERT auf `last_user_msg` ist O(ms) im selben Prozess; Header-Engineering wäre Premature-Optimization.

## Nächster Schritt — sofort starten

**P203c: Sandbox-Workspace-Mount + Code-Execution-Pipeline.** P204 hat den Phase-5a-#17-Slot abgeschlossen, der unabhängig war — die Phase-5a-Kette steht weiter offen ab #5 (Code-Execution). P203c bleibt der nächste Patch im Strang:

`SandboxManager` aus P171 um optionalen `workspace_mount: Optional[Path]` erweitern (Read-Only-Default, Read-Write per separatem Flag). Bestehender Pfad ohne Mount bleibt unverändert. Neue Helper-Funktion `execute_in_workspace(project_id, code, language, base_dir)` die intern `workspace_root_for(slug, base_dir)` zieht und an `SandboxManager.execute(workspace_mount=...)` durchreicht. Tests: existing-Pfad unverändert, Workspace-Mount sichtbar im docker-args, Read-Write-Flag, Sicherheit (Mount-Pfad muss innerhalb `data_dir` liegen).

Konkret (Coda darf abweichen):
1. **`SandboxManager.execute` Signatur erweitern.** Optional `workspace_mount: Optional[Path] = None`, optional `mount_writable: bool = False`. Wenn gesetzt, in `_run_in_container` einen `-v <abs>:/workspace[:ro]` an die docker-args anhängen + `--workdir /workspace`. Wenn `mount_writable=False` → `:ro`-Suffix.
2. **Neue Convenience-Funktion `execute_in_workspace(project_id, code, language, base_dir, *, writable=False)`** in `projects_workspace.py` (oder neuem Modul `projects_sandbox.py`): zieht Slug aus DB, ruft `workspace_root_for`, reicht an `SandboxManager.execute` durch.
3. **Tests:** Pure-Function (docker-args-Audit für `:ro` vs ohne), Sicherheits-Check (Mount-Pfad muss innerhalb `data_dir`-Tree liegen — verhindert `/etc/passwd`-Mount bei manipuliertem Slug), Integration mit Mock-Sandbox.
4. **HitL-Gate kommt erst P206.** P203c läuft direkt durch — sicher, weil Workspace-Mount Read-Only-default und Sandbox bereits hart-isoliert ist.

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P203c** Sandbox-Workspace-Mount — wie oben
- **P203d** Chat-Pipeline-Verdrahtung (Tool-Use-LLM, Output-Synthese, UI) — schließt Ziel #5 ab
- **P205** RAG-Toast in Hel-UI — Upload-Response enthält `rag.{chunks, skipped, reason}`, Frontend ignoriert es, kleiner Füller
- **P206** HitL-Gate vor Code-Execution — Ziel #6
- **P207** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8

(Patch-Nummerierung: P204 wurde für Phase-5a-#17 verbraucht, der ursprüngliche RAG-Toast-Vorschlag rückt auf P205.)

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202)**, **Project-Workspace-Layout (P203a)**, **Hel-UI-Hotfix Event-Delegation (P203b)**, **Prosodie-Kontext im LLM (P204)**.

Helper aus P204 die in spätere Patches direkt nutzbar sind:
- `injector.build_prosody_block(prosody, *, bert_label, bert_score)` — Pure-Function, baut den `[PROSODIE]...[/PROSODIE]`-Block. Idempotent (zweiter Aufruf bei identischem Input liefert dieselbe Ausgabe). Lookup-Tables `_BERT_LABEL_DE`/`_PROSODY_MOOD_DE`/`_PROSODY_TEMPO_DE` sind privat, falls neue Mood/Tempo/Sentiment-Labels eingeführt werden müssen.
- `injector._consensus_label(bert_label, bert_score, prosody)` — Pure-Function für Mehrabian-Konsens. Schwellen identisch zu `utils/sentiment_display.py`. Falls UI-Konsens-Logik ändert: HIER MIT-ÄNDERN.
- `injector.PROSODY_BLOCK_MARKER` / `PROSODY_BLOCK_CLOSE` — String-Konstanten, eindeutig (substring-disjoint von `PROJECT_BLOCK_MARKER` und `PROJECT_RAG_BLOCK_MARKER`). Idempotenz-Check in `inject_prosody_context` nutzt den Marker.

Helper aus P203a die in P203c+ direkt nutzbar sind:
- `projects_workspace.workspace_root_for(slug, base_dir)` — Pfad-Konvention `<base>/projects/<slug>/_workspace/`. Wird von P203c als Sandbox-Mount-Source genommen
- `projects_workspace.materialize_file(workspace_root, relative_path, source_path)` — Sync FS-Operation, idempotent. Falls P203d nach Code-Execution Files in den Workspace zurückrollt: hier ist die Pure-Schicht
- `projects_workspace.sync_workspace(project_id, base_dir)` — Komplett-Resync. Empfohlen als Implementation für künftiges `POST /hel/admin/projects/{id}/resync-workspace`
- `projects_workspace.is_inside_workspace(target, root)` — Pfad-Sicherheits-Check, Pure. Verwendbar in P203c für Mount-Source-Validation
- `projects_workspace.wipe_workspace(workspace_root)` — Complete-Removal mit Sicherheits-Check (Pfad muss auf `_workspace` enden)

Helper aus P203b — wenn neue Hel-Renderer entstehen, die User-Daten in DOM schreiben:
- **Pattern: `data-*`-Attribute + Event-Delegation statt inline `onclick`-String-Concat.** Siehe `loadProjectFiles` als Vorbild. Quote-immun, XSS-sicher.
- **JS-Integrity-Test laufen lassen.** `test_p203b_hel_js_integrity.py::TestJsSyntaxIntegrity` ist generisch — es deckt ALLE inline `<script>`-Blöcke aus `ADMIN_HTML` ab. Wenn neue Bug-Pattern eingeschleust werden, fällt der Test SOFORT.

Helper aus P201 die in P203+ direkt nutzbar sind:
- `nala.nala_projects_list(request)` — JWT-authenticated Read-Endpoint
- JS-`profileHeaders(extra)` — wirkt für alle Nala-Calls. Wenn P203d weitere Header durchschleifen muss (z.B. `X-Sandbox-Run-Id`), hier ergänzen statt am Call-Site
- JS-`getActiveProjectId()` / `getActiveProjectMeta()` — falls ein Frontend-UI-Element den aktuellen Projekt-Slug rendern soll
- JS-`escapeProjectText(s)` — generischer XSS-Helper, in P203d wiederverwendbar für User-eingegebene Code-Filenames/-Outputs

Helper aus P200 die in P203+ direkt nutzbar sind:
- `pwa.render_service_worker(cache_name, shell)` — Pure-Function-SW-Renderer. Achtung: Nach P202 cached er KEINE Navigation mehr — das ist Absicht und muss so bleiben
- `scripts/generate_pwa_icons.py` — deterministischer PIL-Renderer

Helper aus P199 die in P203+ direkt nutzbar sind:
- `projects_rag.query_project_rag(project_id, query, base_dir, *, k=5)` — Top-K-Hits für eine Query. Nutzt P203d für den Code-Generation-Prompt
- `projects_rag.format_rag_block(hits, project_slug=None)` — markdown-formatierter Block für System-Prompt-Anhang
- `projects_rag.chunk_file_content(text, relative_path)` — Pure-Function-Chunker für ein einzelnes File
- `projects_rag.index_project_file(project_id, file_id, base_dir)` / `remove_file_from_index(...)` — bequeme async-API für Trigger-Punkte

Projekt-Bausteine (P194-P199):
- `projects_repo.create_project/get_project/update_project/archive_project/delete_project` — CRUD
- `projects_repo.register_file/list_files/get_file/delete_file` — Datei-Metadaten
- `projects_repo.compute_sha256/storage_path_for/sanitize_relative_path/is_extension_blocked/count_sha_references` — Helper
- `persona_merge.merge_persona/read_active_project_id/resolve_project_overlay` — Persona-Layer (P197)
- `projects_template.template_files_for/materialize_template` — Skelett-Files (P198)
- `projects_rag.index_project_file/remove_file_from_index/query_project_rag/format_rag_block` — RAG-Layer (P199)
- `projects_workspace.workspace_root_for/materialize_file/remove_file/wipe_workspace/sync_workspace` — Workspace-Layer (P203a)
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können umbiegen)

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197), #19-21 (P198), #22-25 (P199), #26-32 (P200), #33-37 (P201), #38-41 (P202), #42-45 (P203a), #46-48 (P203b), **#49-51 (P204: Push, Voice-Input mit `[PROSODIE]`-Block im Server-Log, getippter Text ohne Block)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`) — **lokaler Stand 1645 baseline + 40 neu = 1685 passed, 2 failed (beide pre-existing aus Schuldenliste)**, 4 xfailed sichtbar, 1 skipped — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — die Mehrabian-Logik in `modules/prosody/injector.py` (P204) und `utils/sentiment_display.py` (P192) hat heute IDENTISCHE Schwellen (BERT_HIGH=0.7, PROSODY_DOMINATES=0.5, VALENCE_NEGATIVE=-0.2). Wenn jemand sie in einer Datei ändert, MÜSSEN sie in der anderen mit. Cross-Test-Verifikation kann das einfangen, ist aber nicht heute geschrieben — wenn die Logik nochmal getouched wird: gemeinsamen Helper extrahieren erwägen.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Z. 1653 + Z. 3096) — JS überschreibt im non-strict Mode silent, beide unterschiedlich (Z. 1653 escaped `&<>"`, Z. 3096 zusätzlich `'`). Nicht akut, aber Sauberkeits-Schmerzpunkt — bei Gelegenheit konsolidieren auf die strengere Version.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — Pattern war fragil, P203b hat NUR den `loadProjectFiles`-Renderer gefixt. Andere Renderer in `ADMIN_HTML` mit ähnlichem Pattern sollten beim nächsten Touch auf Event-Delegation migriert werden.
- **NALA_HTML hat keinen `node --check`-Pass.** Schwester-Test wäre sinnvoll — aktuell unklar ob NALA_HTML denselben Bug-Vektor hat.
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — P205 vorgemerkt.
- **Pre-existing Files ohne Workspace** — Files VOR P203a hochgeladen sind nicht im Workspace. Recovery-Pfad steht (`projects_workspace.sync_workspace`).
- **Pre-existing Files ohne RAG-Index** — separat (P199-Kontext).
- **Lazy-Embedder-Latenz** — beim ersten `index_project_file`-Call lädt MiniLM-L6-v2.
- **PWA-Cache-Bust nach Patches** — Cache-Namen `nala-shell-v2`/`hel-shell-v2`. Falls nochmal harter Cache-Bust nötig: auf `-v3` setzen.
- **PWA-Icons nur Initial-Buchstabe** — `scripts/generate_pwa_icons.py` Theme-Konstanten anpassen falls echtes Logo.
- **PWA hat keinen Offline-Modus für die Hauptseite** — akzeptabel für Heimserver.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — Quick-Create-Button später möglich.
- **P203a Workspace-Mount im Sandbox** — kommt mit P203c.
- **Hardlink vs. Copy auf Windows** — `os.link` funktioniert auf NTFS, schlägt aber bei FAT32/exFAT/cross-device fehl. Der Copy-Fallback ist live-getestet via Monkeypatch.
- **P204 BERT-Header-Reuse aus P193** — Whisper-Endpoint hat schon BERT berechnet, aber Frontend reicht das nicht weiter. P204 macht BERT server-seitig nochmal (O(ms)). Falls Latenz drückt: `X-Sentiment-Context`-Header analog `X-Prosody-Context` einführen.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | **Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) — qualitative Labels, keine Zahlen, Mehrabian-Konsens identisch zu UI-Triptychon**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen (P201) | Header-Injektion für Cross-Cutting-Concerns MUSS zentral in `profileHeaders()` passieren (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig: `is_inside_workspace` für jeden Schreib-/Löschvorgang im Workspace, plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet — verhindert Slug-Manipulations-Angriffe (P203a) | Hardlink primär, Copy als Fallback bei `OSError` — die Methode wird im Return ausgewiesen damit Tests/Logs sehen welcher Pfad griff (P203a) | Atomic-Write-Pattern (tempfile + os.replace) gilt auch für Workspace-Spiegelung — parallele Sandbox-Reads dürfen nie ein halb-geschriebenes File sehen (P203a) | Inline `onclick="fn(...)"` mit String-Concat über benutzergenerierte Daten ist verboten in HTML-im-Python-`"""..."""`-Strings — IMMER `data-*`-Attribute + Event-Delegation per `addEventListener` verwenden (P203b) | JS-Integrity-Test (`node --check` über alle inline `<script>`-Blöcke) gehört in jede Test-Suite, die HTML in Python-Source baut (P203b) | **Prosodie-Brücken-Block enthält NIEMALS numerische Werte (Confidence/Score/Valence/Arousal) — nur qualitative Labels — Worker-Protection P191 (P204)** | **Mehrabian-Schwellen für Konsens-Bestimmung sind identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) — UI-Emoji und LLM-Label dürfen nicht voneinander abweichen (P204)** | **Voice-only-Garantie zwei-stufig: (1) Frontend setzt Prosody-Context-Header NUR nach Whisper-Roundtrip; (2) Backend filtert defense-in-depth über Stub-Source-Check (P204)** | **LLM-Brücken-Blöcke (`[PROJEKT-KONTEXT]`, `[PROJEKT-RAG]`, `[PROSODIE]`) MÜSSEN substring-disjoint sein und Idempotenz-Check via Marker-Substring im Prompt machen (P204)**
