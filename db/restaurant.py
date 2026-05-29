"""
Centralized restaurant facts — single source of truth.

Loaded once at import from data/menu.json's `restaurant` block. Import these
constants instead of hardcoding the name/address/phone/hours in UI, email
templates, prompts, FAQ-fallback strings, etc.

If menu.json is unreadable, sensible defaults are used so the app still boots.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_MENU_JSON = _ROOT / "data" / "menu.json"


def _load_block() -> dict:
    try:
        with open(_MENU_JSON) as f:
            return json.load(f).get("restaurant", {}) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_DATA = _load_block()
_LOC  = _DATA.get("location", {}) if isinstance(_DATA.get("location"), dict) else {}

NAME            = _DATA.get("name",        "Talkin' Tacos")
ADDRESS         = _LOC.get("address",      "127 NW 25th St, Miami, FL 33127")
NEIGHBORHOOD    = _LOC.get("neighborhood", "Wynwood")
PHONE           = _LOC.get("phone",        "(305) 555-0142")
HOURS_LINE      = _DATA.get("hours_line",  "Mon–Sun 11am–10pm")

# Derived strings ─────────────────────────────────────────────────────────────

# tel: href format (E.164 with leading +1 for US). Strip everything non-digit.
PHONE_TEL       = "+1" + re.sub(r"\D", "", PHONE)

# Street portion of ADDRESS — everything before the first comma.
# e.g. "127 NW 25th St, Miami, FL 33127" → "127 NW 25th St".
SHORT_STREET    = ADDRESS.split(",", 1)[0].strip()

# Pickup / header form including neighborhood. e.g. "127 NW 25th St, Wynwood, Miami".
SHORT_LOCATION  = f"{SHORT_STREET}, {NEIGHBORHOOD}, Miami"

# Footer line used in email + UI captions.
FOOTER_LINE     = f"{NAME} · {SHORT_STREET} · {NEIGHBORHOOD} · {HOURS_LINE}"

# Same idea, but without the restaurant name (used in email subheaders that
# already show the name in a larger heading above).
LOCATION_TAGLINE = f"{SHORT_STREET} · {NEIGHBORHOOD} · {HOURS_LINE}"
