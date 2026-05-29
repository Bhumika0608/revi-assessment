"""
Tests for db/delivery.py — ZIP/neighborhood → delivery zone, fee, ETA.

Run: python3 -m pytest tests/test_delivery.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.delivery import (
    MIN_ORDER_DELIVERY,
    ZONE_ETAS,
    ZONE_FEES,
    ZONE_LABELS,
    check_delivery,
)


class TestZoneConstants:
    def test_fees_match_spec(self):
        assert ZONE_FEES[1] == pytest.approx(2.99, abs=0.01)
        assert ZONE_FEES[2] == pytest.approx(4.99, abs=0.01)
        assert ZONE_FEES[3] == pytest.approx(7.99, abs=0.01)

    def test_pickup_eta_exists(self):
        assert "pickup" in ZONE_ETAS

    def test_each_zone_has_label_and_eta(self):
        for zone in (1, 2, 3):
            assert zone in ZONE_LABELS
            assert zone in ZONE_ETAS

    def test_min_order_is_ten_dollars(self):
        assert MIN_ORDER_DELIVERY == pytest.approx(10.00, abs=0.01)


# ── ZIP-based lookup ──────────────────────────────────────────────────────────

class TestZipLookup:
    def test_wynwood_zone_1(self):
        r = check_delivery("127 NW 25th St, Miami, FL 33127")
        assert r["deliverable"] is True
        assert r["zone"] == 1
        assert r["fee"] == pytest.approx(2.99, abs=0.01)

    def test_south_beach_zone_2(self):
        r = check_delivery("1500 Collins Ave, Miami Beach, FL 33139")
        assert r["deliverable"] is True
        assert r["zone"] == 2
        assert r["fee"] == pytest.approx(4.99, abs=0.01)

    def test_aventura_zone_3(self):
        # Use a 4-digit street number to avoid the regex tripping on the street
        # number — see test_zip_regex_collides_with_street_number for the known bug.
        r = check_delivery("2999 NE 191st St, Aventura, FL 33180")
        assert r["deliverable"] is True
        assert r["zone"] == 3
        assert r["fee"] == pytest.approx(7.99, abs=0.01)

    def test_zip_wins_over_street_number(self):
        # Regression for #12: when the address has both a 5-digit street number
        # and a real ZIP, we must pick the ZIP. "19501 Biscayne Blvd … 33180".
        r = check_delivery("19501 Biscayne Blvd, Aventura, FL 33180")
        assert r["deliverable"] is True
        assert r["zone"] == 3

    def test_unknown_zip_after_street_number_uses_zip_in_message(self):
        # Both 5-digit runs unknown — message should name the trailing one (the
        # ZIP-shaped value), not the leading street number.
        r = check_delivery("19501 Foo Blvd, Somewhere 99999")
        assert r["deliverable"] is False
        assert "99999" in r["message"]
        assert "19501" not in r["message"]

    def test_zip_alone_still_works(self):
        # No street number — ZIP-only input still resolves.
        r = check_delivery("33127")
        assert r["deliverable"] is True
        assert r["zone"] == 1

    def test_unknown_zip_not_deliverable(self):
        r = check_delivery("999 Main St, Somewhere, XX 99999")
        assert r["deliverable"] is False
        assert r["fee"] is None
        assert "99999" in r["message"]

    def test_zip_with_extra_text(self):
        # The ZIP regex picks 5 consecutive digits.
        r = check_delivery("apt 4 - 33127 - delivery instructions")
        assert r["deliverable"] is True
        assert r["zone"] == 1


# ── Neighborhood-based fallback ───────────────────────────────────────────────

class TestNeighborhoodFallback:
    def test_wynwood_text(self):
        r = check_delivery("Somewhere in Wynwood")
        assert r["deliverable"] is True
        assert r["zone"] == 1

    def test_coral_gables_zone_2(self):
        r = check_delivery("123 Ponce de Leon, Coral Gables")
        assert r["deliverable"] is True
        assert r["zone"] == 2

    def test_kendall_zone_3(self):
        r = check_delivery("Some address in Kendall")
        assert r["deliverable"] is True
        assert r["zone"] == 3

    def test_case_insensitive(self):
        r = check_delivery("123 Main St, WYNWOOD")
        assert r["deliverable"] is True
        assert r["zone"] == 1

    def test_outside_range_neighborhood(self):
        # "homestead" is in NEIGHBORHOOD_ZONES with value None.
        r = check_delivery("Homestead address")
        assert r["deliverable"] is False
        assert "outside our delivery range" in r["message"].lower()


# ── Ambiguous / "needs more info" paths ──────────────────────────────────────

class TestUnknownDeliverable:
    def test_empty_address(self):
        r = check_delivery("")
        assert r["deliverable"] is None

    def test_whitespace_only_address(self):
        r = check_delivery("   ")
        assert r["deliverable"] is None

    def test_generic_miami_needs_zip(self):
        r = check_delivery("somewhere in Miami")
        assert r["deliverable"] is None
        assert r["needs_zip"] is True

    def test_clearly_out_of_state_rejected(self):
        # No FL indicator, no Miami, no ZIP.
        r = check_delivery("123 Broadway, New York, NY")
        assert r["deliverable"] is False


# ── Precedence: ZIP wins over neighborhood text ──────────────────────────────

class TestPrecedence:
    def test_zip_overrides_neighborhood_text(self):
        # Text says "Aventura" (zone 3) but ZIP is 33127 (zone 1) — ZIP wins.
        r = check_delivery("Definitely in Aventura, 33127")
        assert r["zone"] == 1


# ── Response shape ────────────────────────────────────────────────────────────

class TestResponseShape:
    def test_deliverable_response_has_all_fields(self):
        r = check_delivery("33127")
        for field in ("deliverable", "zone", "fee", "eta", "zone_label", "message", "needs_zip"):
            assert field in r

    def test_non_deliverable_has_none_fee_and_zone(self):
        r = check_delivery("99999")
        assert r["fee"] is None
        assert r["zone"] is None
        assert r["eta"] is None
