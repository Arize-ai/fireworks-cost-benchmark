"""Load a Terminal-Bench task from its on-disk directory.

We read tasks straight from a cloned Terminal-Bench checkout (default
`.tb-src/original-tasks`). We only ever read: the instruction, the Dockerfile
build context, the test script, and (for validation) the oracle solution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_TASKS_ROOT = Path(
    os.environ.get(
        "CPST_TASKS_ROOT",
        Path(__file__).resolve().parent.parent / ".tb-src" / "original-tasks",
    )
)


@dataclass
class Task:
    task_id: str
    path: Path
    instruction: str
    difficulty: str | None
    category: str | None
    parser_name: str
    max_agent_timeout_sec: float
    max_test_timeout_sec: float

    @property
    def dockerfile_dir(self) -> Path:
        return self.path

    @property
    def tests_dir(self) -> Path:
        return self.path / "tests"

    @property
    def run_tests_path(self) -> Path:
        return self.path / "run-tests.sh"

    @property
    def solution_path(self) -> Path:
        return self.path / "solution.sh"

    @property
    def has_oracle(self) -> bool:
        return self.solution_path.exists()


def load_task(task_id: str, tasks_root: Path | None = None) -> Task:
    root = tasks_root or DEFAULT_TASKS_ROOT
    path = root / task_id
    yaml_path = path / "task.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"No task.yaml for '{task_id}' at {yaml_path}")

    spec = yaml.safe_load(yaml_path.read_text())

    return Task(
        task_id=task_id,
        path=path,
        instruction=spec["instruction"].strip(),
        difficulty=(spec.get("difficulty") or "").strip() or None,
        category=(spec.get("category") or "").strip() or None,
        parser_name=spec.get("parser_name", "pytest"),
        max_agent_timeout_sec=float(spec.get("max_agent_timeout_sec") or 900.0),
        max_test_timeout_sec=float(spec.get("max_test_timeout_sec") or 180.0),
    )
