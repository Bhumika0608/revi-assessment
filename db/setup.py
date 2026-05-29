"""
SQLite + FTS5 menu database.

Schema:
  menu_items      — full 10k+ catalog, JSON-serialized options/modifiers/tags
  menu_fts        — FTS5 virtual table for BM25 full-text search
  orders          — confirmed orders (saved after payment succeeds)
  inventory       — live stock levels (source of truth for availability)
  inventory_log   — append-only ledger of every stock change
  payments        — Stripe payment records (no card data stored)

init_db() is idempotent: safe to call on every startup.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).parent.parent
DB_PATH = os.getenv("MENU_DB_PATH", str(_ROOT / "data" / "menu.db"))
_MENU_JSON = _ROOT / "data" / "menu.json"
_MENU_EXPANDED_JSON = _ROOT / "data" / "menu_expanded.json"

_initialized = False


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global _initialized
    if _initialized:
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _get_conn()

    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id             TEXT PRIMARY KEY,
                created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                subtotal             REAL NOT NULL,
                delivery_fee         REAL NOT NULL DEFAULT 0.0,
                tax                  REAL NOT NULL DEFAULT 0.0,
                total                REAL NOT NULL DEFAULT 0.0,
                items_json           TEXT NOT NULL DEFAULT '[]',
                special_instructions TEXT NOT NULL DEFAULT '',
                fulfillment_type     TEXT NOT NULL DEFAULT 'pickup',
                email                TEXT NOT NULL DEFAULT '',
                phone                TEXT NOT NULL DEFAULT '',
                conversation_turns   INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS menu_items (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                category      TEXT NOT NULL,
                description   TEXT NOT NULL DEFAULT '',
                price         REAL NOT NULL,
                available     INTEGER NOT NULL DEFAULT 1,
                options       TEXT NOT NULL DEFAULT '{}',
                modifiers     TEXT NOT NULL DEFAULT '[]',
                dietary_tags  TEXT NOT NULL DEFAULT '[]',
                tags          TEXT NOT NULL DEFAULT '[]'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS menu_fts USING fts5(
                item_id,
                name,
                description,
                category,
                dietary_tags_text,
                tags_text,
                tokenize = 'porter ascii'
            );

            CREATE TABLE IF NOT EXISTS inventory (
                item_id             TEXT PRIMARY KEY,
                quantity            INTEGER NOT NULL DEFAULT 0,
                low_stock_threshold INTEGER NOT NULL DEFAULT 10,
                updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );

            CREATE TABLE IF NOT EXISTS inventory_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id   TEXT NOT NULL,
                delta     INTEGER NOT NULL,
                reason    TEXT NOT NULL DEFAULT 'order',
                order_id  TEXT,
                logged_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );

            CREATE TABLE IF NOT EXISTS payments (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id             TEXT NOT NULL,
                stripe_payment_id    TEXT NOT NULL,
                amount_cents         INTEGER NOT NULL,
                currency             TEXT NOT NULL DEFAULT 'usd',
                status               TEXT NOT NULL,
                fulfillment_type     TEXT NOT NULL DEFAULT 'pickup',
                created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
        """)

    _migrate_orders_table(conn)

    _load_json_file(conn, _MENU_JSON)
    if _MENU_EXPANDED_JSON.exists():
        _load_json_file(conn, _MENU_EXPANDED_JSON)

    conn.close()
    _initialized = True


def _migrate_orders_table(conn: sqlite3.Connection) -> None:
    """Add new columns to orders table for existing databases (idempotent)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
    new_cols = [
        ("delivery_fee",     "REAL NOT NULL DEFAULT 0.0"),
        ("tax",              "REAL NOT NULL DEFAULT 0.0"),
        ("total",            "REAL NOT NULL DEFAULT 0.0"),
        ("fulfillment_type", "TEXT NOT NULL DEFAULT 'pickup'"),
        ("email",            "TEXT NOT NULL DEFAULT ''"),
        ("phone",            "TEXT NOT NULL DEFAULT ''"),
    ]
    for col_name, col_def in new_cols:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_def}")


def _load_json_file(conn: sqlite3.Connection, path: Path) -> None:
    with open(path) as f:
        data = json.load(f)

    items = data if isinstance(data, list) else data.get("items", [])

    with conn:
        for item in items:
            _upsert_item(conn, item)


_DEFAULT_STOCK = 100
_DEFAULT_LOW_STOCK_THRESHOLD = 10


def _upsert_item(conn: sqlite3.Connection, item: dict) -> None:
    item_id = item["id"]

    conn.execute("""
        INSERT OR REPLACE INTO menu_items
            (id, name, category, description, price, available,
             options, modifiers, dietary_tags, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item_id,
        item["name"],
        item.get("category", ""),
        item.get("description", ""),
        item["price"],
        1 if item.get("available", True) else 0,
        json.dumps(item.get("options", {})),
        json.dumps(item.get("modifiers", [])),
        json.dumps(item.get("dietary_tags", [])),
        json.dumps(item.get("tags", [])),
    ))

    initial_qty = 0 if not item.get("available", True) else _DEFAULT_STOCK
    conn.execute("""
        INSERT OR IGNORE INTO inventory (item_id, quantity, low_stock_threshold)
        VALUES (?, ?, ?)
    """, (item_id, initial_qty, _DEFAULT_LOW_STOCK_THRESHOLD))

    conn.execute("DELETE FROM menu_fts WHERE item_id = ?", (item_id,))
    conn.execute("""
        INSERT INTO menu_fts (item_id, name, description, category, dietary_tags_text, tags_text)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        item_id,
        item["name"],
        item.get("description", ""),
        item.get("category", ""),
        " ".join(item.get("dietary_tags", [])),
        " ".join(item.get("tags", [])),
    ))


def get_item_by_id(item_id: str) -> dict | None:
    init_db()
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT m.*, i.quantity AS stock_quantity
            FROM menu_items m
            LEFT JOIN inventory i ON i.item_id = m.id
            WHERE m.id = ?
        """, (item_id,)).fetchone()

    if not row:
        return None

    item = _row_to_dict(row)
    # Inventory is the source of truth for availability
    stock_qty = item.pop("stock_quantity", None)
    if stock_qty is not None:
        item["available"] = stock_qty > 0
    return item


def search_items_fts(query: str, limit: int = 20) -> list[dict]:
    """
    BM25-ranked FTS5 search.
    Canonical items (from the real menu) are always sorted before synthetic variants.
    This ensures 'birria taco' → Birria Taco, not Birria Street-Style Taco.
    """
    init_db()
    clean = _sanitize_fts(query)
    if not clean:
        return []

    with _get_conn() as conn:
        try:
            # Weight columns: item_id=0, name=10, description=1, category=2, tags=1, tags=1
            # Heavy name weight means "chips and guac" → Chips & Guacamole beats
            # Loaded Nachos (which has "chips" and "guac" only in its description).
            rows = conn.execute("""
                SELECT item_id, bm25(menu_fts, 0.0, 10.0, 1.0, 2.0, 1.0, 1.0) as rank
                FROM menu_fts
                WHERE menu_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (clean, limit)).fetchall()
        except sqlite3.OperationalError:
            return []

    if not rows:
        tokens = clean.split()
        if len(tokens) > 1:
            return search_items_fts(" OR ".join(tokens), limit)
        return []

    ids_ranked = [r["item_id"] for r in rows]
    rank_map = {r["item_id"]: i for i, r in enumerate(rows)}

    items = _fetch_items_by_ids(ids_ranked)
    # Canonical items first, then by FTS BM25 rank within each group
    items.sort(key=lambda x: rank_map.get(x["id"], 999))
    return items


def _fetch_items_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    with _get_conn() as conn:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM menu_items WHERE id IN ({placeholders})", ids
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["options"] = json.loads(item["options"])
    item["modifiers"] = json.loads(item["modifiers"])
    item["dietary_tags"] = json.loads(item["dietary_tags"])
    item["tags"] = json.loads(item["tags"])
    item["available"] = bool(item["available"])
    return item


def get_inventory() -> list[dict]:
    """Return all items with live inventory levels, ordered by category then name."""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT m.id, m.name, m.category, m.price,
                   COALESCE(i.quantity, 0)            AS quantity,
                   COALESCE(i.low_stock_threshold, 10) AS low_stock_threshold,
                   i.updated_at
            FROM menu_items m
            LEFT JOIN inventory i ON i.item_id = m.id
            ORDER BY m.category, m.name
        """).fetchall()
    return [dict(r) for r in rows]


def decrement_inventory(item_id: str, quantity: int, order_id: str = "") -> None:
    """Atomically reduce stock by quantity (clips to 0). Logs the change.

    Idempotent when order_id is provided: if the (item_id, order_id, 'order') pair
    is already in inventory_log, the call is a no-op. Protects against double
    decrements on Streamlit reruns or retried checkouts.
    """
    init_db()
    with _get_conn() as conn:
        if order_id:
            already = conn.execute("""
                SELECT 1 FROM inventory_log
                WHERE item_id = ? AND order_id = ? AND reason = 'order'
                LIMIT 1
            """, (item_id, order_id)).fetchone()
            if already:
                return
        conn.execute("""
            UPDATE inventory
            SET quantity   = MAX(0, quantity - ?),
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE item_id = ?
        """, (quantity, item_id))
        conn.execute("""
            INSERT INTO inventory_log (item_id, delta, reason, order_id)
            VALUES (?, ?, 'order', ?)
        """, (item_id, -quantity, order_id or None))


def order_exists(order_id: str) -> bool:
    """Check whether an order has already been persisted. Used for idempotent finalization."""
    if not order_id:
        return False
    init_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM orders WHERE order_id = ? LIMIT 1", (order_id,)
        ).fetchone()
    return row is not None


def restock_item(item_id: str, quantity: int, reason: str = "restock") -> None:
    """Add stock to an item and log the change."""
    init_db()
    with _get_conn() as conn:
        conn.execute("""
            UPDATE inventory
            SET quantity   = quantity + ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE item_id = ?
        """, (quantity, item_id))
        conn.execute("""
            INSERT INTO inventory_log (item_id, delta, reason)
            VALUES (?, ?, ?)
        """, (item_id, quantity, reason))


def get_inventory_log(limit: int = 50) -> list[dict]:
    """Return the most recent inventory changes across all items."""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT l.*, m.name AS item_name
            FROM inventory_log l
            JOIN menu_items m ON m.id = l.item_id
            ORDER BY l.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_order(
    order_id: str,
    subtotal: float,
    items: list,
    special_instructions: str = "",
    conversation_turns: int = 1,
    delivery_fee: float = 0.0,
    tax: float = 0.0,
    total: float = 0.0,
    fulfillment_type: str = "pickup",
    email: str = "",
    phone: str = "",
) -> None:
    init_db()
    with _get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO orders
                (order_id, subtotal, delivery_fee, tax, total,
                 items_json, special_instructions, fulfillment_type,
                 email, phone, conversation_turns)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order_id, subtotal, delivery_fee, tax,
            total or round(subtotal + delivery_fee + tax, 2),
            json.dumps(items), special_instructions,
            fulfillment_type, email, phone, conversation_turns,
        ))


def save_payment(
    order_id: str,
    stripe_payment_id: str,
    amount_cents: int,
    status: str,
    fulfillment_type: str = "pickup",
    currency: str = "usd",
) -> None:
    init_db()
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO payments
                (order_id, stripe_payment_id, amount_cents, currency, status, fulfillment_type)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (order_id, stripe_payment_id, amount_cents, currency, status, fulfillment_type))


def get_all_item_ids() -> set[str]:
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("SELECT id FROM menu_items").fetchall()
    return {r["id"] for r in rows}


def get_all_items() -> list[dict]:
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM menu_items ORDER BY category, name"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _sanitize_fts(query: str) -> str:
    """Remove FTS5 special characters to prevent query syntax errors."""
    import re
    cleaned = re.sub(r'["\'\*\(\)\-\:\^~\+]', ' ', query)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned
