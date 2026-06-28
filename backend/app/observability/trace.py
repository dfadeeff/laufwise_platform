"""Trace sink configuration.

The control-plane primitive emits OpenTelemetry spans with gen_ai.* semantic
conventions; any OTLP backend (Langfuse, LangSmith, Grafana Tempo) consumes them.
The platform's job here is only to read endpoint config from settings and hand it to
the engine — we are infrastructure *underneath* observability, not a competing tracer.
"""

from __future__ import annotations

import os


def otlp_configured() -> bool:
    """True when an OTLP export endpoint is set in the environment."""
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))