"""A minimal terminal agent on the OpenAI-compatible protocol.

One tool: run_terminal_cmd. The loop sends the task instruction, executes the
model's tool calls inside the task container, feeds results back, and stops when
the model returns a final message (or a stop condition trips). This deliberately
thin scaffold keeps cost-per-successful-task attributable to the *model*, not the
harness. Guardrails (token cap / wall-clock) arrive in Phase 2; for now a
max_steps safety net bounds runaway loops.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from openai import OpenAI
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .container import TaskContainer
from .models import ModelSpec
from .tasks import Task

_tracer = trace.get_tracer("cpst.agent")

SYSTEM_PROMPT = (
    "You are an autonomous software agent operating a Linux terminal to complete "
    "a task. You have exactly one tool, run_terminal_cmd, which runs a bash "
    "command inside the task's container and returns its stdout, stderr, and exit "
    "code. Work step by step: inspect the environment, then act. Do not ask the "
    "user questions — you cannot receive answers. When you are confident the task "
    "is fully complete, stop calling the tool and reply with a brief summary of "
    "what you did."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_terminal_cmd",
            "description": (
                "Run a bash command inside the task's Linux container. Returns "
                "combined stdout/stderr and the exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    }
]

_MAX_TOOL_OUTPUT_CHARS = 12_000


def _truncate(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n...[{len(text) - limit} chars truncated]...\n{tail}"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, u) -> None:
        if u is None:
            return
        self.prompt_tokens += u.prompt_tokens or 0
        self.completion_tokens += u.completion_tokens or 0
        details = getattr(u, "completion_tokens_details", None)
        if details is not None:
            self.reasoning_tokens += getattr(details, "reasoning_tokens", 0) or 0

    @property
    def total_tokens(self) -> int:
        """Cumulative billed tokens across all steps (context is re-billed each
        turn, so this is the real spend and the right basis for the cap)."""
        return self.prompt_tokens + self.completion_tokens


@dataclass
class AgentResult:
    stop_reason: str  # completed | token_cap | timeout | max_steps | error
    steps: int
    tool_calls: int
    usage: Usage
    final_text: str
    wall_clock_sec: float
    messages: list = field(default_factory=list)
    error: str | None = None


def run_agent(
    model: ModelSpec,
    task: Task,
    container: TaskContainer,
    client: OpenAI | None = None,
    max_steps: int = 40,
    command_timeout_sec: float = 120.0,
    token_cap: int | None = None,
    wall_clock_cap_sec: float | None = None,
) -> AgentResult:
    client = client or model.client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task.instruction},
    ]
    usage = Usage()
    tool_call_count = 0
    start = time.monotonic()

    def _stop(reason: str, step: int, final_text: str = "") -> AgentResult:
        return AgentResult(
            stop_reason=reason, steps=step, tool_calls=tool_call_count,
            usage=usage, final_text=final_text,
            wall_clock_sec=time.monotonic() - start, messages=messages,
        )

    for step in range(1, max_steps + 1):
        # Wall-clock safety net: check before spending another model call.
        if wall_clock_cap_sec is not None and time.monotonic() - start > wall_clock_cap_sec:
            return _stop("timeout", step - 1)
        try:
            resp = client.chat.completions.create(
                model=model.model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except Exception as e:  # provider/API error
            return AgentResult(
                stop_reason="error", steps=step - 1, tool_calls=tool_call_count,
                usage=usage, final_text="", wall_clock_sec=time.monotonic() - start,
                messages=messages, error=f"{type(e).__name__}: {e}",
            )

        usage.add(resp.usage)
        # Token budget cap: the primary, fairness-preserving cutoff. Stop as soon
        # as cumulative spend crosses the ceiling.
        if token_cap is not None and usage.total_tokens >= token_cap:
            return _stop("token_cap", step)
        msg = resp.choices[0].message

        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            tcs: list[dict] = []
            for tc in msg.tool_calls:
                d: dict = {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                # Faithfully echo back any provider-specific payload the model
                # attached to its tool call. Gemini's thinking models return a
                # thought_signature here and 400 on the next turn if it is not
                # returned verbatim; other providers simply omit it.
                extra = getattr(tc, "extra_content", None)
                if extra:
                    d["extra_content"] = extra
                tcs.append(d)
            assistant_msg["tool_calls"] = tcs
        messages.append(assistant_msg)

        if not msg.tool_calls:
            return _stop("completed", step, final_text=msg.content or "")

        for tc in msg.tool_calls:
            tool_call_count += 1
            with _tracer.start_as_current_span("run_terminal_cmd") as tspan:
                tspan.set_attribute("openinference.span.kind", "TOOL")
                tspan.set_attribute("tool.name", tc.function.name)
                tspan.set_attribute("input.value", tc.function.arguments or "")
                content, is_error, reason = _execute_tool_call(
                    tc, container, command_timeout_sec
                )
                tspan.set_attribute("output.value", content)
                # A tool call that didn't cleanly succeed (non-zero exit, timeout,
                # or a malformed call) shows red in the trace so friction points
                # are visible.
                if is_error:
                    tspan.set_attribute("tool.error", reason)
                    tspan.set_status(Status(StatusCode.ERROR, reason))
                else:
                    tspan.set_status(Status(StatusCode.OK))
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": content}
            )
            # A single long-running command can blow the wall-clock budget.
            if wall_clock_cap_sec is not None and time.monotonic() - start > wall_clock_cap_sec:
                return _stop("timeout", step)

    return _stop("max_steps", max_steps)


def _execute_tool_call(
    tc, container: TaskContainer, timeout: float
) -> tuple[str, bool, str]:
    """Run the tool. Returns (content_for_model, is_error, short_reason)."""
    if tc.function.name != "run_terminal_cmd":
        return f"Error: unknown tool '{tc.function.name}'.", True, "unknown_tool"
    try:
        args = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError as e:
        # Malformed tool arguments are a real, costed failure mode — tell the
        # model so it can retry rather than silently swallowing it.
        return f"Error: could not parse tool arguments as JSON ({e}).", True, "bad_arguments"
    command = args.get("command")
    if not command:
        return "Error: 'command' argument is required.", True, "missing_command"
    res = container.exec(command, timeout=timeout)
    prefix = "(command timed out)\n" if res.timed_out else ""
    content = f"{prefix}(exit code {res.exit_code})\n{_truncate(res.output)}"
    if res.timed_out:
        return content, True, "command_timeout"
    if res.exit_code != 0:
        return content, True, f"exit_code={res.exit_code}"
    return content, False, ""
