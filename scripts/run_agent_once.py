"""Phase 1 end-to-end: one task, one model, real agent, real grading.

Usage: python scripts/run_agent_once.py [task_id] [model_key]
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from cpst.agent import run_agent
from cpst.container import TaskContainer
from cpst.grading import grade
from cpst.models import get_model, load_defaults
from cpst.tasks import load_task

TASK_ID = sys.argv[1] if len(sys.argv) > 1 else "csv-to-parquet"
MODEL_KEY = sys.argv[2] if len(sys.argv) > 2 else "fw-gpt-oss-120b"


def main() -> int:
    task = load_task(TASK_ID)
    model = get_model(MODEL_KEY)
    d = load_defaults()
    print(f"Task:  {task.task_id} ({task.difficulty}/{task.category})")
    print(f"Model: {model.key} -> {model.model}")
    print(f"Instruction: {task.instruction}\n")

    with TaskContainer(task) as c:
        print("building image..."); c.build(timeout=task.max_agent_timeout_sec)
        print("starting container..."); c.start()

        print("running agent...\n")
        res = run_agent(
            model, task, c,
            max_steps=d.max_steps,
            command_timeout_sec=d.command_timeout_sec,
        )
        print(f"agent stop_reason={res.stop_reason} steps={res.steps} "
              f"tool_calls={res.tool_calls} wall={res.wall_clock_sec:.1f}s")
        print(f"tokens: prompt={res.usage.prompt_tokens} "
              f"completion={res.usage.completion_tokens} "
              f"reasoning={res.usage.reasoning_tokens}")
        if res.error:
            print(f"agent error: {res.error}")
        if res.final_text:
            print(f"final: {res.final_text[:500]}")

        print("\ngrading...")
        g = grade(c, task)
        print(f"RESULT: {'PASS ✅' if g.resolved else 'FAIL ❌'}  "
              f"tests={ {k: v.value for k,v in g.results.items()} }")
        if g.parse_error:
            print(f"  parse_error: {g.parse_error}")

    return 0 if g.resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
