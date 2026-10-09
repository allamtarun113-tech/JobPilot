"""Durable approvals: the 24-hour rule for email sends (needs re-confirm with one fresh tap)."""

from __future__ import annotations

from jobpilot.safety.approval_window import expired, expires_at

__all__ = ["expired", "expires_at"]
