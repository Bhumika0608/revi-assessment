"""
Tests for db.finalize.finalize_side_effects — covers the success path, the
idempotent re-entry path, and each failure mode (save_order, decrement_inventory,
send_order_receipt). Verifies that failures are logged AND surfaced as
human-readable warnings rather than swallowed silently.

Run: python3 -m pytest tests/test_finalize_side_effects.py -v
"""

import logging
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db import finalize as finalize_mod
from db import setup as setup_mod
from db.finalize import finalize_side_effects
from db.setup import init_db, order_exists, restock_item


@pytest.fixture(scope="module", autouse=True)
def db_and_restore():
    init_db()
    yield
    restock_item("taco_birria", 100, reason="test_finalize_side_effects_teardown")


def _order_id() -> str:
    return f"TT-TEST-{uuid.uuid4().hex[:8].upper()}"


def _cart():
    return [{
        "item_id":   "taco_birria",
        "name":      "Birria Taco",
        "price":     4.99,
        "quantity":  1,
        "modifiers": [],
        "line_total": 4.99,
    }]


def _breakdown():
    return {"subtotal": 4.99, "delivery_fee": 0.0, "tax": 0.40, "total": 5.39}


def _common_kwargs(order_id, cart=None):
    return dict(
        order_id=order_id,
        cart=cart if cart is not None else _cart(),
        breakdown=_breakdown(),
        fulfillment_type="pickup",
        contact_email="",
        contact_phone="",
        special_instructions="",
        conversation_turns=1,
        delivery_address="",
        eta="10–15 minutes",
        payment_result={"transaction_id": "pi_test_xyz"},
        send_email=False,
    )


# ── Success path ──────────────────────────────────────────────────────────────

class TestCleanFinalize:
    def test_returns_no_warnings_and_persists(self):
        oid = _order_id()
        restock_item("taco_birria", 50, reason="test_setup")

        warnings = finalize_side_effects(**_common_kwargs(oid))

        assert warnings == []
        assert order_exists(oid) is True

    def test_re_entry_with_persisted_order_is_noop(self):
        """Streamlit rerun after first finalize: order_exists short-circuits."""
        oid = _order_id()
        restock_item("taco_birria", 50, reason="test_setup")

        first = finalize_side_effects(**_common_kwargs(oid))
        second = finalize_side_effects(**_common_kwargs(oid))

        assert first == []
        assert second == []


# ── Failure modes — each must log AND warn, not silently swallow ──────────────

class TestSaveOrderFailure:
    def test_save_order_failure_returns_warning_and_skips_rest(self, monkeypatch, caplog):
        oid = _order_id()

        def boom(**_kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(setup_mod, "save_order", boom)

        # Track that inventory and email are NOT attempted after save fails.
        decrement_called = []
        monkeypatch.setattr(
            setup_mod, "decrement_inventory",
            lambda *a, **kw: decrement_called.append(a),
        )

        with caplog.at_level(logging.ERROR, logger="db.finalize"):
            warnings = finalize_side_effects(**_common_kwargs(oid))

        assert len(warnings) == 1
        assert oid in warnings[0]
        assert "payment went through" in warnings[0]
        assert decrement_called == [], "should not decrement inventory after save_order fails"
        assert any("save_order failed" in rec.message for rec in caplog.records)


class TestInventoryFailure:
    def test_inventory_failure_returns_warning_continues_other_items(self, monkeypatch, caplog):
        oid = _order_id()
        cart = [
            {"item_id": "taco_birria", "name": "Birria Taco", "quantity": 1,
             "modifiers": [], "line_total": 4.99},
            {"item_id": "drink_coke_mexican", "name": "Mexican Coke", "quantity": 1,
             "modifiers": [], "line_total": 3.49},
        ]
        kwargs = _common_kwargs(oid, cart=cart)

        # Real save_order runs (so the idempotency check would matter on retry).
        # Inject a decrement that fails only for the first item.
        original_decrement = setup_mod.decrement_inventory
        seen = []
        def flaky(item_id, quantity, order_id_arg=""):
            seen.append(item_id)
            if item_id == "taco_birria":
                raise RuntimeError("db locked")
            return original_decrement(item_id, quantity, order_id_arg)
        monkeypatch.setattr(setup_mod, "decrement_inventory", flaky)

        with caplog.at_level(logging.ERROR, logger="db.finalize"):
            warnings = finalize_side_effects(**kwargs)

        assert any("Birria Taco" in w for w in warnings)
        assert all("Mexican Coke" not in w for w in warnings), "Mexican Coke decrement should succeed"
        assert seen == ["taco_birria", "drink_coke_mexican"], "loop continues past failure"
        assert any("decrement_inventory failed" in rec.message for rec in caplog.records)


class TestEmailFailure:
    def test_email_failure_returns_warning_but_order_still_saved(self, monkeypatch, caplog):
        oid = _order_id()
        kwargs = _common_kwargs(oid)
        kwargs["contact_email"] = "customer@example.com"
        kwargs["send_email"] = True

        # Patch the email module's function (imported lazily inside finalize_side_effects).
        from db import email as email_mod
        def boom(**_kw):
            raise RuntimeError("SMTP timeout")
        monkeypatch.setattr(email_mod, "send_order_receipt", boom)

        with caplog.at_level(logging.ERROR, logger="db.finalize"):
            warnings = finalize_side_effects(**kwargs)

        assert any("Receipt email" in w and "customer@example.com" in w for w in warnings)
        assert order_exists(oid) is True   # order saved despite email failure
        assert any("send_order_receipt failed" in rec.message for rec in caplog.records)

    def test_no_email_attempted_when_send_email_false(self, monkeypatch):
        oid = _order_id()
        kwargs = _common_kwargs(oid)
        kwargs["contact_email"] = "customer@example.com"
        kwargs["send_email"] = False

        called = []
        from db import email as email_mod
        monkeypatch.setattr(
            email_mod, "send_order_receipt",
            lambda **kw: called.append(kw),
        )

        warnings = finalize_side_effects(**kwargs)
        assert warnings == []
        assert called == []

    def test_no_email_attempted_when_contact_email_empty(self, monkeypatch):
        oid = _order_id()
        kwargs = _common_kwargs(oid)
        kwargs["contact_email"] = ""
        kwargs["send_email"] = True

        called = []
        from db import email as email_mod
        monkeypatch.setattr(
            email_mod, "send_order_receipt",
            lambda **kw: called.append(kw),
        )

        warnings = finalize_side_effects(**kwargs)
        assert warnings == []
        assert called == []


# ── Logging integration ───────────────────────────────────────────────────────

class TestLoggingNotSilent:
    def test_payment_save_to_db_logs_failure(self, monkeypatch, caplog):
        """db.payment._save_to_db used to swallow exceptions silently."""
        from db import payment as payment_mod
        from db import setup as setup_mod_local

        def boom(**_kw):
            raise RuntimeError("disk full")
        monkeypatch.setattr(setup_mod_local, "save_payment", boom)

        with caplog.at_level(logging.ERROR, logger="db.payment"):
            payment_mod._save_to_db(
                order_id="TT-LOG-TEST",
                stripe_payment_id="pi_xxx",
                amount_cents=599,
                status="succeeded",
                fulfillment_type="pickup",
            )

        # Did not raise, but logged.
        assert any("save_payment failed" in rec.message for rec in caplog.records)
