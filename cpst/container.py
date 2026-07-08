"""Per-task Docker container: build, run, exec, copy, teardown.

Each task runs in its own container built from the task's own Dockerfile. Task
code never runs on the host. The agent interacts with the task only through
`exec()` (a stateless `docker exec`), which is all our terminal agent needs.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .tasks import Task

# Terminal-Bench copies tests here and points TEST_DIR at it (see harness).
CONTAINER_TEST_DIR = "/tests"
WORKDIR = "/app"


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def output(self) -> str:
        """Combined stdout+stderr, as an agent would see in a terminal."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts).strip()


class TaskContainer:
    """Lifecycle wrapper around one task's container."""

    def __init__(self, task: Task, keep: bool = False):
        self.task = task
        self.keep = keep
        suffix = uuid.uuid4().hex[:8]
        self.image_tag = f"cpst/{task.task_id}:{suffix}"
        self.container_name = f"cpst-{task.task_id}-{suffix}"
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    def build(self, timeout: float = 900.0) -> None:
        _run(
            ["docker", "build", "-t", self.image_tag, str(self.task.dockerfile_dir)],
            timeout=timeout,
            what=f"build {self.task.task_id}",
        )

    def start(self) -> None:
        _run(
            [
                "docker", "run", "-d",
                "--name", self.container_name,
                "-w", WORKDIR,
                self.image_tag,
                "sleep", "infinity",
            ],
            timeout=120,
            what=f"start {self.container_name}",
        )
        self._started = True

    def teardown(self) -> None:
        if self._started:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True, text=True,
            )
            self._started = False
        if not self.keep:
            subprocess.run(
                ["docker", "image", "rm", "-f", self.image_tag],
                capture_output=True, text=True,
            )

    def __enter__(self) -> "TaskContainer":
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()

    # -- interaction -------------------------------------------------------
    def exec(
        self,
        command: str,
        timeout: float = 120.0,
        workdir: str = WORKDIR,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run a shell command inside the container via `docker exec`."""
        args = ["docker", "exec", "-w", workdir]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        args += [self.container_name, "bash", "-c", command]
        # errors="replace": task commands can emit non-UTF-8 bytes (binaries,
        # hashes) — never let decoding crash the run.
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            def _dec(x):
                if isinstance(x, bytes):
                    return x.decode("utf-8", errors="replace")
                return x or ""
            return ExecResult(
                exit_code=124, stdout=_dec(e.stdout), stderr=_dec(e.stderr),
                timed_out=True,
            )
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def copy_in(self, src: Path, dest: str) -> None:
        """docker cp a host path into the container."""
        _run(
            ["docker", "cp", str(src), f"{self.container_name}:{dest}"],
            timeout=120,
            what=f"copy {src} -> {dest}",
        )


def _run(args: list[str], timeout: float, what: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker {what} failed (exit {proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc
