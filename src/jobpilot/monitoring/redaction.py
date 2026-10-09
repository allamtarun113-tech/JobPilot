"""PII hygiene for traces and old event payloads: email addresses and phone numbers are masked.

Traces leave the machine (Langfuse Cloud), so every exported span goes through RedactingExporter.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w])(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,11}\d(?![\w])")


def redact(text: str) -> str:
    """Mask email addresses and phone numbers in a string."""
    return PHONE.sub("[phone]", EMAIL.sub("[email]", text))


def redact_value(value: Any) -> Any:
    """Redact strings anywhere inside JSON-like data."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    return value


def _attrs(attributes: Any) -> dict[str, Any]:
    """Return span attributes with every string value redacted."""
    out: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if isinstance(value, str):
            out[key] = redact(value)
        elif isinstance(value, tuple | list):
            out[key] = tuple(redact(v) if isinstance(v, str) else v for v in value)
        else:
            out[key] = value
    return out


def redacted_span(span: ReadableSpan) -> ReadableSpan:
    """Return a copy of a span with its attributes, events and status redacted."""
    status = span.status
    if status.description:
        status = Status(status.status_code, redact(status.description))
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_attrs(span.attributes),
        events=[Event(e.name, _attrs(e.attributes), e.timestamp) for e in span.events],
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class RedactingExporter(SpanExporter):
    """A span exporter that redacts every span before the real exporter sends it."""

    def __init__(self, inner: SpanExporter) -> None:
        """Wrap ``inner``, the exporter that actually sends spans."""
        self.inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Redact the spans, then export them."""
        return self.inner.export([redacted_span(s) for s in spans])

    def shutdown(self) -> None:
        """Shut down the wrapped exporter."""
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush the wrapped exporter."""
        return self.inner.force_flush(timeout_millis)
