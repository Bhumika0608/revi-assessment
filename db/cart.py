"""
Deterministic cart operations — no LLM involved.
Cart is a plain list of dicts stored in st.session_state.cart.
All math (line totals, subtotal) is computed here, never by the agent.
"""

from __future__ import annotations


def add_item(
    cart: list[dict],
    item_id: str,
    name: str,
    price: float,
    quantity: int,
    modifiers: list[str],
    modifier_upcharge: float = 0.0,
    options: dict | None = None,
) -> tuple[list[dict], dict]:
    """Increase quantity if item+modifiers+options already in cart, else append.

    Returns (new_cart, change_info) where change_info reports whether this call
    created a new line or incremented an existing one, plus the before/after
    quantities. Callers surface this so the agent can detect unintentional
    re-adds (a known recurring failure mode where the agent re-validates after
    a clarification turn and accidentally double-adds an item).

    modifier_upcharge is the per-unit dollar total of all paid modifiers on this line
    (e.g. add_guac $1.50 + extra_meat $3.00 → 4.50). line_total = qty × (price + upcharge).

    options is a dict of choice options like {"salsa": "hot", "tortilla": "corn"}.
    Two cart lines with the same item_id and same modifiers but DIFFERENT options
    are kept as separate lines — e.g. one birria taco with hot salsa is a distinct
    cart line from another birria taco with mild salsa. Options carry no upcharge
    (they're categorical choices, not paid add-ons).
    """
    cart = [dict(i) for i in cart]
    upcharge = round(float(modifier_upcharge), 2)
    options  = dict(options or {})
    for entry in cart:
        if (entry["item_id"] == item_id
                and entry.get("modifiers") == list(modifiers)
                and entry.get("options", {}) == options):
            previous_qty = entry["quantity"]
            entry["quantity"] = previous_qty + quantity
            entry["modifier_upcharge"] = upcharge
            entry["line_total"] = round(entry["quantity"] * (entry["price"] + upcharge), 2)
            change = {
                "new_line": False,
                "previous_quantity": previous_qty,
                "new_quantity": entry["quantity"],
            }
            return cart, change
    cart.append({
        "item_id":           item_id,
        "name":              name,
        "price":             price,
        "quantity":          quantity,
        "modifiers":         list(modifiers),
        "modifier_upcharge": upcharge,
        "options":           options,
        "line_total":        round(quantity * (price + upcharge), 2),
    })
    return cart, {
        "new_line": True,
        "previous_quantity": 0,
        "new_quantity": quantity,
    }


def remove_item(cart: list[dict], item_id: str) -> list[dict]:
    return [dict(i) for i in cart if i["item_id"] != item_id]


def get_subtotal(cart: list[dict]) -> float:
    return round(sum(i["line_total"] for i in cart), 2)


def cart_summary_text(cart: list[dict]) -> str:
    """Compact one-line summary used in tool results so the LLM can see cart
    state at a glance. Includes option choices so multiple lines with the same
    item but different options (e.g. three birria tacos with three salsas)
    are visibly distinct — without options in the summary, the LLM would see
    three identical-looking entries and lose situational awareness."""
    if not cart:
        return "Cart is empty."
    parts = []
    for i in cart:
        suffix = ""
        opts = i.get("options") or {}
        if opts:
            opt_str = ", ".join(f"{k}: {v}" for k, v in sorted(opts.items()))
            suffix = f" [{opt_str}]"
        parts.append(f"{i['quantity']}× {i['name']}{suffix} (${i['line_total']:.2f})")
    return ", ".join(parts) + f" — Subtotal: ${get_subtotal(cart):.2f}"
