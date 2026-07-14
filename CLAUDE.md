# CLAUDE.md: Cost per successful task (Fireworks x Arize demo)

Context for working in this repo. Read this before touching anything.

## What this is

A joint Fireworks AI x Arize blog demo. An instrumented, model-agnostic terminal
agent runs [Terminal-Bench](https://www.tbench.ai) tasks in Docker, grades each run
with the task's own tests, and reports **cost per successful task** per model
(total spend across all attempts / successful runs). The point: token price is an
infrastructure metric; cost per successful task is the product metric, and open
models on Fireworks win on it.

See `README.md` for the product-level explanation and `blog/cost-per-successful-task.md`
for the write-up.

## Working conventions (important)

- **Confine all work to this directory** (`.../demos/fireworks-demo`). The user was
  firm about this. Do not read from or write to sibling dirs like
  `../../examples/rosetta` even if they appear as extra working dirs.
- **No em dashes or en dashes** in any prose the user may publish (blog, README).
  Strong standing preference. Use commas, colons, semicolons, or parentheses. Grep
  for `—` and `–` after editing prose.
- **Commit only when asked.** Before any commit, grep the staged diff for secrets
  (`git diff --cached | grep -E 'sk-|fw_|ak-[0-9a-f]{8}|U3BhY2U6|QWNjb3'`). A real
  Arize key leaked into `.env.example` once; the pre-commit grep caught it. Keep
  `.env.example` placeholder-only.
- Don't spawn subagents unless the user asks.

## Key architecture decisions (some reverse the original brief)

- **Agent stack: hand-rolled OpenAI-compatible loop, NOT the Claude Agent SDK.**
  The original brief said Claude Agent SDK; the user and I switched. Fireworks (and
  OpenAI, etc.) are OpenAI-compatible, so one code path handles every model by
  config, and in-process OpenInference instrumentation is clean. If you see the
  brief mention the Claude SDK, that is superseded.
- **Terminal-Bench is a task SOURCE only. Do NOT use Harbor as the runtime.**
  Execution and grading are our own code, so the demo shows our instrumented agent.
- **One Docker container per task**, built from the task's own Dockerfile. Task code
  never runs on the host.
- **Stateless `docker exec` tool**, not an interactive tmux/TTY. This fits
  batch/file-output tasks (the majority); the subset avoids interactive-TUI tasks.
- **Tracing goes to Arize AX only (no Phoenix).** `arize-otel` register() plus the
  OpenInference OpenAI instrumentor.

## Environment

- **Python 3.12 venv at `.venv`.** Always use `./.venv/bin/python`. System python is
  3.9; do not use it.
- **Docker required** and running. Host is arm64 (Apple Silicon). The Terminal-Bench
  base image `ghcr.io/laude-institute/t-bench/ubuntu-24-04` is multi-arch and builds
  natively; individual heavy tasks (qemu/kernel/GPU) may not, and are avoided.
- **Task source** cloned to `.tb-src/` (gitignored). Tasks live in
  `.tb-src/original-tasks/<task-id>/` (241 of them). `cpst/tasks.py` reads from there.
- **Secrets in `.env`** (gitignored): `FIREWORKS_API_KEY`, `OPENAI_API_KEY`,
  `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, `ARIZE_ORG_ID`, `ARIZE_PROJECT_NAME`.

## How the pieces fit (`cpst/`)

- `tasks.py` load a task (instruction, timeouts, paths to Dockerfile/tests/oracle).
- `container.py` Docker lifecycle: build, run (`sleep infinity`), `exec`, `copy_in`,
  teardown. `exec` decodes with `errors="replace"` (task output can be non-UTF-8).
- `agent.py` the agent loop, single `run_terminal_cmd` tool, token accounting,
  token/wall-clock guardrails. Emits TOOL spans (red on non-zero exit / timeout /
  bad args).
- `grading.py` faithful port of Terminal-Bench pytest grading (see below).
- `runner.py` one run end to end: opens the root `AGENT` span, runs the agent,
  grades, computes cost, records a `RunResult` with `trace_id`.
- `report.py` aggregates RunResults into the cost-per-successful-task table.
- `models.py` loads `config/models.yaml`, builds OpenAI clients, computes cost.
- `tracing.py` Arize AX + OpenInference setup; `init_tracing()` / `flush()`.
- `run.py` (repo root) the CLI matrix runner (task x model x trial), bounded
  concurrency via ThreadPoolExecutor, writes JSONL, prints the summary.

## Grading contract (get this right)

- Reproduces Terminal-Bench exactly, without importing its runtime.
- After the agent finishes, copy the task's `tests/` dir to container `/tests`, then
  `run-tests.sh` to `/tests/run-tests.sh`, set `TEST_DIR=/tests`, run it.
- `docker cp <tests_dir> <container>:/tests` when `/tests` does not exist creates it
  correctly. Do NOT use `Path.joinpath(".")` to copy contents; pathlib normalizes
  the `.` away and files land in the wrong place (this bug cost time once).
- Parse pytest's "short test summary info" block (requires `-rA`). **Resolved = at
  least one result and ALL PASSED.** PASSED/XFAIL/SKIPPED count as pass;
  FAILED/XPASS/ERROR as fail.
- The agent must never see the tests; they are copied in only after it finishes.

## Guardrails, cost, success

- `token_cap` (primary, fairness lever) and `wall_clock_cap_sec` (generous safety
  net) in `config/models.yaml` defaults; overridable via CLI.
- A run is a **success only if the agent completed on its own AND all tests pass.**
  Hitting a cap or erroring is a fail with a distinct `failure_reason`
  (`token_cap` / `timeout` / `max_steps` / `agent_error`), not counted as success.
- Pricing (USD per 1M tokens) lives ONLY in `config/models.yaml`, verified
  2026-07-07. Mirror the same numbers into Arize AX model-cost settings so the
  dashboard matches. Do all cost math in Python.

## Arize AX, the `ax` CLI, and skills

- The 12 `arize-*` skills are installed globally (`~/.claude/skills/`) and available
  as Skill tools this session. `ax` CLI 0.8.0 is at `~/.local/bin/ax`.
- Drive `ax` with env vars sourced from `.env` (`set -a; source .env; set +a`).
  Do NOT reconfigure the pre-existing `default` ax profile; it uses a different key
  for other work.
- Identifiers (not secrets): space "lvoss Space" =
  `U3BhY2U6MzEwMjI6NERxbA==`, project `cost-per-successful-task` =
  `TW9kZWw6ODY5NDk2OTI1NTplY0dl`, org = `QWNjb3VudE9yZ2FuaXphdGlvbjoyOTY5NjpvcEF2`.
- `ax` 0.8.0 quirks: `traces export` by project NAME is buggy (pass the project ID);
  `--limit` must be <= 100.
- **AX trace deep links MUST include a `startA`/`endA` time window** or the trace
  will not load. Use `scripts/ax_link.py` (reads a results JSONL, derives the window).

## Models (as of July 2026)

Nine models across four providers. Config keys:
- Fireworks (open): `fw-gpt-oss-120b`, `fw-kimi-k2p6`, `fw-deepseek-v4-pro`,
  `fw-glm-5p2`. Ids look like `accounts/fireworks/models/glm-5p2`.
- OpenAI: `oai-gpt-5.5`, `oai-gpt-5`.
- Anthropic (OpenAI-compat `/v1/`): `ant-claude-sonnet-5`.
- Google (OpenAI-compat `/v1beta/openai/`): `goog-gemini-3.5-flash`,
  `goog-gemini-3.1-flash-lite`.

Query live ids with the OpenAI client's `.models.list()` against each provider's
base URL (Anthropic's compat endpoint 401s on `.models.list()`; that is expected,
chat completions still work). All 9 pricing pairs verified 2026-07-14 (see the
header comment in `config/models.yaml` for source pages).

- **Budget note:** the $560 Fireworks credit only covers the 4 `fw-*` models.
  gpt-5/5.5 bill to OpenAI, sonnet-5 to Anthropic, both geminis to Google.
- **Gemini needs the thought_signature round-trip.** Its thinking models return a
  `thought_signature` on each tool call (at `tool_call.extra_content.google.
  thought_signature`) and 400 on the next turn if it is not echoed back verbatim.
  `agent.py` now preserves any `extra_content` a provider attaches to a tool call,
  so this works with no Google-specific branch. Do not strip it.
- **gemini-3.5-flash is NOT cheap** ($1.50/$9.00, thinking tokens): per-task cost
  can rival the OpenAI models. `gemini-3.1-flash-lite` ($0.25/$1.50) is the genuine
  cheap-closed probe.
- **sonnet-5 tokenizer tax:** its newer tokenizer emits ~30% more tokens for the
  same text, inflating real cost/task above the sticker rate. Billed at the $3/$15
  standard rate (intro $2/$10 runs through Aug 31 2026).

## Current status

- **Original 90-run study** (published baseline): `results/benchmark_3trials.jsonl`
  (10 tasks x 3 models x 3 trials). Headline cost/success: gpt-oss-120b **$0.031**
  (53% pass), kimi-k2.6 **$0.128** (77%), gpt-5.5 **$0.388** (83%). Escalation
  routing: blended **$0.11/success, 9/10 solved**. `tasks/subset.txt` (10 tasks) and
  this JSONL are the published study; keep them intact.
- **Scale-up in progress** (make the study less thin): 9 models x 40 tasks x 6
  trials = 2160 runs planned. Deltas landed:
  - 4 models added to `config/models.yaml` (glm-5p2, sonnet-5, gemini-3.5-flash,
    gemini-3.1-flash-lite), pricing verified 2026-07-14.
  - Gemini thought_signature fix in `agent.py` (see Models section).
  - `tasks/subset40.txt`: 40 validated tasks (8 easy / 20 medium / 12 hard),
    superset of the original 10. Selected deterministically (seed 42) from 139 clean
    candidates, oracle-validated (50 run, 36 passed, 6 held out for balance).
  - **Pilot running:** 40 x 9 x 1 = 360 runs -> `results/benchmark_40x6_pilot.jsonl`
    (trial 1 of 6). Purpose: measure real cost/run per provider before the full run.
- Blog draft `blog/cost-per-successful-task.md` (untracked): framing -> why
  (inference subsidies ending) -> thesis -> result -> methodology -> interpretation.
  Zero em/en dashes. Placeholder `LINK-TO-SUBSIDIES-PIECE` for the user's article.

## Open follow-ups

- **Finish the scale-up run:** after the pilot, run the remaining 5 trials
  (40 x 9 x 5 = 1800 runs), merge shards into `results/benchmark_40x6.jsonl`,
  regenerate the report. Wait for the user's go-ahead before the big run.
- Mirror the 4 new models' costs into Arize AX model-cost settings (UI only; the
  `ax` CLI 0.8.0 has no model-cost command). Study numbers are Python-driven and
  unaffected; this is dashboard parity only.
- The UTF-8 decode fix in `container.exec` landed AFTER the 90-run study, so that
  study has one `harness_error` (kimi on crack-7z-hash) that was the decode bug, now
  fixed. Re-running `crack-7z-hash` and `largest-eigenval` would clean up a couple of
  harness-caused failures (they were the harness, not the model).
- `README.md` still contains em dashes; the user may want them removed too.
- Decide byline/attribution for the blog and whether to genericize "GPT-5.5" to
  "a frontier model".

## Verification scripts (`scripts/`)

`summarize.py` (render table from JSONL), `ax_link.py` (AX deep links),
`validate_grading.py` (oracle passes / empty fails on one task),
`validate_batch.py` (oracle-validate subset candidates),
`verify_tool_spans.py` (tool spans go red; exec survives binary output),
`run_agent_once.py`, and phase2/phase3 dev checks.
