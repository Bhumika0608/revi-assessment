"""
Stripe payment processing for Talkin' Tacos.

Security model:
  - In test mode (sk_test_...) we map the entered card number to a Stripe
    pre-built test PaymentMethod token (pm_card_visa etc.) — raw card digits
    are never sent to the Stripe API, which blocks that by default.
  - In live mode you would replace this with Stripe.js / Payment Element so
    card data goes browser → Stripe directly, bypassing your server entirely.
  - Only the Stripe PaymentIntent ID, amount, and status are stored locally.
    Card number, expiry, and CVC are never persisted anywhere.
"""

from __future__ import annotations

import logging
import os

import stripe

logger = logging.getLogger(__name__)

# ── Test card number → Stripe pre-built test payment method ──────────────────
# Stripe provides these tokens so test-mode integrations don't need raw cards.
# https://stripe.com/docs/testing#cards
_TEST_CARD_TOKENS: dict[str, str] = {
    "4242424242424242": "pm_card_visa",              # Visa — succeeds
    "5555555555554444": "pm_card_mastercard",        # Mastercard — succeeds
    "4000056655665556": "pm_card_visa_debit",        # Visa Debit — succeeds
    "378282246310005":  "pm_card_amex",              # Amex — succeeds
    "6011111111111117": "pm_card_discover",          # Discover — succeeds
    "4000000000009995": "pm_card_chargeDeclinedInsufficientFunds",  # declined
    "4000000000000002": "pm_card_chargeDeclined",   # generic decline
    "4000000000000069": "pm_card_chargeDeclinedExpiredCard",
    "4000000000000127": "pm_card_chargeDeclinedIncorrectCvc",
}
_DEFAULT_TEST_TOKEN = "pm_card_visa"


def _init_stripe() -> None:
    if stripe.api_key:
        return
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            key = os.getenv("STRIPE_SECRET_KEY")
        except ImportError:
            pass
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY not set. Add it to your .env file.")
    stripe.api_key = key


def _resolve_payment_method(card_number: str) -> str:
    """
    In test mode: map entered card number to a Stripe test token.
    Any unrecognised number defaults to pm_card_visa (succeeds).
    In live mode this function is not used — card data goes via Stripe.js.
    """
    clean = card_number.replace(" ", "").replace("-", "")
    return _TEST_CARD_TOKENS.get(clean, _DEFAULT_TEST_TOKEN)


def process_payment(
    *,
    amount_dollars: float,
    card_number: str,
    exp_month: int,
    exp_year: int,
    cvc: str,
    name_on_card: str,
    order_id: str,
    fulfillment_type: str = "pickup",
    idempotency_key: str | None = None,
) -> dict:
    """
    Charge the customer via Stripe and return the result.

    idempotency_key (defaults to order_id) is passed to Stripe so a retried call
    with the same key returns the original PaymentIntent instead of double-charging.

    Returns:
        {
            "success":        bool,
            "transaction_id": str | None,
            "status":         str,
            "message":        str,
        }
    """
    _init_stripe()

    try:
        # Use a pre-built test token — never send raw card digits to the API
        pm_id = _resolve_payment_method(card_number)
        idem_key = idempotency_key or order_id

        intent = stripe.PaymentIntent.create(
            amount=round(amount_dollars * 100),   # Stripe works in cents
            currency="usd",
            payment_method=pm_id,
            confirm=True,
            automatic_payment_methods={
                "enabled":         True,
                "allow_redirects": "never",
            },
            metadata={
                "order_id":         order_id,
                "fulfillment_type": fulfillment_type,
                "name_on_card":     name_on_card,
            },
            idempotency_key=idem_key,
        )

        _save_to_db(
            order_id=order_id,
            stripe_payment_id=intent.id,
            amount_cents=round(amount_dollars * 100),
            status=intent.status,
            fulfillment_type=fulfillment_type,
        )

        succeeded = intent.status == "succeeded"
        return {
            "success":        succeeded,
            "transaction_id": intent.id,
            "status":         intent.status,
            "message":        "Payment successful!" if succeeded else f"Payment status: {intent.status}",
        }

    except stripe.error.CardError as exc:
        return {
            "success":        False,
            "transaction_id": None,
            "status":         "failed",
            "message":        exc.user_message or "Your card was declined.",
        }
    except stripe.error.AuthenticationError:
        return {
            "success":        False,
            "transaction_id": None,
            "status":         "error",
            "message":        "Payment configuration error. Please contact the restaurant.",
        }
    except Exception as exc:
        return {
            "success":        False,
            "transaction_id": None,
            "status":         "error",
            "message":        f"Payment could not be processed: {exc}",
        }


def _save_to_db(
    order_id: str,
    stripe_payment_id: str,
    amount_cents: int,
    status: str,
    fulfillment_type: str,
) -> None:
    """Persist the payment row as a side-effect of process_payment. Best-effort:
    if the local write fails the Stripe charge has still succeeded, so we don't
    raise — but we log the failure with the Stripe ID so it can be reconciled."""
    try:
        from db.setup import save_payment
        save_payment(
            order_id=order_id,
            stripe_payment_id=stripe_payment_id,
            amount_cents=amount_cents,
            status=status,
            fulfillment_type=fulfillment_type,
        )
    except Exception:
        logger.exception(
            "save_payment failed for order_id=%s stripe_payment_id=%s amount_cents=%d",
            order_id, stripe_payment_id, amount_cents,
        )
