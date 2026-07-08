"""Validate candidate tasks for the subset: for each, build the container, run
the task's own oracle solution.sh, then grade. A task is harness-compatible if
the oracle PASSES (and it builds/runs in reasonable time). Prints a table.

Usage: python scripts/validate_batch.py task_a task_b ...
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cpst.container import TaskContainer
from cpst.grading import grade
from cpst.tasks import load_task

ORACLE_TIMEOUT = 420  # seconds per task; slower tasks are dropped


def validate(task_id: str) -> tuple[str, str, str, float]:
    t0 = time.monotonic()
    try:
        task = load_task(task_id)
    except Exception as e:
        return task_id, "?", f"load_error: {e}", 0.0
    if not task.has_oracle:
        return task_id, task.difficulty or "?", "no_oracle", 0.0
    try:
        with TaskContainer(task) as c:
            c.build(timeout=ORACLE_TIMEOUT)
            c.start()
            c.copy_in(task.solution_path, "/oracle.sh")
            res = c.exec("bash /oracle.sh", timeout=ORACLE_TIMEOUT)
            if res.timed_out:
                return task_id, task.difficulty or "?", "oracle_timeout", time.monotonic() - t0
            g = grade(c, task)
            verdict = "OK" if g.resolved else (
                f"ORACLE_FAIL(parse_error)" if g.parse_error else "ORACLE_FAIL(tests)"
            )
            return task_id, task.difficulty or "?", verdict, time.monotonic() - t0
    except Exception as e:
        return task_id, task.difficulty or "?", f"error: {str(e)[:80]}", time.monotonic() - t0


def main() -> int:
    task_ids = sys.argv[1:]
    if not task_ids:
        sys.exit("give task ids")
    print(f"{'TASK':<34} {'DIFF':<7} {'VERDICT':<28} {'SECS':>6}")
    ok = []
    for tid in task_ids:
        task_id, diff, verdict, secs = validate(tid)
        print(f"{task_id:<34} {diff:<7} {verdict:<28} {secs:>6.0f}", flush=True)
        if verdict == "OK":
            ok.append(task_id)
    print(f"\nharness-compatible ({len(ok)}): {' '.join(ok)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
