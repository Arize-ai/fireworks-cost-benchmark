"""Grade a task run using the task's own test script.

This reproduces Terminal-Bench's grading exactly, without importing its runtime:

  1. Copy `run-tests.sh` and `tests/` into the container at /tests.
  2. Run `bash /tests/run-tests.sh` with TEST_DIR=/tests.
  3. Parse pytest's "short test summary info" block (produced by `-rA`).
  4. Resolved iff there is >=1 parsed result and ALL are PASSED.

Semantics mirror terminal_bench/parsers/pytest_parser.py and
harness.py::_is_resolved. The agent never sees tests/ — it is copied in only
after the agent has finished.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .container import CONTAINER_TEST_DIR, TaskContainer
from .tasks import Task


class TestStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


# pytest short-summary statuses -> pass/fail (mirrors terminal-bench)
_PYTEST_PASS = {"PASSED", "XFAIL", "SKIPPED"}
_PYTEST_FAIL = {"FAILED", "XPASS", "ERROR"}
_SUMMARY_HEADER = re.compile(r"=+\s*short test summary info\s*=+", re.IGNORECASE)


def parse_pytest(content: str) -> dict[str, TestStatus]:
    """Parse per-test results from the short test summary info section."""
    parts = _SUMMARY_HEADER.split(content, maxsplit=1)
    if len(parts) < 2:
        raise ValueError("No 'short test summary info' section in test output.")

    results: dict[str, TestStatus] = {}
    for line in parts[1].splitlines():
        # e.g. "PASSED tests/test_outputs.py::test_data_matches"
        #      "FAILED tests/test_outputs.py::test_x - AssertionError: ..."
        head = line.split(" - ", 1)[0]  # drop failure description
        bits = head.split(maxsplit=1)
        if len(bits) != 2:
            continue
        status_word = bits[0].strip().strip(":")
        test_path = bits[1].strip()
        name = test_path.split("::", 1)[-1]
        if not name:
            continue
        if status_word in _PYTEST_PASS:
            results[name] = TestStatus.PASSED
        elif status_word in _PYTEST_FAIL:
            results[name] = TestStatus.FAILED
        # anything else (UNKNOWN lines) is ignored
    return results


def is_resolved(results: dict[str, TestStatus] | None) -> bool:
    if not results:  # None or empty -> not resolved
        return False
    return all(s == TestStatus.PASSED for s in results.values())


@dataclass
class GradeResult:
    resolved: bool
    results: dict[str, TestStatus] = field(default_factory=dict)
    raw_output: str = ""
    parse_error: str | None = None
    test_timed_out: bool = False


def grade(container: TaskContainer, task: Task, timeout: float | None = None) -> GradeResult:
    """Copy tests in, run them, parse, and decide pass/fail."""
    test_timeout = timeout if timeout is not None else task.max_test_timeout_sec

    # Copy the tests dir and run-tests.sh into /tests, matching Terminal-Bench:
    # test_outputs.py must end up at $TEST_DIR/test_outputs.py.
    # `docker cp <dir> <container>:/tests` creates /tests from the dir when it
    # does not yet exist, so copy the tests dir first, then drop run-tests.sh in.
    if task.tests_dir.exists():
        container.copy_in(task.tests_dir, CONTAINER_TEST_DIR)
    else:
        container.exec(f"mkdir -p {CONTAINER_TEST_DIR}", timeout=30)
    container.copy_in(task.run_tests_path, f"{CONTAINER_TEST_DIR}/run-tests.sh")

    res = container.exec(
        f"bash {CONTAINER_TEST_DIR}/run-tests.sh",
        timeout=test_timeout,
        env={"TEST_DIR": CONTAINER_TEST_DIR},
    )

    if res.timed_out:
        return GradeResult(
            resolved=False, raw_output=res.output, test_timed_out=True
        )

    try:
        results = parse_pytest(res.output)
    except ValueError as e:
        return GradeResult(
            resolved=False, raw_output=res.output, parse_error=str(e)
        )

    return GradeResult(
        resolved=is_resolved(results), results=results, raw_output=res.output
    )
