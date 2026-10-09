"""OpenTelemetry setup. Traces go to the hosted tier (Langfuse Cloud over OTLP) when keys are set.

Every run is one trace; each job, LLM call, MCP call and tap is a span inside it.
"""

from __future__ import annotations

import base64
import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from jobpilot.core.config import get_config, get_settings
from jobpilot.monitoring.redaction import RedactingExporter

log = logging.getLogger(__name__)
# Set once per process; the global tracer provider can't be replaced.
_configured = False


def setup_tracing(process: str, exporter: SpanExporter | None = None) -> None:
    """Install the global tracer provider once per process. Tests pass an in-memory exporter."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": f"jobpilot-{process}"}))
    if exporter is not None:  # tests: export synchronously
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif (default := _default_exporter()) is not None:
        provider.add_span_processor(BatchSpanProcessor(RedactingExporter(default)))
    trace.set_tracer_provider(provider)
    _configured = True


def _default_exporter() -> SpanExporter | None:
    """Pick where spans go: Langfuse when keys are set, else the console or nowhere."""
    settings = get_settings()
    has_public = bool(settings.langfuse_public_key)
    has_secret = bool(settings.langfuse_secret_key and settings.langfuse_secret_key.get_secret_value())
    if has_public != has_secret:
        log.warning(
            "tracing: only one of LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY is set; check .env spelling"
        )
    if has_public and has_secret and settings.langfuse_secret_key is not None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Langfuse's OTLP endpoint takes HTTP basic auth
        # (public:secret).
        auth = base64.b64encode(
            f"{settings.langfuse_public_key}:{settings.langfuse_secret_key.get_secret_value()}".encode()
        ).decode()
        return OTLPSpanExporter(
            endpoint=f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )
    if get_config().tracing.console:
        return ConsoleSpanExporter()
    log.info("tracing: no hosted tracing keys set; spans are created but not exported")
    return None


def trace_url(trace_id: str | None) -> str | None:
    """Link from the Runs tab into the hosted trace viewer."""
    if not trace_id:
        return None
    host = get_settings().langfuse_host.rstrip("/")
    return get_config().tracing.trace_url.format(host=host, trace_id=trace_id)


def current_trace_id() -> str | None:
    """Return the current span's trace id as 32 hex characters, or None."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def shutdown_tracing() -> None:
    """Flush pending spans and stop the tracer provider."""
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
