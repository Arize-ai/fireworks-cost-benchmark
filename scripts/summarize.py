"""Render the cost-per-successful-task summary from a results JSONL.

Usage: python scripts/summarize.py results/benchmark_3trials.jsonl
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cpst.report import render
from cpst.runner import RunResult

path = sys.argv[1] if len(sys.argv) > 1 else "results/benchmark_3trials.jsonl"
rows = [RunResult(**json.loads(l)) for l in open(path)]
render(rows)
