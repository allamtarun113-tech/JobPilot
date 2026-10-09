"""Run stop rules and Telegram update parsing."""

from typing import Any

from jobpilot.bot.router import parse
from jobpilot.models.run import StopReason
from jobpilot.run.stop_rules import evaluate

RUN: dict[str, Any] = {"stop_requested": False}


def state(**kw: Any) -> dict[str, Any]:
    """Return a run state that would continue, overridden by ``kw``."""
    return {"card_cap": 5, "cards_made": 0, "pending_job_ids": [1], "consecutive_errors": 0} | kw


def test_stop_rules_order() -> None:
    """Stop rules apply in priority order; a used quota doesn't stop."""
    assert (
        evaluate(state(), RUN, killswitch=True, token_budget_left=10, api_quota_left=None)
        == StopReason.KILLSWITCH
    )
    assert (
        evaluate(
            state(), {"stop_requested": True}, killswitch=False, token_budget_left=10, api_quota_left=None
        )
        == StopReason.STOP_REQUESTED
    )
    assert (
        evaluate(
            state(consecutive_errors=3), RUN, killswitch=False, token_budget_left=10, api_quota_left=None
        )
        == StopReason.ERROR_SPIKE
    )
    assert (
        evaluate(state(), RUN, killswitch=False, token_budget_left=0, api_quota_left=None)
        == StopReason.BUDGET
    )
    assert (
        evaluate(state(), RUN, killswitch=False, token_budget_left=1, api_quota_left=0)
        is None  # a used-up jobs-API quota skips the jobs API but never stops processing jobs
    )
    assert (
        evaluate(state(cards_made=5), RUN, killswitch=False, token_budget_left=1, api_quota_left=None)
        == StopReason.CARD_CAP
    )
    assert (
        evaluate(state(pending_job_ids=[]), RUN, killswitch=False, token_budget_left=1, api_quota_left=None)
        == StopReason.QUEUE_EMPTY
    )
    assert evaluate(state(), RUN, killswitch=False, token_budget_left=1, api_quota_left=None) is None


def test_parse_update_kinds() -> None:
    """Updates are told apart as command, message, tap or other."""
    assert parse({"update_id": 1, "message": {"chat": {"id": 9}, "text": "/start 3"}}).type == "command"
    assert parse({"update_id": 1, "message": {"chat": {"id": 9}, "text": "make it formal"}}).type == "message"
    tap = parse(
        {"update_id": 1, "callback_query": {"id": "x", "data": "a:1", "message": {"chat": {"id": 9}}}}
    )
    assert (tap.type, tap.chat_id, tap.text) == ("tap", 9, "a:1")
    assert parse({"update_id": 1, "poll": {}}).type == "other"
