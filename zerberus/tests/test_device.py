"""
Patch 111 — Device-Detection Tests.

Rein logik-basiert; `_cuda_state()` wird gemockt, damit der Test weder
`torch` noch eine echte GPU braucht.
"""
from __future__ import annotations

import pytest

from zerberus.modules.rag import device as dev_mod
from zerberus.modules.rag.device import get_rag_device


@pytest.fixture(autouse=True)
def _reset_cuda_mock(monkeypatch):
    """Default: keine GPU. Einzelne Tests überschreiben das per monkeypatch."""
    monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (False, 0.0, 0.0, ""))
    yield


class TestAutoMode:
    def test_auto_no_cuda_returns_cpu(self):
        assert get_rag_device("auto") == "cpu"

    def test_auto_none_treated_as_auto(self):
        assert get_rag_device(None) == "cpu"

    def test_auto_with_sufficient_vram_returns_cuda(self, monkeypatch):
        monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 6.0, 12.0, "RTX 3060"))
        assert get_rag_device("auto") == "cuda"

    def test_auto_below_min_vram_returns_cpu(self, monkeypatch):
        # Threshold ist 2.0 GB — 1.5 sollte CPU ergeben
        monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 1.5, 12.0, "RTX 3060"))
        assert get_rag_device("auto") == "cpu"

    def test_auto_at_exact_threshold_returns_cuda(self, monkeypatch):
        monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 2.0, 12.0, "RTX 3060"))
        assert get_rag_device("auto") == "cuda"


class TestForceMode:
    def test_force_cpu_always_cpu(self, monkeypatch):
        monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 100.0, 128.0, "A100"))
        assert get_rag_device("cpu") == "cpu"

    def test_force_cuda_without_cuda_falls_back_to_cpu(self):
        assert get_rag_device("cuda") == "cpu"

    def test_force_cuda_with_cuda_returns_cuda_even_below_threshold(self, monkeypatch):
        # Bei explizitem "cuda" ignorieren wir den VRAM-Check
        monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 0.5, 12.0, "RTX 3060"))
        assert get_rag_device("cuda") == "cuda"


class TestCaseNormalization:
    def test_uppercase_config_is_accepted(self, monkeypatch):
        monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 8.0, 12.0, "RTX 3060"))
        assert get_rag_device("CUDA") == "cuda"
        assert get_rag_device("Cpu") == "cpu"

    def test_whitespace_is_stripped(self):
        assert get_rag_device("  auto  ") == "cpu"
