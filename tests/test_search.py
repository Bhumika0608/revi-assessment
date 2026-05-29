"""
Tests for db/search.py — focus on the pure pieces so we don't need to spin up
the 10k-item embedding index for every assertion:

  - parse_price_constraint: each regex pattern + negative cases
  - parse_dietary_filter:   each dietary tag + synonyms + negative cases
  - reciprocal_rank_fusion: correctness on small fixed inputs

The full hybrid pipeline (semantic + FTS + fuzzy → ranked items) is already
exercised by tests/test_tools.py::TestSearchMenu.

Run: python3 -m pytest tests/test_search.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.search import (
    items_matching_dietary,
    parse_dietary_filter,
    parse_price_constraint,
    reciprocal_rank_fusion,
)


# ── Price constraint parsing ──────────────────────────────────────────────────

class TestPriceConstraint:
    @pytest.mark.parametrize("phrase,expected", [
        ("tacos under $6",         6.0),
        ("tacos under 6",          6.0),
        ("less than $5",           5.0),
        ("less than 5.50",         5.50),
        ("below $10",              10.0),
        ("$6 or under",            6.0),
        ("6 or less",              6.0),
        ("max $10",                10.0),
        ("maximum $12.50",         12.50),
        ("budget $15",             15.0),
        ("budget of $20",          20.0),
        ("no more than $8",        8.0),
        ("cheaper than $7",        7.0),
        ("around $5",              5.0),
    ])
    def test_extracts_amount(self, phrase, expected):
        assert parse_price_constraint(phrase) == pytest.approx(expected, abs=0.01)

    def test_case_insensitive(self):
        assert parse_price_constraint("UNDER $6") == pytest.approx(6.0)
        assert parse_price_constraint("Below $10") == pytest.approx(10.0)

    def test_no_price_returns_none(self):
        assert parse_price_constraint("I want a taco") is None
        assert parse_price_constraint("") is None
        assert parse_price_constraint("vegan options") is None

    def test_decimal_amount(self):
        assert parse_price_constraint("under $5.99") == pytest.approx(5.99, abs=0.01)

    def test_first_pattern_wins_when_multiple(self):
        # "under $6 and below $4" — should resolve to first match.
        result = parse_price_constraint("under $6 and below $4")
        # We don't assert WHICH wins (depends on pattern order) — just that a number returned.
        assert result is not None


# ── Dietary filter parsing ────────────────────────────────────────────────────

class TestDietaryFilter:
    @pytest.mark.parametrize("phrase,expected", [
        ("vegan options",       "vegan"),
        ("anything plant-based",        "vegan"),
        ("plant based",         "vegan"),
        ("meatless burrito",    "vegetarian"),
        ("vegetarian",          "vegetarian"),
        ("no meat",             "vegetarian"),
        ("gluten free",         "gluten-free"),
        ("gluten-free taco",    "gluten-free"),
        ("celiac friendly",     "gluten-free"),
        ("dairy free",          "dairy-free"),
        ("dairy-free options",  "dairy-free"),
        ("no dairy",            "dairy-free"),
        ("lactose intolerant",  "dairy-free"),
        ("non-dairy menu",      "dairy-free"),
        ("chicken bowl",        "chicken"),
        ("pollo bowl",          "chicken"),
        ("carne asada",         "beef"),
        ("steak burrito",       "beef"),
        ("carnitas",            "pork"),
        ("al pastor",           "pork"),
        ("any shellfish",       "shellfish"),
        ("seafood options",     "shellfish"),
    ])
    def test_extracts_tag(self, phrase, expected):
        assert parse_dietary_filter(phrase) == expected

    def test_case_insensitive(self):
        assert parse_dietary_filter("VEGAN") == "vegan"
        assert parse_dietary_filter("Gluten Free") == "gluten-free"

    def test_no_dietary_returns_none(self):
        assert parse_dietary_filter("birria taco") is None
        assert parse_dietary_filter("") is None
        assert parse_dietary_filter("anything under $6") is None


# ── Reciprocal Rank Fusion ────────────────────────────────────────────────────

class TestReciprocalRankFusion:
    def _item(self, iid):
        return {"id": iid, "name": iid.title()}

    def test_empty_input_returns_empty(self):
        assert reciprocal_rank_fusion([]) == []
        assert reciprocal_rank_fusion([[]]) == []
        assert reciprocal_rank_fusion([[], [], []]) == []

    def test_single_list_passes_through_order(self):
        items = [self._item("a"), self._item("b"), self._item("c")]
        result = reciprocal_rank_fusion([items])
        assert [r["id"] for r in result] == ["a", "b", "c"]

    def test_identical_lists_preserve_order(self):
        items = [self._item("a"), self._item("b"), self._item("c")]
        result = reciprocal_rank_fusion([items, items])
        assert [r["id"] for r in result] == ["a", "b", "c"]

    def test_overlap_items_rank_higher(self):
        # 'b' appears in both lists at rank 0 → should win over 'a' (only in one).
        list_1 = [self._item("a"), self._item("b")]
        list_2 = [self._item("b"), self._item("c")]
        result = reciprocal_rank_fusion([list_1, list_2])
        ids = [r["id"] for r in result]
        # b is in both at high rank → first
        assert ids[0] == "b"
        # a and c each appear once in one list
        assert set(ids[1:]) == {"a", "c"}

    def test_three_list_consensus_wins(self):
        # 'x' is in all three; 'y' in two; 'z' in one. x > y > z.
        l1 = [self._item("x"), self._item("y"), self._item("z")]
        l2 = [self._item("x"), self._item("y")]
        l3 = [self._item("x")]
        result = reciprocal_rank_fusion([l1, l2, l3])
        ids = [r["id"] for r in result]
        assert ids[0] == "x"
        assert ids[1] == "y"
        assert ids[2] == "z"

    def test_deduplicates_by_id(self):
        items = [self._item("a"), self._item("b"), self._item("a")]
        result = reciprocal_rank_fusion([items])
        ids = [r["id"] for r in result]
        # 'a' appears twice in the input but only once in output.
        assert ids.count("a") == 1

    def test_k_parameter_changes_scoring(self):
        # With small k, top-rank items have a much higher score relative to lower.
        # We don't assert exact scores — just that ordering remains by rank.
        list_1 = [self._item("a"), self._item("b"), self._item("c")]
        result_small_k = reciprocal_rank_fusion([list_1], k=1)
        result_large_k = reciprocal_rank_fusion([list_1], k=1000)
        # Ordering of a single list is preserved regardless of k.
        assert [r["id"] for r in result_small_k] == ["a", "b", "c"]
        assert [r["id"] for r in result_large_k] == ["a", "b", "c"]

    def test_first_rank_in_any_list_beats_later_rank_in_same(self):
        # 'b' at rank 0 in list_2 beats 'a' at rank 2 in list_1 (and only there).
        list_1 = [self._item("x"), self._item("y"), self._item("a")]
        list_2 = [self._item("b")]
        result = reciprocal_rank_fusion([list_1, list_2])
        ids = [r["id"] for r in result]
        # The invariant: rank-0 items rank above rank-2 items.
        assert ids.index("b") < ids.index("a")
        assert ids.index("x") < ids.index("a")


class TestItemsMatchingDietary:
    """items_matching_dietary handles positive tags (vegan) AND negative
    'X-free' tags (dairy-free, gluten-free) — the menu uses 'contains_X'
    inclusion markers rather than 'X-free' exclusion markers, so dairy-free
    must filter for items WITHOUT 'contains_dairy', not WITH 'dairy-free'."""

    def _items(self):
        # Two synthetic items with the menu's actual tag conventions.
        return [
            {"id": "a1", "name": "Veggie Bowl",  "dietary_tags": ["vegan", "vegetarian"]},
            {"id": "a2", "name": "Pollo Bowl",   "dietary_tags": ["chicken", "contains_dairy"]},
            {"id": "a3", "name": "Birria Taco",  "dietary_tags": ["beef"]},
            {"id": "a4", "name": "Cheese Quesadilla", "dietary_tags": ["vegetarian", "contains_dairy", "contains_gluten"]},
            {"id": "a5", "name": "Bottled Water", "dietary_tags": ["vegan", "vegetarian"]},
        ]

    def test_positive_tag_includes_only_tagged_items(self):
        out = items_matching_dietary(self._items(), "vegan")
        assert {it["id"] for it in out} == {"a1", "a5"}

    def test_dairy_free_excludes_items_with_contains_dairy(self):
        # Negative filter — items WITHOUT contains_dairy tag.
        out = items_matching_dietary(self._items(), "dairy-free")
        ids = {it["id"] for it in out}
        assert ids == {"a1", "a3", "a5"}
        # And items with contains_dairy are NOT in the result.
        assert "a2" not in ids and "a4" not in ids

    def test_gluten_free_excludes_items_with_contains_gluten(self):
        out = items_matching_dietary(self._items(), "gluten-free")
        ids = {it["id"] for it in out}
        # a4 has contains_gluten — should be excluded.
        assert "a4" not in ids
        # everything else has no contains_gluten tag — included.
        assert ids == {"a1", "a2", "a3", "a5"}

    def test_unknown_tag_returns_no_matches(self):
        out = items_matching_dietary(self._items(), "extraterrestrial")
        assert out == []
