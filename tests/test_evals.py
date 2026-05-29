"""
Unit tests for the eval framework — no API calls. These cover the scoring layer
that was broken when the agent moved from place_order to signal_checkout.

Run: python3 -m pytest tests/test_evals.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.setup import get_all_item_ids, init_db
from evals.metrics import TurnResult, score_case
from evals.run_evals import _order_from_cart


@pytest.fixture(scope="session", autouse=True)
def db():
    init_db()


@pytest.fixture(scope="session")
def valid_ids():
    return get_all_item_ids()


def _turn(idx, status, cart=None, msg="ok"):
    return TurnResult(
        turn_idx=idx,
        user_message=f"turn {idx}",
        agent_message=msg,
        status=status,
        order=_order_from_cart(cart) if cart else None,
        latency_ms=10.0,
    )


# ── _order_from_cart ──────────────────────────────────────────────────────────

class TestOrderFromCart:
    def test_empty_cart_returns_none(self):
        assert _order_from_cart([]) is None

    def test_subtotal_includes_modifier_upcharge(self):
        cart = [{
            "item_id": "bowl_pollo", "name": "Pollo Bowl", "price": 12.49,
            "quantity": 1, "modifiers": ["add_guac"],
            "modifier_upcharge": 1.50, "line_total": 13.99,
        }]
        order = _order_from_cart(cart)
        assert order["subtotal"] == pytest.approx(13.99, abs=0.01)
        assert order["items"][0]["modifiers"] == ["add_guac"]
        assert order["items"][0]["quantity"] == 1

    def test_multi_item_subtotal_sums_line_totals(self):
        cart = [
            {"item_id": "taco_birria", "name": "Birria Taco", "quantity": 2,
             "modifiers": [], "line_total": 9.98},
            {"item_id": "drink_coke_mexican", "name": "Mexican Coke", "quantity": 1,
             "modifiers": [], "line_total": 3.49},
        ]
        order = _order_from_cart(cart)
        assert order["subtotal"] == pytest.approx(13.47, abs=0.01)
        assert len(order["items"]) == 2


# ── score_case — status mapping ───────────────────────────────────────────────

class TestStatusScoring:
    def test_confirmed_passes_when_checkout_signaled(self, valid_ids):
        case = {
            "id": "t", "title": "x", "category": "simple", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "taco_birria", "quantity": 1}],
            "expected_subtotal": 4.99,
        }
        cart = [{"item_id": "taco_birria", "name": "Birria Taco",
                 "quantity": 1, "modifiers": [], "line_total": 4.99}]
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed, r.failure_reasons
        assert r.status_correct is True
        assert r.iia == 1.0
        assert r.subtotal_correct is True

    def test_confirmed_fails_when_never_reached_checkout(self, valid_ids):
        case = {
            "id": "t", "title": "x", "category": "simple", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "taco_birria", "quantity": 1}],
        }
        results = [_turn(0, "in_progress", cart=None)]
        r = score_case(case, results, valid_ids)
        assert r.passed is False
        assert r.status_correct is False
        assert any("Status" in reason for reason in r.failure_reasons)

    def test_refused_passes_on_refused_status(self, valid_ids):
        case = {
            "id": "t", "title": "x", "category": "refusal", "turns": ["msg"],
            "expected_status": "refused",
        }
        results = [_turn(0, "refused")]
        r = score_case(case, results, valid_ids)
        assert r.passed
        assert r.status_correct is True
        assert r.refusal_given is True

    def test_in_progress_passes_for_clarification(self, valid_ids):
        case = {
            "id": "t", "title": "x", "category": "ambiguous", "turns": ["msg"],
            "expected_status": "in_progress",
            "requires_clarification": True,
        }
        results = [_turn(0, "in_progress")]
        r = score_case(case, results, valid_ids)
        assert r.passed
        assert r.clarification_given is True


# ── score_case — item / modifier / subtotal scoring ───────────────────────────

class TestItemScoring:
    def test_item_id_correct_modifier_correct(self, valid_ids):
        # tc_02_modifier_guac equivalent
        case = {
            "id": "x", "title": "x", "category": "modifiers", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "bowl_pollo", "quantity": 1,
                                "modifiers": ["add_guac"]}],
            "expected_subtotal": 13.99,
        }
        cart = [{"item_id": "bowl_pollo", "name": "Pollo Bowl",
                 "quantity": 1, "modifiers": ["add_guac"],
                 "line_total": 13.99}]
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed, r.failure_reasons
        assert r.iia == 1.0
        assert r.modifier_acc == 1.0
        assert r.subtotal_correct is True

    def test_subtotal_wrong_fails_case(self, valid_ids):
        # Modifier upcharge missing — old bug: cart returned $12.49 instead of $13.99.
        case = {
            "id": "x", "title": "x", "category": "modifiers", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "bowl_pollo", "quantity": 1,
                                "modifiers": ["add_guac"]}],
            "expected_subtotal": 13.99,
        }
        cart = [{"item_id": "bowl_pollo", "name": "Pollo Bowl",
                 "quantity": 1, "modifiers": ["add_guac"],
                 "line_total": 12.49}]  # bug — missing upcharge
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed is False
        assert r.subtotal_correct is False
        assert any("Subtotal" in reason for reason in r.failure_reasons)

    def test_missing_modifier_fails_case(self, valid_ids):
        case = {
            "id": "x", "title": "x", "category": "modifiers", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "bowl_pollo", "quantity": 1,
                                "modifiers": ["add_guac"]}],
        }
        cart = [{"item_id": "bowl_pollo", "name": "Pollo Bowl",
                 "quantity": 1, "modifiers": [],  # forgot the modifier
                 "line_total": 12.49}]
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed is False
        assert any("Missing modifiers" in reason for reason in r.failure_reasons)

    def test_extra_item_fails_case(self, valid_ids):
        case = {
            "id": "x", "title": "x", "category": "simple", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "taco_birria", "quantity": 1}],
        }
        cart = [
            {"item_id": "taco_birria", "name": "Birria Taco", "quantity": 1,
             "modifiers": [], "line_total": 4.99},
            {"item_id": "drink_coke_mexican", "name": "Mexican Coke", "quantity": 1,
             "modifiers": [], "line_total": 3.49},
        ]
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed is False
        assert any("Unexpected items" in reason for reason in r.failure_reasons)

    def test_wrong_quantity_fails(self, valid_ids):
        case = {
            "id": "x", "title": "x", "category": "simple", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "taco_birria", "quantity": 2}],
        }
        cart = [{"item_id": "taco_birria", "name": "Birria Taco",
                 "quantity": 1, "modifiers": [], "line_total": 4.99}]
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed is False
        assert any("quantity" in reason.lower() for reason in r.failure_reasons)


# ── Hallucination detection ───────────────────────────────────────────────────

class TestHallucinationDetection:
    def test_hallucinated_item_id_fails(self, valid_ids):
        case = {
            "id": "x", "title": "x", "category": "simple", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "taco_birria", "quantity": 1}],
        }
        cart = [{"item_id": "ghost_item", "name": "Ghost",
                 "quantity": 1, "modifiers": [], "line_total": 9.99}]
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed is False
        assert "ghost_item" in r.hallucinated_ids
        assert any("Hallucinated" in reason for reason in r.failure_reasons)

    def test_hallucinated_modifier_fails(self, valid_ids):
        case = {
            "id": "x", "title": "x", "category": "modifiers", "turns": ["msg"],
            "expected_status": "confirmed",
            "expected_items": [{"item_id": "taco_birria", "quantity": 1}],
        }
        cart = [{"item_id": "taco_birria", "name": "Birria Taco", "quantity": 1,
                 "modifiers": ["nonexistent_modifier"], "line_total": 4.99}]
        results = [_turn(0, "checkout", cart=cart)]
        r = score_case(case, results, valid_ids)
        assert r.passed is False
        assert "nonexistent_modifier" in r.hallucinated_ids


# ── Multi-turn: scoring picks the cart at checkout time, not earlier turns ───

class TestMultiTurnScoring:
    def test_uses_final_cart_at_checkout(self, valid_ids):
        # Turn 1: cart=[birria]. Turn 2: cart=[birria, coke]. Turn 3: checkout.
        case = {
            "id": "x", "title": "x", "category": "multi_turn",
            "turns": ["a", "b", "c"],
            "expected_status": "confirmed",
            "expected_items": [
                {"item_id": "taco_birria", "quantity": 1},
                {"item_id": "drink_coke_mexican", "quantity": 1},
            ],
            "expected_subtotal": 8.48,
        }
        cart_1 = [{"item_id": "taco_birria", "name": "Birria Taco",
                   "quantity": 1, "modifiers": [], "line_total": 4.99}]
        cart_2 = cart_1 + [{"item_id": "drink_coke_mexican", "name": "Mexican Coke",
                            "quantity": 1, "modifiers": [], "line_total": 3.49}]
        results = [
            _turn(0, "in_progress", cart=cart_1),
            _turn(1, "in_progress", cart=cart_2),
            _turn(2, "checkout",    cart=cart_2),
        ]
        r = score_case(case, results, valid_ids)
        assert r.passed, r.failure_reasons
        assert r.iia == 1.0
        assert r.subtotal_correct is True
