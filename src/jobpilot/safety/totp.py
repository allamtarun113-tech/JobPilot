"""RFC 6238 TOTP (SHA-1, 30 s, 6 digits) for the single-user dashboard login."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct

from jobpilot.core import clock


def new_secret() -> str:
    """Return a new random base32 TOTP secret (160 bits)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, counter: int, digits: int = 6) -> str:
    """Return the TOTP code for a time step (RFC 4226 truncation).

    Args:
        secret: The base32 secret, padding optional.
        counter: The 30-second time step.
        digits: The code length.

    Returns:
        The zero-padded code.
    """
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    # Dynamic truncation: the last nibble picks 4 bytes of the digest.
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def matching_counter(secret: str, code: str, window: int = 1) -> int | None:
    """The 30-second time step the code belongs to (for replay protection), or None if it is wrong."""
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return None
    counter = int(clock.now().timestamp()) // 30
    # Accept the previous and next step too, for clock drift.
    for d in range(-window, window + 1):
        if hmac.compare_digest(code_at(secret, counter + d), code):
            return counter + d
    return None


def verify(secret: str, code: str, window: int = 1) -> bool:
    """Return True if ``code`` is valid now (within the drift window)."""
    return matching_counter(secret, code, window) is not None


def provisioning_uri(secret: str, account: str = "owner", issuer: str = "JobPilot") -> str:
    """Return the ``otpauth://`` URI an authenticator app scans."""
    return f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}"
