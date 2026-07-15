"""Per-task Docker container: build, run, exec, copy, teardown.

Each task runs in its own container built from the task's own Dockerfile. Task
code never runs on the host. The agent interacts with the task only through
`exec()` (a stateless `docker exec`), which is all our terminal agent needs.
"""

from __future__ import annotations

import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

# Per-stream cap on captured command output. subprocess capture is unbounded, so a
# task command that streams runaway output balloons the HOST process (not the
# container) until the OS OOM-kills it. Keep at most this many bytes per stream and
# discard the rest; the agent never needs megabytes of one command's output.
MAX_EXEC_OUTPUT_BYTES = 2_000_000

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
        # Read stdout/stderr on threads with a per-stream byte cap, discarding the
        # rest, so runaway output cannot OOM the host. Binary/non-UTF-8 output is
        # decoded with errors="replace" and never crashes the run.
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        captured: dict[str, bytes] = {"stdout": b"", "stderr": b""}
        truncated: dict[str, bool] = {"stdout": False, "stderr": False}

        def _drain(stream, key: str) -> None:
            buf = bytearray()
            for chunk in iter(lambda: stream.read(65536), b""):
                if len(buf) < MAX_EXEC_OUTPUT_BYTES:
                    buf += chunk[: MAX_EXEC_OUTPUT_BYTES - len(buf)]
                    if len(buf) >= MAX_EXEC_OUTPUT_BYTES:
                        truncated[key] = True
                # keep draining past the cap (and discarding) so a full pipe never
                # blocks the process; memory stays bounded by MAX_EXEC_OUTPUT_BYTES.
            captured[key] = bytes(buf)

        threads = [
            threading.Thread(target=_drain, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            timed_out = True
        for t in threads:
            t.join()

        def _dec(key: str) -> str:
            s = captured[key].decode("utf-8", errors="replace")
            if truncated[key]:
                s += f"\n[output truncated at {MAX_EXEC_OUTPUT_BYTES} bytes]"
            return s

        return ExecResult(
            exit_code=124 if timed_out else (proc.returncode or 0),
            stdout=_dec("stdout"), stderr=_dec("stderr"), timed_out=timed_out,
        )

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
