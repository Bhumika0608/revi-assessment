"""
Delivery zone and fee logic for Talkin' Tacos.

No external API needed — uses zip code and neighborhood name matching.
Zone boundaries are approximate driving distances from the restaurant.
"""

from __future__ import annotations
import re

from db.restaurant import ADDRESS as RESTAURANT_ADDRESS  # noqa: F401 — re-exported

# ── Zone definitions ──────────────────────────────────────────────────────────

ZONE_FEES = {
    1: 2.99,   # 0–2 miles
    2: 4.99,   # 2–5 miles
    3: 7.99,   # 5–10 miles
}

ZONE_LABELS = {
    1: "0–2 miles",
    2: "2–5 miles",
    3: "5–10 miles",
}

ZONE_ETAS = {
    "pickup":   "10–15 minutes",
    1:          "20–25 minutes",
    2:          "30–40 minutes",
    3:          "45–60 minutes",
}

# ── Miami-Dade zip codes → delivery zone ─────────────────────────────────────

ZIP_ZONES: dict[str, int] = {
    # Zone 1 — Wynwood, Midtown, Design District, Brickell, Downtown, Overtown
    "33127": 1,  # Wynwood (restaurant itself)
    "33137": 1,  # Midtown Miami / Design District
    "33132": 1,  # Downtown / Biscayne Boulevard
    "33128": 1,  # Government Center / Overtown
    "33130": 1,  # Brickell / Little Havana edge
    "33131": 1,  # Brickell South / Claughton Island
    "33136": 1,  # Health District / Jackson Memorial
    "33142": 1,  # Allapattah / Civic Center
    "33125": 1,  # Little Havana (north)
    "33129": 1,  # Coconut Grove / Brickell adjacent
    "33133": 1,  # Coconut Grove main

    # Zone 2 — South Beach, Coral Gables, Upper East Side, Little Haiti, North Beach
    "33134": 2,  # Coral Gables
    "33135": 2,  # Little Havana (west)
    "33138": 2,  # Upper East Side / MiMo District
    "33139": 2,  # South Beach (SoBe)
    "33140": 2,  # Mid-Beach
    "33141": 2,  # North Beach
    "33144": 2,  # Westchester
    "33145": 2,  # Coral Gables (east)
    "33146": 2,  # South Miami / Dadeland adjacent
    "33150": 2,  # Little Haiti
    "33126": 2,  # Flagami / Tamiami
    "33109": 2,  # Fisher Island / Star Island

    # Zone 3 — Aventura, Kendall, Doral, Key Biscayne, North Miami, Hialeah
    "33149": 3,  # Key Biscayne
    "33155": 3,  # West Miami / Bird Road
    "33157": 3,  # Cutler Bay / Palmetto Bay
    "33160": 3,  # Aventura / Sunny Isles Beach
    "33162": 3,  # North Miami Beach
    "33163": 3,  # North Miami Beach (west)
    "33166": 3,  # Doral / Miami Springs
    "33172": 3,  # Sweetwater / FIU area
    "33174": 3,  # FIU / Tamiami
    "33175": 3,  # Kendall West
    "33176": 3,  # Kendall
    "33177": 3,  # Perrine
    "33178": 3,  # Doral (west)
    "33179": 3,  # North Miami Beach
    "33180": 3,  # Aventura
    "33181": 3,  # North Miami Beach (east)
    "33183": 3,  # Kendall
    "33186": 3,  # Kendall South / The Hammocks
    "33193": 3,  # Kendall West
    "33167": 3,  # Miami Gardens (south)
    "33168": 3,  # Miami Gardens
    "33169": 3,  # Miami Gardens
    "33056": 3,  # Miami Gardens
    "33054": 3,  # Opa-locka
    "33055": 3,  # Miami Gardens (west)
    "33147": 3,  # Liberty City / Hialeah border
    "33161": 3,  # North Miami
    "33010": 3,  # Hialeah
    "33012": 3,  # Hialeah
    "33013": 3,  # Hialeah
    "33014": 3,  # Hialeah (north)
    "33015": 3,  # Miami Lakes / Hialeah
    "33016": 3,  # Hialeah Gardens
}

# ── Neighborhood name → zone (fallback when no zip found) ────────────────────

NEIGHBORHOOD_ZONES: dict[str, int | None] = {
    # Zone 1
    "wynwood":         1, "midtown":          1, "design district":  1,
    "brickell":        1, "downtown miami":   1, "downtown":         1,
    "overtown":        1, "allapattah":       1, "little havana":    1,
    "health district": 1, "edgewater":        1, "arts district":    1,

    # Zone 2
    "coconut grove":   2, "coral gables":     2, "south beach":      2,
    "sobe":            2, "miami beach":       2, "mid-beach":        2,
    "mid beach":       2, "north beach":       2, "upper east side":  2,
    "mimo":            2, "little haiti":      2, "flagami":          2,
    "westchester":     2, "south miami":       2, "bird road":        2,

    # Zone 3
    "key biscayne":    3, "aventura":          3, "sunny isles":      3,
    "kendall":         3, "doral":             3, "north miami":      3,
    "miami gardens":   3, "hialeah":           3, "sweetwater":       3,
    "opa-locka":       3, "opa locka":         3, "miami lakes":      3,
    "cutler bay":      3, "palmetto bay":      3, "pinecrest":        3,
    "liberty city":    3, "hialeah gardens":   3, "medley":           3,

    # Outside delivery range
    "homestead":   None, "florida city":  None, "marathon":     None,
    "key west":    None, "fort lauderdale": None, "hollywood fl": None,
    "hallandale":  None, "miramar":       None, "pembroke":      None,
    "sunrise":     None, "plantation":    None, "weston":        None,
    "boca raton":  None, "miami gardens north": None,
}

MIN_ORDER_DELIVERY = 10.00   # minimum subtotal for delivery


# ── Main function ─────────────────────────────────────────────────────────────

def check_delivery(address: str) -> dict:
    """
    Determine whether we deliver to the given address and at what fee.

    Returns:
        {
            "deliverable":  True | False | None,  # None = can't determine
            "zone":         int | None,
            "fee":          float | None,
            "eta":          str | None,
            "zone_label":   str | None,           # "0–2 miles" etc.
            "message":      str,
            "needs_zip":    bool,                 # True = ask user to add zip
        }
    """
    if not address or not address.strip():
        return _unknown("Please enter your delivery address.")

    addr = address.strip()
    addr_lower = addr.lower()

    # ── 1. ZIP code extraction ────────────────────────────────────────────────
    # A US address can have multiple 5-digit runs (street number + ZIP, e.g.
    # "19501 Biscayne Blvd, Aventura, FL 33180"). Prefer any run that matches a
    # known ZIP; only fall back to the last run if none of them are recognised.
    five_digit_runs = re.findall(r'\b(\d{5})\b', addr)
    known_zips = [z for z in five_digit_runs if z in ZIP_ZONES]
    if known_zips:
        zip_code = known_zips[-1]   # last known ZIP — the trailing one in the address
        zone = ZIP_ZONES[zip_code]
        return _hit(zone, f"We deliver to your area (ZIP {zip_code}).")

    if five_digit_runs:
        zip_code = five_digit_runs[-1]   # for the rejection message, name the ZIP-shaped trailing run
        return _no(
            f"Sorry, ZIP code {zip_code} is outside our delivery range. "
            "We currently deliver within Miami-Dade County (up to 10 miles from Wynwood)."
        )

    # ── 2. Neighborhood name matching ─────────────────────────────────────────
    for neighborhood, zone in NEIGHBORHOOD_ZONES.items():
        if neighborhood in addr_lower:
            if zone is None:
                return _no(
                    f"{neighborhood.title()} is outside our delivery range. "
                    "We deliver up to 10 miles from Wynwood."
                )
            return _hit(zone, f"We deliver to {neighborhood.title()}!")

    # ── 3. Generic "Miami" check without enough detail ────────────────────────
    if "miami" in addr_lower:
        return _unknown(
            "We deliver across Miami-Dade! Please include your ZIP code so we can "
            "confirm delivery and calculate the exact fee.",
            needs_zip=True,
        )

    # ── 4. Clearly outside Florida ────────────────────────────────────────────
    fl_indicators = ["fl ", "florida", "miami", "33"]
    if not any(ind in addr_lower for ind in fl_indicators):
        return _no(
            "We only deliver within Miami-Dade County. "
            "Your address doesn't appear to be in our service area."
        )

    return _unknown(
        "Please include your ZIP code so we can check delivery to your address.",
        needs_zip=True,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hit(zone: int, message: str) -> dict:
    return {
        "deliverable": True,
        "zone":        zone,
        "fee":         ZONE_FEES[zone],
        "eta":         ZONE_ETAS[zone],
        "zone_label":  ZONE_LABELS[zone],
        "message":     message,
        "needs_zip":   False,
    }


def _no(message: str) -> dict:
    return {
        "deliverable": False,
        "zone":        None,
        "fee":         None,
        "eta":         None,
        "zone_label":  None,
        "message":     message,
        "needs_zip":   False,
    }


def _unknown(message: str, needs_zip: bool = False) -> dict:
    return {
        "deliverable": None,
        "zone":        None,
        "fee":         None,
        "eta":         None,
        "zone_label":  None,
        "message":     message,
        "needs_zip":   needs_zip,
    }
