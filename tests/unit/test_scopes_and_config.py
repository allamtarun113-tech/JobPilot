"""MCP scopes, config defaults, pinned prompts and pricing."""

import pytest

from jobpilot.core.config import get_config
from jobpilot.core.errors import ScopeDenied
from jobpilot.integrations import mcp_scopes
from jobpilot.llm import prompts
from jobpilot.llm.pricing import cost_usd


def test_worker_cannot_send_dispatcher_can() -> None:
    """Only the dispatcher may send; only the worker may search and fetch."""
    with pytest.raises(ScopeDenied):
        mcp_scopes.check("worker", "telegram", "send_message")
    mcp_scopes.check("dispatcher", "telegram", "send_message")
    with pytest.raises(ScopeDenied):
        mcp_scopes.check("worker", "gmail", "send_draft")
    mcp_scopes.check("worker", "web", "search")
    mcp_scopes.check("worker", "web", "fetch")
    with pytest.raises(ScopeDenied):
        mcp_scopes.check("dispatcher", "web", "fetch")


def test_config_defaults_match_design() -> None:
    """Shadow mode is on and the defaults match the design."""
    cfg = get_config()
    assert cfg.shadow_mode is True
    assert cfg.match_threshold == 70
    assert cfg.queue.expiry_days == 10
    assert cfg.outreach["company_window_days"] == 30
    assert set(cfg.models.values()) == {"gpt-4o-mini"}
    assert cfg.model_for("tailor") == "gpt-4o-mini"
    assert cfg.model_for("extract_requirements") == "gpt-4o-mini"
    assert cfg.llm.effort_for("tailor", "gpt-5-nano") == "low"
    assert cfg.llm.effort_for("resume_composer", "gpt-5-nano") == "medium"
    assert cfg.llm.effort_for("tailor", "gpt-4o-mini") is None


def test_prompt_renders_pinned_version() -> None:
    """A prompt renders at its pinned version."""
    p = prompts.render("system/ping", word="pilot")
    assert p.id == "system/ping_v1"
    assert "pilot" in p.user and p.system


def test_pricing() -> None:
    """Costs follow the configured prices; unknown models cost 0."""
    assert cost_usd("gpt-4o-mini", input_tokens=1_000_000, output_tokens=0) == pytest.approx(0.15)
    assert cost_usd("gpt-4o-mini", input_tokens=0, output_tokens=1_000_000) == pytest.approx(0.60)
    assert cost_usd("gpt-5-nano", input_tokens=1_000_000, output_tokens=1_000_000) == pytest.approx(0.45)
    assert cost_usd("unknown-model", input_tokens=10, output_tokens=10) == 0.0
