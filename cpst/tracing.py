"""Arize AX tracing via OpenTelemetry + OpenInference.

Registers an OTLP exporter pointed at Arize AX and auto-instruments the
OpenAI-compatible client so every model call becomes an LLM span (with token
counts and latency). The agent adds a root AGENT span and per-command TOOL
spans on top. No Phoenix involved.
"""

from __future__ import annotations

import os

_provider = None  # set once by init_tracing()


def init_tracing():
    """Idempotently register Arize AX export + instrument the OpenAI client.

    Returns the tracer provider, or None if Arize creds are absent (in which
    case the agent still runs; spans just go to a no-op provider).
    """
    global _provider
    if _provider is not None:
        return _provider

    space_id = os.environ.get("ARIZE_SPACE_ID")
    api_key = os.environ.get("ARIZE_API_KEY")
    if not (space_id and api_key):
        return None

    # Quiet gRPC's benign fork/poll chatter (we shell out to docker via
    # subprocess, which forks). Must be set before gRPC initializes.
    os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
    os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "1")

    from arize.otel import register
    from openinference.instrumentation.openai import OpenAIInstrumentor

    _provider = register(
        space_id=space_id,
        api_key=api_key,
        project_name=os.environ.get("ARIZE_PROJECT_NAME", "cost-per-successful-task"),
    )
    OpenAIInstrumentor().instrument(tracer_provider=_provider)
    return _provider


def flush() -> None:
    """Force-export any buffered spans. Call before the process exits so the
    BatchSpanProcessor doesn't drop the tail of a short run."""
    if _provider is not None:
        try:
            _provider.force_flush()
        except Exception:
            pass
