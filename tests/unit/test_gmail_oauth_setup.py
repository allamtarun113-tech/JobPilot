"""Gmail sign-in: the OAuth callback checks the anti-forgery state."""

from jobpilot.servers.gmail.oauth_setup import callback_result


def test_callback_requires_current_state() -> None:
    """Only a callback with the current state yields the code."""
    assert callback_result("/?state=old&code=secret", "current") == {}
    assert callback_result("/favicon.ico", "current") == {}
    assert callback_result("/?code=secret", "current") == {}
    assert callback_result("/?state=current&code=secret", "current") == {"code": "secret"}


def test_callback_reports_denial_without_accepting_code() -> None:
    """A denial is reported and its code ignored."""
    assert callback_result("/?state=current&error=access_denied&code=secret", "current") == {
        "error": "access_denied"
    }
    assert callback_result("/?state=old&error=access_denied", "current") == {}
