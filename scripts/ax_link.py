"""Build correct Arize AX deep links to the traces in a results JSONL.

The trace view filters by the visible time window, so a link MUST carry
startA/endA (epoch ms) that bracket the trace, or the trace won't load. We
derive the window from each run's timestamp.

Usage:
    python scripts/ax_link.py results/benchmark_run1.jsonl
    python scripts/ax_link.py results/benchmark_run1.jsonl --task feal-linear-cryptanalysis
    python scripts/ax_link.py results/benchmark_run1.jsonl --failed-only
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Space/project for this demo; override via env if reused elsewhere.
SPACE_ID = os.environ.get("ARIZE_SPACE_ID", "")
PROJECT_ID = os.environ.get("ARIZE_PROJECT_ID", "TW9kZWw6ODY5NDk2OTI1NTplY0dl")
WINDOW_SEC = 3600  # padding on each side of the run timestamp


def link(org: str, trace_id: str, ts: float) -> str:
    base = f"https://app.arize.com/organizations/{org}/spaces/{SPACE_ID}/projects/{PROJECT_ID}"
    params = {
        "selectedTraceId": trace_id,
        "traceViewId": "__arize_default",
        "queryFilterA": "",
        "selectedTab": "llmTracing",
        "timeZoneA": "America/Los_Angeles",
        "startA": int((ts - WINDOW_SEC) * 1000),
        "endA": int((ts + WINDOW_SEC) * 1000),
        "envA": "tracing",
        "modelType": "generative_llm",
    }
    # space_id/project_id are already in the path; keep '=' in query readable.
    return base + "?" + urlencode(params, safe="")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", help="path to a results JSONL")
    ap.add_argument("--org", default=os.environ.get("ARIZE_ORG_ID"), help="Arize org id (base64)")
    ap.add_argument("--task")
    ap.add_argument("--model")
    ap.add_argument("--failed-only", action="store_true")
    args = ap.parse_args()
    if not args.org:
        sys.exit("Need --org or ARIZE_ORG_ID in .env")

    rows = [json.loads(l) for l in open(args.results)]
    for r in rows:
        if not r.get("trace_id"):
            continue
        if args.task and r["task_id"] != args.task:
            continue
        if args.model and r["model_key"] != args.model:
            continue
        if args.failed_only and r["passed"]:
            continue
        status = "PASS" if r["passed"] else f"FAIL:{r['failure_reason']}"
        cost = f"${r['cost_usd']:.4f}" if r["cost_usd"] is not None else "n/a"
        print(f"{r['task_id']} x {r['model_key']} t{r['trial']} [{status} {cost}]")
        print("  " + link(args.org, r["trace_id"], r["timestamp"]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
