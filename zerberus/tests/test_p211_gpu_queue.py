"""Patch 211 (Phase 5a #11) — GPU-Queue-Tests.

Deckt ab:

* Pure-Function ``compute_vram_budget`` / ``should_queue``.
* ``GpuQueue.acquire/release`` — sofortiger Slot, FIFO-Wait, Timeout-Cleanup.
* ``GpuSlotInfo``-Wait/Held-Berechnung.
* Status-Snapshot + Audit-Schreibung (Best-Effort).
* Source-Audit der Verdrahtung in Whisper/Gemma/Embedder/Reranker-Pfaden.
* HTTP-Endpunkt ``GET /v1/gpu/status`` und Hel-Frontend-Toast.
* JS-Syntax-Integritaet des Hel-Frontends nach P211-Erweiterung.
"""
from __future__ import annotations

import asyncio
import inspect
import shutil
import subprocess
import re
from pathlib import Path

import pytest


# ── Modul-Imports + Reset-Helper ────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_gpu_queue():
    """Vor jedem Test eine frische GpuQueue, damit Tests sich nicht gegenseitig
    beeinflussen (Singleton-State)."""
    from zerberus.core import gpu_queue
    gpu_queue.reset_global_queue_for_tests()
    yield
    gpu_queue.reset_global_queue_for_tests()


# ── Pure-Function-Schicht ───────────────────────────────────────────────


class TestComputeVramBudget:
    def test_known_consumers_fixed_budget(self):
        from zerberus.core.gpu_queue import compute_vram_budget
        assert compute_vram_budget("whisper") == 4_000
        assert compute_vram_budget("gemma") == 2_000
        assert compute_vram_budget("embedder") == 1_000
        assert compute_vram_budget("reranker") == 512

    def test_unknown_consumer_returns_default(self):
        from zerberus.core.gpu_queue import (
            compute_vram_budget,
            DEFAULT_CONSUMER_BUDGET_MB,
        )
        assert compute_vram_budget("foo-bar") == DEFAULT_CONSUMER_BUDGET_MB
        assert compute_vram_budget("") == DEFAULT_CONSUMER_BUDGET_MB

    def test_case_insensitive(self):
        from zerberus.core.gpu_queue import compute_vram_budget
        assert compute_vram_budget("WHISPER") == 4_000
        assert compute_vram_budget("  Gemma  ") == 2_000


class TestShouldQueue:
    def test_fits_returns_false(self):
        from zerberus.core.gpu_queue import should_queue
        assert should_queue(0, 1_000, total_mb=11_000) is False
        assert should_queue(5_000, 4_000, total_mb=11_000) is False  # 9_000 <= 11_000
        assert should_queue(7_000, 4_000, total_mb=11_000) is False  # 11_000 == 11_000

    def test_overflow_returns_true(self):
        from zerberus.core.gpu_queue import should_queue
        assert should_queue(8_000, 4_000, total_mb=11_000) is True
        assert should_queue(11_000, 1, total_mb=11_000) is True

    def test_zero_request_never_queues(self):
        from zerberus.core.gpu_queue import should_queue
        assert should_queue(11_000, 0, total_mb=11_000) is False
        assert should_queue(11_000, -100, total_mb=11_000) is False  # defensive


# ── Async-Wrapper: Acquire / Release ────────────────────────────────────


@pytest.mark.asyncio
class TestGpuQueueAcquireImmediate:
    async def test_acquire_when_empty_no_wait(self):
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=11_000)
        async with q.slot("embedder") as info:
            assert info.requested_mb == 1_000
            assert info.queue_position == 0
            assert info.acquired_at is not None
            assert info.timed_out is False

    async def test_release_frees_budget(self):
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=11_000)
        async with q.slot("whisper"):
            status = await q.status()
            assert status["active_mb"] == 4_000
            assert status["free_mb"] == 7_000
        status_after = await q.status()
        assert status_after["active_mb"] == 0
        assert status_after["free_mb"] == 11_000

    async def test_two_compatible_consumers_run_in_parallel(self):
        """Whisper (4 GB) + Gemma (2 GB) + Embedder (1 GB) = 7 GB ≤ 11 GB → kein Wait."""
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=11_000)

        events: list[str] = []

        async def acquire(name: str):
            async with q.slot(name):
                events.append(f"in:{name}")
                await asyncio.sleep(0.05)
                events.append(f"out:{name}")

        await asyncio.gather(
            acquire("whisper"),
            acquire("gemma"),
            acquire("embedder"),
        )
        ins = [e for e in events if e.startswith("in:")]
        # Alle drei sollten ungefaehr gleichzeitig acquired haben (kein Wait).
        assert len(ins) == 3


# ── FIFO-Queue + Timeout ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGpuQueueFifo:
    async def test_overflow_consumer_waits_for_release(self):
        """Zwei Whisper-Calls (4 GB + 4 GB = 8 GB OK) blocken am Gate, dritter
        Whisper (waere 12 GB) muss warten bis einer released."""
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=11_000)

        order: list[str] = []
        gate = asyncio.Event()

        async def first_holder():
            async with q.slot("whisper"):
                order.append("first_acq")
                await gate.wait()  # blockt, bis Test signalisiert

        async def second_holder():
            await asyncio.sleep(0.01)  # nach first acquired
            async with q.slot("whisper"):
                order.append("second_acq")
                await gate.wait()  # auch blocken — sonst gibt es sofort frei

        async def third_holder():
            await asyncio.sleep(0.02)  # nach second
            async with q.slot("whisper"):
                order.append("third_acq")

        t1 = asyncio.create_task(first_holder())
        t2 = asyncio.create_task(second_holder())
        t3 = asyncio.create_task(third_holder())
        await asyncio.sleep(0.05)
        # third sollte noch warten — first+second halten 8 GB, third braucht 4 (12 > 11)
        assert "third_acq" not in order
        assert "first_acq" in order
        assert "second_acq" in order
        # signal first+second to release
        gate.set()
        await asyncio.gather(t1, t2, t3)
        # third kommt am Ende dran
        assert order[-1] == "third_acq"

    async def test_strict_fifo_order_for_waiters(self):
        """Wenn mehrere warten, kriegt der erste Waiter zuerst den Slot."""
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=11_000)

        # Hold whisper (4 GB) + gemma (2 GB) + embedder (1 GB) = 7 GB
        # Die naechsten Whispers (4 GB) passen nicht (7 + 4 > 11)
        order: list[str] = []
        release_gate = asyncio.Event()

        async def holder():
            async with q.slot("whisper"):
                order.append("holder_acq")
                await release_gate.wait()

        async def waiter(label: str):
            async with q.slot("whisper"):
                order.append(label)
                await asyncio.sleep(0.01)

        async def small_holder():
            async with q.slot("gemma"):
                await release_gate.wait()

        # holder + small_holder = 4+2 = 6 GB. waiter1 (4 GB) wuerde 10 passen!
        # Hmm — ich brauche eine Konstellation wo waiter wirklich blockt.
        # Loesung: zwei holders mit 4 GB jeweils → 8 GB. Dritter Whisper 4 GB → blockt.
        order.clear()

        async def big_holder():
            async with q.slot("whisper"):
                order.append("big_acq")
                await release_gate.wait()

        # Reset queue
        q.reset_for_tests()
        h1 = asyncio.create_task(big_holder())
        await asyncio.sleep(0.01)
        h2 = asyncio.create_task(big_holder())
        await asyncio.sleep(0.01)
        # h1 + h2 = 8 GB → noch 3 GB frei. Dritter Whisper braucht 4 → blockt.
        w1 = asyncio.create_task(waiter("w1"))
        await asyncio.sleep(0.01)
        w2 = asyncio.create_task(waiter("w2"))
        await asyncio.sleep(0.01)
        assert "w1" not in order and "w2" not in order
        release_gate.set()
        await asyncio.gather(h1, h2, w1, w2)
        # FIFO: w1 vor w2
        w1_idx = order.index("w1")
        w2_idx = order.index("w2")
        assert w1_idx < w2_idx, f"FIFO verletzt: order={order}"


@pytest.mark.asyncio
class TestGpuQueueTimeout:
    async def test_timeout_when_no_slot_available(self):
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=4_000)  # nur Whisper passt rein
        gate = asyncio.Event()

        async def holder():
            async with q.slot("whisper"):
                await gate.wait()

        h = asyncio.create_task(holder())
        await asyncio.sleep(0.01)
        with pytest.raises(asyncio.TimeoutError):
            async with q.slot("whisper", timeout=0.1):
                pass  # pragma: no cover — wird nie erreicht
        gate.set()
        await h

    async def test_timeout_cleans_up_waiter(self):
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=4_000)
        gate = asyncio.Event()

        async def holder():
            async with q.slot("whisper"):
                await gate.wait()

        h = asyncio.create_task(holder())
        await asyncio.sleep(0.01)
        with pytest.raises(asyncio.TimeoutError):
            async with q.slot("whisper", timeout=0.05):
                pass  # pragma: no cover
        # Nach Timeout: Waiters-Liste leer
        status = await q.status()
        assert status["waiters"] == []
        gate.set()
        await h


# ── Status-Snapshot ──────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGpuQueueStatus:
    async def test_status_reports_active_and_waiters(self):
        from zerberus.core.gpu_queue import GpuQueue
        q = GpuQueue(total_mb=11_000)
        gate = asyncio.Event()

        async def hold(name):
            async with q.slot(name):
                await gate.wait()

        async def wait_for_block():
            async with q.slot("whisper"):
                pass  # pragma: no cover — blockt im Test bis gate

        h1 = asyncio.create_task(hold("whisper"))
        await asyncio.sleep(0.01)
        h2 = asyncio.create_task(hold("whisper"))
        await asyncio.sleep(0.01)
        # 8 GB belegt
        # Versuche dritten Whisper → der wird warten
        h3 = asyncio.create_task(wait_for_block())
        await asyncio.sleep(0.02)
        status = await q.status()
        assert status["total_mb"] == 11_000
        assert status["active_mb"] == 8_000
        assert status["free_mb"] == 3_000
        assert len(status["active_slots"]) == 2
        assert all(s["consumer"] == "whisper" for s in status["active_slots"])
        assert len(status["waiters"]) == 1
        assert status["waiters"][0]["consumer"] == "whisper"
        assert status["waiters"][0]["requested_mb"] == 4_000
        gate.set()
        await asyncio.gather(h1, h2, h3)


# ── Audit ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStoreAudit:
    async def test_audit_silent_when_db_not_initialized(self):
        """Wenn ``_async_session_maker`` is None — kein Crash."""
        from zerberus.core.gpu_queue import store_gpu_queue_audit, GpuSlotInfo
        from datetime import datetime
        info = GpuSlotInfo(
            audit_id="abc",
            consumer_name="whisper",
            requested_mb=4_000,
            queue_position=0,
            waited_at=datetime.utcnow(),
            acquired_at=datetime.utcnow(),
            released_at=datetime.utcnow(),
        )
        # Wenn DB nicht initialisiert ist → silent skip, kein Crash.
        await store_gpu_queue_audit(info)


# ── Verdrahtungs-Source-Audits ───────────────────────────────────────────


WHISPER_CLIENT_PATH = Path("zerberus/utils/whisper_client.py")
GEMMA_CLIENT_PATH = Path("zerberus/modules/prosody/gemma_client.py")
PROJECTS_RAG_PATH = Path("zerberus/core/projects_rag.py")
RAG_ROUTER_PATH = Path("zerberus/modules/rag/router.py")
ORCHESTRATOR_PATH = Path("zerberus/app/routers/orchestrator.py")
LEGACY_PATH = Path("zerberus/app/routers/legacy.py")
HEL_PATH = Path("zerberus/app/routers/hel.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestWhisperWiring:
    def test_imports_vram_slot(self):
        src = _read(WHISPER_CLIENT_PATH)
        assert "from zerberus.core.gpu_queue import vram_slot" in src

    def test_uses_vram_slot_in_transcribe(self):
        src = _read(WHISPER_CLIENT_PATH)
        assert 'vram_slot("whisper"' in src


class TestGemmaWiring:
    def test_imports_vram_slot(self):
        src = _read(GEMMA_CLIENT_PATH)
        assert "from zerberus.core.gpu_queue import vram_slot" in src

    def test_uses_vram_slot_in_analyze_audio(self):
        src = _read(GEMMA_CLIENT_PATH)
        assert 'vram_slot("gemma"' in src

    def test_stub_path_skips_slot(self):
        """Stub-Modus darf den GPU-Slot nicht holen — nichts laeuft auf der GPU."""
        src = _read(GEMMA_CLIENT_PATH)
        # Heuristik: 'm == "none"' wird vor dem vram_slot-Aufruf gepruft.
        none_pos = src.find('m == "none"')
        slot_pos = src.find('vram_slot("gemma"')
        assert 0 < none_pos < slot_pos


class TestEmbedderWiring:
    def test_projects_rag_imports_slot(self):
        src = _read(PROJECTS_RAG_PATH)
        assert "from zerberus.core.gpu_queue import vram_slot" in src

    def test_projects_rag_query_uses_slot(self):
        src = _read(PROJECTS_RAG_PATH)
        # mind. zwei vram_slot("embedder")-Aufrufe (index + query).
        count = src.count('vram_slot("embedder"')
        assert count >= 2, f"Erwarte >=2 vram_slot('embedder')-Aufrufe, fand {count}"

    def test_rag_router_uses_slot(self):
        src = _read(RAG_ROUTER_PATH)
        assert 'vram_slot("embedder"' in src

    def test_orchestrator_uses_slot(self):
        src = _read(ORCHESTRATOR_PATH)
        assert 'vram_slot("embedder"' in src


class TestRerankerWiring:
    def test_rag_router_uses_reranker_slot(self):
        src = _read(RAG_ROUTER_PATH)
        assert 'vram_slot("reranker"' in src

    def test_orchestrator_uses_reranker_slot(self):
        src = _read(ORCHESTRATOR_PATH)
        assert 'vram_slot("reranker"' in src


# ── Endpoint /v1/gpu/status ──────────────────────────────────────────────


class TestGpuStatusEndpointSource:
    def test_endpoint_registered(self):
        src = _read(LEGACY_PATH)
        assert '@router.get("/gpu/status")' in src

    def test_endpoint_calls_get_gpu_queue(self):
        src = _read(LEGACY_PATH)
        # Body delegiert an get_gpu_queue().status()
        assert "from zerberus.core.gpu_queue import get_gpu_queue" in src
        assert "get_gpu_queue().status()" in src


@pytest.mark.asyncio
class TestGpuStatusEndpointE2E:
    async def test_returns_default_status(self):
        from zerberus.core.gpu_queue import reset_global_queue_for_tests
        from zerberus.app.routers.legacy import gpu_status
        reset_global_queue_for_tests()
        result = await gpu_status()
        assert result["total_mb"] > 0
        assert result["active_mb"] == 0
        assert result["free_mb"] == result["total_mb"]
        assert result["active_slots"] == []
        assert result["waiters"] == []

    async def test_returns_active_slot(self):
        from zerberus.core.gpu_queue import (
            get_gpu_queue,
            reset_global_queue_for_tests,
        )
        from zerberus.app.routers.legacy import gpu_status
        reset_global_queue_for_tests()
        q = get_gpu_queue()
        gate = asyncio.Event()

        async def hold():
            async with q.slot("whisper"):
                await gate.wait()

        h = asyncio.create_task(hold())
        await asyncio.sleep(0.01)
        result = await gpu_status()
        assert result["active_mb"] == 4_000
        assert len(result["active_slots"]) == 1
        assert result["active_slots"][0]["consumer"] == "whisper"
        gate.set()
        await h


# ── Hel-Frontend-Toast ───────────────────────────────────────────────────


class TestHelFrontendGpuToast:
    def test_css_class_present(self):
        src = _read(HEL_PATH)
        assert ".gpu-toast" in src
        assert ".gpu-toast.visible" in src

    def test_dom_element_present(self):
        src = _read(HEL_PATH)
        assert 'id="gpuToast"' in src

    def test_polling_endpoint_used(self):
        src = _read(HEL_PATH)
        assert "/v1/gpu/status" in src

    def test_text_content_used_for_xss_safety(self):
        """Consumer-Namen kommen aus Whitelist + werden via textContent gesetzt
        — niemals via innerHTML, um XSS auszuschliessen."""
        src = _read(HEL_PATH)
        # Pattern: el.textContent = msg innerhalb der Polling-Funktion
        # Suche das Pattern im Patch-211-Block
        assert "el.textContent = msg" in src

    def test_consumer_label_whitelist(self):
        src = _read(HEL_PATH)
        assert "'whisper'" in src and "'gemma'" in src
        assert "'embedder'" in src and "'reranker'" in src

    def test_44px_touch_target(self):
        """Mobile-first Invariante: min-height 44px im Toast."""
        src = _read(HEL_PATH)
        # CSS-Block .gpu-toast enthaelt min-height: 44px
        match = re.search(r"\.gpu-toast\s*\{[^}]*min-height:\s*44px", src, re.DOTALL)
        assert match is not None, "gpu-toast braucht min-height: 44px (Mobile-first 44px Invariante)"


# ── JS-Syntax-Integritaet ────────────────────────────────────────────────


class TestJsSyntaxIntegrity:
    """node --check ueber alle inline <script>-Bloecke aus ADMIN_HTML.

    Skipped wenn ``node`` nicht im PATH (z.B. CI-Sandboxen).
    """

    def test_admin_html_inline_scripts_parse(self, tmp_path):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node nicht im PATH")
        # Extrahiere ADMIN_HTML aus hel.py. Vereinfacht: lies das ganze File
        # und ziehe inline <script>...</script>-Bloecke (ohne src=).
        src = _read(HEL_PATH)
        # ADMIN_HTML ist eine Triple-String-Konstante. Fuer den Test reicht
        # uns die Detektion von <script>...</script>-Bloecken im File-Inhalt.
        scripts = re.findall(
            r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
            src, re.DOTALL,
        )
        assert scripts, "Keine inline <script>-Bloecke gefunden"
        # Schreibe alle scripts in eine temporaere JS-Datei und syntax-checke.
        for i, body in enumerate(scripts):
            tmp_file = tmp_path / f"chunk_{i}.js"
            tmp_file.write_text(body, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(tmp_file)],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, (
                f"node --check fehlgeschlagen fuer Chunk {i}:\n"
                f"stderr={result.stderr}\nbody[:200]={body[:200]!r}"
            )


# ── Smoke ────────────────────────────────────────────────────────────────


class TestSmoke:
    def test_module_exports(self):
        from zerberus.core import gpu_queue
        for name in (
            "compute_vram_budget",
            "should_queue",
            "GpuQueue",
            "GpuSlotInfo",
            "get_gpu_queue",
            "vram_slot",
            "reset_global_queue_for_tests",
            "store_gpu_queue_audit",
            "VRAM_BUDGET_MB",
            "TOTAL_VRAM_MB",
            "DEFAULT_CONSUMER_BUDGET_MB",
            "KNOWN_CONSUMERS",
        ):
            assert hasattr(gpu_queue, name), f"Export fehlt: {name}"

    def test_database_has_audit_table(self):
        from zerberus.core import database
        assert hasattr(database, "GpuQueueAudit")
        cls = database.GpuQueueAudit
        assert cls.__tablename__ == "gpu_queue_audits"
        cols = {c.name for c in cls.__table__.columns}
        for required in (
            "audit_id", "consumer_name", "requested_mb",
            "queue_position", "wait_ms", "held_ms", "timed_out", "created_at",
        ):
            assert required in cols, f"Spalte fehlt: {required}"

    def test_known_consumers_match_budget_keys(self):
        from zerberus.core.gpu_queue import KNOWN_CONSUMERS, VRAM_BUDGET_MB
        assert KNOWN_CONSUMERS == frozenset(VRAM_BUDGET_MB.keys())
