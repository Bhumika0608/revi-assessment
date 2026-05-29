"""
Tests for db/tax.py — Miami-Dade 8% prepared food tax.

Run: python3 -m pytest tests/test_tax.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.tax import MIAMI_DADE_TAX_RATE, calculate_tax, order_breakdown


class TestRate:
    def test_rate_is_eight_percent(self):
        # 7% FL state + 1% Miami-Dade surtax = 8%.
        assert MIAMI_DADE_TAX_RATE == pytest.approx(0.08)


class TestCalculateTax:
    def test_round_number(self):
        assert calculate_tax(100.0) == pytest.approx(8.0, abs=0.01)

    def test_zero(self):
        assert calculate_tax(0.0) == 0.0

    def test_rounds_to_cents(self):
        # 12.49 * 0.08 = 0.9992 → 1.00
        assert calculate_tax(12.49) == pytest.approx(1.00, abs=0.01)

    def test_rounds_half_up(self):
        # 6.25 * 0.08 = 0.5 → 0.50
        assert calculate_tax(6.25) == pytest.approx(0.50, abs=0.01)

    def test_typical_order(self):
        # Two birria tacos + Mexican Coke = 13.47 * 0.08 = 1.0776 → 1.08
        assert calculate_tax(13.47) == pytest.approx(1.08, abs=0.01)


class TestOrderBreakdown:
    def test_pickup_no_delivery_fee(self):
        b = order_breakdown(subtotal=10.0)
        assert b["subtotal"]     == pytest.approx(10.0, abs=0.01)
        assert b["delivery_fee"] == 0.0
        assert b["tax"]          == pytest.approx(0.80, abs=0.01)
        assert b["total"]        == pytest.approx(10.80, abs=0.01)
        assert b["tax_rate_pct"] == 8

    def test_delivery_adds_to_total(self):
        b = order_breakdown(subtotal=10.0, delivery_fee=2.99)
        assert b["delivery_fee"] == pytest.approx(2.99, abs=0.01)
        # Tax is on subtotal only (not delivery fee in this implementation)
        assert b["tax"]          == pytest.approx(0.80, abs=0.01)
        assert b["total"]        == pytest.approx(13.79, abs=0.01)

    def test_zero_order(self):
        b = order_breakdown(subtotal=0.0)
        assert b["total"] == 0.0
        assert b["tax"]   == 0.0

    def test_total_rounded_to_cents(self):
        # Should not produce .999... artifacts.
        b = order_breakdown(subtotal=13.47, delivery_fee=4.99)
        # 13.47 + 4.99 + 1.08 = 19.54
        assert b["total"] == pytest.approx(19.54, abs=0.01)

    def test_all_required_keys_present(self):
        b = order_breakdown(subtotal=10.0, delivery_fee=2.99)
        assert set(b.keys()) == {"subtotal", "delivery_fee", "tax", "tax_rate_pct", "total"}
