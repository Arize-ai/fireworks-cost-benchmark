"""Phase 2 checks: cost accounting + guardrail (token cap) behaves distinctly."""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from cpst.models import get_model
from cpst.runner import run_task
from cpst.tasks import load_task

task = load_task("csv-to-parquet")
model = get_model("fw-gpt-oss-120b")


def show(label, r):
    print(f"\n=== {label} ===")
    print(f"passed={r.passed} failure_reason={r.failure_reason} stop_reason={r.stop_reason}")
    print(f"tokens: prompt={r.prompt_tokens} completion={r.completion_tokens} "
          f"reasoning={r.reasoning_tokens} total={r.total_tokens}")
    print(f"cost_usd={r.cost_usd} wall={r.wall_clock_sec}s steps={r.steps} tool_calls={r.tool_calls}")
    print(f"test_results={r.test_results}")


# 1) Normal run: expect cost populated; likely PASS.
r1 = run_task(model, task, trial=0)
show("normal run", r1)
assert r1.cost_usd is not None, "cost should be computed from pricing config"

# 2) Tiny token cap: expect stop_reason=token_cap, passed=False, reason=token_cap.
r2 = run_task(model, task, trial=1, token_cap=1500)
show("token_cap=1500", r2)
assert r2.stop_reason == "token_cap", f"expected token_cap, got {r2.stop_reason}"
assert r2.passed is False and r2.failure_reason == "token_cap"
assert r2.total_tokens >= 1500

print("\nPhase 2 assertions passed ✅")
print("\nJSONL row preview:")
print(json.dumps(r1.to_dict()))
