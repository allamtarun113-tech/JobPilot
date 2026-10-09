"""Run vocabulary shared by every layer."""

from enum import StrEnum


class RunStatus(StrEnum):
    """The life of one run, from running to finished or failed."""

    RUNNING = "running"
    DRAINING = "draining"
    PAUSED = "paused"  # /pause: holds after the current job; /resume (or /start) continues the same run
    FINISHED = "finished"
    FAILED = "failed"


class StopReason(StrEnum):
    """Why a run stopped or started draining."""

    QUEUE_EMPTY = "queue_empty"
    CARD_CAP = "card_cap"
    BUDGET = "budget"
    API_QUOTA = "api_quota"
    STOP_REQUESTED = "stop_requested"
    PAUSED = "paused"
    KILLSWITCH = "killswitch"
    ERROR_SPIKE = "error_spike"
    PREFLIGHT_FAILED = "preflight_failed"
    ERROR = "error"
