# ZERBERUS_MARATHON_WORKFLOW.md

**Phase:** 5a — Nala-Projekte
**Letzter Patch:** P195 | **Tests:** 1382 passed (4 xfailed pre-existing, 2 pre-existing Failures unrelated)

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
| 2 | **Projekte haben Struktur** — Template-Dateien, Ordner, optional Git | Damit Projekte nicht leer starten | #1 | ⬜ |
| 3 | **Projekte haben eigenes Wissen** — isolierter RAG-Index pro Projekt | Code-LLM braucht Projektkontext | #1 | ⬜ |
| 4 | **Dateien kommen ins Projekt** — Upload in Nala-Chat, Indexierung | Dateien müssen rein | #1 | ⬜ |
| 5 | **Code wird ausgeführt** — vom Chat zur Docker-Sandbox und zurück | Kernfeature | #1, #3 | ⬜ |
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

Abhängigkeits-Kurzform:
- #1 ← fast alles
- #5 ← #1, #3
- #6, #7 ← #5
- #10 ← #9 ← #5
- #11, #15 ← unabhängig (jederzeit einschiebbar)

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

---

## Offene Fragen (DECISIONS_PENDING)

> Coda: Parke hier was du nicht allein entscheiden kannst oder willst.
> Chris: Beantworte und setz Status auf BEANTWORTET. Coda liest es im nächsten Fenster.

| # | Frage | Kontext | Antwort Chris | Status |
|---|-------|---------|---------------|--------|
| 1 | Projekt-DB: eigene SQLite oder Tabellen in bunker_memory.db? | Isolation vs. Einfachheit | **bunker_memory.db** mit eigenen Tabellen (projects, project_files). Vermeidet zwei Connections/WAL-Configs/Backup-Pfade und ATTACH bei Joins. Isolation via Foreign Keys + Namespaces. | BEANTWORTET 2026-05-01, UMGESETZT P194 |
| 2 | Projekt-UI zuerst in Hel oder auch in Nala? | Admin-first vs. Mobile-first | **Hel-first.** Projekt-Verwaltung (anlegen/konfigurieren/Dateien) = Admin-Arbeit, Desktop-Kontext. Nala-Integration zweiter Schritt: im Chat "Wechsel zu Projekt X", Projektkontext fließt in Antworten. | BEANTWORTET 2026-05-01, BACKEND P194, UI offen → P195 |
| 3 | Persona-Hierarchie: Projekt überschreibt User-Persona? | "Mein Ton" vs. Projekt-Ton | **Merge, nicht Override.** Layer-Order: System-Default → User-Persona ("Mein Ton") → Projekt-Persona. Projekt darf Fachsprache und Kontext-Regeln hinzufügen, Grundton bleibt erhalten. | BEANTWORTET 2026-05-01, SCHEMA-FELD `persona_overlay` IN P194, MERGE-LAYER OFFEN |

---

## Bekannte Schulden

| Item | Status |
|------|--------|
| system_prompt_chris.json Mutzenbacher-Persona-Experiment | gedroppt 2026-05-01 (Chris-Entscheidung) |
| interactions-Tabelle ohne User-Spalte | Alembic nötig vor Per-User-Metriken |
| 2 pre-existing Test-Failures (SentenceTransformer-Mock, edge-tts-Install) | Nicht blockierend |
