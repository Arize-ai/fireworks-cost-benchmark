"""Cost-per-successful-task benchmark runner.

Runs an instrumented terminal agent over a matrix of (task x model x trial),
grades each run with the task's own tests, records per-run results to JSONL, and
prints a cost-per-successful-task summary per model.

Example:
    python run.py --tasks tasks/subset.txt \
        --models fw-gpt-oss-120b,oai-gpt-5.5 \
        --trials 3 --token-cap 200000 --wall-clock-cap 900 --concurrency 4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from cpst.models import get_model, load_defaults, load_models  # noqa: E402
from cpst.report import render  # noqa: E402
from cpst.runner import RunResult, run_task  # noqa: E402
from cpst.tasks import load_task  # noqa: E402
from cpst.tracing import flush, init_tracing  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tasks", help="Path to a file with one task id per line (e.g. tasks/subset.txt).")
    p.add_argument("--task", action="append", default=[], help="Inline task id (repeatable).")
    p.add_argument("--models", required=True, help="Comma-separated model keys from config/models.yaml.")
    p.add_argument("--trials", type=int, default=1, help="Trials per (task, model). Default 1.")
    p.add_argument("--token-cap", type=int, default=None, help="Override token budget cap.")
    p.add_argument("--wall-clock-cap", type=float, default=None, help="Override wall-clock cap (seconds).")
    p.add_argument("--concurrency", type=int, default=4, help="Max parallel runs. Default 4.")
    p.add_argument("--output", default=None, help="JSONL output path. Default results/run_<ts>.jsonl.")
    p.add_argument("--keep-containers", action="store_true", help="Do not remove containers/images (debug).")
    p.add_argument("--no-trace", action="store_true", help="Disable Arize AX tracing.")
    return p.parse_args()


def load_task_ids(args: argparse.Namespace) -> list[str]:
    ids: list[str] = list(args.task)
    if args.tasks:
        path = Path(args.tasks)
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    # de-dupe, preserve order
    seen, out = set(), []
    for t in ids:
        if t not in seen:
            seen.add(t); out.append(t)
    if not out:
        sys.exit("No tasks given. Use --tasks <file> and/or --task <id>.")
    return out


def main() -> int:
    args = parse_args()
    model_keys = [m.strip() for m in args.models.split(",") if m.strip()]
    known = load_models()
    for k in model_keys:
        if k not in known:
            sys.exit(f"Unknown model '{k}'. Known: {', '.join(known)}")
    task_ids = load_task_ids(args)
    defaults = load_defaults()

    if not args.no_trace:
        provider = init_tracing()
        print(f"Arize AX tracing: {'ENABLED' if provider else 'DISABLED (no creds)'}")

    out_path = Path(args.output) if args.output else ROOT / "results" / f"run_{int(time.time())}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the job matrix.
    jobs = [
        (task_id, model_key, trial)
        for task_id in task_ids
        for model_key in model_keys
        for trial in range(args.trials)
    ]
    total = len(jobs)
    print(f"Matrix: {len(task_ids)} tasks x {len(model_keys)} models x {args.trials} trials "
          f"= {total} runs, concurrency={args.concurrency}")
    print(f"Results -> {out_path}\n")

    def run_one(job) -> RunResult:
        task_id, model_key, trial = job
        try:
            task = load_task(task_id)
            model = get_model(model_key)
            return run_task(
                model, task, trial=trial, defaults=defaults,
                token_cap=args.token_cap, wall_clock_cap_sec=args.wall_clock_cap,
                keep_container=args.keep_containers,
            )
        except Exception as e:  # harness-level failure shouldn't kill the matrix
            return RunResult(
                task_id=task_id, model_key=model_key, model_name="",
                trial=trial, passed=False, failure_reason="harness_error",
                stop_reason="harness_error", resolved=None,
                agent_error=f"{type(e).__name__}: {e}",
            )

    results: list[RunResult] = []
    done = 0
    with out_path.open("w") as fh:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(run_one, job): job for job in jobs}
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                fh.write(json.dumps(r.to_dict()) + "\n")
                fh.flush()
                done += 1
                cost = f"${r.cost_usd:.4f}" if r.cost_usd is not None else "n/a"
                status = "PASS" if r.passed else f"FAIL:{r.failure_reason}"
                print(f"[{done}/{total}] {r.task_id} x {r.model_key} t{r.trial} -> "
                      f"{status} {cost} ({r.tool_calls} calls, {r.wall_clock_sec:.0f}s)"
                      + (f" trace={r.trace_id[:12]}" if r.trace_id else ""))

    if not args.no_trace:
        flush()

    print()
    render(results)
    print(f"\nWrote {len(results)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
