"""The single model client. Only this module talks to the model API.

Pinned model per task, typed (Pydantic) structured output, one retry on invalid output,
OpenAI automatic cache accounting, token + cost accounting
in llm_usage, and a span per call.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from opentelemetry import trace
from pydantic import BaseModel, ValidationError

from jobpilot.core.config import get_config, get_settings
from jobpilot.core.errors import LLMInvalidOutput, LLMRefusal
from jobpilot.db.queries import llm_usage
from jobpilot.db.queries import runs as runs_repo
from jobpilot.db.session import transaction
from jobpilot.llm.pricing import cost_usd
from jobpilot.llm.prompts import RenderedPrompt
from jobpilot.llm.schema import strict_schema

if TYPE_CHECKING:
    from openai import AsyncOpenAI

log = logging.getLogger(__name__)
tracer = trace.get_tracer("jobpilot.llm")
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMResult[R: BaseModel]:
    """A validated model output with its model, prompt id, tokens and cost."""

    output: R
    model: str
    prompt_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


class LLMClient:
    """The one client for model calls (OpenAI Responses API)."""

    def __init__(self, api: AsyncOpenAI | None = None) -> None:
        """Create the client.

        Args:
            api: An OpenAI client to use (tests pass a fake); by default
                one is built from ``OPENAI_API_KEY``.
        """
        if api is None:
            key = get_settings().openai_api_key
            from openai import AsyncOpenAI  # loaded on first use: the dashboard never needs the SDK

            api = AsyncOpenAI(api_key=key.get_secret_value() if key else None)
        self.api = api

    async def structured(
        self,
        task: str,
        prompt: RenderedPrompt,
        output: type[T],
        *,
        run_id: int | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult[T]:
        """One structured call for `task`. Retries once on invalid output, then raises LLMInvalidOutput."""
        cfg = get_config()
        model = cfg.model_for(task)
        token_limit = max_tokens if max_tokens is not None else cfg.llm.max_tokens
        if not 1 <= token_limit <= 128000:
            raise ValueError("max_tokens must be between 1 and 128000")
        effort = cfg.llm.effort_for(task, model)
        if effort is not None:  # hidden reasoning tokens count against the output limit
            token_limit = min(token_limit + cfg.llm.reasoning_headroom_tokens, 128000)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_output_tokens": token_limit,
            # Don't let OpenAI keep the prompts (they hold the user's
            # profile).
            "store": False,
            "input": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": output.__name__,
                    "strict": True,
                    "schema": strict_schema(output),
                }
            },
        }
        if effort is not None:
            kwargs["reasoning"] = {"effort": effort}

        last_error: Exception | None = None
        for attempt in (1, 2):
            with tracer.start_as_current_span(
                "llm.call",
                attributes={
                    "llm.task": task,
                    "llm.model": model,
                    "llm.prompt": prompt.id,
                    "llm.attempt": attempt,
                    "llm.reasoning_effort": effort or "none",
                },
            ) as span:
                response = await self.api.responses.create(**kwargs)
                usage = response.usage
                if usage is None:
                    raise LLMInvalidOutput(f"{task}: response has no usage accounting")
                cache_read = usage.input_tokens_details.cached_tokens
                cache_write = 0
                # OpenAI includes cached tokens in input_tokens; our ledger stores them separately.
                input_tokens = usage.input_tokens - cache_read
                served_by = response.model or model
                cost = cost_usd(
                    served_by,
                    input_tokens=input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read=cache_read,
                    cache_write=cache_write,
                )
                parsed: T | None = None
                # A refusal arrives as a content part, not as invalid
                # JSON.
                refused = any(
                    getattr(part, "type", None) == "refusal"
                    for item in response.output
                    for part in getattr(item, "content", [])
                )
                if refused:
                    last_error = LLMRefusal(f"{task}: model declined")
                else:
                    try:
                        if response.status != "completed":
                            raise ValueError(f"response status: {response.status}")
                        data = json.loads(response.output_text)
                        # Strict schemas require unused proposal fields to be explicit nulls.
                        if output.__name__ == "JobChatDecision" and isinstance(data, dict):
                            proposal = data.get("proposal")
                            if isinstance(proposal, dict):
                                data["proposal"] = {k: v for k, v in proposal.items() if v is not None}
                        parsed = output.model_validate(data)
                    except (ValidationError, ValueError) as exc:
                        last_error = exc
                span.set_attributes(
                    {
                        "llm.served_by": served_by,
                        "llm.input_tokens": input_tokens,
                        "llm.output_tokens": usage.output_tokens,
                        "llm.cache_read_tokens": cache_read,
                        "llm.cost_usd": cost,
                        "llm.valid": parsed is not None,
                        "llm.stop_reason": str(response.status),
                    }
                )
                await self._record(
                    task,
                    served_by,
                    prompt.id,
                    input_tokens,
                    usage.output_tokens,
                    cache_read,
                    cache_write,
                    cost,
                    parsed is not None,
                    run_id,
                )
                if parsed is not None:
                    return LLMResult(
                        parsed,
                        served_by,
                        prompt.id,
                        input_tokens,
                        usage.output_tokens,
                        cache_read,
                        cache_write,
                        cost,
                    )
                # A refusal won't change on retry: give up at once.
                if isinstance(last_error, LLMRefusal):
                    raise last_error
                log.warning("%s: invalid output on attempt %s: %s", task, attempt, last_error)
        raise LLMInvalidOutput(f"{task}: invalid output after retry: {last_error}")

    async def _record(
        self,
        task: str,
        model: str,
        prompt_id: str,
        tin: int,
        tout: int,
        cread: int,
        cwrite: int,
        cost: float,
        valid: bool,
        run_id: int | None,
    ) -> None:
        """Store one call's usage in llm_usage and add it to the run's totals."""
        async with transaction() as conn:
            await llm_usage.record(
                conn,
                run_id=run_id,
                task=task,
                model=model,
                prompt_version=prompt_id,
                input_tokens=tin,
                output_tokens=tout,
                cache_read_tokens=cread,
                cache_write_tokens=cwrite,
                cost_usd=cost,
                valid_output=valid,
            )
            if run_id is not None:
                await runs_repo.add_usage(conn, run_id, tin + cread + cwrite, tout, cost)
