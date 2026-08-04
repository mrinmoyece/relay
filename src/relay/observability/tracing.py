"""OpenTelemetry tracing with a graceful no-op fallback.

Every agent step (LLM call, tool execution, approval wait, recovery) is a
span, so one trace shows the full causal chain of a run - which is how you
debug "why did the agent do THAT" in production.

If the otel extra isn't installed or no endpoint is configured, `span()`
degrades to a no-op context manager. Application code never checks whether
tracing is on - it just calls span(). Graceful degradation beats a hard
dependency for a runtime meant to run anywhere.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _HAVE_OTEL = True
except ImportError:  # pragma: no cover
    _HAVE_OTEL = False

_tracer: Any = None


def setup_tracing(*, service_name: str, endpoint: str | None) -> None:
    """Idempotent. Without otel installed or an endpoint set, tracing
    stays a no-op."""
    global _tracer
    if not _HAVE_OTEL or endpoint is None:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    _otel_trace.set_tracer_provider(provider)
    _tracer = _otel_trace.get_tracer("relay")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Start a span (or do nothing). Attributes are flattened to
    OTel-safe primitives."""
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as s:  # pragma: no cover
        for key, value in attributes.items():
            if isinstance(value, (str, bool, int, float)):
                s.set_attribute(f"relay.{key}", value)
            else:
                s.set_attribute(f"relay.{key}", str(value))
        yield
