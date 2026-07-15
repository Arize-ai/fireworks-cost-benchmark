#!/usr/bin/env python
"""Ad-hoc status for the 40x9x6 scale-up run. No args:

    ./.venv/bin/python scripts/run_status.py

Reads the pilot (trial 1) and rest (trials 2-6) JSONL, plus process/Docker health,
and prints progress, throughput, ETA, spend-by-provider, pass and error rates.
Safe to run anytime; it only reads files and process state.
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "results" / "benchmark_40x6_pilot.jsonl"
REST = ROOT / "results" / "benchmark_40x6_rest.jsonl"
FINAL = ROOT / "results" / "benchmark_40x6.jsonl"
REST_TARGET = 1800  # 40 tasks x 9 models x 5 trials


def load(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.open():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def alive(pattern: str) -> int:
    r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    return len([x for x in r.stdout.split() if x])


def containers() -> int:
    r = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True)
    return len([x for x in r.stdout.split() if x])


def summarize(label: str, rows: list[dict]) -> None:
    if not rows:
        print(f"{label}: (no rows yet)")
        return
    spend = sum(r.get("cost_usd") or 0 for r in rows)
    passes = sum(1 for r in rows if r.get("passed"))
    errs = sum(1 for r in rows if r.get("agent_error"))
    prov = collections.defaultdict(float)
    for r in rows:
        prov[r["model_key"].split("-")[0]] += r.get("cost_usd") or 0
    print(f"{label}: {len(rows)} runs | ${spend:.2f} | "
          f"pass {passes}/{len(rows)} ({100*passes/len(rows):.0f}%) | "
          f"errors {errs} ({100*errs/len(rows):.1f}%)")
    print("   by provider: " + "  ".join(
        f"{k}=${v:.2f}" for k, v in sorted(prov.items(), key=lambda x: -x[1])))


def main() -> int:
    if FINAL.exists():
        rows = load(FINAL)
        print("=== RUN COMPLETE ===")
        summarize("final (all 6 trials)", rows)
        return 0

    rest = load(REST)
    print("=== SCALE-UP RUN IN PROGRESS ===")
    pct = 100 * len(rest) / REST_TARGET if REST_TARGET else 0
    print(f"trials 2-6: {len(rest)}/{REST_TARGET} ({pct:.0f}%)")

    if rest:
        birth = os.stat(REST).st_birthtime
        el = datetime.datetime.now().timestamp() - birth
        rate = len(rest) / el if el else 0
        remaining = REST_TARGET - len(rest)
        eta_h = (remaining / rate / 3600) if rate else 0
        print(f"elapsed {el/60:.0f} min | {rate*60:.1f} runs/min | ETA ~{eta_h:.1f}h")

    print()
    summarize("pilot (trial 1, done)", load(PILOT))
    summarize("rest  (trials 2-6)   ", rest)

    print("\nhealth: "
          f"rest_run={'up' if alive('run.py.*benchmark_40x6_rest') else 'DOWN'} | "
          f"orchestrator={'up' if alive('orchestrate_re') else 'down'} | "
          f"caffeinate={'on' if alive('^caffeinate') or alive('caffeinate') else 'OFF'} | "
          f"containers={containers()}")
    print("note: rest $/run reads low early (easy tasks first); trust the pilot "
          "average (~$0.23/run, ~$495 for all 6 trials) until the hard tail lands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
