"""Centralized logging setup. Call setup_logging() once at app entry points."""

from __future__ import annotations

import logging
import os
import sys

_configured = False


def setup_logging(level: str | None = None) -> None:
    """Configure stdlib logging with a single line-per-event format on stderr.

    Idempotent — safe to call from multiple entry points (ui/app.py, demo.py,
    evals/run_evals.py). The format keeps logs parseable for production grep.
    LOG_LEVEL env var overrides the default (INFO).
    """
    global _configured
    if _configured:
        return
    effective_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=effective_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
        force=False,
    )
    _configured = True
