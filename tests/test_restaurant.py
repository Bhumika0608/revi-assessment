"""
Tests for db/restaurant.py — single source of truth for restaurant facts.

Verifies the constants load from data/menu.json's `restaurant` block and that
the derived strings (PHONE_TEL, SHORT_STREET, FOOTER_LINE) are formatted
correctly. Also a smoke test that the literal phone is no longer hardcoded
across Python source files.

Run: python3 -m pytest tests/test_restaurant.py -v
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db import restaurant


def _menu_json() -> dict:
    with open(ROOT / "data" / "menu.json") as f:
        return json.load(f).get("restaurant", {})


class TestConstants:
    def test_name_matches_json(self):
        assert restaurant.NAME == _menu_json().get("name")

    def test_address_matches_json(self):
        assert restaurant.ADDRESS == _menu_json()["location"]["address"]

    def test_neighborhood_matches_json(self):
        assert restaurant.NEIGHBORHOOD == _menu_json()["location"]["neighborhood"]

    def test_phone_matches_json(self):
        assert restaurant.PHONE == _menu_json()["location"]["phone"]

    def test_hours_line_matches_json(self):
        assert restaurant.HOURS_LINE == _menu_json().get("hours_line")


class TestDerivedStrings:
    def test_phone_tel_is_e164(self):
        # +1 followed by digits only.
        assert restaurant.PHONE_TEL.startswith("+1")
        assert restaurant.PHONE_TEL[2:].isdigit()
        # Same digits as PHONE, just punctuation stripped.
        digits_only = "".join(c for c in restaurant.PHONE if c.isdigit())
        assert restaurant.PHONE_TEL == "+1" + digits_only

    def test_short_street_strips_city_state_zip(self):
        # First comma-delimited segment.
        assert restaurant.SHORT_STREET == restaurant.ADDRESS.split(",", 1)[0].strip()
        # Sanity: no commas in the result.
        assert "," not in restaurant.SHORT_STREET

    def test_short_location_includes_neighborhood(self):
        assert restaurant.NEIGHBORHOOD in restaurant.SHORT_LOCATION
        assert restaurant.SHORT_STREET in restaurant.SHORT_LOCATION

    def test_footer_line_includes_all_parts(self):
        # name · street · neighborhood · hours
        for part in (restaurant.NAME, restaurant.SHORT_STREET,
                     restaurant.NEIGHBORHOOD, restaurant.HOURS_LINE):
            assert part in restaurant.FOOTER_LINE

    def test_location_tagline_excludes_name(self):
        assert restaurant.NAME not in restaurant.LOCATION_TAGLINE
        assert restaurant.SHORT_STREET in restaurant.LOCATION_TAGLINE
        assert restaurant.HOURS_LINE in restaurant.LOCATION_TAGLINE


# ── No straggler hardcoded literals in code ──────────────────────────────────

_SCAN_DIRS = ["agent", "db", "ui", "evals", "demo.py"]
_LITERAL_PHONE = "(305) 555-0142"
# db/restaurant.py legitimately contains the literal as a fallback when
# data/menu.json is unreadable. That's the ONE place it's allowed to live.
_ALLOWED_FILES = {"db/restaurant.py"}


class TestNoHardcodedStragglers:
    def test_no_literal_phone_in_code(self):
        """If centralization regressed, this catches it. Skips: data/, tests/,
        and the email tel:href which uses PHONE_TEL (different format)."""
        offenders = []
        for entry in _SCAN_DIRS:
            path = ROOT / entry
            if path.is_file() and path.suffix == ".py":
                files = [path]
            elif path.is_dir():
                files = list(path.rglob("*.py"))
            else:
                continue
            for f in files:
                if "__pycache__" in f.parts:
                    continue
                rel = str(f.relative_to(ROOT))
                if rel in _ALLOWED_FILES:
                    continue
                text = f.read_text(encoding="utf-8")
                if _LITERAL_PHONE in text:
                    offenders.append(rel)
        assert offenders == [], (
            f"Literal {_LITERAL_PHONE!r} should not appear in code; "
            f"import db.restaurant.PHONE instead. Offenders: {offenders}"
        )


# ── FAQ template substitution ────────────────────────────────────────────────

class TestFaqPhoneSubstitution:
    def test_phone_placeholder_replaced_at_load(self):
        # _load_faq mutates and returns the entries with {phone} substituted.
        from db.search import _load_faq
        entries = _load_faq()
        # Some entries (dietary_query type) have no answer field — coerce to "".
        joined = " ".join((e.get("answer") or "") for e in entries)
        assert restaurant.PHONE in joined, "phone should be substituted into FAQ answers"
        assert "{phone}" not in joined, "phone placeholder should be replaced"
