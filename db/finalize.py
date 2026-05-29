"""
Post-payment finalization: save order, decrement inventory, send receipt.

Extracted from ui/app.py so it can be unit-tested without a Streamlit context.
Stripe has already succeeded by the time finalize_side_effects() is called —
any failure below is a bookkeeping issue. We log with traceback and return
human-readable warnings; we never raise (refusing to confirm an already-paid
order is worse than a drifted inventory row).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def finalize_side_effects(
    *,
    order_id: str,
    cart: list[dict],
    breakdown: dict,
    fulfillment_type: str,
    contact_email: str,
    contact_phone: str,
    special_instructions: str,
    conversation_turns: int,
    delivery_address: str,
    eta: str,
    payment_result: dict,
    send_email: bool = True,
) -> list[str]:
    """Persist the order, decrement inventory, send the receipt.

    Idempotent — short-circuits if order_id is already in the orders table.

    Returns a list of warning strings (empty on a clean finalize).
    """
    from db.setup import decrement_inventory, order_exists, save_order

    warnings: list[str] = []

    if order_exists(order_id):
        return warnings

    try:
        save_order(
            order_id=order_id,
            subtotal=breakdown.get("subtotal", 0.0),
            items=cart,
            special_instructions=special_instructions,
            conversation_turns=conversation_turns,
            delivery_fee=breakdown.get("delivery_fee", 0.0),
            tax=breakdown.get("tax", 0.0),
            total=breakdown.get("total", 0.0),
            fulfillment_type=fulfillment_type,
            email=contact_email,
            phone=contact_phone,
        )
    except Exception:
        logger.exception("save_order failed for order_id=%s", order_id)
        warnings.append(
            f"Could not record order {order_id} locally. Your payment went through — "
            "please show this order ID at pickup and contact the restaurant."
        )
        return warnings   # bail: no point decrementing inventory for an un-saved order

    for item in cart:
        try:
            decrement_inventory(item["item_id"], item["quantity"], order_id)
        except Exception:
            logger.exception(
                "decrement_inventory failed for item_id=%s order_id=%s",
                item.get("item_id"), order_id,
            )
            warnings.append(
                f"Inventory wasn't updated for {item.get('name', item.get('item_id', '?'))} — "
                "staff will reconcile."
            )

    if send_email and contact_email:
        try:
            from db.email import send_order_receipt
            send_order_receipt(
                to_email=contact_email,
                order_id=order_id,
                items=cart,
                breakdown=breakdown,
                fulfillment_type=fulfillment_type,
                eta=eta,
                transaction_id=payment_result.get("transaction_id", "—"),
                delivery_address=delivery_address,
            )
        except Exception:
            logger.exception(
                "send_order_receipt failed for to=%s order_id=%s",
                contact_email, order_id,
            )
            warnings.append(
                f"Receipt email to {contact_email} didn't go through. Your order "
                f"({order_id}) is confirmed — show this ID at pickup."
            )

    return warnings
