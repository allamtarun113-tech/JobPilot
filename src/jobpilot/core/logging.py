"""Plain key=value logging to stderr. Never log secrets, tokens or message bodies with PII."""

from __future__ import annotations

import logging
import sys


def setup_logging(process: str, level: str = "INFO") -> None:
    """Send log records to stderr in a plain one-line format.

    Args:
        process: Process name shown on each line (http, worker, ...).
        level: The root log level.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(f"%(asctime)s %(levelname)s {process} %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    root = logging.getLogger()
    # Replace any handlers installed earlier so lines aren't printed
    # twice.
    root.handlers[:] = [handler]
    root.setLevel(level)
    # These libraries log every request at INFO; keep only warnings.
    for noisy in ("httpx", "httpcore", "httpx2", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
