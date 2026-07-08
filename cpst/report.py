"""Aggregate RunResults into the cost-per-successful-task summary.

The headline metric: cost per successful task = total spend on a model across ALL
its attempts / number of successful runs. Shown next to mean cost per attempt so
the retry tax (a model that needs N attempts to pass costs ~N x its per-attempt
price) is legible.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from .runner import RunResult


@dataclass
class ModelSummary:
    model_key: str
    attempts: int
    successes: int
    total_cost: float
    cost_known: bool
    failure_reasons: dict[str, int]
    total_tokens: int

    @property
    def pass_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def mean_cost_per_attempt(self) -> float | None:
        if not self.cost_known or not self.attempts:
            return None
        return self.total_cost / self.attempts

    @property
    def cost_per_success(self) -> float | None:
        if not self.cost_known:
            return None
        if self.successes == 0:
            return math.inf
        return self.total_cost / self.successes


def summarize(results: list[RunResult]) -> list[ModelSummary]:
    by_model: dict[str, list[RunResult]] = defaultdict(list)
    for r in results:
        by_model[r.model_key].append(r)

    summaries = []
    for model_key, runs in by_model.items():
        successes = sum(1 for r in runs if r.passed)
        costs = [r.cost_usd for r in runs]
        # Cost is "known" if the model has pricing (at least one row costed);
        # rows with no cost (e.g. a harness_error before any LLM call) count as
        # $0 spend rather than blanking the whole model.
        cost_known = any(c is not None for c in costs)
        total_cost = sum(c for c in costs if c is not None)
        reasons: dict[str, int] = defaultdict(int)
        for r in runs:
            if not r.passed:
                reasons[r.failure_reason or "unknown"] += 1
        summaries.append(
            ModelSummary(
                model_key=model_key,
                attempts=len(runs),
                successes=successes,
                total_cost=total_cost,
                cost_known=cost_known,
                failure_reasons=dict(reasons),
                total_tokens=sum(r.total_tokens for r in runs),
            )
        )
    # Cheapest cost-per-success first (the metric we care about).
    summaries.sort(key=lambda s: (s.cost_per_success is None, s.cost_per_success or 0))
    return summaries


def _money(x: float | None) -> str:
    if x is None:
        return "n/a"
    if x == math.inf:
        return "∞ (0 passed)"
    return f"${x:,.4f}"


def render(results: list[RunResult], console: Console | None = None) -> None:
    console = console or Console()
    summaries = summarize(results)

    table = Table(
        title="Cost per successful task",
        caption="cost/success = total spend on the model ÷ successful runs",
    )
    table.add_column("Model", style="bold")
    table.add_column("Attempts", justify="right")
    table.add_column("Passed", justify="right")
    table.add_column("Pass rate", justify="right")
    table.add_column("Mean $/attempt", justify="right")
    table.add_column("$/success", justify="right", style="bold")
    table.add_column("Retry tax", justify="right")

    for s in summaries:
        # Retry tax = attempts needed per success = 1 / pass_rate.
        retry_tax = f"{1 / s.pass_rate:.1f}x" if s.pass_rate else "—"
        table.add_row(
            s.model_key,
            str(s.attempts),
            str(s.successes),
            f"{s.pass_rate * 100:.0f}%",
            _money(s.mean_cost_per_attempt),
            _money(s.cost_per_success),
            retry_tax,
        )
    console.print(table)

    # Failure-reason breakdown per model.
    fail_table = Table(title="Failure reasons", show_header=True)
    fail_table.add_column("Model", style="bold")
    fail_table.add_column("Failure breakdown")
    any_fail = False
    for s in summaries:
        if s.failure_reasons:
            any_fail = True
            parts = ", ".join(f"{k}={v}" for k, v in sorted(s.failure_reasons.items()))
            fail_table.add_row(s.model_key, parts)
    if any_fail:
        console.print(fail_table)
