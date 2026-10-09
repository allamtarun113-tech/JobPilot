"""CPU-heavy work (PDF text extraction, corpus parsing) off the event loop.

apps/app installs a process pool at start-up (only apps/app starts processes); without one - tests, scripts -
work runs in a thread. Functions passed here must be top-level and picklable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Executor
from typing import Any

_executor: Executor | None = None


def set_executor(executor: Executor | None) -> None:
    """Install (or clear) the process pool used for CPU-heavy work.

    Args:
        executor: The pool, or None to fall back to a thread.
    """
    global _executor
    _executor = executor


async def run[R](fn: Callable[..., R], *args: Any) -> R:
    """Run ``fn(*args)`` off the event loop and await its result.

    Args:
        fn: A top-level, picklable function.
        *args: Picklable positional arguments.

    Returns:
        Whatever ``fn`` returns.
    """
    # No pool installed (tests, scripts): a thread is good enough.
    if _executor is None:
        return await asyncio.to_thread(fn, *args)
    return await asyncio.get_running_loop().run_in_executor(_executor, fn, *args)
