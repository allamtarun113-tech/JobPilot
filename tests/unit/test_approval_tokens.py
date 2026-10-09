"""Send tokens: exact draft binding, expiry and signatures."""

import pytest

from jobpilot.core.clock import FrozenClock
from jobpilot.core.errors import InvalidApprovalToken
from jobpilot.safety import approval_tokens

KEY = b"k" * 32


def test_valid_token_verifies(frozen_clock: FrozenClock) -> None:
    """A fresh token verifies for its own draft and version."""
    token = approval_tokens.mint(KEY, job_id=1, draft_id="d1", version=3)
    claim = approval_tokens.verify(KEY, token, job_id=1, draft_id="d1", version=3)
    assert (claim.job_id, claim.draft_id, claim.version) == (1, "d1", 3)


def test_token_for_other_version_is_refused(frozen_clock: FrozenClock) -> None:
    """A token for another version is refused."""
    token = approval_tokens.mint(KEY, job_id=1, draft_id="d1", version=3)
    with pytest.raises(InvalidApprovalToken):
        approval_tokens.verify(KEY, token, job_id=1, draft_id="d1", version=4)


def test_expired_token_is_refused(frozen_clock: FrozenClock) -> None:
    """A token past its lifetime is refused."""
    token = approval_tokens.mint(KEY, job_id=1, draft_id="d1", version=3, ttl_seconds=60)
    frozen_clock.advance(seconds=61)
    with pytest.raises(InvalidApprovalToken, match="expired"):
        approval_tokens.verify(KEY, token, job_id=1, draft_id="d1", version=3)


def test_tampered_or_foreign_token_is_refused(frozen_clock: FrozenClock) -> None:
    """A token signed with another key, or garbage, is refused."""
    token = approval_tokens.mint(KEY, job_id=1, draft_id="d1", version=3)
    with pytest.raises(InvalidApprovalToken):
        approval_tokens.verify(b"other-key", token, job_id=1, draft_id="d1", version=3)
    with pytest.raises(InvalidApprovalToken):
        approval_tokens.verify(KEY, "garbage", job_id=1, draft_id="d1", version=3)


def test_option_b_signed_tokens_verify_with_the_public_key_only() -> None:
    """Ed25519 tokens verify only with the matching public key."""
    from jobpilot.safety.approval_tokens import mint_signed, new_signing_keypair, verify_signed

    private, public = new_signing_keypair()
    token = mint_signed(private, job_id=1, draft_id="d", version=2)
    assert verify_signed(public, token, job_id=1, draft_id="d", version=2).job_id == 1
    _, other_public = new_signing_keypair()
    with pytest.raises(InvalidApprovalToken):
        verify_signed(other_public, token, job_id=1, draft_id="d", version=2)
    with pytest.raises(InvalidApprovalToken):
        verify_signed(public, token, job_id=1, draft_id="d", version=3)
    with pytest.raises(InvalidApprovalToken):  # an HMAC (v1) token is refused once a verify key is used
        verify_signed(public, token.replace("v2.", "", 1), job_id=1, draft_id="d", version=2)
