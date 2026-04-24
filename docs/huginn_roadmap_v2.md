# Huginn-Roadmap v2 — Post-Review Konsolidierung

**Stand:** Patch 159 (+ Patch 160 Whisper-Hardening erledigt, Commit `d3dd485`, 538 Tests)
**Basis:** 7-Reviewer-Architektur-Review (49 deduplizierte Findings, siehe [`huginn_review_final.md`](huginn_review_final.md))
**Prinzip:** Zwei Schichten — Huginn-jetzt (Kumpels/Telegram) + Rosa-Skelett (Konzern/eigener Messenger)
**Abgelegt:** Patch 161 (2026-04-25) — Doku-only

> **Renumber-Hinweis (Patch 161, Doku-Patch):** Diese Roadmap wurde beim Ablegen unverändert übernommen.
> Da der Doku-Patch selbst die Nummer **161** belegt, verschieben sich die unten aufgeführten Patches
> um +1: ehemals **161 → 162** (Input-Sanitizer), **162 → 163** (Rate-Limiting), **163 → 164** (Intent-Router),
> **164 → 165** (Policy→Persona), **165 → 166** (HitL-Hardening), **166 → 167** (Datei-Output),
> **167 → 168** (Sandbox), **168 → 169** (Stresstests), **169+ → 170+** (Rosa-Skelett).
> Die Roadmap-Phasen (A–E) bleiben unverändert. Im Folgenden stehen die ursprünglichen Nummern;
> die [SUPERVISOR_ZERBERUS.md](../SUPERVISOR_ZERBERUS.md)-Roadmap führt die effektive Nummerierung.

---

## Architektur-Leitlinie

Alles was wir für Huginn bauen, wird so designed, dass Rosa es später verschärfen kann.
Konkret heißt das:

- **Interfaces statt Hardcodes:** Input-Sanitizer, Guard, Policy-Layer bekommen abstrakte Schnittstellen.
  Huginn-Implementierung = pragmatisch. Rosa-Implementierung = streng. Selbes Interface.
- **Config-driven Severity:** Jede Sicherheitsentscheidung (WARNUNG → durchlassen vs. blocken)
  wird über Config-Flags gesteuert, nicht im Code. Rosa dreht die Regler hoch.
- **Transport-Agnostik:** Die gesamte Pipeline (Input → Sanitize → Intent → Policy → LLM → Guard → Output)
  ist vom Transport entkoppelt. Telegram ist nur EIN Frontend. Ein interner Messenger (WebSocket,
  XMPP, Matrix, whatever) dockt an dieselbe Pipeline an.

---

## Phase A — Sicherheits-Fundament (vor Intent-Router!)

### Patch 160 — Whisper Timeout-Hardening ✅ (erledigt: Commit d3dd485)
- httpx-Timeout 120s, Short-Audio-Guard, Retry

### Patch 161 — Input-Sanitizer + Telegram-Hardening
**Review-Findings:** K1, K3, O1, O2, O3, D8, D9, D10, N8

**Huginn-jetzt:**
- **Input-Sanitizer** (regelbasiert, kein LLM-Call):
  - Max-Länge pro Nachricht (z.B. 4096 Zeichen = Telegram-Limit)
  - Bekannte Injection-Patterns (Regex-Blocklist: "ignore previous", "system:", etc.)
  - Zeichensatz-Prüfung (keine Steuerzeichen, kein Null-Byte)
  - `[SANITIZE-161]`-Log bei Treffer
- **Telegram-Protokoll-Härtung:**
  - `update_id`-Tracking persistent (SQLite oder Datei) → Replay-Prevention (D8)
  - Chat-Typ-Filter: nur `private` + `group`/`supergroup` verarbeiten, `channel_post` ignorieren (D9)
  - `message_thread_id`-Awareness: Antworten im richtigen Thread (D10)
  - Unbekannte Update-Typen (Contacts, Polls, Dice etc.) lautlos ignorieren statt crashen (O1)
  - Edited Messages: `edited_message` → Log + ignorieren (kein Re-Processing) (O2)
- **Callback-Query-Validation:**
  - Bei HitL-Buttons: `callback_query.from.id == original_requester_id` prüfen (O3)
  - Fremde Klicks → "Das ist nicht deine Anfrage" + ignorieren
  - Das ist der wichtigste einzelne Fix — ohne das kann Daniel deine Code-Execution bestätigen

**Rosa-Skelett:**
- Interface `InputSanitizer` mit Methode `sanitize(text, metadata) → (cleaned_text, findings[])`
- Huginn-Implementierung = Regex-Blocklist
- Rosa-Implementierung (später) = ML-basierter Classifier + Firmenpolicy-Regeln
- Config-Key `security.input_sanitizer.mode: "regex"` (Rosa: `"ml"`)

### Patch 162 — Rate-Limiting + Graceful Degradation
**Review-Findings:** N3, D1, K4, O10

**Huginn-jetzt:**
- **Per-User Rate-Limit:** Max N Nachrichten pro Minute pro `user_id` (Config-Key, Default 10)
  - Über Limit → Huginn antwortet einmal "Sachte, Keule" und ignoriert danach für Cooldown-Periode
  - Verhindert HitL-State-Poisoning (N3) und Bot-Spam
- **Telegram-Ausgangs-Queue:** Nachrichten nicht direkt senden sondern über Queue mit Rate-Limiting
  - Telegram erlaubt ~30 msg/s an verschiedene Chats, ~20/min pro Gruppe
  - Queue verhindert 429-Shadowban bei autonomen Einwürfen (D1)
- **OpenRouter-Fallback:**
  - Bei 503/429: Retry mit Backoff (3 Versuche)
  - Bei anhaltendem Ausfall: Huginn antwortet "Meine Kristallkugel ist gerade trüb. Versucht's später nochmal."
  - Guard-Ausfall separat behandeln: Config-Flag `security.guard_fail_policy: "allow"` (Huginn) vs. `"block"` (Rosa)
  - Budget-Warnung: Wenn Tageskosten > Config-Threshold → Admin-DM (O10, K4)

**Rosa-Skelett:**
- Config-Key `security.guard_fail_policy`: `"allow"` | `"block"` | `"degrade"`
- Config-Key `limits.daily_budget_eur`: Threshold für Admin-Warnung
- Config-Key `limits.per_user_rpm`: Rate pro User pro Minute
- Bei `"degrade"`: Fallback auf lokales Modell (wenn vorhanden), kein Cloud-Call

---

## Phase B — Intent-Router + Policy-Pipeline

### Patch 163 — Intent-Router (LLM-gestützt statt pure Regex)
**Review-Findings:** P2, O4, O12, fehlende Intents (alle 7 Reviewer)

**Warum LLM statt Regex:** Gemini hat Recht — Whisper-Transkription zerstört Regex-Matching.
"Schreibt mir ein Artikel" matcht nicht auf `^schreib\s+mir`. LLM-basierte Intent-Erkennung
ist fehlertoleranter. ABER: Kein extra Cloud-Call. Stattdessen wird der Intent als
strukturierter Output vom Haupt-LLM-Call mitgeliefert.

**Technik:** System-Prompt-Anweisung an DeepSeek:
```
Antworte IMMER mit einem JSON-Header in der ersten Zeile:
{"intent": "CHAT|CODE|FILE|SEARCH|ADMIN|HELP|ABORT", "confidence": 0.0-1.0, "effort": 0-10}
---
[Deine eigentliche Antwort]
```

Der Router parst die erste Zeile, entscheidet basierend auf Intent + Confidence,
und die eigentliche Antwort geht an den User. EIN LLM-Call, kein Extra-Roundtrip.

**Intent-Katalog (konsolidiert aus allen 7 Reviews):**

| Intent | Beschreibung | Aktion |
|--------|-------------|--------|
| CHAT | Smalltalk, Gespräch | Fastlane (wie bisher) |
| CODE | Code-Aufgabe | → Sandbox (ab Patch 167) |
| FILE | "Schreib mir einen Artikel" | → LLM → Datei-Output |
| SEARCH | Web-Suche gewünscht | → (später) Such-Agent |
| ADMIN | /stats, /config, /restart | → Admin-Endpoints (nur Admin-User) |
| HELP | "Was kannst du?" | → Statische Info, kein LLM nötig |
| ABORT | "Vergiss es" / "Abbruch" | → Wartenden HitL-Task canceln |

**Bewusst NICHT als eigener Intent:**
- RISKY_ACTION (G6) → wird über effort_score + HitL abgefangen
- MEDIA_ANALYZE → geht über Vision-Pfad, kein eigener Intent
- AUDIO_RETRY (P9) → wird im Whisper-Layer behandelt, nicht im Intent-Router
- TRANSFORM / GREETING → nur für Nala relevant (RAG-Skip), Huginn hat kein RAG
- ESCALATE_TO_NALA → kein Cross-Frontend-Routing in Phase 1

**Confidence-Handling:**
- ≥ 0.8 → direkt ausführen
- 0.5–0.8 → Rückfrage als Inline-Keyboard
- < 0.5 → als CHAT behandeln (Fallback)

**Rosa-Skelett:**
- Intent-Liste erweiterbar über Config (nicht hardcoded)
- Confidence-Thresholds konfigurierbar
- Rosa kann zusätzliche Intents definieren (CLASSIFIED, EXPORT_RESTRICTED, etc.)

### Patch 164 — Policy→Persona Pipeline
**Review-Findings:** G3, O5, N6, O6, D3

**Das Problem:** Aktuell ist die Persona (zynischer Rabe) Teil des System-Prompts,
und der Guard prüft danach ob die Antwort okay ist. Das führt zu:
- Effort-Score verstärkt provokante Antworten → Policy-Bypass durch eigenes System (O5)
- caller_context ist ein Patch, kein Design (N6)
- System-Prompt ist editierbar ohne Audit-Trail (O6)
- Admin kann versehentlich Guard-Bypass in Persona einbauen (D3)

**Fix — Dreistufige Antwort-Pipeline:**
```
1. LLM generiert NEUTRALE Antwort (ohne Persona)
2. Policy-Filter prüft: Ist der Inhalt okay? (deterministische Regeln + Guard)
3. Persona-Layer: Neutrale Antwort → Raben-Ton (zweiter LLM-Call oder Prompt-Rewrite)
```

**Huginn-jetzt (pragmatische Variante):**
- Kein zweiter LLM-Call für Persona-Rewrite (zu teuer)
- Stattdessen: System-Prompt wird in zwei Teile gesplittet:
  - **Policy-Block** (nicht editierbar, hardcoded): "Du darfst keine Anleitungen für Waffen geben, etc."
  - **Persona-Block** (editierbar via Hel): "Du bist ein zynischer Rabe..."
- Policy-Block wird VOR Persona-Block im System-Prompt platziert → LLM priorisiert Policy
- System-Prompt-Änderungen werden geloggt (wer, wann, vorher/nachher) → Audit-Trail
- Effort-Score beeinflusst NUR den Persona-Block, nicht die Policy

**Rosa-Skelett:**
- Interface `PolicyEngine` mit Methode `evaluate(response, context) → (allowed, redacted_response, reason)`
- Huginn = einfache Keyword-Blocklist im Policy-Block
- Rosa = Firmenpolicy-Regeln, Compliance-Check, Data-Loss-Prevention
- System-Prompt-Versionierung: Jede Änderung bekommt eine Version-ID, Rollback möglich
- Config-Key `security.persona_editable: true` (Rosa: `false` oder `admin_only_with_audit`)

---

## Phase C — HitL-Hardening + Datei-Output

### Patch 165 — HitL-Hardening
**Review-Findings:** N2, N4, D2, P4, P8, O3

- **Task-ID-System:** Jeder HitL-Task bekommt eine UUID. Inline-Keyboard-Buttons
  enthalten die Task-ID im `callback_data`. "Mach" bei zwei wartenden Tasks → Rückfrage
  "Welchen meinst du?" mit Task-Liste (D2)
- **Task-Ownership:** `task.requester_id = user_id`. Nur der Requester darf bestätigen.
  Admin kann override (aber wird geloggt) (O3, N4)
- **Auto-Reject-Timeout:** Wartende Tasks verfallen nach Config-Timeout (Default 5 Min).
  Huginn sagt "Zu langsam, Anfrage verworfen." HitL-State wird aufgeräumt (N2)
- **Persistenter State:** HitL-Tasks in SQLite statt RAM. Server-Restart killt keine
  wartenden Tasks mehr (P4). Tabelle `hitl_tasks(id, requester_id, chat_id, intent,
  payload_json, status, created_at, resolved_at)`
- **Natürliche Sprache NUR für CHAT:** Für CODE/FILE → Inline-Keyboard-Buttons.
  "Ja genau, mach den Server kaputt" wird nicht als GO interpretiert (P8)

**Rosa-Skelett:**
- HitL-Tasks bekommen Severity-Level (LOW/MEDIUM/HIGH/CRITICAL)
- CRITICAL → braucht zwei Admin-Bestätigungen (Four-Eyes-Principle)
- Config-Key `hitl.require_dual_approval_above: "HIGH"`

### Patch 166 — Datei-Output + Aufwands-Kalibrierung
**Review-Findings:** K5, P5, P6, D7

- **Datei-Output-Logik (intent-basiert, nicht zeichenbasiert):**
  - Intent = FILE → immer Datei
  - Intent = CODE → immer Datei (`.py`, `.js` etc.)
  - Intent = CHAT + Response > 2000 Zeichen → Datei als Fallback
  - Telegram-Limit ist 4096 Zeichen, nicht 500 — P5 hat Recht (P5)
- **Content-Review vor Datei-Versand:**
  - Guard prüft Datei-Inhalt (nicht nur Chat-Antworten) (K5)
  - Size-Limit: Max 10 MB pro Datei
  - MIME-Type-Whitelist: `.txt`, `.md`, `.py`, `.js`, `.pdf` — kein `.exe`, `.sh`, `.bat`
- **PDF-Generierung (serverseitig):**
  - `markdown` → HTML → `weasyprint` oder `pdfkit`
  - LLM-Output Sanitizer vor PDF-Rendering: ungeschlossene Tags fixen, Tabellen validieren (P6)
- **Bild-Kompression vor Vision:**
  - Resize auf max 1024px längste Seite vor Cloud-Send (D7)
  - Spart Faktor 5-10 an Payload-Kosten
- **Aufwands-Kalibrierung:**
  - `effort_score` aus dem Intent-Router JSON-Header (Patch 163)
  - Score beeinflusst Persona-Ton (nur den Persona-Block, nicht Policy)
  - Score 0-3: kommentarlos liefern. 4-6: kurzer Kommentar. 7-10: ausführlicher Sarkasmus + Rückfrage

**Rosa-Skelett:**
- Datei-Output durchläuft DLP-Check (Data Loss Prevention): keine Firmennamen, keine IP-Adressen etc.
- PDF-Templates mit Firmen-Header (konfigurierbar)
- Config-Key `output.dlp_enabled: false` (Rosa: `true`)

---

## Phase D — Sandbox + Stresstests

### Patch 167 — Docker-Sandbox-Anbindung
**Review-Findings:** K2, G1, G7, D4, O8

- **Pre-Flight-Check (D4):** Vor Sandbox-Ausführung:
  - Syntax-Check (Python: `ast.parse`, JS: `esprima`)
  - Blocklist: `os.system`, `subprocess`, `eval`, `exec`, `__import__`, `open("/etc`
  - Offensichtlich defekter Code → direkt ablehnen, kein Sandbox-Start
- **Sandbox-Härtung:**
  - `--network=none` (kein Internet aus der Sandbox)
  - Memory-Limit: `--memory=256m`
  - CPU-Limit: `--cpus=0.5`
  - Timeout: 30s hard kill
  - Read-only Filesystem außer `/tmp`
  - Kein Volume-Mount zum Host
- **Sancho-Panza-Veto (zweiter LLM-Call):**
  - Guard (Mistral Small) prüft generierten Code auf Sicherheit
  - Entscheidung: ALLOW / DENY / MODIFY
  - Bei DENY → Huginn sagt dem User warum
- **Tool-Output-Sanitizing (G1):**
  - Sandbox-Output wird als UNTRUSTED behandelt
  - Geht nochmal durch den Output-Guard bevor er an Telegram geht
  - Verhindert Tool-Chain-Injection: Code→Output→"ignore previous instructions"
- **Orchestrator entscheidet, nicht LLM (G7):**
  - LLM sagt "ich brauche eine Sandbox"
  - Orchestrator prüft: Hat der User das Recht? Ist der Intent CODE? HitL bestätigt?
  - Erst dann wird die Sandbox gestartet

**Rosa-Skelett:**
- gVisor statt Docker-Containerd (stärkere Isolation, K2)
- Sandbox-Logs in Audit-Trail
- Code-Review durch zweiten Approver bei CRITICAL-Severity
- Config-Key `sandbox.runtime: "docker"` (Rosa: `"gvisor"`)

### Patch 168 — Guard-Stresstests
**Review-Findings:** K6, N1, N5

- **Input+Output Guard-Tests:**
  - Jailbreak-Versuche gegen Input-Sanitizer (Patch 161)
  - Prompt-Injection via verschiedene Telegram-Features (Forwarded, Reply, Edit)
  - Guard-Bypass via Persona-Exploitation
  - Multi-Message Injection-Ketten (K3)
- **WARNUNG→BLOCK Eskalation testen (N1):**
  - Szenarien wo WARNUNG zu BLOCK werden sollte
  - Ergebnisse → Config-Thresholds anpassen
- **Guard-als-Policy-Engine-Grenzen (N5):**
  - Wo versagt der LLM-basierte Guard zuverlässig?
  - Deterministische Regeln identifizieren die VOR dem Guard greifen müssen

---

## Phase E — Rosa-Skelett (Transport-Agnostik)

### Patch 169+ — Message-Bus-Abstraktion

**Kein Telegram-spezifischer Code in der Pipeline.** Die gesamte Verarbeitung läuft über ein
abstraktes Message-Interface:

```python
class IncomingMessage:
    text: str
    user_id: str
    channel: str          # "telegram" | "nala" | "rosa_internal"
    attachments: list     # Bilder, Dateien
    metadata: dict        # Thread-ID, Reply-To, etc.
    trust_level: str      # "public" | "internal" | "admin"

class OutgoingMessage:
    text: str | None
    file: bytes | None    # PDF, Code-Datei etc.
    file_name: str | None
    reply_to: str | None
    keyboard: list | None # Inline-Buttons
```

Telegram-Adapter übersetzt `Update` → `IncomingMessage` → Pipeline → `OutgoingMessage` → `sendMessage`/`sendDocument`.

Rosa-Interner-Messenger übersetzt WebSocket-Frame → `IncomingMessage` → selbe Pipeline → WebSocket-Frame zurück.

**Homeoffice-Use-Case:**
- Alles lokal: FastAPI + lokales LLM (Ollama) + kein OpenRouter
- Eigener Messenger (Web-UI oder Electron-App) über LAN
- Kein Traffic verlässt das Firmennetz
- Selbe Pipeline, selbe Policy, selber Guard — nur transport und LLM-Provider sind anders

**Rosa-Skelett-Dateien (jetzt schon anlegen, leer):**
```
zerberus/core/message_bus.py          # IncomingMessage, OutgoingMessage, MessageHandler Interface
zerberus/core/policy_engine.py        # PolicyEngine Interface + HuginnPolicy (pragmatisch)
zerberus/core/input_sanitizer.py      # InputSanitizer Interface + RegexSanitizer
zerberus/adapters/telegram_adapter.py # Telegram → MessageBus (Refactor aus telegram/router.py)
zerberus/adapters/nala_adapter.py     # Placeholder
zerberus/adapters/rosa_adapter.py     # Placeholder
```

---

## Offene Architektur-Entscheidungen (aus Review)

| # | Frage | Empfehlung | Wann |
|---|-------|------------|------|
| 1 | Huginn eigener Prozess? (P3) | Nein — Long-Polling ist async, kein Thread-Block. Erst bei Last-Problemen | Wenn nötig |
| 2 | HitL-State: RAM oder SQLite? | SQLite (Patch 165) | Phase C |
| 3 | Guard seriell oder parallel? (O7) | Seriell, aber Input-Guard = regelbasiert (kein Cloud-Call) → nur 1 Cloud-Roundtrip | Phase A |
| 4 | Graceful Degradation? (O10) | Config-Flag `guard_fail_policy` (Patch 162) | Phase A |
| 5 | Trust-Boundary-Diagramm? (G4) | Ja, als Teil von Rosa-Doku | Phase E |
| 6 | Datenschutz-Info? (D6) | /datenschutz Command für Huginn, zeigt welche Dienste genutzt werden | Phase B |
| 7 | Admin-Rollen? (O11) | Jetzt: admin_chat_id = single admin. Rosa: Rollen-System | Phase E |
| 8 | VRAM-Scheduling? (P1) | Whisper hat Prio, FAISS-Reindex nur nachts (04:30 Cron) | Bereits gelöst |
| 9 | System-Prompt Audit? (O6) | Logging ab Patch 164, Versionierung ab Rosa | Phase B/E |

---

## Zusammenfassung

| Phase | Patches (Original) | Patches (effektiv nach 161-Renumber) | Fokus | Timeline |
|-------|---------|---------|-------|----------|
| A | 160-162 | 160 ✅ / 162 / 163 | Sicherheits-Fundament | Sofort |
| B | 163-164 | 164 / 165 | Intent-Router + Policy | Danach |
| C | 165-166 | 166 / 167 | HitL + Datei-Output | Danach |
| D | 167-168 | 168 / 169 | Sandbox + Stresstests | Danach |
| E | 169+ | 170+ | Rosa-Skelett + Transport-Agnostik | Langzeit |

**Jeder Patch ist ein einzelner Block.** Keine Mega-Patches. Lessons learned.
