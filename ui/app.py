"""
Streamlit chat UI for the Talkin' Tacos ordering agent.

Flow:
  1. CART BUILDING  — agent adds items via add_to_cart tool; cart lives in session state
  2. CHECKOUT       — deterministic 4-step UI (fulfillment → address → review → payment)
  3. COMPLETE       — order saved to DB after payment succeeds; confirmation shown

Run: streamlit run ui/app.py
"""

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st

from agent.agent import MODEL, take_order
from db.logging_config import setup_logging
from db.restaurant import (
    HOURS_LINE,
    NAME as RESTAURANT_NAME,
    PHONE,
    SHORT_LOCATION,
)
from db.setup import get_item_by_id, init_db

setup_logging()


# ── Cart line rendering helpers ───────────────────────────────────────────────
#
# Cart entries store modifier IDs (`add_guac`, `extra_meat`) — internal keys
# used by add_to_cart for price lookup and dedup. Customer-facing surfaces
# (Step 3 review, confirmation page, future receipts) must show the menu's
# human label (`Extra Guacamole`) and the upcharge in dollars, never the raw
# ID. Same for options — the cart stores `{"salsa": "hot"}`; the UI displays
# `Salsa: hot`. These helpers centralize that mapping so a future modifier
# rename in menu.json automatically flows through every render site.

def _format_modifiers(item: dict) -> str:
    """Human-readable comma-separated modifier list for one cart entry.
    Falls back to the raw modifier ID if the menu lookup fails (so the
    customer at least sees *something* rather than a blank line)."""
    mods = item.get("modifiers") or []
    if not mods:
        return ""
    menu_item = get_item_by_id(item["item_id"])
    mod_lookup = {m["id"]: m for m in (menu_item or {}).get("modifiers", [])}

    parts = []
    for mod_id in mods:
        meta = mod_lookup.get(mod_id)
        if meta is None:
            parts.append(mod_id)
            continue
        name = meta.get("name", mod_id)
        price = float(meta.get("price", 0.0))
        parts.append(f"{name} (+${price:.2f})" if price > 0 else name)
    return ", ".join(parts)


def _format_options(item: dict) -> str:
    """Human-readable comma-separated option list for one cart entry.
    Option values are stored as menu-schema keys (e.g. ``al_pastor``,
    ``brown``); we prettify with underscore→space + title-case so the
    receipt reads ``Protein: Al Pastor`` instead of ``Protein: al_pastor``."""
    opts = item.get("options") or {}
    if not opts:
        return ""
    def _pretty(v: object) -> str:
        s = str(v).replace("_", " ").strip()
        return s.title() if s else s
    return ", ".join(f"{k.capitalize()}: {_pretty(v)}" for k, v in sorted(opts.items()))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=f"{RESTAURANT_NAME} 🌮",
    page_icon="🌮",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_resource
def setup():
    init_db()

setup()

# ── Session state ─────────────────────────────────────────────────────────────
_STATE_DEFAULTS = {
    # Conversation
    "history":              [],
    "messages":             [],
    "turn_count":           0,
    "latencies":            [],
    "traces":               [],
    # Cart & order status
    "cart":                 [],      # [{item_id, name, price, quantity, modifiers, line_total}]
    "validated_ids":        set(),   # item_ids seen by get_item_details this session
    "status":               "in_progress",  # in_progress | checkout | complete | refused
    # Checkout steps (1–4 active, 5 = done / status becomes complete)
    "checkout_step":        1,
    "fulfillment_type":     None,    # "pickup" | "delivery"
    "delivery_address":     "",
    "delivery_result":      None,
    "order_breakdown":      None,    # {subtotal, delivery_fee, tax, total}
    "special_instructions": "",
    "contact_email":        "",
    "contact_phone":        "",
    "payment_result":       None,
    "payment_error":        None,
    "order_id":             None,
    "finalization_warnings": [],   # human-readable issues from save/inventory/email
}

for _k, _v in _STATE_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Agent helper ──────────────────────────────────────────────────────────────

def _send_to_agent(msg: str) -> None:
    st.session_state.messages.append({"role": "user", "content": msg})
    t0 = time.perf_counter()
    response = take_order(
        msg,
        st.session_state.history or None,
        st.session_state.cart,
        st.session_state.validated_ids,
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    st.session_state.latencies.append(latency_ms)
    st.session_state.turn_count  += 1
    st.session_state.cart         = response["cart"]
    st.session_state.validated_ids = response.get("validated_ids", set())
    st.session_state.traces.append(response.get("trace"))
    st.session_state.messages.append({"role": "assistant", "content": response["agent_message"]})
    st.session_state.history.append({"role": "user",      "content": msg})
    st.session_state.history.append({"role": "assistant", "content": response["agent_message"]})

    new_status = response["status"]
    if new_status == "checkout":
        st.session_state.status       = "checkout"
        st.session_state.checkout_step = 1
    elif new_status == "refused":
        st.session_state.status = "refused"


# ── Trace renderer ────────────────────────────────────────────────────────────

def _render_trace(trace: dict) -> None:
    llm_calls  = trace.get("llm_calls", [])
    tool_calls = trace.get("tool_calls", [])
    label = (
        f"⏱ {trace['total_ms']:.0f}ms · "
        f"{len(llm_calls)} LLM · "
        f"{len(tool_calls)} tool call(s)"
    )
    with st.expander(label, expanded=False):
        for llm in llm_calls:
            st.markdown(
                f"**iter {llm['iteration']}** LLM → `{llm['stop_reason']}` "
                f"({llm['latency_ms']:.0f}ms)"
            )
            for t in [tc for tc in tool_calls if tc["iteration"] == llm["iteration"]]:
                import json as _json
                input_str = _json.dumps(t["input"])[:80]
                err_badge = " 🔴" if t["error"] else ""
                st.markdown(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;→ `{t['name']}({input_str})` "
                    f"[{t['latency_ms']:.0f}ms, {t['output_chars']} chars]{err_badge}",
                    unsafe_allow_html=True,
                )


# ── Checkout step functions ───────────────────────────────────────────────────

def _checkout_step1_fulfillment() -> None:
    st.markdown("#### Step 1 of 4 — How would you like your order?")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🏃 Pickup — Free\n10–15 min", use_container_width=True, type="primary", key="btn_pickup"):
            st.session_state.fulfillment_type = "pickup"
            st.session_state.checkout_step    = 3
            st.rerun()
    with col2:
        if st.button("🚚 Delivery\nCheck availability", use_container_width=True, key="btn_delivery"):
            st.session_state.fulfillment_type = "delivery"
            st.session_state.checkout_step    = 2
            st.rerun()


def _checkout_step2_delivery() -> None:
    from db.delivery import MIN_ORDER_DELIVERY, check_delivery
    from db.cart import get_subtotal

    st.markdown("#### Step 2 of 4 — Delivery Address")

    subtotal = get_subtotal(st.session_state.cart)
    if subtotal < MIN_ORDER_DELIVERY:
        st.error(
            f"Minimum order for delivery is **${MIN_ORDER_DELIVERY:.2f}**. "
            f"Your subtotal is ${subtotal:.2f}. Please add more items or choose pickup."
        )
        if st.button("← Choose Pickup instead", key="btn_switch_pickup"):
            st.session_state.fulfillment_type = "pickup"
            st.session_state.checkout_step    = 3
            st.rerun()
        return

    addr = st.text_input(
        "Enter your delivery address",
        value=st.session_state.delivery_address,
        placeholder="1234 Collins Ave, Miami Beach, FL 33139",
        key="delivery_addr",
    )
    if addr != st.session_state.delivery_address:
        st.session_state.delivery_address = addr
        st.session_state.delivery_result  = None

    if st.button("Check availability →", type="primary", key="btn_check_delivery"):
        if addr.strip():
            st.session_state.delivery_result = check_delivery(addr.strip())
            st.rerun()
        else:
            st.warning("Please enter your address first.")

    dr = st.session_state.delivery_result
    if dr is not None:
        if dr["deliverable"] is True:
            st.success(
                f"✅ {dr['message']}  \n"
                f"Zone: {dr['zone_label']} · Delivery fee: **${dr['fee']:.2f}** · ETA: {dr['eta']}"
            )
            if st.button("Continue to Review →", type="primary", key="btn_continue_review"):
                st.session_state.checkout_step = 3
                st.rerun()
        elif dr["deliverable"] is False:
            st.error(dr["message"])
            if st.button("← Choose Pickup instead", key="btn_switch_pickup2"):
                st.session_state.fulfillment_type = "pickup"
                st.session_state.delivery_result  = None
                st.session_state.checkout_step    = 3
                st.rerun()
        else:
            st.warning(dr["message"])
            if dr.get("needs_zip"):
                st.caption("Tip: include your 5-digit ZIP code for instant confirmation.")


def _checkout_step3_review() -> None:
    from db.cart import get_subtotal
    from db.tax import order_breakdown

    st.markdown("#### Step 3 of 4 — Review Your Order")

    cart = st.session_state.cart
    ft   = st.session_state.fulfillment_type
    dr   = st.session_state.delivery_result

    delivery_fee = dr["fee"] if ft == "delivery" and dr and dr.get("fee") is not None else 0.0
    subtotal     = get_subtotal(cart)
    breakdown    = order_breakdown(subtotal, delivery_fee)
    st.session_state.order_breakdown = breakdown

    # Cart items
    for item in cart:
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(f"**{item['quantity']}× {item['name']}**")
            opt_text = _format_options(item)
            if opt_text:
                st.caption(f"Options: {opt_text}")
            mod_text = _format_modifiers(item)
            if mod_text:
                st.caption(f"Modifiers: {mod_text}")
        with c2:
            # `\$` so Streamlit's markdown renders a literal dollar sign
            # rather than interpreting it as a LaTeX inline-math delimiter.
            st.markdown(f"**\\${item['line_total']:.2f}**")

    st.markdown("---")

    # Price breakdown
    rows = [("Subtotal", f"${breakdown['subtotal']:.2f}")]
    if ft == "delivery" and breakdown["delivery_fee"] > 0:
        label = f"Delivery ({dr['zone_label']})" if dr and dr.get("zone_label") else "Delivery"
        rows.append((label, f"${breakdown['delivery_fee']:.2f}"))
    rows.append((f"Tax ({breakdown['tax_rate_pct']}% Miami-Dade)", f"${breakdown['tax']:.2f}"))
    rows.append(("**Total**", f"**${breakdown['total']:.2f}**"))

    for label, value in rows:
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(label)
        with c2:
            st.markdown(value)

    st.markdown("---")
    st.markdown("**Contact info** *(for your receipt)*")

    email = st.text_input(
        "Email *",
        value=st.session_state.contact_email,
        placeholder="your@email.com",
        key="contact_email_input",
    )
    phone = st.text_input(
        "Phone *(optional)*",
        value=st.session_state.contact_phone,
        placeholder="+1 (305) 555-0000",
        key="contact_phone_input",
    )
    special = st.text_area(
        "Special instructions *(optional)*",
        value=st.session_state.special_instructions,
        placeholder="e.g. extra napkins, everything in one bag",
        key="special_instructions_input",
    )

    if st.button("Continue to Payment →", type="primary", use_container_width=True, key="btn_to_payment"):
        if not email.strip():
            st.warning("Please enter your email for the receipt.")
        else:
            st.session_state.contact_email        = email.strip()
            st.session_state.contact_phone        = phone.strip()
            st.session_state.special_instructions = special.strip()
            st.session_state.checkout_step        = 4
            st.rerun()


def _checkout_step4_payment() -> None:
    breakdown = st.session_state.order_breakdown or {}
    total     = breakdown.get("total", 0.0)

    # Reserve a stable order_id for this checkout attempt. Reused across submit
    # retries / Streamlit reruns so Stripe's idempotency key is consistent and
    # _finalize_order can detect a duplicate save.
    if not st.session_state.order_id:
        st.session_state.order_id = f"TT-{uuid.uuid4().hex[:8].upper()}"

    st.markdown(f"#### Step 4 of 4 — Payment · **${total:.2f}**")
    st.caption("🔒 Your card details are sent directly to Stripe and never stored by us.")
    st.caption("Test card: `4242 4242 4242 4242` · any future MM/YY · any 3-digit CVC")

    if st.session_state.payment_error:
        st.error(st.session_state.payment_error)

    with st.form("payment_form"):
        name_on_card = st.text_input("Name on card", placeholder="Jane Smith")
        card_number  = st.text_input("Card number",  placeholder="4242 4242 4242 4242", max_chars=19)
        col_mm, col_yy, col_cvc = st.columns(3)
        with col_mm:
            exp_month = st.number_input("MM", min_value=1,    max_value=12,   step=1, value=12)
        with col_yy:
            exp_year  = st.number_input("YY", min_value=2025, max_value=2040, step=1, value=2026)
        with col_cvc:
            cvc = st.text_input("CVC", placeholder="123", max_chars=4, type="password")

        submitted = st.form_submit_button(
            f"Pay ${total:.2f}  🔒", type="primary", use_container_width=True
        )

    if submitted:
        if not all([name_on_card.strip(), card_number.strip(), cvc.strip()]):
            st.session_state.payment_error = "Please fill in all card fields."
            st.rerun()
        else:
            st.session_state.payment_error = None
            with st.spinner(f"Processing ${total:.2f}…"):
                from db.payment import process_payment
                ft       = st.session_state.fulfillment_type or "pickup"
                order_id = st.session_state.order_id

                result = process_payment(
                    amount_dollars=total,
                    card_number=card_number,
                    exp_month=int(exp_month),
                    exp_year=int(exp_year),
                    cvc=cvc,
                    name_on_card=name_on_card,
                    order_id=order_id,
                    fulfillment_type=ft,
                    idempotency_key=order_id,
                )

            if result["success"]:
                _finalize_order(order_id, result)
            else:
                st.session_state.payment_error = result["message"]
                st.rerun()


def _finalize_order(order_id: str, payment_result: dict) -> None:
    """Wrap the pure side-effect runner with Streamlit session-state plumbing."""
    from db.delivery import ZONE_ETAS
    from db.finalize import finalize_side_effects

    breakdown = st.session_state.order_breakdown or {}
    ft        = st.session_state.fulfillment_type or "pickup"
    dr        = st.session_state.delivery_result
    eta       = ZONE_ETAS.get("pickup") if ft == "pickup" else (dr["eta"] if dr else "30–40 minutes")

    warnings = finalize_side_effects(
        order_id=order_id,
        cart=st.session_state.cart,
        breakdown=breakdown,
        fulfillment_type=ft,
        contact_email=st.session_state.contact_email,
        contact_phone=st.session_state.contact_phone,
        special_instructions=st.session_state.special_instructions,
        conversation_turns=st.session_state.turn_count,
        delivery_address=st.session_state.delivery_address,
        eta=eta,
        payment_result=payment_result,
    )

    st.session_state.order_id              = order_id
    st.session_state.payment_result        = payment_result
    st.session_state.finalization_warnings = warnings
    st.session_state.status                = "complete"
    st.rerun()


def _render_confirmation() -> None:
    from db.delivery import ZONE_ETAS
    ft        = st.session_state.fulfillment_type or "pickup"
    breakdown = st.session_state.order_breakdown or {}
    pr        = st.session_state.payment_result or {}
    dr        = st.session_state.delivery_result

    eta = ZONE_ETAS.get("pickup") if ft == "pickup" else (dr["eta"] if dr else "30–40 minutes")

    st.success("## ✅ Order Confirmed!")
    st.markdown(f"**Order ID:** `{st.session_state.order_id}`")
    st.markdown(f"**ETA:** {eta}")
    st.markdown(f"**Transaction:** `{pr.get('transaction_id', '—')}`")

    for warning in st.session_state.get("finalization_warnings", []) or []:
        st.warning(warning)

    st.markdown("---")
    st.markdown("**Your order:**")
    for item in st.session_state.cart:
        # Escape `$` as `\$` — Streamlit's markdown parses `$...$` as inline
        # LaTeX, which mangles cart lines that include both `$13.99` and
        # `(+$1.50)` on the same render. Each `\$` produces a literal `$` in
        # the rendered output.
        opt_text = _format_options(item)
        mod_text = _format_modifiers(item)
        suffix_parts = []
        if opt_text:
            suffix_parts.append(opt_text)
        if mod_text:
            suffix_parts.append(mod_text.replace("$", "\\$"))
        suffix = f"  *({' · '.join(suffix_parts)})*" if suffix_parts else ""
        st.markdown(
            f"- {item['quantity']}× **{item['name']}** — "
            f"\\${item['line_total']:.2f}{suffix}"
        )

    st.markdown("---")
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown("Subtotal")
        if ft == "delivery":
            st.markdown("Delivery fee")
        st.markdown("Tax")
        st.markdown("**Total paid**")
    with c2:
        st.markdown(f"${breakdown.get('subtotal', 0):.2f}")
        if ft == "delivery":
            st.markdown(f"${breakdown.get('delivery_fee', 0):.2f}")
        st.markdown(f"${breakdown.get('tax', 0):.2f}")
        st.markdown(f"**${breakdown.get('total', 0):.2f}**")

    if st.session_state.contact_email:
        st.markdown("---")
        st.info(f"📧 Receipt details saved for **{st.session_state.contact_email}**")

    if ft == "pickup":
        st.info(f"📍 Pick up at: **{SHORT_LOCATION}**")
    elif dr:
        st.info(f"🚚 Delivering to: **{st.session_state.delivery_address}**")


# ── Layout ────────────────────────────────────────────────────────────────────
header_col, reset_col = st.columns([4, 1])
with header_col:
    st.title(f"🌮 {RESTAURANT_NAME}")
    st.caption(f"{SHORT_LOCATION} · {HOURS_LINE} · {PHONE}")
with reset_col:
    st.write("")
    if st.button("🔄 New Order", use_container_width=True):
        for k, v in _STATE_DEFAULTS.items():
            if isinstance(v, list):
                st.session_state[k] = []
            elif isinstance(v, dict):
                st.session_state[k] = {}
            else:
                st.session_state[k] = v
        st.rerun()

chat_col, summary_col = st.columns([3, 1])

# ── Chat column ───────────────────────────────────────────────────────────────
with chat_col:
    # Chat history
    chat_container = st.container(height=420)
    with chat_container:
        if not st.session_state.messages:
            st.markdown(
                f"> 👋 **Welcome to {RESTAURANT_NAME}!** Tell me what you'd like and I'll build your cart. "
                "When you're done, just say 'that's all' and we'll head to checkout."
            )
        else:
            assistant_turn = 0
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    if msg["role"] == "assistant":
                        trace = (
                            st.session_state.traces[assistant_turn]
                            if assistant_turn < len(st.session_state.traces)
                            else None
                        )
                        if trace:
                            _render_trace(trace)
                        assistant_turn += 1

    # ── Below chat: input OR checkout OR confirmation ─────────────────────────
    status = st.session_state.status

    if status == "in_progress":
        user_input = st.chat_input("Type your order…")
        if user_input:
            _send_to_agent(user_input)
            st.rerun()

        # Sample prompts
        st.markdown("---")
        st.caption("**Try these:**")
        sample_prompts = [
            "2 birria tacos + Mexican Coke",
            "Chicken bowl, extra guac",
            "Something comforting under $8",
            "What's vegan here?",
            "Do you have parking?",
            "What are your hours?",
        ]
        cols = st.columns(3)
        for i, prompt in enumerate(sample_prompts):
            with cols[i % 3]:
                if st.button(prompt, key=f"sample_{prompt}", use_container_width=True):
                    _send_to_agent(prompt)
                    st.rerun()

    elif status == "refused":
        # Cart-preserving refusal recovery: if the customer hit a refusal
        # mid-order (e.g. asked an off-topic question after adding items),
        # don't make them lose the cart. Offer to continue the order or
        # start fresh. The agent's prompt is supposed to suppress REFUSED
        # when the cart is non-empty (§7), but this is the safety net for
        # when the model slips — protecting real orders from one rogue
        # turn matters more than rigid status semantics.
        if st.session_state.cart:
            st.warning(
                "Looks like that question was outside our menu. "
                "Your cart is still here — keep ordering or hit 'New Order' to start fresh."
            )
            if st.button("← Continue ordering", key="btn_continue_after_refused"):
                st.session_state.status = "in_progress"
                st.rerun()
        else:
            st.warning("Request declined. Hit 'New Order' to start fresh.")

    elif status == "checkout":
        st.markdown("---")
        # Back to cart button
        if st.button("← Edit cart", key="btn_back_to_cart"):
            st.session_state.status       = "in_progress"
            st.session_state.checkout_step = 1
            st.session_state.fulfillment_type = None
            st.session_state.delivery_result  = None
            st.session_state.order_breakdown  = None
            # Discard the reserved order_id — cart may change, so the next
            # checkout attempt should get a fresh idempotency key.
            st.session_state.order_id         = None
            st.rerun()

        step = st.session_state.checkout_step
        st.progress((step - 1) / 4)

        if step == 1:
            _checkout_step1_fulfillment()
        elif step == 2:
            _checkout_step2_delivery()
        elif step == 3:
            _checkout_step3_review()
        elif step == 4:
            _checkout_step4_payment()

    elif status == "complete":
        st.markdown("---")
        _render_confirmation()


# ── Summary column ────────────────────────────────────────────────────────────
with summary_col:
    st.subheader("Your Cart")

    status_color = {
        "in_progress": "🟡",
        "checkout":    "🔵",
        "complete":    "🟢",
        "refused":     "🔴",
    }.get(st.session_state.status, "⚪")
    st.caption(f"Status: {status_color} `{st.session_state.status}`")

    cart = st.session_state.cart
    if cart:
        st.markdown("---")
        from db.cart import get_subtotal
        for item in cart:
            # Render quantity/name, then any modifier/option details as captions,
            # then the price on its own line. Splitting into separate calls
            # prevents Streamlit's markdown from interpreting two unescaped `$`
            # signs on one line as a LaTeX inline-math pair — which would
            # mangle "Add guacamole (+$1.50)" and "$13.99" into rendered math.
            st.markdown(f"**{item['quantity']}×** {item['name']}")
            opt_text = _format_options(item)
            if opt_text:
                st.caption(opt_text)
            mod_text = _format_modifiers(item)
            if mod_text:
                st.caption(mod_text)
            st.markdown(f"**\\${item['line_total']:.2f}**")
        st.markdown("---")

        breakdown = st.session_state.order_breakdown
        if breakdown and st.session_state.status in ("checkout", "complete"):
            ft = st.session_state.fulfillment_type
            st.markdown(f"Subtotal: ${breakdown['subtotal']:.2f}")
            if ft == "delivery" and breakdown.get("delivery_fee", 0) > 0:
                st.markdown(f"Delivery: ${breakdown['delivery_fee']:.2f}")
            st.markdown(f"Tax (8%): ${breakdown['tax']:.2f}")
            st.markdown(f"**Total: ${breakdown['total']:.2f}**")
        else:
            st.markdown(f"**Subtotal: ${get_subtotal(cart):.2f}**")

        if st.session_state.status == "in_progress":
            st.markdown("---")
            if st.button("🛒 Proceed to Checkout", type="primary", use_container_width=True, key="btn_checkout"):
                st.session_state.status       = "checkout"
                st.session_state.checkout_step = 1
                st.rerun()

        if st.session_state.status == "complete" and st.session_state.payment_result:
            st.success("💳 Paid ✅")
            st.caption(f"Txn: `{st.session_state.payment_result.get('transaction_id', '—')}`")

    elif st.session_state.history:
        st.caption("Items will appear here as you order.")
    else:
        st.caption("Your cart is empty.")

    # ── Session metrics ───────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Session")
    st.metric("Turns", st.session_state.turn_count)
    lats = st.session_state.latencies
    if lats:
        st.metric("Last latency", f"{lats[-1]:.0f}ms")
        if len(lats) > 1:
            import statistics
            st.metric("Avg latency", f"{statistics.mean(lats):.0f}ms")
    st.caption(f"Model: `{MODEL}`")
