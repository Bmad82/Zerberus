"""
RAG-Eval – Patch 70
Liest 11 Fragen aus rag_eval_questions.txt, sendet jede an POST /rag/search
und schreibt die Ergebnisse in rag_eval_results.txt.

Konfiguration (oben anpassen falls nötig):
  BASE_URL   – Server-Adresse inkl. Port
  API_KEY    – X-API-Key (aus config.yaml → auth.static_api_key)
  TOP_K      – Anzahl zurückgelieferter Chunks pro Frage
  QUESTIONS  – Pfad zur Fragen-Datei
  RESULTS    – Pfad zur Ergebnis-Datei
"""

import os
import ssl
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

# ── Konfiguration ────────────────────────────────────────────────────────────

# Override per Env-Var moeglich: RAG_EVAL_URL=https://andere-host:port
BASE_URL   = os.environ.get("RAG_EVAL_URL", "https://127.0.0.1:5000")
API_KEY    = os.environ.get(
    "RAG_EVAL_API_KEY",
    "ca55f7c73d68e45abfdae92dbba67c209cdc0eac5d483275118efd34d2e2b868",
)
TOP_K      = 8  # Patch 101 (R-07): von 5 auf 8 für Multi-Chunk-Aggregation
QUESTIONS  = Path("rag_eval_questions.txt")
RESULTS    = Path("rag_eval_results.txt")

# SSL-Context: akzeptiert Self-Signed Certs (Server seit Patch 82 auf HTTPS).
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ─────────────────────────────────────────────────────────────────────────────


def _load_questions(path: Path) -> list[str]:
    """Liest Fragen aus Datei – ignoriert leere Zeilen und Nummerierungen."""
    questions = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Nummerierung am Anfang entfernen: "1. Frage" → "Frage"
            if line[0].isdigit() and ". " in line[:4]:
                line = line.split(". ", 1)[1]
            questions.append(line)
    return questions


def _query_rag(question: str) -> str:
    """Sendet eine Frage an /rag/search und gibt den besten Chunk-Text zurück."""
    endpoint = f"{BASE_URL}/rag/search"
    payload = json.dumps({"query": question, "top_k": TOP_K}).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    # Self-signed Cert: Context nur fuer https-URLs uebergeben (urllib ignoriert ihn sonst).
    ctx = _SSL_CTX if endpoint.lower().startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return f"[HTTP {e.code}] {error_body[:300]}"
    except Exception as e:
        return f"[FEHLER] {e}"

    results = body.get("results", [])
    if not results:
        return "[Keine Treffer im Index]"

    # Alle Chunks zusammenführen, abgetrennt durch Trennlinie
    parts = []
    for i, r in enumerate(results, 1):
        score = r.get("score", 0)
        text  = r.get("text", "").strip()
        source = r.get("source", r.get("filename", "?"))
        parts.append(f"[Chunk {i} | Score {score:.3f} | {source}]\n{text}")

    return "\n\n".join(parts)


def main() -> None:
    if not QUESTIONS.exists():
        print(f"Fehler: {QUESTIONS} nicht gefunden.", file=sys.stderr)
        sys.exit(1)

    questions = _load_questions(QUESTIONS)
    print(f"RAG-Eval startet - {len(questions)} Fragen -> {RESULTS}")

    lines = []
    for i, q in enumerate(questions, 1):
        print(f"  [{i:02d}/{len(questions)}] {q[:70]}...")
        answer = _query_rag(q)
        lines.append(f"FRAGE: {q}\nANTWORT:\n{answer}\n---")

    RESULTS.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nFertig. Ergebnisse in: {RESULTS}")


if __name__ == "__main__":
    main()
