"""Verify: (1) exec survives non-UTF-8 output, (2) _execute_tool_call flags
errors, (3) TOOL spans carry OK/ERROR status (not UNSET)."""

import sys
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

# in-memory tracing before importing agent
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)

from cpst.agent import _execute_tool_call, run_agent
from cpst.container import TaskContainer
from cpst.models import get_model
from cpst.tasks import load_task

def tc(cmd):
    import json
    return SimpleNamespace(id="x", function=SimpleNamespace(
        name="run_terminal_cmd", arguments=json.dumps({"command": cmd})))

task = load_task("csv-to-parquet")
with TaskContainer(task) as c:
    c.build(); c.start()

    # (1) non-UTF-8 output must not crash
    r = c.exec(r"printf '\xbc\xbc\xbc'")
    print(f"[decode] non-UTF-8 output handled: exit={r.exit_code} len={len(r.output)} (no crash)")

    # (2) error flagging
    _, err_bad, reason_bad = _execute_tool_call(tc("ls /nope_nonexistent"), c, 30)
    _, err_ok, _ = _execute_tool_call(tc("echo hi"), c, 30)
    print(f"[flag] failing cmd -> is_error={err_bad} ({reason_bad}); good cmd -> is_error={err_ok}")
    assert err_bad and not err_ok

    # (3) run a short agent turn; check TOOL span statuses
    model = get_model("fw-gpt-oss-120b")
    run_agent(model, task, c, max_steps=40, token_cap=4000)

tool_spans = [s for s in exporter.get_finished_spans()
              if s.attributes.get("openinference.span.kind") == "TOOL"]
statuses = {}
for s in tool_spans:
    statuses[s.status.status_code.name] = statuses.get(s.status.status_code.name, 0) + 1
print(f"[spans] {len(tool_spans)} TOOL spans, statuses={statuses}")
assert "UNSET" not in statuses, "tool spans should be OK/ERROR, not UNSET"
print("\nAll tool-span checks passed ✅")
