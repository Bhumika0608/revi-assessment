"""
Tests for db/admin_auth.py — the gate guarding the Inventory admin page.

Verifies:
  - ADMIN_PASSWORD env unset → all access denied (hard lock, never open).
  - Correct password → True; wrong password → False; empty → False.
  - is_admin_configured reflects env state.

Run: python3 -m pytest tests/test_admin_auth.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.admin_auth import is_admin_configured, verify_admin_password


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Clear ADMIN_PASSWORD before each test, and stub out load_dotenv so .env
    on the developer machine can't leak the real password into the test run."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    import db.admin_auth as auth_mod
    # If python-dotenv is installed it will load .env — block that import for tests.
    monkeypatch.setattr(auth_mod, "_configured_password",
                        lambda: __import__("os").environ.get("ADMIN_PASSWORD") or None)
    yield


# ── is_admin_configured ───────────────────────────────────────────────────────

class TestIsAdminConfigured:
    def test_false_when_env_unset(self):
        assert is_admin_configured() is False

    def test_false_when_env_empty_string(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "")
        assert is_admin_configured() is False

    def test_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
        assert is_admin_configured() is True


# ── verify_admin_password: hard-lock when env unset ───────────────────────────

class TestHardLockWhenUnconfigured:
    def test_empty_submission_denied(self):
        assert verify_admin_password("") is False

    def test_none_submission_denied(self):
        assert verify_admin_password(None) is False

    def test_any_submission_denied(self):
        # No ADMIN_PASSWORD in env → no value can ever authenticate.
        assert verify_admin_password("anything") is False
        assert verify_admin_password("admin") is False
        assert verify_admin_password("password") is False

    def test_empty_env_var_does_not_unlock_with_empty_submission(self, monkeypatch):
        # Critical: "" == "" must NOT authenticate. Empty env var = unconfigured.
        monkeypatch.setenv("ADMIN_PASSWORD", "")
        assert verify_admin_password("") is False


# ── verify_admin_password: configured env ─────────────────────────────────────

class TestVerifyWhenConfigured:
    def test_correct_password_returns_true(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
        assert verify_admin_password("correct-horse") is True

    def test_wrong_password_returns_false(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
        assert verify_admin_password("wrong-horse") is False

    def test_empty_submission_denied(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
        assert verify_admin_password("") is False

    def test_none_submission_denied(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
        assert verify_admin_password(None) is False

    def test_case_sensitive(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "Hunter2")
        assert verify_admin_password("hunter2") is False
        assert verify_admin_password("Hunter2") is True

    def test_whitespace_matters(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
        assert verify_admin_password(" hunter2") is False
        assert verify_admin_password("hunter2 ") is False

    def test_prefix_of_correct_password_denied(self, monkeypatch):
        # Defends against truncation bugs / short comparisons.
        monkeypatch.setenv("ADMIN_PASSWORD", "hunter2longer")
        assert verify_admin_password("hunter2") is False

    def test_unicode_password(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pässwörd-🌮")
        assert verify_admin_password("pässwörd-🌮") is True
        assert verify_admin_password("password-🌮") is False
