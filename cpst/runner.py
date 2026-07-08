"""Run one (task, model, trial): container -> agent -> grade -> cost -> result.

A run is a SUCCESS only if the agent finished on its own AND the task's own
tests all pass. Hitting a guardrail (token_cap / timeout / max_steps) or an API
error is a FAIL recorded with that reason — distinct from a genuine test
failure, so cost-per-successful-task counts only real successes.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .agent import run_agent
from .container import TaskContainer
from .grading import grade
from .models import Defaults, ModelSpec, load_defaults
from .tasks import Task

_tracer = trace.get_tracer("cpst.runner")


@dataclass
class RunResult:
    task_id: str
    model_key: str
    model_name: str
    trial: int
    passed: bool
    failure_reason: str | None  # None if passed; else test_failed/token_cap/timeout/max_steps/agent_error
    stop_reason: str            # raw agent stop reason
    resolved: bool | None       # test-level pass/fail; None if not graded
    test_results: dict = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    wall_clock_sec: float = 0.0
    steps: int = 0
    tool_calls: int = 0
    agent_error: str | None = None
    trace_id: str | None = None
    task_difficulty: str | None = None
    task_category: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def run_task(
    model: ModelSpec,
    task: Task,
    trial: int = 0,
    defaults: Defaults | None = None,
    token_cap: int | None = None,
    wall_clock_cap_sec: float | None = None,
    keep_container: bool = False,
) -> RunResult:
    d = defaults or load_defaults()
    token_cap = token_cap if token_cap is not None else d.token_cap
    wall_clock_cap_sec = (
        wall_clock_cap_sec if wall_clock_cap_sec is not None else d.wall_clock_cap_sec
    )

    trace_id: str | None = None
    with TaskContainer(task, keep=keep_container) as c:
        c.build(timeout=max(task.max_agent_timeout_sec, 900))
        c.start()

        # Root span wraps agent + grading so LLM/TOOL spans nest under it and the
        # trace carries the final pass/fail outcome (incl. test failures).
        with _tracer.start_as_current_span("task-run") as root:
            root.set_attribute("openinference.span.kind", "AGENT")
            root.set_attribute("input.value", task.instruction)
            root.set_attribute("cpst.task_id", task.task_id)
            root.set_attribute("cpst.model_key", model.key)
            root.set_attribute("cpst.trial", trial)

            agent = run_agent(
                model, task, c,
                max_steps=d.max_steps,
                command_timeout_sec=d.command_timeout_sec,
                token_cap=token_cap,
                wall_clock_cap_sec=wall_clock_cap_sec,
            )

            # Decide outcome. Only a clean completion is eligible to be graded.
            resolved: bool | None = None
            test_results: dict = {}
            if agent.stop_reason == "completed":
                g = grade(c, task)
                resolved = g.resolved
                test_results = {k: v.value for k, v in g.results.items()}
                passed = bool(resolved)
                failure_reason = None if passed else "test_failed"
            elif agent.stop_reason == "error":
                passed, failure_reason = False, "agent_error"
            else:  # token_cap | timeout | max_steps
                passed, failure_reason = False, agent.stop_reason

            u = agent.usage
            root.set_attribute("output.value", agent.final_text or "")
            root.set_attribute("cpst.passed", passed)
            root.set_attribute("llm.token_count.prompt", u.prompt_tokens)
            root.set_attribute("llm.token_count.completion", u.completion_tokens)
            root.set_attribute("llm.token_count.total", u.total_tokens)
            if passed:
                root.set_status(Status(StatusCode.OK))
            else:
                # Make the failure point filterable/visible on the trace.
                root.set_attribute("cpst.failure_reason", failure_reason)
                root.set_status(Status(StatusCode.ERROR, failure_reason or "failed"))
                if agent.error:
                    root.set_attribute("cpst.agent_error", agent.error)

            ctx = root.get_span_context()
            if ctx.trace_id:  # 0 => no real provider registered
                trace_id = format(ctx.trace_id, "032x")
    return RunResult(
        task_id=task.task_id,
        model_key=model.key,
        model_name=model.model,
        trial=trial,
        passed=passed,
        failure_reason=failure_reason,
        stop_reason=agent.stop_reason,
        resolved=resolved,
        test_results=test_results,
        prompt_tokens=u.prompt_tokens,
        completion_tokens=u.completion_tokens,
        reasoning_tokens=u.reasoning_tokens,
        total_tokens=u.total_tokens,
        cost_usd=model.cost(u.prompt_tokens, u.completion_tokens),
        wall_clock_sec=round(agent.wall_clock_sec, 2),
        steps=agent.steps,
        tool_calls=agent.tool_calls,
        agent_error=agent.error,
        trace_id=trace_id,
        task_difficulty=task.difficulty,
        task_category=task.category,
    )
