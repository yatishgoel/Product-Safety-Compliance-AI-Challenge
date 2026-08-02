"""Phoenix tracing for the pipeline.

Inert unless PHOENIX_COLLECTOR_ENDPOINT is set, in the same way vision is inert without a
Gemini key. Nothing else in the codebase has to know whether tracing is on: `stage()` is a
context manager that costs a branch when it is off.

Token counts and cost come from instrumenting the google-genai client rather than from
anything hand-rolled, so the numbers are the ones the SDK actually reported.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from typing import Any

PROJECT = os.getenv("PHOENIX_PROJECT_NAME", "product-safety-compliance")

_tracer: Any = None
_started = False


def configured() -> bool:
    return bool(os.getenv("PHOENIX_COLLECTOR_ENDPOINT"))


def setup(project_name: str | None = None) -> bool:
    """Register the tracer provider and instrument the Gemini client. Safe to call twice."""
    global _tracer, _started
    if _started:
        return _tracer is not None
    _started = True
    if not configured():
        return False

    try:
        from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor
        from phoenix.otel import register
    except ImportError:
        print("PHOENIX_COLLECTOR_ENDPOINT is set but tracing is not installed "
              "(uv sync --extra obs); continuing untraced", file=sys.stderr)
        return False

    provider = register(
        project_name=project_name or PROJECT,
        auto_instrument=False,
        batch=True,
        set_global_tracer_provider=True,
    )
    GoogleGenAIInstrumentor().instrument(tracer_provider=provider)
    _tracer = provider.get_tracer(__name__)
    return True


def instrument_fastapi(app) -> None:
    """Off by default, because HTTP spans bury the ones worth reading.

    Serving the page is one GET for the HTML and one per asset, health is polled by container
    checks, and none of it says anything about a verdict. The `evaluate <file>` span is already
    the root of each trace and carries the latency, so the HTTP layer only adds a parent with
    no information in it. Set TRACE_HTTP=1 if you are debugging the transport itself.
    """
    if os.getenv("TRACE_HTTP") != "1" or not setup():
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,docs,openapi.json,static",
        exclude_spans=["send", "receive"],
    )


def flush() -> None:
    """Force the batch exporter to send. Needed for short-lived CLI runs."""
    if _tracer is None:
        return
    from opentelemetry import trace as otel

    provider = otel.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


class _Span:
    """Wraps an OTel span so callers can set output without knowing whether tracing is on."""

    def __init__(self, span: Any = None):
        self._span = span

    def set(self, **attributes: Any) -> None:
        if self._span is None:
            return
        for key, value in attributes.items():
            if value is not None:
                self._span.set_attribute(key, value)

    def output(self, value: Any) -> None:
        if self._span is not None:
            self._span.set_attribute("output.value", _render(value))


_session: str | None = None


@contextmanager
def session(name: str):
    """Group every trace opened inside this block, so a batch run reads as one unit."""
    global _session
    previous, _session = _session, name
    try:
        yield
    finally:
        _session = previous


@contextmanager
def stage(name: str, kind: str = "CHAIN", inputs: Any = None, **attributes: Any):
    """Open a span for one pipeline stage. Yields a handle that is inert when tracing is off."""
    if _tracer is None:
        yield _Span()
        return

    with _tracer.start_as_current_span(name) as span:
        span.set_attribute("openinference.span.kind", kind)
        if _session is not None:
            span.set_attribute("session.id", _session)
        if inputs is not None:
            span.set_attribute("input.value", _render(inputs))
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value if isinstance(value, (int, float, bool))
                                   else _render(value))
        yield _Span(span)
