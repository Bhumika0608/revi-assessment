"""
Tests for db/payment.py — Stripe payment processing.

Stripe is monkey-patched so no real network calls occur. We verify:
  - Card number → test-token mapping is correct (and strips spaces/dashes)
  - process_payment returns the success shape on a "succeeded" intent
  - Idempotency key is passed to Stripe (defaulting to order_id)
  - CardError, AuthenticationError, generic Exception each return the right
    failure shape without raising

Run: python3 -m pytest tests/test_payment.py -v
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import stripe

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db import payment as payment_mod
from db.payment import _resolve_payment_method, process_payment


@pytest.fixture(autouse=True)
def stub_stripe_api_key(monkeypatch):
    """Set a fake api key so _init_stripe() doesn't try to read .env."""
    monkeypatch.setattr(stripe, "api_key", "sk_test_fake_for_unit_tests")
    yield


def _fake_intent(intent_id: str = "pi_test_123", status: str = "succeeded"):
    return SimpleNamespace(id=intent_id, status=status)


# ── Card number → test token mapping ──────────────────────────────────────────

class TestResolvePaymentMethod:
    def test_visa(self):
        assert _resolve_payment_method("4242424242424242") == "pm_card_visa"

    def test_mastercard(self):
        assert _resolve_payment_method("5555555555554444") == "pm_card_mastercard"

    def test_amex(self):
        assert _resolve_payment_method("378282246310005") == "pm_card_amex"

    def test_declined_card_maps_to_decline_token(self):
        assert _resolve_payment_method("4000000000000002") == "pm_card_chargeDeclined"

    def test_unknown_card_defaults_to_visa(self):
        assert _resolve_payment_method("1234567890123456") == "pm_card_visa"

    def test_spaces_stripped(self):
        assert _resolve_payment_method("4242 4242 4242 4242") == "pm_card_visa"

    def test_dashes_stripped(self):
        assert _resolve_payment_method("4242-4242-4242-4242") == "pm_card_visa"


# ── process_payment — success path ────────────────────────────────────────────

class TestProcessPaymentSuccess:
    def test_returns_success_shape(self, monkeypatch):
        captured = {}
        def fake_create(**kwargs):
            captured.update(kwargs)
            return _fake_intent("pi_abc", "succeeded")
        monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)
        # Suppress local save_to_db (touches sqlite — covered by other tests)
        monkeypatch.setattr(payment_mod, "_save_to_db", lambda **kw: None)

        result = process_payment(
            amount_dollars=13.99, card_number="4242424242424242",
            exp_month=12, exp_year=2030, cvc="123",
            name_on_card="Jane Smith", order_id="TT-ABCD1234",
        )

        assert result["success"] is True
        assert result["transaction_id"] == "pi_abc"
        assert result["status"] == "succeeded"
        assert captured["amount"] == 1399   # cents
        assert captured["payment_method"] == "pm_card_visa"

    def test_idempotency_key_defaults_to_order_id(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(stripe.PaymentIntent, "create",
                            lambda **kw: captured.update(kw) or _fake_intent())
        monkeypatch.setattr(payment_mod, "_save_to_db", lambda **kw: None)

        process_payment(
            amount_dollars=5.00, card_number="4242424242424242",
            exp_month=12, exp_year=2030, cvc="123",
            name_on_card="x", order_id="TT-IDEM-001",
        )

        assert captured["idempotency_key"] == "TT-IDEM-001"

    def test_explicit_idempotency_key_overrides_default(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(stripe.PaymentIntent, "create",
                            lambda **kw: captured.update(kw) or _fake_intent())
        monkeypatch.setattr(payment_mod, "_save_to_db", lambda **kw: None)

        process_payment(
            amount_dollars=5.00, card_number="4242424242424242",
            exp_month=12, exp_year=2030, cvc="123",
            name_on_card="x", order_id="TT-001", idempotency_key="custom-key",
        )

        assert captured["idempotency_key"] == "custom-key"


# ── process_payment — failure paths ───────────────────────────────────────────

class TestProcessPaymentFailures:
    def test_card_declined_returns_failed_status(self, monkeypatch):
        # CardError.user_message is a read-only descriptor — construct without it
        # and rely on the fallback message in process_payment.
        def fake_create(**kwargs):
            raise stripe.error.CardError(
                message="Your card was declined.",
                param=None, code="card_declined",
            )
        monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)
        monkeypatch.setattr(payment_mod, "_save_to_db", lambda **kw: None)

        result = process_payment(
            amount_dollars=10.0, card_number="4000000000000002",
            exp_month=12, exp_year=2030, cvc="123",
            name_on_card="x", order_id="TT-DECL-001",
        )

        assert result["success"] is False
        assert result["transaction_id"] is None
        assert result["status"] == "failed"
        assert "declined" in result["message"].lower()

    def test_authentication_error_returns_config_message(self, monkeypatch):
        def fake_create(**kwargs):
            raise stripe.error.AuthenticationError("bad key")
        monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)
        monkeypatch.setattr(payment_mod, "_save_to_db", lambda **kw: None)

        result = process_payment(
            amount_dollars=10.0, card_number="4242424242424242",
            exp_month=12, exp_year=2030, cvc="123",
            name_on_card="x", order_id="TT-AUTH-001",
        )

        assert result["success"] is False
        assert result["status"] == "error"
        assert "configuration" in result["message"].lower()

    def test_generic_exception_does_not_raise(self, monkeypatch):
        def fake_create(**kwargs):
            raise RuntimeError("network blip")
        monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)
        monkeypatch.setattr(payment_mod, "_save_to_db", lambda **kw: None)

        result = process_payment(
            amount_dollars=10.0, card_number="4242424242424242",
            exp_month=12, exp_year=2030, cvc="123",
            name_on_card="x", order_id="TT-RND-001",
        )

        assert result["success"] is False
        assert result["status"] == "error"
        assert "network blip" in result["message"]


# ── _save_to_db logs (no longer swallows silently) ────────────────────────────

class TestSaveToDbLogsFailure:
    def test_failure_is_logged(self, monkeypatch, caplog):
        from db import setup as setup_mod
        def boom(**_):
            raise RuntimeError("disk full")
        monkeypatch.setattr(setup_mod, "save_payment", boom)

        with caplog.at_level(logging.ERROR, logger="db.payment"):
            payment_mod._save_to_db(
                order_id="TT-LOG-X", stripe_payment_id="pi_xxx",
                amount_cents=999, status="succeeded", fulfillment_type="pickup",
            )

        assert any("save_payment failed" in rec.message for rec in caplog.records)
