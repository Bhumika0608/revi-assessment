"""
Shared-secret authentication for the Inventory admin page.

The admin page exposes live stock levels, the restock form, and the order
history. In any non-local deployment this needs to be gated.

Contract:
  - `is_admin_configured()` returns True only when ADMIN_PASSWORD is set in env.
  - `verify_admin_password(submitted)` returns True only when the submitted
    string is non-empty AND constant-time-equals the configured password.
  - When ADMIN_PASSWORD is NOT set, verify_admin_password always returns False
    — admin features are hard-locked, never open-by-default.

Set the password in .env:
    ADMIN_PASSWORD=correct-horse-battery-staple
"""

from __future__ import annotations

import hmac
import os


def _configured_password() -> str | None:
    """Read ADMIN_PASSWORD from env, falling back to .env on first miss."""
    pw = os.getenv("ADMIN_PASSWORD")
    if pw:
        return pw
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    pw = os.getenv("ADMIN_PASSWORD")
    return pw if pw else None


def is_admin_configured() -> bool:
    """True when ADMIN_PASSWORD is set to a non-empty value."""
    return _configured_password() is not None


def verify_admin_password(submitted: str | None) -> bool:
    """Constant-time compare submitted password to ADMIN_PASSWORD.

    Returns False if:
      - ADMIN_PASSWORD is unset/empty (hard lock — never open),
      - submitted is None or empty,
      - submitted doesn't match.
    """
    expected = _configured_password()
    if not expected:
        return False
    if not submitted:
        return False
    # constant-time compare prevents timing-based leakage of correct prefixes.
    return hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))
