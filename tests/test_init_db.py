"""
Tests that init_db() rebuilds the full schema and loads menu data from JSON
into a brand-new SQLite file. This is the cold-start path on a fresh checkout
(where data/menu.db has never existed) — if it regresses, the app silently
boots with an empty catalog.

Uses tmp_path so we don't touch the real data/menu.db.

Run: python3 -m pytest tests/test_init_db.py -v
"""

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db import setup as setup_mod


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Function-scoped — for tests that need to verify the DB doesn't exist BEFORE init_db."""
    db_path = tmp_path / "fresh_menu.db"
    monkeypatch.setattr(setup_mod, "DB_PATH", str(db_path))
    monkeypatch.setattr(setup_mod, "_initialized", False)
    yield db_path


@pytest.fixture(scope="module")
def shared_fresh_db(tmp_path_factory):
    """Module-scoped — one init_db run shared across all read-only structure
    assertions. init_db on the full 10k catalog is expensive (~30s); we don't
    need to repeat it per test."""
    db_path = tmp_path_factory.mktemp("init_db_shared") / "shared_menu.db"
    saved_path        = setup_mod.DB_PATH
    saved_initialized = setup_mod._initialized

    setup_mod.DB_PATH       = str(db_path)
    setup_mod._initialized  = False
    setup_mod.init_db()

    yield db_path

    setup_mod.DB_PATH      = saved_path
    setup_mod._initialized = saved_initialized


def _query_one(db_path: Path, sql: str, *params):
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(sql, params).fetchone()
    return row


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


class TestColdStart:
    """Each of these queries the same one-time-built fresh DB — keeps the suite fast."""

    def test_db_file_is_created(self, fresh_db):
        # The only test that needs its own DB — verifies cold-start file creation.
        assert not fresh_db.exists()
        setup_mod.init_db()
        assert fresh_db.exists()

    def test_all_expected_tables_present(self, shared_fresh_db):
        names = _table_names(shared_fresh_db)
        expected = {"orders", "menu_items", "menu_fts", "inventory",
                    "inventory_log", "payments"}
        missing = expected - names
        assert not missing, f"missing tables: {missing}"

    def test_canonical_menu_loaded(self, shared_fresh_db):
        row = _query_one(shared_fresh_db, "SELECT COUNT(*) FROM menu_items")
        # menu.json has 29 canonical items + menu_expanded.json adds ~10,060.
        assert row[0] >= 29

    def test_canonical_birria_taco_present(self, shared_fresh_db):
        row = _query_one(shared_fresh_db,
                         "SELECT name, price FROM menu_items WHERE id = ?", "taco_birria")
        assert row is not None
        assert row[0] == "Birria Taco"
        assert row[1] == pytest.approx(4.99, abs=0.01)

    def test_fts_index_populated(self, shared_fresh_db):
        row = _query_one(shared_fresh_db, "SELECT COUNT(*) FROM menu_fts")
        assert row[0] >= 29

    def test_inventory_seeded_for_canonical_items(self, shared_fresh_db):
        row = _query_one(shared_fresh_db,
                         "SELECT quantity FROM inventory WHERE item_id = ?", "taco_birria")
        assert row is not None
        assert row[0] > 0
        # Out-of-stock seeded as 0 (taco_shrimp marked available=false in menu.json).
        row_oos = _query_one(shared_fresh_db,
                             "SELECT quantity FROM inventory WHERE item_id = ?", "taco_shrimp")
        assert row_oos is not None
        assert row_oos[0] == 0


class TestIdempotentReInit:
    """Re-running init_db on an existing DB must preserve live state."""

    def test_orders_and_inventory_survive_re_init(self, fresh_db):
        # Own fresh DB so the mutations don't leak into other tests.
        setup_mod.init_db()

        with sqlite3.connect(str(fresh_db)) as conn:
            conn.execute(
                "INSERT INTO orders (order_id, subtotal, total) VALUES (?, ?, ?)",
                ("TT-PERSIST-1", 9.99, 10.80),
            )
            conn.execute(
                "UPDATE inventory SET quantity = 7 WHERE item_id = ?",
                ("taco_birria",),
            )
            conn.commit()

        # Re-run init_db on the same path — must not clobber orders or live inventory.
        setup_mod._initialized = False
        setup_mod.init_db()

        oid = _query_one(fresh_db,
                         "SELECT order_id FROM orders WHERE order_id = ?", "TT-PERSIST-1")
        assert oid is not None, "init_db must not wipe orders"

        stock = _query_one(fresh_db,
                           "SELECT quantity FROM inventory WHERE item_id = ?", "taco_birria")
        assert stock[0] == 7, "init_db must not reset live inventory levels"
