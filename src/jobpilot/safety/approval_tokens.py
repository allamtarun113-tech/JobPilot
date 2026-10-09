"""Short-lived send tokens over (job id, draft id, version, expiry).

Minted only by the dispatcher, at delivery time, from a durable approvals row; verified by the Gmail
MCP server before send_draft. Tokens are never written to the database, the outbox or traces.

Two formats:
- v1 (HMAC, APPROVAL_TOKEN_KEY): the same secret mints and verifies (threat model Option A).
- v2 (Ed25519, Option B): the dispatcher signs with APPROVAL_SIGNING_KEY; the Gmail server holds only
  APPROVAL_VERIFY_KEY (public), so a process that can reach the Gmail server can't mint a token unless it
  also holds the private key. When a verify key is configured, v1 tokens are refused.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

from jobpilot.core import clock
from jobpilot.core.errors import InvalidApprovalToken

DEFAULT_TTL_SECONDS = 120


@dataclass(frozen=True)
class SendClaim:
    """What a send token grants: one draft version of one job, until expiry."""

    job_id: int
    draft_id: str
    version: int
    expires_at: int  # unix seconds


def _b64(data: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    """Decode URL-safe base64, restoring the stripped padding."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def mint(
    key: bytes, *, job_id: int, draft_id: str, version: int, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    """Mint a v1 (HMAC) send token for one exact draft version.

    Args:
        key: The shared HMAC secret (APPROVAL_TOKEN_KEY).
        job_id: The job the email belongs to.
        draft_id: The Gmail draft id.
        version: The approved email version.
        ttl_seconds: How long the token stays valid.

    Returns:
        ``<body>.<signature>`` in URL-safe base64.

    Raises:
        ValueError: No key is configured.
    """
    if not key:
        raise ValueError("APPROVAL_TOKEN_KEY is not set")
    expires = int(clock.now().timestamp()) + ttl_seconds
    # Compact JSON so the token stays short.
    body = json.dumps(
        {"j": job_id, "d": draft_id, "v": version, "e": expires}, separators=(",", ":")
    ).encode()
    sig = hmac.new(key, body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(sig)}"


def new_signing_keypair() -> tuple[str, str]:
    """(private, public) Ed25519 keys as URL-safe base64 of the raw bytes - for .env."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    raw_public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b64(raw_private), _b64(raw_public)


def mint_signed(
    private_key: str, *, job_id: int, draft_id: str, version: int, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    """Mint a v2 (Ed25519) send token for one exact draft version.

    Args:
        private_key: The dispatcher's signing key (base64).
        job_id: The job the email belongs to.
        draft_id: The Gmail draft id.
        version: The approved email version.
        ttl_seconds: How long the token stays valid.

    Returns:
        ``v2.<body>.<signature>``.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    expires = int(clock.now().timestamp()) + ttl_seconds
    body = json.dumps(
        {"j": job_id, "d": draft_id, "v": version, "e": expires}, separators=(",", ":")
    ).encode()
    signature = Ed25519PrivateKey.from_private_bytes(_unb64(private_key)).sign(body)
    return f"v2.{_b64(body)}.{_b64(signature)}"


def verify_signed(public_key: str, token: str, *, job_id: int, draft_id: str, version: int) -> SendClaim:
    """Check a v2 token against the public key and the draft.

    Args:
        public_key: The Gmail server's verify key (base64).
        token: The token to check.
        job_id: The job being sent.
        draft_id: The draft being sent.
        version: The version being sent.

    Returns:
        The verified claim.

    Raises:
        InvalidApprovalToken: Bad signature, wrong format, expired, or
            for a different draft.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        prefix, body_part, sig_part = token.split(".", 2)
        if prefix != "v2":
            raise ValueError("not a v2 token")
        body, sig = _unb64(body_part), _unb64(sig_part)
        Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(sig, body)
    except (ValueError, InvalidSignature) as exc:
        raise InvalidApprovalToken("bad or unsigned token") from exc
    return _check_claim(json.loads(body), job_id=job_id, draft_id=draft_id, version=version)


def _check_claim(data: dict, *, job_id: int, draft_id: str, version: int) -> SendClaim:
    """Check a decoded token body for expiry and an exact draft match.

    Returns:
        The claim.

    Raises:
        InvalidApprovalToken: It expired or names another draft/version.
    """
    claim = SendClaim(job_id=data["j"], draft_id=data["d"], version=data["v"], expires_at=data["e"])
    if claim.expires_at < int(clock.now().timestamp()):
        raise InvalidApprovalToken("expired")
    if (claim.job_id, claim.draft_id, claim.version) != (job_id, draft_id, version):
        raise InvalidApprovalToken("token is for a different draft or version")
    return claim


def verify(key: bytes, token: str, *, job_id: int, draft_id: str, version: int) -> SendClaim:
    """Raise InvalidApprovalToken unless the token is authentic, unexpired and for exactly this draft."""
    try:
        body_part, sig_part = token.split(".", 1)
        body = _unb64(body_part)
        sig = _unb64(sig_part)
    except ValueError as exc:
        raise InvalidApprovalToken("malformed token") from exc
    # Constant-time comparison, so timing can't reveal the signature.
    if not hmac.compare_digest(sig, hmac.new(key, body, hashlib.sha256).digest()):
        raise InvalidApprovalToken("bad signature")
    return _check_claim(json.loads(body), job_id=job_id, draft_id=draft_id, version=version)
