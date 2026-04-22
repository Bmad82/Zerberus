"""
Patch 111 — Category-Boost im Retrieval.
"""
from __future__ import annotations

from zerberus.modules.rag.category_router import apply_category_boost


class TestApplyCategoryBoost:
    def test_no_query_category_returns_unchanged(self):
        results = [{"score": 0.5, "category": "narrative"}]
        out = apply_category_boost(results, None, 0.1)
        assert out == results

    def test_empty_results_returns_empty(self):
        assert apply_category_boost([], "narrative", 0.1) == []

    def test_matching_category_gets_boost_and_moves_up(self):
        results = [
            {"score": 0.8, "category": "technical", "text": "a"},
            {"score": 0.5, "category": "narrative", "text": "b"},
        ]
        out = apply_category_boost(results, "narrative", 0.4)
        assert out[0]["text"] == "b"
        assert out[0]["score"] == 0.9
        assert out[0]["category_boosted"] is True
        assert out[1]["text"] == "a"
        assert "category_boosted" not in out[1]

    def test_boost_uses_rerank_score_when_present(self):
        results = [
            {"rerank_score": 0.8, "score": 0.1, "category": "technical", "text": "a"},
            {"rerank_score": 0.5, "score": 0.2, "category": "narrative", "text": "b"},
        ]
        out = apply_category_boost(results, "narrative", 0.4)
        assert out[0]["text"] == "b"
        assert out[0]["rerank_score"] == 0.9

    def test_no_matching_category_returns_unchanged_order(self):
        results = [
            {"score": 0.8, "category": "technical", "text": "a"},
            {"score": 0.5, "category": "narrative", "text": "b"},
        ]
        out = apply_category_boost(results, "lore", 0.4)
        assert out[0]["text"] == "a"
        assert out[1]["text"] == "b"

    def test_multiple_matches_all_get_boosted(self):
        results = [
            {"score": 0.9, "category": "technical", "text": "a"},
            {"score": 0.5, "category": "narrative", "text": "b"},
            {"score": 0.4, "category": "narrative", "text": "c"},
        ]
        out = apply_category_boost(results, "narrative", 0.6)
        # b: 0.5+0.6=1.1, c: 0.4+0.6=1.0, a: 0.9
        assert out[0]["text"] == "b"
        assert out[1]["text"] == "c"
        assert out[2]["text"] == "a"
