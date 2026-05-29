"""
Tool implementations for the ordering agent.

search_menu      — Hybrid (semantic + FTS5 + fuzzy) search with Python-side disambiguation
get_item_details — direct DB lookup, validates item before it can be added to cart
add_to_cart      — adds validated item to the in-memory cart (price always from DB)
remove_from_cart — removes item from cart by item_id
get_cart         — returns current cart contents and subtotal
signal_checkout  — marks cart as ready for the deterministic checkout flow
search_faq       — semantic search over restaurant FAQ + live menu dietary queries

place_order is kept for backward-compat with unit tests but is no longer an agent tool.
"""

from __future__ import annotations

import re
import uuid

from rapidfuzz import fuzz, process

from db.setup import (
    decrement_inventory,
    get_all_items,
    get_item_by_id,
    init_db,
    search_items_fts,
)
from db.search import hybrid_search, search_faq as _search_faq_impl

_all_items_cache: list[dict] | None = None
_canonical_ids_cache: set[str] | None = None


def _get_all_items_cached() -> list[dict]:
    global _all_items_cache
    if _all_items_cache is None:
        _all_items_cache = get_all_items()
    return _all_items_cache


def _get_canonical_ids() -> set[str]:
    """Item IDs from the canonical menu (data/menu.json), distinguished from the
    10k synthetic catalog (data/menu_expanded.json). Used to break ties in the
    clear-winner search fast-path: when canonical and synthetic items score
    similarly for a query, the canonical wins. Loaded once and cached."""
    global _canonical_ids_cache
    if _canonical_ids_cache is None:
        import json
        from pathlib import Path
        canonical_path = Path(__file__).parent.parent / "data" / "menu.json"
        try:
            data = json.loads(canonical_path.read_text())
            _canonical_ids_cache = {it["id"] for it in data.get("items", [])}
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            _canonical_ids_cache = set()
    return _canonical_ids_cache


# Clear-winner thresholds used by search_menu.
#   _CLEAR_WINNER_MIN_SCORE — the top fuzzy match must be at least this strong
#     in absolute terms (out of 100). 85 admits plural forms ("birria tacos" vs
#     "Birria Taco"), capitalization variants, and minor wording noise; rejects
#     loosely-related items.
#   _CLEAR_WINNER_MIN_GAP — gap to the runner-up. 15 ensures the top match is
#     decisively ahead of the next-best candidate; a tied or close-second
#     result falls through to descriptor matching or ambiguous handling.
_CLEAR_WINNER_MIN_SCORE = 85
_CLEAR_WINNER_MIN_GAP   = 15

# Fields included in search_menu results sent to the LLM. Deliberately slim:
# description / dietary_tags / tags were dropped because the LLM doesn't need
# them to decide what to add — it picks an item_id and the cart layer fetches
# whatever else it needs from the DB. Each search result lands in the
# conversation history and lives there for the rest of the order, so trimming
# unused fields is a direct cost win at multi-turn scale.
_SEARCH_RESULT_FIELDS = {"id", "name", "category", "price", "available"}

_FOOD_TYPE_WORDS = {
    "taco", "tacos", "burrito", "burritos", "bowl", "bowls",
    "quesadilla", "quesadillas", "nachos", "nacho", "torta", "tortas",
    "chips", "flan", "agua", "horchata", "jarritos", "coke", "cola",
}

_STOP_WORDS = {
    "a", "an", "the", "i", "me", "my", "please", "want", "get",
    "have", "some", "can", "could", "would", "like", "order", "give",
    "and", "with", "no", "without", "extra", "add", "one", "two",
    "three", "just", "is", "for",
}


def _clean_words(text: str) -> set[str]:
    return {re.sub(r"[^\w]", "", w).lower() for w in text.split()}


def _extract_food_type(query: str) -> str | None:
    for word in _clean_words(query):
        if word in _FOOD_TYPE_WORDS:
            return word
        match = process.extractOne(word, _FOOD_TYPE_WORDS, scorer=fuzz.ratio)
        if match and match[1] >= 75:
            return match[0]
    return None


def _filter_by_food_type(items: list[dict], food_type: str) -> list[dict]:
    stem = food_type.rstrip("s")

    def _matches(item: dict) -> bool:
        name = item["name"].lower()
        if stem in name or food_type in name:
            return True
        return any(stem in tag.lower() or food_type in tag.lower() for tag in item.get("tags", []))

    filtered = [item for item in items if _matches(item)]
    return filtered if filtered else items


def _find_descriptor_match(query: str, items: list[dict]) -> dict | None:
    words = _clean_words(query) - _STOP_WORDS - _FOOD_TYPE_WORDS
    words -= {w + "s" for w in _FOOD_TYPE_WORDS}
    for word in words:
        if len(word) < 3:
            continue
        hits = [
            item for item in items
            if word in item["name"].lower()
            or any(word in tag.lower() for tag in item.get("tags", []))
        ]
        if len(hits) == 1:
            return hits[0]
    return None


def search_menu(query: str) -> dict:
    """
    Hybrid search: semantic (dense) + FTS5 BM25 (sparse) + rapidfuzz, merged via RRF.

    Return shape:
        {
            "match":           "exact" | "ambiguous" | "none",
            "items":           list[dict],
            "top_item":        dict | None,
            "filters_applied": {"max_price": float|None, "dietary": str|None}
        }
    """
    init_db()

    if not query or not query.strip():
        return {"match": "none", "items": [], "top_item": None, "filters_applied": {}}

    query = query.strip()
    all_items = _get_all_items_cached()
    names = [item["name"] for item in all_items]
    fuzzy_matches = process.extract(query, names, scorer=fuzz.token_sort_ratio, limit=15)

    tier1, tier1_ids = [], set()
    for _name, score, idx in fuzzy_matches:
        item = all_items[idx]
        if score >= 55:
            tier1.append(item)
            tier1_ids.add(item["id"])

    # Always include canonical items that score above threshold — even if they
    # got squeezed out of the top-15 by synthetic variants. Without this, a
    # query like "dame dos tacos de carne asada" against the 10k catalog can
    # have so many synthetic "Carne Asada X Taco" variants competing that the
    # canonical "Carne Asada Taco" itself ranks 20+ and never makes it into
    # tier1. The 29 canonical items are cheap to score directly.
    canonical_ids_set = _get_canonical_ids()
    canonical_items = [it for it in all_items if it["id"] in canonical_ids_set]
    for it in canonical_items:
        if it["id"] in tier1_ids:
            continue
        score = max(
            fuzz.token_sort_ratio(query, it["name"]),
            fuzz.partial_ratio(query, it["name"]),
        )
        if score >= 55:
            tier1.append(it)
            tier1_ids.add(it["id"])

    fts_results = search_items_fts(query, limit=7)
    tier2 = [r for r in fts_results if r["id"] not in tier1_ids]
    tier2_ids = tier1_ids | {r["id"] for r in tier2}

    tier3 = []
    for _name, score, idx in fuzzy_matches:
        item = all_items[idx]
        if score >= 45 and item["id"] not in tier2_ids:
            tier3.append(item)
            tier2_ids.add(item["id"])

    merged, max_price, dietary = hybrid_search(
        query=query, fts_results=tier2, fuzzy_results=tier3, top_k=30,
    )
    tier1_ids_set = {it["id"] for it in tier1}
    merged = tier1 + [it for it in merged if it["id"] not in tier1_ids_set]

    filters_applied = {"max_price": max_price, "dietary": dietary}

    if not merged:
        return {"match": "none", "items": [], "top_item": None, "filters_applied": filters_applied}

    def _fmt(item: dict) -> dict:
        return {k: v for k, v in item.items() if k in _SEARCH_RESULT_FIELDS}

    if len(merged) == 1:
        top = _fmt(merged[0])
        return {"match": "exact", "items": [top], "top_item": top, "filters_applied": filters_applied}

    # Clear-winner fast-path — three independent rules, any one fires exact:
    #
    #   Rule A — Strong score advantage. Top score >= 85 AND gap to runner-up
    #     >= 15. The original discipline; catches "birria taco" → canonical
    #     "Birria Taco" beating unrelated items.
    #
    #   Rule B — Same score, top is clearly shorter. Top score >= 85, score
    #     gap < 5, AND runner-up's name is >= 5 characters longer. Handles
    #     "birria taco" matching canonical "Birria Taco" (11 chars) AND
    #     synthetic "Birria Taco Salad" (17 chars) — both score 100 via
    #     partial_ratio; the shorter is canonical.
    #
    #   Rule C — Canonical beats synthetic at near-equal score. Top is in the
    #     canonical menu (data/menu.json), runner-up is a synthetic variant,
    #     top score >= 80, score difference within ±2 points. Handles "chips
    #     and guac" → canonical "Chips & Guacamole" winning over synthetic
    #     "Kids Chips & Guac" at the same fuzzy score, and "dame dos tacos de
    #     carne asada" → canonical "Carne Asada Taco" winning over synthetic
    #     "Carne Asada Norteño Taco". When the query doesn't qualify the item
    #     (no "kids" or "norteño" mentioned), default to canonical intent.
    #
    # Score = max(token_sort_ratio, partial_ratio). token_set_ratio is NOT
    # used — it scores 100 for any token-superset match, ties canonical with
    # synthetic, and breaks the gap discipline.
    canonical_ids = _get_canonical_ids()
    query_lower = query.lower()
    def _best_score(name: str) -> float:
        n = name.lower()
        return max(
            fuzz.token_sort_ratio(query_lower, n),
            fuzz.partial_ratio(query_lower, n),
        )
    # Sort by (score DESC, canonical-first, length ASC).
    scored = sorted(
        (
            (_best_score(it["name"]),
             0 if it["id"] in canonical_ids else 1,
             len(it["name"]),
             it)
            for it in merged
        ),
        key=lambda x: (-x[0], x[1], x[2]),
    )
    top_score, top_is_synth, top_len, top_item = scored[0]
    if len(scored) >= 2:
        runner_up_score, runner_up_is_synth, runner_up_len, _ = scored[1]
    else:
        runner_up_score, runner_up_is_synth, runner_up_len = 0, 1, 0
    score_gap  = top_score - runner_up_score
    length_gap = runner_up_len - top_len

    rule_a = top_score >= _CLEAR_WINNER_MIN_SCORE and score_gap >= _CLEAR_WINNER_MIN_GAP
    rule_b = top_score >= _CLEAR_WINNER_MIN_SCORE and score_gap < 5 and length_gap >= 5
    rule_c = (top_is_synth == 0
              and runner_up_is_synth == 1
              and top_score >= 80
              and score_gap >= -2)
    if rule_a or rule_b or rule_c:
        top = _fmt(top_item)
        return {"match": "exact", "items": [top], "top_item": top, "filters_applied": filters_applied}

    food_type = _extract_food_type(query)
    candidates = _filter_by_food_type(merged, food_type) if food_type else merged

    if len(candidates) == 1:
        top = _fmt(candidates[0])
        return {"match": "exact", "items": [top], "top_item": top, "filters_applied": filters_applied}

    descriptor_hit = _find_descriptor_match(query, candidates)
    if descriptor_hit:
        top = _fmt(descriptor_hit)
        return {"match": "exact", "items": [top], "top_item": top, "filters_applied": filters_applied}

    # Cap ambiguous candidates at 5 — real customer disambiguation needs 3-5
    # options, more is overwhelming UX and bloats conversation history.
    return {
        "match": "ambiguous",
        "items": [_fmt(item) for item in candidates[:5]],
        "top_item": None,
        "filters_applied": filters_applied,
    }


def search_faq(query: str) -> dict:
    """Semantic FAQ search. Dietary questions also query the live menu DB."""
    init_db()
    return _search_faq_impl(query)


# Fields the LLM needs from get_item_details to decide what to add. dietary_tags
# and tags are search-internal metadata — the LLM picks an item_id and the cart
# layer fetches whatever else it needs from the DB.
_ITEM_DETAILS_FIELDS = {
    "id", "name", "category", "price", "available",
    "description",   # kept — used to answer "what's a birria taco?" type queries
    "modifiers",     # required — LLM needs valid modifier IDs + prices
    "options",       # required — LLM needs to know which options are required (e.g. protein)
}


def get_item_details(item_id: str) -> dict:
    """Return the LLM-facing details for a menu item: price, description,
    options, modifiers, availability. Internal fields (dietary_tags, tags) are
    stripped — they're search-pipeline metadata, not ordering inputs."""
    init_db()
    item = get_item_by_id(item_id)
    if item is None:
        return {
            "error": f"Item '{item_id}' not found on the menu.",
            "suggestion": "Use search_menu to find the correct item ID.",
        }
    return {k: v for k, v in item.items() if k in _ITEM_DETAILS_FIELDS}


# ── Cart context ──────────────────────────────────────────────────────────────

class _CartCtx:
    """Mutable cart injected into every take_order() call. Not thread-safe across sessions."""
    __slots__ = ("cart", "checkout_signaled")

    def __init__(self, cart: list[dict]) -> None:
        self.cart = list(cart)
        self.checkout_signaled = False


# ── Cart tools ────────────────────────────────────────────────────────────────

def add_to_cart(
    item_id: str,
    quantity: int,
    modifiers: list[str],
    cart_ctx: _CartCtx,
    options: dict | None = None,
) -> dict:
    """
    Add a validated item to the cart. Price is always fetched from DB — never from agent.
    Enforced at dispatch level: item_id must first pass through get_item_details.

    Modifier upcharges (e.g. add_guac $1.50, extra_meat $3.00) are looked up from the
    menu item's modifier definitions. Unknown modifier IDs return an error so the agent
    can self-correct — silently dropping them would let the customer pay $0 for a real
    $3 modifier when the LLM hallucinates an ID like 'add_extra_meat' (correct is
    'extra_meat').

    options is a dict of choice options like {"salsa": "hot", "tortilla": "corn"}.
    Unknown option keys or invalid choices return an error. Lines with the same
    item_id and modifiers but different options stay as separate cart lines, so
    "3 birria tacos with different salsas" creates 3 distinct lines.
    """
    from db.cart import add_item, cart_summary_text, get_subtotal

    item = get_item_by_id(item_id)
    if item is None:
        return {"error": f"Item '{item_id}' not found on the menu."}
    if not item.get("available", True):
        return {"error": f"{item['name']} is currently unavailable."}

    modifier_price_map = {m["id"]: float(m.get("price", 0.0)) for m in item.get("modifiers", [])}
    unknown = [m for m in modifiers if m not in modifier_price_map]
    if unknown:
        return {
            "error": (
                f"Unknown modifier(s) for {item['name']}: {unknown}. "
                f"Valid modifiers: {sorted(modifier_price_map.keys())}. "
                f"Re-check the modifier IDs returned by get_item_details."
            ),
        }
    modifier_upcharge  = round(sum(modifier_price_map[m_id] for m_id in modifiers), 2)

    # Validate options against the item's allowed options/choices.
    options = dict(options or {})
    item_options = item.get("options", {}) or {}
    for opt_key, opt_value in options.items():
        if opt_key not in item_options:
            return {
                "error": (
                    f"Unknown option '{opt_key}' for {item['name']}. "
                    f"Valid options: {sorted(item_options.keys())}."
                ),
            }
        choices = item_options[opt_key].get("choices") or []
        if choices and opt_value not in choices:
            return {
                "error": (
                    f"Invalid choice '{opt_value}' for option '{opt_key}' on {item['name']}. "
                    f"Valid choices: {choices}."
                ),
            }

    cart_ctx.cart, change = add_item(
        cart_ctx.cart,
        item_id=item["id"],
        name=item["name"],
        price=item["price"],
        quantity=quantity,
        modifiers=list(modifiers),
        modifier_upcharge=modifier_upcharge,
        options=options,
    )
    response = {
        "added":              item["name"],
        "quantity":           quantity,
        "modifiers":          list(modifiers),
        "options":            options,
        "modifier_upcharge":  modifier_upcharge,
        "line_total":         round(change["new_quantity"] * (item["price"] + modifier_upcharge), 2),
        "cart":               cart_summary_text(cart_ctx.cart),
        "subtotal":           get_subtotal(cart_ctx.cart),
        "new_line":           change["new_line"],
    }
    # Hint when we just incremented an existing line — the recurring failure
    # mode is the agent re-validating after a clarification turn and
    # accidentally re-adding an item that's already there. Tell the agent so
    # it can self-correct via set_item_quantity if the increment wasn't what
    # the customer asked for.
    if not change["new_line"]:
        response["already_in_cart"] = True
        response["previous_quantity"] = change["previous_quantity"]
        response["hint"] = (
            f"This line already existed at qty={change['previous_quantity']}; "
            f"it's now qty={change['new_quantity']}. If the customer didn't "
            f"explicitly ask for MORE of this item ('add another', 'one more'), "
            f"this was likely an unintentional re-add — revert with "
            f"set_item_quantity('{item['id']}', {change['previous_quantity']})."
        )
    return response


def _resolve_target_line(
    cart: list[dict], item_id: str, options: dict | None,
) -> tuple[int | None, dict | None]:
    """Pick the single cart line a line-targeting tool should operate on.

    Per-line options can create multiple cart entries with the same item_id
    (e.g. three birria tacos with three salsa choices). When that happens, the
    caller must supply `options` to disambiguate; otherwise we'd silently
    mutate the first line and corrupt one of the customer's other choices.

    Returns (index, None) on success, (None, error_dict) on failure.
    """
    matching = [(idx, e) for idx, e in enumerate(cart) if e["item_id"] == item_id]
    if not matching:
        return None, {
            "error": f"'{item_id}' is not in the cart. Use add_to_cart to add it first.",
        }
    if len(matching) == 1 and not options:
        return matching[0][0], None

    if options:
        opts = dict(options)
        filtered = [(idx, e) for idx, e in matching if (e.get("options") or {}) == opts]
        if len(filtered) == 1:
            return filtered[0][0], None
        if not filtered:
            return None, {
                "error": (
                    f"No cart line for '{item_id}' matches options={opts}. "
                    f"Current lines: "
                    + "; ".join(
                        f"#{idx} options={e.get('options') or {}}" for idx, e in matching
                    )
                ),
            }
        return None, {
            "error": (
                f"Multiple cart lines for '{item_id}' match options={opts} — "
                "this should not happen (add_to_cart dedupes on identical options). "
                f"Lines: " + "; ".join(
                    f"#{idx} mods={e.get('modifiers')}" for idx, e in filtered
                )
            ),
        }

    # Multiple lines exist and the caller didn't disambiguate.
    return None, {
        "error": (
            f"Multiple cart lines exist for '{item_id}' "
            f"(found {len(matching)}). Pass options=... to pick which one. "
            f"Current lines: " + "; ".join(
                f"#{idx} options={e.get('options') or {}}" for idx, e in matching
            )
        ),
    }


def remove_from_cart(item_id: str, cart_ctx: _CartCtx, options: dict | None = None) -> dict:
    """Remove a single cart line by item_id (and options, when multiple lines
    exist for the same item_id). When only one line matches, options is
    optional. When multiple lines match (e.g. three birria tacos with
    different salsas), options is required to pick one — otherwise the call
    returns an error listing the lines."""
    from db.cart import cart_summary_text

    line_idx, err = _resolve_target_line(cart_ctx.cart, item_id, options)
    if err is not None:
        return err

    removed = cart_ctx.cart[line_idx]
    cart_ctx.cart = [e for i, e in enumerate(cart_ctx.cart) if i != line_idx]
    return {
        "removed":  item_id,
        "options":  removed.get("options") or {},
        "cart":     cart_summary_text(cart_ctx.cart),
    }


def set_item_quantity(
    item_id: str,
    quantity: int,
    cart_ctx: _CartCtx,
    options: dict | None = None,
) -> dict:
    """Set the quantity on an existing cart line (REPLACE, not increment).

    Use this when the customer says "make it 2", "actually 3", "change to N",
    "I want N total". Calling add_to_cart with quantity=N would ADD N more on
    top of the existing quantity — the opposite of what the customer means.

    To increment (customer says "add another", "one more"), use add_to_cart
    instead — that keeps the additive semantics.

    When multiple cart lines share the same item_id (per-line options), pass
    `options` to pick which line to update; otherwise the call returns an
    error listing the lines.

    Returns:
        {"updated": name, "quantity": int, "line_total": float,
         "cart": summary, "subtotal": float}
      or
        {"error": "..."} if item not in cart, quantity < 1, item unknown,
        or multiple lines exist and options weren't provided.
    """
    from db.cart import cart_summary_text, get_subtotal

    if not isinstance(quantity, int) or quantity < 1:
        return {
            "error": (
                f"Quantity must be a positive integer (got {quantity!r}). "
                f"To remove the item entirely, use remove_from_cart."
            ),
        }

    line_idx, err = _resolve_target_line(cart_ctx.cart, item_id, options)
    if err is not None:
        return err

    item = get_item_by_id(item_id)
    if item is None:
        return {"error": f"Item '{item_id}' not found on the menu."}

    line = dict(cart_ctx.cart[line_idx])
    upcharge = float(line.get("modifier_upcharge", 0.0))
    line["quantity"]   = quantity
    line["line_total"] = round(quantity * (item["price"] + upcharge), 2)
    cart_ctx.cart[line_idx] = line

    return {
        "updated":    item["name"],
        "quantity":   quantity,
        "options":    line.get("options") or {},
        "line_total": line["line_total"],
        "cart":       cart_summary_text(cart_ctx.cart),
        "subtotal":   get_subtotal(cart_ctx.cart),
    }


def update_item_modifiers(
    item_id: str,
    modifiers: list[str],
    cart_ctx: _CartCtx,
    options: dict | None = None,
) -> dict:
    """Replace the modifier list on an existing cart line. Use this when a
    customer wants to add, change, or remove modifiers on something already in
    the cart — calling add_to_cart with new modifiers would create a duplicate
    line instead of updating the existing one.

    The `modifiers` argument must be the FULL new modifier list. To preserve
    existing modifiers, include them; to remove one, omit it from the list.
    Quantity and line_total are recomputed from the menu price + new upcharge.

    When multiple cart lines share the same item_id (per-line options), pass
    `options` to pick which line to update; otherwise the call returns an
    error listing the lines.

    Returns:
        {"updated": name, "modifiers": [...], "modifier_upcharge": float,
         "line_total": float, "cart": summary, "subtotal": float}
      or
        {"error": "..."} if the item isn't in the cart, doesn't exist, or
        multiple lines exist and options weren't provided.
    """
    from db.cart import cart_summary_text, get_subtotal

    line_idx, err = _resolve_target_line(cart_ctx.cart, item_id, options)
    if err is not None:
        return err

    item = get_item_by_id(item_id)
    if item is None:
        return {"error": f"Item '{item_id}' not found on the menu."}

    modifier_price_map = {m["id"]: float(m.get("price", 0.0)) for m in item.get("modifiers", [])}
    unknown = [m for m in modifiers if m not in modifier_price_map]
    if unknown:
        return {
            "error": (
                f"Unknown modifier(s) for {item['name']}: {unknown}. "
                f"Valid modifiers: {sorted(modifier_price_map.keys())}. "
                f"Re-check the modifier IDs returned by get_item_details."
            ),
        }
    upcharge = round(sum(modifier_price_map[m] for m in modifiers), 2)

    line = dict(cart_ctx.cart[line_idx])
    line["modifiers"]         = list(modifiers)
    line["modifier_upcharge"] = upcharge
    line["line_total"]        = round(line["quantity"] * (item["price"] + upcharge), 2)
    cart_ctx.cart[line_idx] = line

    return {
        "updated":           item["name"],
        "modifiers":         list(modifiers),
        "options":           line.get("options") or {},
        "modifier_upcharge": upcharge,
        "line_total":        line["line_total"],
        "cart":              cart_summary_text(cart_ctx.cart),
        "subtotal":          get_subtotal(cart_ctx.cart),
    }


def get_cart_contents(cart_ctx: _CartCtx) -> dict:
    from db.cart import get_subtotal, cart_summary_text
    return {
        "items":    cart_ctx.cart,
        "count":    sum(i["quantity"] for i in cart_ctx.cart),
        "subtotal": get_subtotal(cart_ctx.cart),
        "summary":  cart_summary_text(cart_ctx.cart),
    }


def signal_checkout(cart_ctx: _CartCtx) -> dict:
    from db.cart import get_subtotal
    if not cart_ctx.cart:
        return {"error": "Cart is empty. Please add items before checking out."}
    cart_ctx.checkout_signaled = True
    return {
        "ready":    True,
        "subtotal": get_subtotal(cart_ctx.cart),
        "message":  "Checkout initiated. The payment UI will now appear.",
    }


# ── place_order — kept for unit tests; NOT an agent tool ─────────────────────

def place_order(items: list[dict], special_instructions: str = "") -> dict:
    """
    Standalone order builder used by unit tests and the checkout finalization flow.
    Computes subtotal from DB prices + modifier prices, decrements inventory.
    """
    init_db()

    order_items = []
    subtotal = 0.0

    for entry in items:
        item_id  = entry.get("item_id", "")
        quantity = max(1, int(entry.get("quantity", 1)))
        applied_modifiers = entry.get("modifiers", []) or []

        details = get_item_by_id(item_id)
        if details is None:
            order_items.append({
                "item_id": item_id, "name": "UNKNOWN ITEM",
                "quantity": quantity, "modifiers": applied_modifiers,
                "line_total": 0.0, "error": f"Item '{item_id}' not found",
            })
            continue

        modifier_map   = {m["id"]: m["price"] for m in details.get("modifiers", [])}
        modifier_total = sum(modifier_map.get(m_id, 0.0) for m_id in applied_modifiers)
        line_total     = round((details["price"] + modifier_total) * quantity, 2)
        subtotal      += line_total

        order_items.append({
            "item_id":   item_id,
            "name":      details["name"],
            "quantity":  quantity,
            "modifiers": applied_modifiers,
            "line_total": line_total,
        })

    subtotal  = round(subtotal, 2)
    order_id  = f"TT-{uuid.uuid4().hex[:8].upper()}"

    for entry, result_item in zip(items, order_items):
        if "error" not in result_item:
            decrement_inventory(result_item["item_id"], result_item["quantity"], order_id)

    return {
        "order_id":              order_id,
        "items":                 order_items,
        "subtotal":              subtotal,
        "special_instructions":  special_instructions,
    }
