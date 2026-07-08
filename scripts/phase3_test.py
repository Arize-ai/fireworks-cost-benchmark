"""Phase 3: verify Arize AX tracing. A passing and a failing run should each
produce a trace id; the failing run's root span is marked ERROR."""

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from cpst.models import get_model
from cpst.runner import run_task
from cpst.tasks import load_task
from cpst.tracing import flush, init_tracing

provider = init_tracing()
print(f"Arize AX tracing: {'ENABLED' if provider else 'DISABLED (no creds)'}\n")

task = load_task("csv-to-parquet")
model = get_model("fw-gpt-oss-120b")

r_pass = run_task(model, task, trial=0)
print(f"[PASS run ] passed={r_pass.passed} reason={r_pass.failure_reason} "
      f"trace_id={r_pass.trace_id} cost=${r_pass.cost_usd:.5f} tool_calls={r_pass.tool_calls}")

r_fail = run_task(model, task, trial=1, token_cap=1500)
print(f"[FAIL run ] passed={r_fail.passed} reason={r_fail.failure_reason} "
      f"trace_id={r_fail.trace_id} cost=${r_fail.cost_usd:.5f} tool_calls={r_fail.tool_calls}")

print("\nflushing spans to Arize AX...")
flush()
assert r_pass.trace_id and r_fail.trace_id, "expected trace ids on both runs"
print("Phase 3 checks passed ✅  (view traces in AX project 'cost-per-successful-task')")
