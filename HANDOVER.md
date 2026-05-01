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
- DECISIONS_PENDING in Workflow-Datei prüfen — vielleicht hat Chris geantwortet
- Manuelle-Tests-Liste prüfen — vielleicht hat Chris was abgehakt
- Stash `Mutzenbacher-Persona-Experiment` (system_prompt_chris.json) liegt offen — Chris entscheidet pop/drop via `git stash list`

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram

## Invarianten (nie brechen)
/v1/ auth-frei | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit
