"""Local proof (no AX UI needed) that a run emits the expected span tree:
a root AGENT span (ERROR on a failed run) with nested LLM and TOOL spans, so
the failure point is visible in the trace. Uses an in-memory exporter.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

# Set up an in-memory tracer provider BEFORE importing cpst modules so their
# module-level get_tracer() binds to it. This replaces Arize export for the test.
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from openinference.instrumentation.openai import OpenAIInstrumentor

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
OpenAIInstrumentor().instrument(tracer_provider=provider)

from cpst.models import get_model
from cpst.runner import run_task
from cpst.tasks import load_task

task = load_task("csv-to-parquet")
model = get_model("fw-gpt-oss-120b")

# Low token cap => a fast, deterministic FAILED run with a couple of tool calls.
r = run_task(model, task, trial=0, token_cap=3000)
print(f"run: passed={r.passed} reason={r.failure_reason} trace_id={r.trace_id}\n")

spans = exporter.get_finished_spans()
by_id = {s.context.span_id: s for s in spans}

def kind(s):
    return s.attributes.get("openinference.span.kind", "?")

def depth(s):
    d, p = 0, s.parent
    while p is not None:
        d += 1
        parent = by_id.get(p.span_id)
        p = parent.parent if parent else None
    return d

print(f"{len(spans)} spans captured (root -> children):")
for s in sorted(spans, key=lambda s: s.start_time):
    status = s.status.status_code.name
    extra = ""
    if kind(s) == "TOOL":
        cmd = (s.attributes.get("input.value", "") or "")[:60].replace("\n", " ")
        extra = f'  cmd={cmd!r}'
    if s.attributes.get("cpst.failure_reason"):
        extra += f"  failure_reason={s.attributes['cpst.failure_reason']}"
    print(f"  {'  ' * depth(s)}[{kind(s):5}] {s.name:18} status={status}{extra}")

kinds = [kind(s) for s in spans]
assert "AGENT" in kinds and "TOOL" in kinds and "LLM" in kinds, f"missing span kinds: {set(kinds)}"
root = next(s for s in spans if kind(s) == "AGENT")
assert root.status.status_code.name == "ERROR", "failed run's root should be ERROR"
print("\nSpan tree verified ✅  (AGENT root ERROR, with nested LLM + TOOL spans)")
