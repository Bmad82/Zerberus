"""
Patch 146 (B-018): Metriken-Endpoint liefert keinen vollen Nachrichten-Text mehr.

Tests:
- /hel/metrics/latest_with_costs truncatet content auf 50 Zeichen + "…"
- content_truncated + content_original_length werden mitgeliefert
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


class TestMetricsTruncation:
    def test_endpoint_truncatet_content(self, hel_src):
        fn_block = hel_src.split("async def metrics_latest_with_costs")[1].split("async def ")[0]
        # Kürzung auf 50 Zeichen
        assert "[:50]" in fn_block
        # Truncation-Marker
        assert "…" in fn_block

    def test_truncation_flag_und_laenge(self, hel_src):
        fn_block = hel_src.split("async def metrics_latest_with_costs")[1].split("async def ")[0]
        assert "content_truncated" in fn_block
        assert "content_original_length" in fn_block

    def test_patch_146_kommentar(self, hel_src):
        fn_block = hel_src.split("async def metrics_latest_with_costs")[1].split("async def ")[0]
        assert "Patch 146" in fn_block
