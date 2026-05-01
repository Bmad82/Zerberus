# HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-01
**Letzter Patch:** P193
**Tests:** 1316 passed, 0 neue Failures
**Commit:** 412c57a (P192/P193) — Bootstrap-Commit folgt

## Zuletzt passiert
Phase 4 abgeschlossen. Bootstrap-Session: Marathon-Workflow eingerichtet (HANDOVER.md, ZERBERUS_MARATHON_WORKFLOW.md, CLAUDE_ZERBERUS.md-Sektion).

## Nächster Schritt
ZERBERUS_MARATHON_WORKFLOW.md lesen. Phase 5a starten. Erstes Ziel: #1 (Projekte als Entität).

## Offenes
- DECISIONS_PENDING #1-3 BEANTWORTET (2026-05-01) — Phase 5a Ziel #1 entblockt
  - DB: bunker_memory.db mit eigenen Tabellen (projects, project_files)
  - UI: Hel-first, Nala-Integration zweiter Schritt
  - Persona: Merge (System → User → Projekt), kein Override
- Manuelle Tests #2-4 noch offen (Prosodie, Triptychon iOS/Android, Live-Update)

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram

## Invarianten (nie brechen)
/v1/ auth-frei | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit
