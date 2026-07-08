"""Phase 1 grading validation (no LLM needed).

Proves our grader reproduces the task's intended grading in both directions:
  - a fresh container (nothing done) must grade as FAIL
  - after running the task's own oracle solution.sh, it must grade as PASS
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpst.container import TaskContainer
from cpst.grading import grade
from cpst.tasks import load_task

TASK_ID = sys.argv[1] if len(sys.argv) > 1 else "csv-to-parquet"


def main() -> int:
    task = load_task(TASK_ID)
    print(f"Task: {task.task_id}  (difficulty={task.difficulty}, category={task.category})")
    print(f"Instruction: {task.instruction!r}")
    print(f"Has oracle solution.sh: {task.has_oracle}\n")

    ok = True
    with TaskContainer(task) as c:
        print("building image..."); c.build(timeout=task.max_agent_timeout_sec)
        print("starting container..."); c.start()

        # sanity: where are we, is bash there
        env = c.exec("pwd && whoami && which bash && ls -la")
        print(f"[env] exit={env.exit_code}\n{env.output}\n")

        # 1) negative control: empty attempt should FAIL
        pre = grade(c, task)
        print(f"[before oracle] resolved={pre.resolved} results={ {k: v.value for k,v in pre.results.items()} } "
              f"parse_error={pre.parse_error} timed_out={pre.test_timed_out}")
        if pre.resolved:
            print("  !! UNEXPECTED: empty attempt graded as PASS"); ok = False
        else:
            print("  ok: empty attempt correctly FAILS")

        # 2) run the oracle solution, then it should PASS
        if not task.has_oracle:
            print("no oracle solution.sh; skipping positive check"); return 0 if ok else 1
        c.copy_in(task.solution_path, "/solution.sh")
        sol = c.exec("bash /solution.sh", timeout=task.max_agent_timeout_sec)
        print(f"\n[oracle run] exit={sol.exit_code}\n{sol.output[-1500:]}\n")

        post = grade(c, task)
        print(f"[after oracle] resolved={post.resolved} results={ {k: v.value for k,v in post.results.items()} } "
              f"parse_error={post.parse_error} timed_out={post.test_timed_out}")
        if post.resolved:
            print("  ok: oracle solution correctly PASSES")
        else:
            print("  !! UNEXPECTED: oracle solution graded as FAIL"); ok = False

    print("\nRESULT:", "GRADING FAITHFUL ✅" if ok else "MISMATCH ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
