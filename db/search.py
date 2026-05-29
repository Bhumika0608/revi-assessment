"""
Hybrid search pipeline: semantic (dense) + FTS5 BM25 (sparse) + fuzzy.
Merges results via Reciprocal Rank Fusion (RRF).

Also handles FAQ search and natural-language price/dietary filter parsing.

Embedding index (BAAI/bge-small-en-v1.5 via fastembed):
  - Built lazily on first semantic search call.
  - Stored in SQLite as BLOBs so it survives restarts without recomputing.
  - Loaded into a numpy matrix for fast cosine similarity (dot product on
    pre-normalized vectors).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from db.restaurant import PHONE

_ROOT = Path(__file__).parent.parent
_FAQ_PATH = _ROOT / "data" / "faq.json"

# ── Price / dietary parsing ───────────────────────────────────────────────────

_PRICE_PATTERNS = [
    r"under\s+\$?(\d+(?:\.\d+)?)",
    r"less\s+than\s+\$?(\d+(?:\.\d+)?)",
    r"below\s+\$?(\d+(?:\.\d+)?)",
    r"\$?(\d+(?:\.\d+)?)\s+or\s+(?:under|less)",
    r"(?:max(?:imum)?|budget(?:\s+of)?)\s+\$?(\d+(?:\.\d+)?)",
    r"no\s+more\s+than\s+\$?(\d+(?:\.\d+)?)",
    r"cheap(?:er)?\s+than\s+\$?(\d+(?:\.\d+)?)",
    r"around\s+\$?(\d+(?:\.\d+)?)",
]

_DIETARY_MAP = {
    "vegan":        ["vegan", "plant-based", "plant based", "no animal", "animal free"],
    "vegetarian":   ["vegetarian", "no meat", "meatless", "meat free", "meat-free"],
    "gluten-free":  ["gluten free", "gluten-free", "no gluten", "celiac", "coeliac"],
    # "X-free" tags are NEGATIVE filters — items WITHOUT the "contains_X" tag.
    # Handled by items_matching_dietary below.
    "dairy-free":   ["dairy free", "dairy-free", "no dairy", "lactose intolerant",
                     "lactose-free", "without dairy", "non-dairy", "non dairy"],
    "shellfish":    ["shellfish", "seafood"],
    "chicken":      ["chicken", "pollo"],
    "beef":         ["beef", "carne", "steak"],
    "pork":         ["pork", "carnitas", "al pastor"],
}


def parse_price_constraint(query: str) -> float | None:
    for pattern in _PRICE_PATTERNS:
        m = re.search(pattern, query, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def parse_dietary_filter(query: str) -> str | None:
    q = query.lower()
    for tag, keywords in _DIETARY_MAP.items():
        if any(kw in q for kw in keywords):
            return tag
    return None


def items_matching_dietary(items: list[dict], dietary_tag: str) -> list[dict]:
    """Filter items by a dietary tag.

    Positive tags ("vegan", "gluten-free", "chicken", ...) match items where
    the tag is present in dietary_tags. Negative tags ending in "-free"
    (e.g. "dairy-free") match items WITHOUT the corresponding "contains_X"
    tag — the menu uses "contains_dairy" / "contains_gluten" inclusion markers
    rather than "dairy-free" exclusion markers.
    """
    if dietary_tag.endswith("-free") and dietary_tag != "gluten-free":
        # Negative filter — "dairy-free" → items without "contains_dairy".
        contains_tag = "contains_" + dietary_tag[:-len("-free")]
        return [it for it in items if contains_tag not in it.get("dietary_tags", [])]
    if dietary_tag == "gluten-free":
        # The menu uses "contains_gluten" inclusion markers too.
        return [it for it in items if "contains_gluten" not in it.get("dietary_tags", [])]
    return [it for it in items if dietary_tag in it.get("dietary_tags", [])]


# ── Embedding index ───────────────────────────────────────────────────────────

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_INDEX_LOCK = threading.Lock()

_embed_model = None
_item_ids: list[str] = []
_item_lookup: dict[str, dict] = {}
_embed_matrix: np.ndarray | None = None   # shape (N, D), L2-normalised


# BGE-small-en-v1.5 produces 384-dim vectors. Pinned here so the matmul/index
# loaders can validate shape — silently writing the wrong width once poisoned
# the on-disk index in production (everything downstream blew up with a shape
# mismatch on first search). Better to assert the contract.
_EMBED_DIM = 384


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        try:
            from fastembed import TextEmbedding
            _embed_model = TextEmbedding(_EMBED_MODEL)
        except Exception:
            logger = __import__("logging").getLogger(__name__)
            logger.exception("Failed to load fastembed model %s", _EMBED_MODEL)
            _embed_model = None
    return _embed_model


def _embed_texts(texts: list[str]) -> np.ndarray:
    """Compute L2-normalised embeddings for `texts`. Raises RuntimeError when
    fastembed is unavailable — callers should check `_get_embed_model()` first
    and handle the missing-model case rather than letting zero-shape garbage
    flow into the on-disk index or the matmul kernel."""
    model = _get_embed_model()
    if model is None:
        raise RuntimeError(
            "Embedding model unavailable (fastembed failed to load). "
            "Cannot embed query/items; the search caller must degrade to "
            "FTS + fuzzy without semantic ranking."
        )
    vecs = np.array(list(model.embed(texts, batch_size=256)), dtype=np.float32)
    if vecs.ndim != 2 or vecs.shape[1] != _EMBED_DIM:
        raise RuntimeError(
            f"Embedding model returned vectors of unexpected shape {vecs.shape} "
            f"(expected (*, {_EMBED_DIM})). Refusing to persist or rank."
        )
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


def _db_path() -> str:
    from db.setup import DB_PATH
    return DB_PATH


def _ensure_embeddings_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_embeddings (
            item_id   TEXT PRIMARY KEY,
            embedding BLOB NOT NULL
        )
    """)


def build_embedding_index(force: bool = False) -> int:
    """
    Compute and persist embeddings for every menu item.
    Skips items already stored unless force=True.
    Returns the number of items embedded.

    Short-circuits to 0 if fastembed isn't available — refusing to write
    zero/garbage vectors. Without this, a one-time fastembed failure
    permanently poisons the on-disk index until the DB is rebuilt.
    """
    from db.setup import get_all_items
    items = get_all_items()
    if not items:
        return 0

    if _get_embed_model() is None:
        return 0

    conn = sqlite3.connect(_db_path())
    _ensure_embeddings_table(conn)

    if not force:
        existing = {r[0] for r in conn.execute("SELECT item_id FROM menu_embeddings")}
        to_embed = [it for it in items if it["id"] not in existing]
    else:
        conn.execute("DELETE FROM menu_embeddings")
        to_embed = items

    if not to_embed:
        conn.close()
        return 0

    texts = [f"{it['name']}. {it['description']}" for it in to_embed]
    try:
        vecs = _embed_texts(texts)
    except RuntimeError:
        conn.close()
        return 0

    with conn:
        for it, vec in zip(to_embed, vecs):
            conn.execute(
                "INSERT OR REPLACE INTO menu_embeddings (item_id, embedding) VALUES (?, ?)",
                (it["id"], vec.astype(np.float32).tobytes()),
            )
    conn.close()
    return len(to_embed)


def _load_index_from_db() -> bool:
    """Load pre-computed embeddings from SQLite into memory. Returns True on success.

    Validates that every stored vector has the expected dimension. If even one
    row is the wrong width (typically a previously-poisoned (N, 1) write from
    a fastembed failure), the index is discarded — caller will rebuild it.
    """
    global _item_ids, _item_lookup, _embed_matrix

    from db.setup import get_all_items
    items = get_all_items()
    if not items:
        return False

    conn = sqlite3.connect(_db_path())
    _ensure_embeddings_table(conn)
    rows = conn.execute("SELECT item_id, embedding FROM menu_embeddings").fetchall()
    conn.close()

    if not rows:
        return False

    item_by_id = {it["id"]: it for it in items}
    ids, vecs = [], []
    for item_id, blob in rows:
        if item_id not in item_by_id:
            continue
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape != (_EMBED_DIM,):
            # Poisoned row from a prior fastembed-failed write — discard the
            # whole index, force a clean rebuild, and clear the table so the
            # rebuild starts from scratch.
            _purge_embeddings_table()
            return False
        ids.append(item_id)
        vecs.append(vec)

    if not ids:
        return False

    _item_ids = ids
    _item_lookup = {it["id"]: it for it in items}
    _embed_matrix = np.stack(vecs)
    # Re-normalise in case of floating-point drift
    norms = np.linalg.norm(_embed_matrix, axis=1, keepdims=True)
    _embed_matrix = _embed_matrix / np.maximum(norms, 1e-9)
    return True


def _purge_embeddings_table() -> None:
    conn = sqlite3.connect(_db_path())
    _ensure_embeddings_table(conn)
    with conn:
        conn.execute("DELETE FROM menu_embeddings")
    conn.close()


def _ensure_index() -> bool:
    """Return True when the in-memory index is ready."""
    global _embed_matrix
    if _embed_matrix is not None and len(_item_ids) > 0:
        return True

    with _INDEX_LOCK:
        if _embed_matrix is not None and len(_item_ids) > 0:
            return True
        if _load_index_from_db():
            return True
        # Not in DB yet — build and persist
        build_embedding_index()
        return _load_index_from_db()


# ── Semantic search ───────────────────────────────────────────────────────────

def semantic_search(query: str, top_k: int = 20) -> list[dict]:
    """Return items ranked by cosine similarity to query embedding."""
    if not _ensure_index():
        return []
    model = _get_embed_model()
    if model is None:
        return []

    q_vec = _embed_texts([query])[0]
    scores = _embed_matrix @ q_vec                      # cosine similarity
    top_idx = np.argsort(-scores)[:top_k]
    return [_item_lookup[_item_ids[i]] for i in top_idx if _item_ids[i] in _item_lookup]


# ── Reciprocal Rank Fusion ────────────────────────────────────────────────────

def reciprocal_rank_fusion(result_lists: list[list[dict]], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    seen: dict[str, dict] = {}
    for lst in result_lists:
        for rank, item in enumerate(lst):
            iid = item["id"]
            scores[iid] = scores.get(iid, 0.0) + 1.0 / (k + rank + 1)
            seen[iid] = item
    return [seen[iid] for iid in sorted(scores, key=lambda x: -scores[x])]


# ── Full hybrid pipeline (called from agent/tools.py) ─────────────────────────

def hybrid_search(
    query: str,
    fts_results: list[dict],
    fuzzy_results: list[dict],
    top_k: int = 20,
) -> tuple[list[dict], float | None, str | None]:
    """
    Merge semantic + FTS + fuzzy results via RRF.
    Also extracts price / dietary constraints from the query.

    Returns:
        (merged_items, max_price, dietary_filter)
    """
    max_price = parse_price_constraint(query)
    dietary = parse_dietary_filter(query)

    sem = semantic_search(query, top_k=40)
    merged = reciprocal_rank_fusion([sem, fts_results, fuzzy_results])

    if max_price is not None:
        merged = [it for it in merged if it.get("price", 9999) <= max_price]

    if dietary:
        merged = items_matching_dietary(merged, dietary)

    return merged[:top_k], max_price, dietary


# ── FAQ search ────────────────────────────────────────────────────────────────

_faq_entries: list[dict] | None = None
_faq_embed_matrix: np.ndarray | None = None
_faq_ids: list[str] = []


def _load_faq() -> list[dict]:
    global _faq_entries
    if _faq_entries is None:
        with open(_FAQ_PATH) as f:
            raw = json.load(f)
        # Substitute {phone} placeholders so FAQ answers always show the
        # current restaurant phone (single source of truth in db/restaurant.py).
        for entry in raw:
            ans = entry.get("answer")
            if isinstance(ans, str) and "{phone}" in ans:
                entry["answer"] = ans.replace("{phone}", PHONE)
        _faq_entries = raw
    return _faq_entries


def _ensure_faq_index() -> None:
    global _faq_embed_matrix, _faq_ids
    if _faq_embed_matrix is not None:
        return
    entries = _load_faq()
    texts = [f"{e['question']} {' '.join(e.get('tags', []))}" for e in entries]
    _faq_ids = [e["id"] for e in entries]
    _faq_embed_matrix = _embed_texts(texts)


_canonical_ids_cache: set[str] | None = None

def _canonical_item_ids() -> set[str]:
    """IDs from data/menu.json — the curated 29-item menu, distinguished from
    the 10k synthetic catalog in menu_expanded.json. Used by _dietary_answer
    to prefer real menu items over generated variants. Cached after first
    read; the file is shipped, not user-mutable at runtime."""
    global _canonical_ids_cache
    if _canonical_ids_cache is None:
        try:
            from pathlib import Path
            canonical_path = Path(__file__).parent.parent / "data" / "menu.json"
            data = json.loads(canonical_path.read_text())
            _canonical_ids_cache = {it["id"] for it in data.get("items", [])}
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            _canonical_ids_cache = set()
    return _canonical_ids_cache


def _dietary_answer(dietary_tag: str, display_label: str) -> str:
    """Query the DB for items matching a dietary tag and build a natural
    answer. Prefers canonical menu items (data/menu.json) over the 10k
    synthetic catalog so the customer sees recognizable names like
    "Veggie Taco" rather than "Hongos Antojito Gourmet"; falls back to
    a tight category summary when the canonical menu has nothing matching."""
    from db.setup import get_all_items
    all_items = get_all_items()

    matches = [
        it for it in items_matching_dietary(all_items, dietary_tag)
        if it.get("available", True)
    ]
    if not matches:
        return f"Sorry, we don't currently have any {display_label} items available."

    canonical_ids = _canonical_item_ids()
    canonical = [it for it in matches if it["id"] in canonical_ids]
    total = len(matches)

    # Common case: canonical menu has matches — show all of them by name.
    # The canonical menu is ~29 items so even "vegan" maxes out at a handful.
    # Lines join with "\n\n" (blank line between) because Streamlit's markdown
    # renderer collapses single newlines into a single space — a hard line
    # break needs either two trailing spaces or a blank-line separator. Blank
    # lines are more visually scannable for a bullet list.
    if canonical:
        names = [it["name"] for it in canonical]
        lines = [f"We've got {len(canonical)} {display_label} items on our menu:"]
        for name in names:
            lines.append(f"- {name}")
        extra = total - len(canonical)
        if extra > 0:
            lines.append(
                f"...plus {extra:,} more variations across our extended menu. "
                f"Want me to look up anything specific (tacos, bowls, drinks)?"
            )
        else:
            lines.append("Anything sound good?")
        return "\n\n".join(lines)

    # Fallback: nothing canonical matched — give a tight category summary
    # capped at 5 categories × 3 items so the message stays scannable.
    cats: dict[str, list[str]] = {}
    for it in matches:
        cats.setdefault(it["category"], []).append(it["name"])

    top_cats = sorted(cats.items(), key=lambda kv: -len(kv[1]))[:5]
    lines = [
        f"We have {total:,} {display_label} options across {len(cats)} categories. "
        f"Top categories:"
    ]
    for cat, names in top_cats:
        sample = names[:3]
        more = len(names) - len(sample)
        entry = ", ".join(sample)
        if more:
            entry += f" (+{more} more)"
        lines.append(f"- {cat.title()}: {entry}")
    lines.append("What are you in the mood for? I can narrow it down.")
    return "\n\n".join(lines)


def search_faq(query: str) -> dict:
    """
    Search FAQ entries by semantic similarity.
    For dietary-type entries, queries live menu data.

    Returns:
        {
            "found": bool,
            "answer": str,
            "question": str,   # matched FAQ question
            "confidence": str  # "high" | "medium" | "low"
        }
    """
    entries = _load_faq()
    _ensure_faq_index()

    if _faq_embed_matrix is None or len(_faq_ids) == 0:
        return {"found": False, "answer": "I don't have that information right now.", "question": "", "confidence": "low"}

    model = _get_embed_model()
    if model is None:
        # Fallback: keyword tag matching
        return _faq_keyword_search(query, entries)

    q_vec = _embed_texts([query])[0]
    scores = _faq_embed_matrix @ q_vec
    best_idx = int(np.argmax(scores))
    best_score = float(scores[best_idx])

    if best_score < 0.30:
        return {"found": False, "answer": f"I don't have information about that. You can call us at {PHONE} for help.", "question": "", "confidence": "low"}

    entry = entries[best_idx]
    confidence = "high" if best_score >= 0.55 else "medium"

    if entry.get("type") == "dietary_query":
        tag = entry["dietary_tag"]
        label_map = {
            "vegan": "vegan",
            "vegetarian": "vegetarian",
            "gluten-free": "gluten-free",
            "dairy-free": "dairy-free",
        }
        answer = _dietary_answer(tag, label_map.get(tag, tag))
    else:
        answer = entry["answer"]

    return {
        "found": True,
        "answer": answer,
        "question": entry["question"],
        "confidence": confidence,
    }


def _faq_keyword_search(query: str, entries: list[dict]) -> dict:
    """Fallback FAQ search using tag overlap when embeddings aren't available."""
    q_words = set(re.sub(r"[^\w\s]", "", query.lower()).split())
    best_entry, best_score = None, 0
    for entry in entries:
        tags = set(t.lower() for t in entry.get("tags", []))
        score = len(q_words & tags)
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_score == 0 or best_entry is None:
        return {"found": False, "answer": f"I don't have information about that. Call us at {PHONE}.", "question": "", "confidence": "low"}

    if best_entry.get("type") == "dietary_query":
        answer = _dietary_answer(best_entry["dietary_tag"], best_entry["dietary_tag"])
    else:
        answer = best_entry["answer"]

    return {"found": True, "answer": answer, "question": best_entry["question"], "confidence": "medium"}
