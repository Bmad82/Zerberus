# Trust-Boundary-Diagramm — Zerberus Pro 4.0

**Patch 175 (Phase-E-Abschluss).** Übersicht über alle eingehenden Transport-Kanäle, ihre Trust-Stufen und die Schichten die zwischen Eingang und LLM-Call/Sandbox sitzen.

Das Diagramm dokumentiert die Architektur die in Phase E (P173/P174/P175) skelettiert wurde. Die Implementierung der Schichten existiert (P162 Sanitizer, P163 Rate-Limit, P164 HitL-Policy, P171 Sandbox, P175 Policy-Engine-Fassade), die Adapter-Verkabelung wird mit Phase F vollendet.

---

## Diagramm

```
┌──────────────────────────────────────────────────────────────┐
│                    ZERBERUS SERVER                           │
│                                                              │
│  ┌──────────┐   ┌────────────┐   ┌──────────────────────┐    │
│  │ Telegram │   │   Nala     │   │  Rosa (Placeholder)  │    │
│  │ Adapter  │   │  Adapter   │   │     Adapter          │    │
│  │ PUBLIC/  │   │ AUTHENTI-  │   │     (intern)         │    │
│  │ ADMIN    │   │ CATED/     │   │                      │    │
│  │  (P174)  │   │  ADMIN     │   │     (P175 Stub)      │    │
│  │          │   │  (P175)    │   │                      │    │
│  └────┬─────┘   └─────┬──────┘   └──────────┬───────────┘    │
│       │               │                     │                │
│       └───────────────┼─────────────────────┘                │
│                       │                                      │
│              ┌────────▼────────┐                             │
│              │  Policy Engine  │ ◄── Deterministische Regeln │
│              │  (HuginnPolicy) │     VOR dem LLM-Guard       │
│              │  • Rate-Limit   │     Reihenfolge:            │
│              │  • Sanitizer    │      1. Rate-Limit (1 µs)   │
│              │  • HitL-Check   │      2. Sanitizer (ms)      │
│              │     (P175)      │      3. HitL (nur wenn      │
│              │                 │         Intent geparst)     │
│              └────────┬────────┘                             │
│                       │                                      │
│              ┌────────▼────────┐                             │
│              │    Pipeline     │     Linearer Text-Pfad      │
│              │ (Sanitize→LLM→  │     transport-agnostisch    │
│              │  Guard→Output)  │     (P174)                  │
│              └────────┬────────┘                             │
│                       │                                      │
│              ┌────────▼────────┐                             │
│              │     Guard       │ ◄── LLM-basiert             │
│              │  (Mistral via   │     (mistral-small-24b      │
│              │   OpenRouter)   │     -instruct-2501)         │
│              │   semantisch    │     fail-open by default    │
│              │     (P120)      │     (P163 K4)               │
│              └────────┬────────┘                             │
│                       │                                      │
│              ┌────────▼────────┐                             │
│              │    Sandbox      │ ◄── Docker-isoliert         │
│              │  (Optional)     │     --network none          │
│              │   --read-only   │     --memory 256m           │
│              │   no-new-priv   │     --pids-limit 64         │
│              │     (P171)      │     CODE-Intent only        │
│              └─────────────────┘                             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Trust-Stufen

| Stufe          | Wer                                                  | Wo gesetzt                                 |
|----------------|------------------------------------------------------|--------------------------------------------|
| `PUBLIC`       | Telegram-Gruppen, unbekannte User, kein gültiger JWT | TelegramAdapter (Gruppe), NalaAdapter (kein JWT) |
| `AUTHENTICATED`| Telegram-DM, Nala-User mit gültigem JWT              | TelegramAdapter (private), NalaAdapter (JWT vorhanden) |
| `ADMIN`        | `admin_chat_id` (Telegram) / Admin-JWT (Nala)        | TelegramAdapter (private+admin_id match), NalaAdapter (`permission_level=admin`) |

**Konservatives Mapping in Gruppen:** Auch wenn der `admin_chat_id`-User in einer Gruppe schreibt, bleibt der `trust_level=PUBLIC`. Begründung in [lessons.md](../lessons.md): die Gruppe ist ein öffentlicher Kontext, der Admin-Status der Person ändert daran nichts; Admin-Aktionen gehen per DM.

---

## Severity-Mapping (HuginnPolicy)

`PolicyDecision.severity` wird vom Trust-Level moduliert:

- `PUBLIC` hebt eine Stufe an (max bis `high` — `critical` ist Rosa-Audit-Trail-Trigger und hier noch ungenutzt).
- `AUTHENTICATED` bleibt auf der Basis-Stufe.
- `ADMIN` senkt eine Stufe (mind. `low`).

**Beispiel:** `sanitizer_blocked` (Basis `high`) → bei PUBLIC bleibt `high`, bei ADMIN wird `medium`. Ein Admin der einen Pattern-Treffer kassiert ist seltener akut bedrohlich (häufiger ein Test/Debug-Versuch); ein PUBLIC-User der Patterns triggert bekommt mehr Logging-Aufmerksamkeit.

---

## Daten-Flüsse

| Was            | Wohin                                       | Warum                                |
|----------------|---------------------------------------------|--------------------------------------|
| **EXTERNAL**   | OpenRouter (LLM-Calls), Whisper (Docker-lokal) | LLM-Inferenz + ASR                |
| **NEVER LEAVES** | `bunker_memory.db`, `config.yaml`, FAISS-Index, `system_prompt_*.json` | Lokale Persistenz + Konfig — Tailscale-only Zugriff |
| **INTRA-SERVER** | EventBus (`zerberus/core/event_bus.py`)   | Nala-SSE-Pipeline + Hel-Dashboard-Updates |

---

## Patch-Mapping

| Schicht                       | Patch | Datei                                            |
|-------------------------------|-------|--------------------------------------------------|
| TransportAdapter Interface    | P173  | `core/transport.py`                              |
| Message-Bus-Datenmodelle      | P173  | `core/message_bus.py`                            |
| TelegramAdapter               | P174  | `adapters/telegram_adapter.py`                   |
| NalaAdapter                   | P175  | `adapters/nala_adapter.py`                       |
| RosaAdapter (Stub)            | P175  | `adapters/rosa_adapter.py`                       |
| Policy-Engine Interface + HuginnPolicy | P175 | `core/policy_engine.py`                  |
| Pipeline (linearer Text-Pfad) | P174  | `core/pipeline.py`                               |
| Input-Sanitizer (Regex+NFKC)  | P162/P173 | `core/input_sanitizer.py`                    |
| Rate-Limiter                  | P163  | `core/rate_limiter.py`                           |
| HitL-Policy (Intent-basiert)  | P164  | `core/hitl_policy.py`                            |
| HitL-Manager (DB+Sweep)       | P167  | `modules/telegram/hitl.py`                       |
| LLM-Guard (Mistral)           | P120/P163 | `hallucination_guard.py`                     |
| Docker-Sandbox                | P171  | `modules/sandbox/manager.py`                     |

---

## Phase-E-Status

Mit P175 sind alle Skelett-Dateien und das Architektur-Diagramm da. Die nächsten Schritte (Phase F):

1. **Cutover** `process_update` → `handle_telegram_update` (im Telegram-Pfad).
2. **NalaAdapter-Integration** in `legacy.py` / `nala.py` — schrittweise, beginnend mit Guard + Intent.
3. **Audit-Trail** (im Diagramm erwähnt, noch nicht implementiert): jede `PolicyDecision` mit `severity ∈ {high, critical}` schreibt in eine `audit.log`-Tabelle.
4. **Rosa-Messenger** + RosaPolicy (Multi-Layer, strenger als HuginnPolicy).

Siehe [PROJEKTDOKUMENTATION.md — Patch 175](PROJEKTDOKUMENTATION.md) für den vollständigen Phase-E-Abschluss-Vermerk.
