"""Typed errors shared by every layer."""


class JobPilotError(Exception):
    """Base class for every JobPilot error."""

    pass


class VersionConflict(JobPilotError):
    """A version-checked update found a different version: re-read and retry, never overwrite."""


class LeaseLost(JobPilotError):
    """This process no longer holds the lease it was working under."""


class ScopeDenied(JobPilotError):
    """A caller asked for an MCP tool outside its scope."""


class DeliveryError(JobPilotError):
    """An outbox delivery failed. retryable=False marks the row dead (its partition stays paused)."""

    def __init__(self, message: str, *, retryable: bool = True, retry_after: float | None = None) -> None:
        """Create a delivery error.

        Args:
            message: What went wrong.
            retryable: False marks the outbox row dead.
            retry_after: Seconds to wait before retrying, if known.
        """
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class LLMInvalidOutput(JobPilotError):
    """The model's output failed schema validation twice."""


class LLMRefusal(JobPilotError):
    """The model declined the request (stop_reason == 'refusal')."""


class InvalidApprovalToken(JobPilotError):
    """An approval token is missing, forged, expired or mismatched."""

    pass


class DeliverySkipped(DeliveryError):
    """The approved operation expired or was superseded before an external send."""
