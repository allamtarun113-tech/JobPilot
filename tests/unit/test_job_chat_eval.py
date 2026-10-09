"""The offline Job Chat eval: prompt isolation and the allowed actions."""

from evals.runners import job_chat


def test_fifty_scripted_requests_keep_injected_jds_isolated() -> None:
    """All 50 scripted cases keep the JD fenced away from instructions."""
    rows, failures = job_chat.run()
    assert len(rows) == 50
    assert failures == []


def test_job_chat_has_no_direct_approval_apply_send_or_confirm_action() -> None:
    """Job Chat can't approve, apply, send or confirm; tier 2 needs Confirm."""
    for action in ("approve", "apply", "send_email", "confirm_proposal"):
        assert not job_chat.action_allowed(action)
    for action in ("update_profile", "change_prefs", "skip_snooze"):
        assert job_chat.action_allowed(action)
        assert job_chat.needs_confirmation(action)
