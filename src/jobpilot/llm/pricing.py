"""Model prices from config/defaults.yaml (USD per million tokens)."""

from __future__ import annotations

import logging

from jobpilot.core.config import get_config

log = logging.getLogger(__name__)


def cost_usd(
    model: str, *, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
) -> float:
    """Return the USD cost of one call from the configured prices.

    Args:
        model: The model id that served the call.
        input_tokens: Uncached input tokens.
        output_tokens: Output tokens.
        cache_read: Input tokens served from the prompt cache.
        cache_write: Tokens written to the prompt cache.

    Returns:
        The cost in USD, or 0.0 (with a warning) for an unknown model.
    """
    price = get_config().pricing.get(model)
    if price is None:
        log.warning("no price configured for model %s; cost recorded as 0", model)
        return 0.0
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read * price.cache_read
        + cache_write * price.cache_write
    ) / 1_000_000
