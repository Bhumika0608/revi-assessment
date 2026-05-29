#!/usr/bin/env python3
"""
Validate evals/test_cases.json before committing changes.

Checks each case for:
  - Required schema fields (id, title, category, turns, expected_status)
  - Every expected item_id exists in data/menu.json
  - Every modifier id is a real modifier on its parent item
  - expected_subtotal equals Σ qty × (price + modifier_upcharge) within $0.02
  - expected_status is one of {confirmed, in_progress, refused}

Run before pushing changes to test cases:
    python3 -m evals.validate_cases
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CASES = ROOT / "evals" / "test_cases.json"
MENU  = ROOT / "data" / "menu.json"

VALID_STATUSES = {"confirmed", "in_progress", "refused"}
SUBTOTAL_TOLERANCE = 0.02


def validate() -> tuple[list[str], list[str]]:
    cases = json.loads(CASES.read_text())
    menu  = json.loads(MENU.read_text())["items"]
    by_id = {it["id"]: it for it in menu}

    errors, warnings = [], []

    for c in cases:
        cid = c.get("id", "<missing id>")

        for f in ("id", "title", "category", "turns", "expected_status"):
            if f not in c:
                errors.append(f"{cid}: missing required field {f!r}")

        for e in c.get("expected_items", []):
            item_id = e.get("item_id")
            if item_id not in by_id:
                errors.append(f"{cid}: unknown item_id {item_id!r}")
                continue
            valid_mods = {m["id"] for m in by_id[item_id].get("modifiers", [])}
            for m in e.get("modifiers", []):
                if m not in valid_mods:
                    errors.append(f"{cid}: unknown modifier {m!r} on {item_id}")

        if c.get("expected_items") and c.get("expected_subtotal") is not None:
            total = 0.0
            for e in c["expected_items"]:
                it = by_id.get(e["item_id"])
                if not it:
                    continue
                price = it["price"]
                mod_prices = {m["id"]: float(m.get("price", 0.0)) for m in it.get("modifiers", [])}
                upcharge = sum(mod_prices.get(m, 0.0) for m in e.get("modifiers", []))
                qty = e.get("quantity", 1)
                total += qty * (price + upcharge)
            total = round(total, 2)
            expected = c["expected_subtotal"]
            if abs(total - expected) > SUBTOTAL_TOLERANCE:
                errors.append(
                    f"{cid}: subtotal mismatch — computed ${total:.2f}, expected ${expected:.2f}"
                )

        status = c.get("expected_status")
        if status not in VALID_STATUSES:
            errors.append(f"{cid}: invalid expected_status {status!r}")

        if status == "confirmed" and not c.get("expected_items"):
            warnings.append(f"{cid}: confirmed without expected_items — item metrics will be skipped")

    print(f"Validated {len(cases)} cases.")
    print(f"Errors:   {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    for e in errors:
        print(f"  ERROR    {e}")
    for w in warnings:
        print(f"  warning  {w}")

    return errors, warnings


if __name__ == "__main__":
    errors, _ = validate()
    sys.exit(1 if errors else 0)
