"""
Idempotency tests for the checkout finalization path.

Covers the DB-layer guarantees that protect against Streamlit reruns / browser
refreshes during the payment spinner causing double-saves or double-decrements.
No Stripe / API calls — pure DB logic.

Run: python3 -m pytest tests/test_finalize.py -v
"""

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.setup import (
    DB_PATH,
    decrement_inventory,
    get_inventory,
    init_db,
    order_exists,
    restock_item,
    save_order,
)


@pytest.fixture(scope="module", autouse=True)
def db_and_restore():
    """Initialize the DB and restore stock at module teardown so other test files
    that depend on items being in stock (test_tools.py) aren't broken by the
    destructive scenarios below (clip-to-zero, repeated decrements)."""
    init_db()
    yield
    # Refill the items this module decrements so the shared menu.db is left healthy.
    restock_item("taco_birria", 100, reason="test_finalize_teardown")


def _order_id() -> str:
    return f"TT-TEST-{uuid.uuid4().hex[:8].upper()}"


def _stock(item_id: str) -> int:
    """Read current stock for an item via direct SQL."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT quantity FROM inventory WHERE item_id = ?", (item_id,)
        ).fetchone()
    return row[0] if row else 0


def _inventory_log_rows(item_id: str, order_id: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM inventory_log
            WHERE item_id = ? AND order_id = ? AND reason = 'order'
        """, (item_id, order_id)).fetchone()
    return row[0]


class TestOrderExists:
    def test_returns_false_for_unknown(self):
        assert order_exists("TT-DOES-NOT-EXIST") is False

    def test_returns_false_for_empty_string(self):
        assert order_exists("") is False

    def test_returns_true_after_save(self):
        oid = _order_id()
        save_order(
            order_id=oid, subtotal=4.99,
            items=[{"item_id": "taco_birria", "quantity": 1, "modifiers": []}],
        )
        assert order_exists(oid) is True


class TestSaveOrderIdempotent:
    def test_duplicate_save_is_noop(self):
        # INSERT OR IGNORE — second call must not error and must not overwrite.
        oid = _order_id()
        save_order(order_id=oid, subtotal=4.99, items=[], total=4.99)
        save_order(order_id=oid, subtotal=999.99, items=[], total=999.99)

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT subtotal, total FROM orders WHERE order_id = ?", (oid,)
            ).fetchone()
        # Original values preserved.
        assert row[0] == pytest.approx(4.99, abs=0.01)
        assert row[1] == pytest.approx(4.99, abs=0.01)


class TestDecrementInventoryIdempotent:
    def test_first_decrement_works(self):
        # Pick an item we can safely manipulate. Restock first so test is repeatable.
        item_id = "taco_birria"
        restock_item(item_id, 50, reason="test_setup")
        before = _stock(item_id)
        oid    = _order_id()

        decrement_inventory(item_id, 3, oid)

        assert _stock(item_id) == before - 3
        assert _inventory_log_rows(item_id, oid) == 1

    def test_repeated_call_with_same_order_id_does_not_double_decrement(self):
        """Streamlit rerun during finalize: same order_id retried — must be a no-op."""
        item_id = "taco_birria"
        restock_item(item_id, 50, reason="test_setup")
        before = _stock(item_id)
        oid    = _order_id()

        decrement_inventory(item_id, 5, oid)
        decrement_inventory(item_id, 5, oid)   # retry
        decrement_inventory(item_id, 5, oid)   # another retry

        assert _stock(item_id) == before - 5, "stock decremented only once"
        assert _inventory_log_rows(item_id, oid) == 1, "log entry not duplicated"

    def test_different_order_ids_decrement_independently(self):
        item_id = "taco_birria"
        restock_item(item_id, 50, reason="test_setup")
        before = _stock(item_id)
        oid_a  = _order_id()
        oid_b  = _order_id()

        decrement_inventory(item_id, 2, oid_a)
        decrement_inventory(item_id, 3, oid_b)

        assert _stock(item_id) == before - 5
        assert _inventory_log_rows(item_id, oid_a) == 1
        assert _inventory_log_rows(item_id, oid_b) == 1

    def test_decrement_without_order_id_still_works(self):
        # No order_id → no dedupe (back-compat path).
        item_id = "taco_birria"
        restock_item(item_id, 50, reason="test_setup")
        before = _stock(item_id)

        decrement_inventory(item_id, 1)
        decrement_inventory(item_id, 1)   # no dedupe — both fire

        assert _stock(item_id) == before - 2


class TestStockClipsToZero:
    def test_decrement_below_zero_clips(self):
        item_id = "taco_birria"
        # Reset stock to a small known number for this test.
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE inventory SET quantity = 2 WHERE item_id = ?", (item_id,)
            )
            conn.commit()

        decrement_inventory(item_id, 100, _order_id())
        assert _stock(item_id) == 0
