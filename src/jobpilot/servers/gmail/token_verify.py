"""Gmail MCP side of the send token.

An Ed25519-signed v2 token when APPROVAL_VERIFY_KEY is set (Option B -
this server can't mint tokens), otherwise the v1 HMAC token.
"""

from jobpilot.core.config import get_settings
from jobpilot.core.errors import InvalidApprovalToken
from jobpilot.safety.approval_tokens import verify, verify_signed


def check(token: str, job_id: int, draft_id: str, version: int) -> None:
    """Raise InvalidApprovalToken unless the token approves this exact draft.

    Args:
        token: The token from the dispatcher.
        job_id: The job being sent.
        draft_id: The draft being sent.
        version: The version being sent.
    """
    settings = get_settings()
    if settings.approval_verify_key:
        verify_signed(settings.approval_verify_key, token, job_id=job_id, draft_id=draft_id, version=version)
        return
    key = settings.approval_token_key
    if key is None or not key.get_secret_value():
        raise InvalidApprovalToken("APPROVAL_TOKEN_KEY is not set")
    verify(key.get_secret_value().encode(), token, job_id=job_id, draft_id=draft_id, version=version)
