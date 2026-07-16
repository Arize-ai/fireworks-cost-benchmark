#!/usr/bin/env python
"""Routing analysis over a results JSONL.

Two policies, because they answer different questions:

  ORACLE     For each task, send it to the cheapest model that reliably solves it
             (pass rate >= threshold). Requires hindsight (you must already know
             which model solves which task), so it is a best-case bound, not a
             deployable policy.
  ESCALATION Try the cheapest model; if the attempt fails, escalate to the next
             model up the ladder. Deployable whenever you can verify success (here,
             the task's own tests). Simulated by Monte Carlo over the real trials.

Usage: python scripts/routing.py results/benchmark_40x6.jsonl
"""
from __future__ import annotations

import collections
import json
import random
import sys
from pathlib import Path


def load(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).open() if l.strip()]


def cell_stats(rows: list[dict]) -> tuple[dict, dict, list[str]]:
    """-> per_cell[(task,model)] = list of (passed, cost); per_model mean $/attempt."""
    cell: dict[tuple[str, str], list[tuple[bool, float]]] = collections.defaultdict(list)
    for r in rows:
        cell[(r["task_id"], r["model_key"])].append(
            (bool(r.get("passed")), float(r.get("cost_usd") or 0.0))
        )
    spend = collections.defaultdict(float)
    n = collections.Counter()
    for (_t, m), trials in cell.items():
        for _p, c in trials:
            spend[m] += c
            n[m] += 1
    per_model_cost = {m: spend[m] / n[m] for m in n}
    ladder = sorted(per_model_cost, key=lambda m: per_model_cost[m])  # cheapest first
    return cell, per_model_cost, ladder


def oracle(cell, ladder, tasks, threshold: float):
    """Per task: cheapest model with pass rate >= threshold. Cost-per-success uses
    that model's full spend/successes on that task, matching the study's metric."""
    spend = succ = 0.0
    solved = 0
    picks = {}
    for t in tasks:
        for m in ladder:  # cheapest first
            trials = cell.get((t, m))
            if not trials:
                continue
            rate = sum(1 for p, _ in trials if p) / len(trials)
            if rate >= threshold:
                spend += sum(c for _p, c in trials)
                succ += sum(1 for p, _ in trials if p)
                picks[t] = m
                solved += 1
                break
    return (spend / succ if succ else None), solved, picks


def escalate(cell, ladder, tasks, sims: int, seed: int = 0):
    """Try each model once, cheapest first, stop on first pass. Monte Carlo over the
    actual recorded trials (sampling with replacement from each cell's 6 trials)."""
    rng = random.Random(seed)
    total_cost = 0.0
    successes = 0
    depth = collections.Counter()
    for _ in range(sims):
        for t in tasks:
            for m in ladder:
                trials = cell.get((t, m))
                if not trials:
                    continue
                passed, cost = rng.choice(trials)
                total_cost += cost
                if passed:
                    successes += 1
                    depth[m] += 1
                    break
    return (total_cost / successes if successes else None), successes / sims, depth


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "results/benchmark_40x6.jsonl"
    rows = load(path)
    tasks = sorted({r["task_id"] for r in rows})
    cell, per_model_cost, ladder = cell_stats(rows)

    print(f"{len(rows)} runs | {len(tasks)} tasks | {len(ladder)} models\n")
    print("cost ladder (mean $/attempt, cheapest first):")
    for m in ladder:
        print(f"  {m:28s} ${per_model_cost[m]:.4f}")

    # single-model baselines
    print("\n--- single-model baselines ($/success) ---")
    base = {}
    for m in ladder:
        sp = sum(c for (t, mm), tr in cell.items() if mm == m for _p, c in tr)
        sc = sum(1 for (t, mm), tr in cell.items() if mm == m for p, _c in tr if p)
        base[m] = sp / sc if sc else None
    for m, v in sorted(base.items(), key=lambda x: (x[1] is None, x[1])):
        print(f"  {m:28s} {'n/a' if v is None else f'${v:.4f}'}")

    print("\n--- ORACLE routing (hindsight: cheapest model that reliably solves) ---")
    for thr in (4 / 6, 3 / 6):
        cps, solved, picks = oracle(cell, ladder, tasks, thr)
        label = f">={round(thr*6)}/6 trials"
        print(f"  {label:16s} blended ${cps:.4f}/success, {solved}/{len(tasks)} tasks routed")
        by = collections.Counter(picks.values())
        print(f"      picks: {dict(by)}")

    print("\n--- ESCALATION routing (deployable: try cheap, escalate on failure) ---")
    full_cps, solve_rate, depth = escalate(cell, ladder, tasks, sims=200)
    print(f"  full 9-model ladder: blended ${full_cps:.4f}/success, "
          f"{solve_rate:.1f}/{len(tasks)} tasks solved per pass")
    print(f"      solved at: {dict(depth.most_common())}")

    # curated ladder: drop poor-value rungs, keep a cheap->frontier escalation
    curated = [m for m in ladder if m in (
        "fw-gpt-oss-120b", "goog-gemini-3.1-flash-lite", "fw-kimi-k2p6", "oai-gpt-5.5")]
    cur_cps, cur_rate, cur_depth = escalate(cell, curated, tasks, sims=200)
    print(f"  curated ladder {curated}:")
    print(f"      blended ${cur_cps:.4f}/success, {cur_rate:.1f}/{len(tasks)} solved per pass")
    print(f"      solved at: {dict(cur_depth.most_common())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
