"""
Tax calculation — Miami-Dade County prepared food.
7% Florida state sales tax + 1% county discretionary surtax = 8%.
"""

from __future__ import annotations

MIAMI_DADE_TAX_RATE = 0.08


def calculate_tax(subtotal: float) -> float:
    return round(subtotal * MIAMI_DADE_TAX_RATE, 2)


def order_breakdown(subtotal: float, delivery_fee: float = 0.0) -> dict:
    """Return full price breakdown: subtotal, delivery_fee, tax, total."""
    tax = calculate_tax(subtotal)
    return {
        "subtotal":     round(subtotal, 2),
        "delivery_fee": round(delivery_fee, 2),
        "tax":          tax,
        "tax_rate_pct": int(MIAMI_DADE_TAX_RATE * 100),
        "total":        round(subtotal + delivery_fee + tax, 2),
    }
