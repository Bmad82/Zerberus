"""Patch 199 (Phase 5a #3) — Projekt-spezifischer RAG-Index.

Jedes Projekt bekommt einen eigenen, isolierten Index unter
``<projects.data_dir>/projects/<slug>/_rag/{vectors.npy, meta.json}``. Der
globale RAG-Index in ``modules/rag/router.py`` bleibt unberuehrt — die
beiden Stores haben unterschiedliche Lebenszyklen (global = Memory aller
Sessions, projekt = nur Files dieses Projekts).

Designentscheidungen:

* **Pure-Numpy-Linearscan statt FAISS**, weil Per-Projekt-Indizes klein
  sind (typisch 10-2000 Chunks). Ein ``argpartition`` auf einem
  ``(N, 384)``-Array ist auf der Groessenordnung schneller als FAISS-Setup-
  Overhead und macht die Tests dependency-frei (kein faiss-Mock noetig).
  Persistierung als ``vectors.npy`` (float32) + ``meta.json``.

* **MiniLM-L6-v2 (384 dim)** als Default-Embedder — kompatibel mit dem
  Legacy-Globalpfad und ohne sprach-spezifisches Setup. Lazy-Loaded; Tests
  monkeypatchen ``_embed_text`` mit einer deterministischen Hash-Funktion.

* **Chunker-Reuse**: Code-Files (.py/.js/.ts/.html/.css/.json/.yaml/.sql)
  via ``modules.rag.code_chunker.chunk_code``. Prosa (.md/.txt/Default)
  via lokalem Para-Splitter (max 1500 Zeichen, snap an Doppel-Newline).

* **Idempotenz**: Pro ``file_id`` hoechstens ein Chunk-Set im Index. Beim
  Re-Index wird der alte Block zuerst entfernt (gleicher ``sha256``: skip;
  anderer ``sha256``: ersetzen).

* **Best-Effort-Verdrahtung**: Indexing-Fehler brechen den Upload-/
  Materialize-Pfad NICHT ab. Im Chat ein Query-Fehler → Fallback auf
  "kein RAG-Block".

* **Feature-Flag** ``ProjectsConfig.rag_enabled`` (Default ``True``). Tests
  + lokale Setups ohne ``sentence-transformers`` koennen den Pfad
  abschalten.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


RAG_SUBDIR = "_rag"
VECTORS_FILENAME = "vectors.npy"
META_FILENAME = "meta.json"

PROJECT_RAG_BLOCK_MARKER = "[PROJEKT-RAG — Kontext aus Projektdateien]"

DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"
DEFAULT_EMBED_DIM = 384

# Prosa-Chunker: bevorzuge harte Absatz-Grenzen (Doppel-Newline). Wenn ein
# Absatz selbst zu gross ist, wird er an Saetze gesplittet. Limits sind
# hand-getuned: 1500 Zeichen sind typisch ~250-350 Tokens, was dem
# MiniLM-Kontext (max 256-512 Tokens) gut entspricht.
_PROSE_MAX_CHARS = 1500
_PROSE_MIN_CHARS = 64
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])")


# ---------------------------------------------------------------------------
# Pure-Function: Chunking
# ---------------------------------------------------------------------------


def _split_prose(text: str, *, max_chars: int = _PROSE_MAX_CHARS) -> list[str]:
    """Splittet Prosa in Chunks mit weichen Absatz-Grenzen.

    Strategie:
      1. Splitte an Doppel-Newline (Absatz-Grenze).
      2. Fuehre kleine Stuecke zusammen, bis ``max_chars`` erreicht.
      3. Wenn ein einzelner Absatz selbst zu gross ist: splitte an
         Satz-Grenzen, fall-back auf hartes Char-Slice wenn auch das
         nicht reicht.
    """
    if not text or not text.strip():
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buf: str = ""
    for para in paragraphs:
        if len(para) > max_chars:
            # Buffer zuerst flushen
            if buf:
                chunks.append(buf)
                buf = ""
            # Sentence-Split fuer den grossen Absatz
            for piece in _split_long_paragraph(para, max_chars=max_chars):
                chunks.append(piece)
            continue
        if buf and len(buf) + 2 + len(para) > max_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = (buf + "\n\n" + para) if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def _split_long_paragraph(para: str, *, max_chars: int) -> list[str]:
    sentences = _SENTENCE_SPLIT_RE.split(para)
    if len(sentences) <= 1:
        # Keine Satz-Grenzen erkennbar → hartes Char-Slice
        return [para[i : i + max_chars] for i in range(0, len(para), max_chars)]
    out: list[str] = []
    buf = ""
    for s in sentences:
        if len(s) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(s[i : i + max_chars] for i in range(0, len(s), max_chars))
            continue
        if buf and len(buf) + 1 + len(s) > max_chars:
            out.append(buf)
            buf = s
        else:
            buf = (buf + " " + s) if buf else s
    if buf:
        out.append(buf)
    return out


def chunk_file_content(text: str, relative_path: str) -> list[dict[str, Any]]:
    """Liefert eine Chunk-Liste fuer eine Datei.

    Fuer Code-Endungen ruft die Funktion ``code_chunker.chunk_code`` auf
    und liefert dessen Output 1:1 weiter (mit ``content`` + ``metadata``).
    Fuer alles andere (Prosa, .md, .txt, unbekannte Extension) wird der
    lokale Para-Splitter verwendet — die Eintraege haben dieselbe Struktur
    (``content``-Feld + ``metadata``-Dict mit ``chunk_type="prose"``).

    Pure Function: kein I/O, kein DB-Zugriff. Macht Unit-Tests trivial.
    """
    if not text or not text.strip():
        return []

    from zerberus.modules.rag.code_chunker import chunk_code, is_code_file

    if is_code_file(relative_path):
        chunks = chunk_code(text, relative_path)
        if chunks:
            return chunks
        # Fall-Through: Code-Chunker hatte keine Treffer (z.B. SyntaxError-
        # Fallback) → nutze Prose-Splitter, damit der File trotzdem
        # indexiert wird.

    pieces = _split_prose(text)
    out: list[dict[str, Any]] = []
    for i, piece in enumerate(pieces):
        if len(piece) < _PROSE_MIN_CHARS and i > 0:
            # Kleine Tail-Chunks an den vorherigen anhaengen
            out[-1]["content"] = out[-1]["content"] + "\n\n" + piece
            continue
        out.append({
            "content": piece,
            "metadata": {
                "file_path": relative_path,
                "chunk_type": "prose",
                "name": f"chunk_{i}",
                "start_line": None,
                "end_line": None,
                "language": "text",
            },
        })
    return out


# ---------------------------------------------------------------------------
# Pure-Function: Top-K
# ---------------------------------------------------------------------------


def top_k_indices(
    query_vec: list[float] | np.ndarray,
    vectors: Optional[np.ndarray],
    k: int,
) -> list[tuple[int, float]]:
    """Top-K Cosinus-Aehnlichkeit (oder Dot-Product, wenn beide Seiten
    bereits L2-normiert sind — was MiniLM via ``normalize_embeddings``
    liefert).

    Liefert eine Liste ``[(index, score), ...]`` sortiert nach absteigendem
    Score. Leerer Index → leere Liste. ``k`` wird auf ``len(vectors)``
    gedeckelt.
    """
    if vectors is None or len(vectors) == 0 or k <= 0:
        return []
    q = np.asarray(query_vec, dtype="float32")
    if q.ndim != 1:
        q = q.reshape(-1)
    if vectors.shape[1] != q.shape[0]:
        # Dim-Mismatch → leerer Output. Kann passieren, wenn der Embedder
        # zwischen zwei Sessions getauscht wurde und der Index noch alte
        # Vektoren enthaelt. Aufrufer sollte den Index neu bauen.
        logger.warning(
            f"[RAG-199] Dim-Mismatch: vectors={vectors.shape}, query={q.shape} → leeres Ergebnis"
        )
        return []
    sims = vectors @ q
    k = min(k, len(sims))
    if k == len(sims):
        order = np.argsort(-sims)
    else:
        part = np.argpartition(-sims, k - 1)[:k]
        order = part[np.argsort(-sims[part])]
    return [(int(i), float(sims[i])) for i in order]


# ---------------------------------------------------------------------------
# Pfade + I/O
# ---------------------------------------------------------------------------


def index_dir_for(slug: str, base_dir: Path) -> Path:
    return Path(base_dir) / "projects" / slug / RAG_SUBDIR


def index_paths_for(slug: str, base_dir: Path) -> tuple[Path, Path]:
    """Pfade zu ``vectors.npy`` und ``meta.json`` fuer ein Projekt-Slug."""
    d = index_dir_for(slug, base_dir)
    return d / VECTORS_FILENAME, d / META_FILENAME


def load_index(slug: str, base_dir: Path) -> tuple[Optional[np.ndarray], list[dict[str, Any]]]:
    """Laedt den Index oder liefert ``(None, [])`` wenn er nicht existiert.

    Wenn nur eine der beiden Dateien existiert (kaputter Zustand z.B. nach
    Crash mitten im ``save_index``), wird ``(None, [])`` zurueckgegeben und
    eine WARN-Zeile geloggt — der Aufrufer baut dann auf einer leeren
    Basis neu auf.
    """
    vec_path, meta_path = index_paths_for(slug, base_dir)
    if not vec_path.exists() and not meta_path.exists():
        return None, []
    if not (vec_path.exists() and meta_path.exists()):
        logger.warning(f"[RAG-199] Inkonsistenter Index slug={slug}: nur eine Datei vorhanden")
        return None, []
    try:
        vectors = np.load(str(vec_path))
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if not isinstance(meta, list):
            raise ValueError(f"meta.json ist keine Liste: {type(meta).__name__}")
        if vectors.shape[0] != len(meta):
            logger.warning(
                f"[RAG-199] Index slug={slug}: vectors {vectors.shape[0]} != meta {len(meta)} → reset"
            )
            return None, []
        return vectors, meta
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning(f"[RAG-199] Lade-Fehler slug={slug}: {e} — leerer Index")
        return None, []


def save_index(slug: str, base_dir: Path, vectors: np.ndarray, meta: list[dict[str, Any]]) -> None:
    """Persistiert Vektoren + Meta. Atomar via tempfile + os.replace."""
    import os
    import tempfile

    d = index_dir_for(slug, base_dir)
    d.mkdir(parents=True, exist_ok=True)
    vec_path, meta_path = index_paths_for(slug, base_dir)

    fd, tmp_vec = tempfile.mkstemp(dir=str(d), prefix=".vec_", suffix=".tmp.npy")
    os.close(fd)
    np.save(tmp_vec, vectors.astype("float32"))
    # numpy haengt automatisch ".npy" an, wenn der Pfad nicht so endet — wir
    # haben ".tmp.npy" als Suffix, also bleibt der Name unveraendert.
    os.replace(tmp_vec, str(vec_path))

    fd, tmp_meta = tempfile.mkstemp(dir=str(d), prefix=".meta_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
        os.replace(tmp_meta, str(meta_path))
    except Exception:
        try:
            os.unlink(tmp_meta)
        except OSError:
            pass
        raise


def remove_project_index(slug: str, base_dir: Path) -> bool:
    """Loescht den ganzen ``_rag``-Ordner eines Projekts. Best-Effort —
    Fehler werden geschluckt + geloggt.
    """
    d = index_dir_for(slug, base_dir)
    if not d.exists():
        return False
    try:
        shutil.rmtree(d)
        logger.info(f"[RAG-199] Index entfernt slug={slug}")
        return True
    except OSError as e:
        logger.warning(f"[RAG-199] remove_project_index slug={slug}: {e}")
        return False


# ---------------------------------------------------------------------------
# Embedder-Wrapper
# ---------------------------------------------------------------------------


_embedder_singleton: Any = None


def _get_embedder() -> Any:
    """Lazy-Loaded SentenceTransformer-Instanz. Tests monkeypatchen
    ``_embed_text`` und kommen hier nie an."""
    global _embedder_singleton
    if _embedder_singleton is None:
        from sentence_transformers import SentenceTransformer

        from zerberus.modules.rag.device import get_rag_device

        device = get_rag_device(None)
        logger.info(f"[RAG-199] Lade Projekt-Embedder {DEFAULT_EMBED_MODEL} (device={device})")
        _embedder_singleton = SentenceTransformer(DEFAULT_EMBED_MODEL, device=device)
    return _embedder_singleton


def _embed_text(text: str) -> list[float]:
    """Embeddet einen einzelnen Text. Liefert eine ``list[float]`` der
    Laenge ``DEFAULT_EMBED_DIM`` (oder die Dim des aktiven Modells).

    Tests monkeypatchen diese Funktion direkt — der echte Embedder wird
    nie geladen.
    """
    model = _get_embedder()
    vec = model.encode([text], normalize_embeddings=True)
    arr = np.asarray(vec[0])
    return arr.astype("float32").tolist()


# ---------------------------------------------------------------------------
# Async-API: Indexieren + Loeschen + Query
# ---------------------------------------------------------------------------


def _max_index_bytes_default() -> int:
    """Liefert die max. Datei-Groesse beim Indexen — Default 5 MB. Wird
    via ``get_settings().projects.rag_max_file_bytes`` ueberschrieben (wenn
    das Feld vorhanden ist, sonst Default).
    """
    try:
        from zerberus.core.config import get_settings

        s = get_settings()
        return int(getattr(s.projects, "rag_max_file_bytes", 5 * 1024 * 1024))
    except Exception:
        return 5 * 1024 * 1024


def _is_rag_enabled() -> bool:
    try:
        from zerberus.core.config import get_settings

        s = get_settings()
        return bool(getattr(s.projects, "rag_enabled", True))
    except Exception:
        return True


async def index_project_file(
    project_id: int,
    file_id: int,
    base_dir: Path,
) -> dict[str, Any]:
    """Indexiert eine einzelne Datei: liest die Bytes, chunked, embeddet,
    persistiert.

    Idempotent: vorhandene Eintraege fuer dieselbe ``file_id`` werden vor
    dem Schreiben entfernt. Ein Re-Index nach Inhalts-Aenderung (anderer
    ``sha256``) ersetzt damit den alten Block.

    Liefert ein Status-Dict::

        {"chunks": int, "skipped": bool, "reason": str}

    ``reason`` ist ein kurzer Code (``indexed``, ``empty``, ``binary``,
    ``too_large``, ``no_chunks``, ``file_not_found``, ``project_not_found``,
    ``bytes_missing``, ``rag_disabled``). Wird vom Aufrufer fuer Logging
    + manuelle Tests genutzt.
    """
    if not _is_rag_enabled():
        return {"chunks": 0, "skipped": True, "reason": "rag_disabled"}

    from zerberus.core import projects_repo

    file_meta = await projects_repo.get_file(file_id)
    if not file_meta or file_meta.get("project_id") != project_id:
        return {"chunks": 0, "skipped": True, "reason": "file_not_found"}

    project = await projects_repo.get_project(project_id)
    if not project:
        return {"chunks": 0, "skipped": True, "reason": "project_not_found"}

    storage_path = Path(file_meta["storage_path"])
    if not storage_path.exists():
        return {"chunks": 0, "skipped": True, "reason": "bytes_missing"}

    if int(file_meta.get("size_bytes") or 0) > _max_index_bytes_default():
        logger.info(
            f"[RAG-199] skip slug={project['slug']} path={file_meta['relative_path']} (too_large)"
        )
        return {"chunks": 0, "skipped": True, "reason": "too_large"}

    raw = storage_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"chunks": 0, "skipped": True, "reason": "binary"}

    if not text.strip():
        return {"chunks": 0, "skipped": True, "reason": "empty"}

    chunks = chunk_file_content(text, file_meta["relative_path"])
    if not chunks:
        return {"chunks": 0, "skipped": True, "reason": "no_chunks"}

    slug = project["slug"]
    vectors, meta = load_index(slug, base_dir)

    # Idempotenz: alte Eintraege fuer diese file_id rauswerfen.
    if meta:
        keep_idx = [i for i, m in enumerate(meta) if m.get("file_id") != file_id]
        if len(keep_idx) != len(meta):
            if vectors is not None and keep_idx:
                vectors = vectors[keep_idx]
            elif not keep_idx:
                vectors = None
            meta = [meta[i] for i in keep_idx]

    new_vectors: list[list[float]] = []
    new_meta: list[dict[str, Any]] = []
    indexed_at = datetime.utcnow().isoformat()
    for ci, chunk in enumerate(chunks):
        chunk_text = chunk.get("content") or ""
        if not chunk_text.strip():
            continue
        try:
            vec = _embed_text(chunk_text)
        except Exception as e:
            logger.warning(
                f"[RAG-199] Embed-Fehler slug={slug} path={file_meta['relative_path']} chunk={ci}: {e}"
            )
            return {"chunks": 0, "skipped": True, "reason": "embed_failed"}
        new_vectors.append(vec)
        cmeta = dict(chunk.get("metadata") or {})
        cmeta.update({
            "file_id": file_id,
            "relative_path": file_meta["relative_path"],
            "sha256": file_meta["sha256"],
            "chunk_id": ci,
            "text": chunk_text,
            "indexed_at": indexed_at,
        })
        new_meta.append(cmeta)

    if not new_vectors:
        return {"chunks": 0, "skipped": True, "reason": "no_chunks"}

    new_arr = np.asarray(new_vectors, dtype="float32")
    if vectors is None:
        combined = new_arr
    else:
        if vectors.shape[1] != new_arr.shape[1]:
            # Embedder-Wechsel zur Laufzeit — alten Index ersetzen statt
            # vstack zu erzwingen, sonst kracht der naechste Query.
            logger.warning(
                f"[RAG-199] Embedder-Dim-Wechsel slug={slug} ({vectors.shape[1]} → {new_arr.shape[1]}) — Index neu aufgebaut"
            )
            combined = new_arr
            meta = []
        else:
            combined = np.vstack([vectors, new_arr])

    save_index(slug, base_dir, combined, meta + new_meta)
    logger.info(
        f"[RAG-199] indexed slug={slug} file_id={file_id} path={file_meta['relative_path']} "
        f"chunks={len(new_vectors)} total={combined.shape[0]}"
    )
    return {"chunks": len(new_vectors), "skipped": False, "reason": "indexed"}


async def remove_file_from_index(
    project_id: int,
    file_id: int,
    base_dir: Path,
) -> int:
    """Entfernt alle Chunks einer Datei aus dem Projekt-Index.

    Liefert die Anzahl entfernter Chunks. Wird vom Delete-Endpoint
    aufgerufen — das Projekt ueberlebt den Aufruf, der File-Eintrag wird
    ggf. erst danach geloescht (oder bereits davor — egal, wir lesen
    nicht aus der DB).
    """
    from zerberus.core import projects_repo

    project = await projects_repo.get_project(project_id)
    if not project:
        return 0
    slug = project["slug"]
    vectors, meta = load_index(slug, base_dir)
    if vectors is None or not meta:
        return 0
    keep_idx = [i for i, m in enumerate(meta) if m.get("file_id") != file_id]
    removed = len(meta) - len(keep_idx)
    if removed == 0:
        return 0
    if not keep_idx:
        # Ganzer Index leer → Files weg.
        remove_project_index(slug, base_dir)
        logger.info(f"[RAG-199] removed slug={slug} file_id={file_id} (index leer)")
        return removed
    new_vectors = vectors[keep_idx]
    new_meta = [meta[i] for i in keep_idx]
    save_index(slug, base_dir, new_vectors, new_meta)
    logger.info(
        f"[RAG-199] removed slug={slug} file_id={file_id} chunks_removed={removed} remaining={len(new_meta)}"
    )
    return removed


async def query_project_rag(
    project_id: int,
    query: str,
    base_dir: Path,
    *,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Liefert die Top-``k`` relevantesten Chunks fuer eine Query.

    Defensives Verhalten:
    - Projekt nicht gefunden / Feature-Flag aus / Index leer → ``[]``
    - Query leer / nur Whitespace → ``[]``
    - Embed-Fehler → ``[]`` (mit WARN-Log)

    Jeder Hit-Eintrag enthaelt: ``score`` (cos-sim) + alle Metadaten +
    ``text`` (der Chunk-Inhalt).
    """
    if not _is_rag_enabled():
        return []
    if not query or not query.strip():
        return []
    if k <= 0:
        return []

    from zerberus.core import projects_repo

    project = await projects_repo.get_project(project_id)
    if not project:
        return []
    slug = project["slug"]
    vectors, meta = load_index(slug, base_dir)
    if vectors is None or not meta:
        return []
    try:
        qvec = _embed_text(query)
    except Exception as e:
        logger.warning(f"[RAG-199] Query-Embed-Fehler slug={slug}: {e}")
        return []

    hits = top_k_indices(qvec, vectors, k)
    out: list[dict[str, Any]] = []
    for idx, score in hits:
        if idx >= len(meta):
            continue
        entry = dict(meta[idx])
        entry["score"] = score
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Format-Helper fuer den Chat-Endpoint
# ---------------------------------------------------------------------------


def format_rag_block(hits: list[dict[str, Any]], *, project_slug: Optional[str] = None) -> str:
    """Baut den Kontext-Block, der an den System-Prompt angehaengt wird.

    Leere Hit-Liste → leerer String (Caller haengt nichts an). Format
    bewusst markdown-aehnlich: das LLM versteht die Sektionen und kann
    sie zitieren.
    """
    if not hits:
        return ""
    parts: list[str] = ["", "", "---", PROJECT_RAG_BLOCK_MARKER]
    if project_slug:
        parts.append(f"Projekt: {project_slug}")
    parts.append(
        "Die folgenden Auszuege stammen aus den im Projekt hinterlegten Dateien. "
        "Nutze sie als verbindlichen Kontext, wenn die Frage des Users dazu passt."
    )
    for hit in hits:
        path = hit.get("relative_path", "?")
        chunk_type = hit.get("chunk_type", "chunk")
        name = hit.get("name") or ""
        score = float(hit.get("score", 0.0) or 0.0)
        header = f"### {path}"
        if name and chunk_type not in ("prose", "chunk"):
            header += f" — {chunk_type}: {name}"
        header += f" (relevance={score:.2f})"
        parts.append("")
        parts.append(header)
        parts.append((hit.get("text") or "").strip())
    return "\n".join(parts)


def _content_hash(text: str) -> str:
    """Hilfs-Hash fuer Tests + Logs — nicht der File-SHA, sondern ein
    kurzer Content-Fingerprint."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
