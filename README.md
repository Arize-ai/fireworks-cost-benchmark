# Cost per successful task

**A benchmark that measures what it actually costs to get a task *done* — not what a token costs.**

Most model comparisons quote price per million tokens. Production systems care
about something different: *what did it cost to successfully complete the task?*
Retries, failed tool calls, malformed outputs, and runs that grind to the token
limit all cost real money that a token-price benchmark hides.

```
cost per successful task = total $ spent on a model across ALL attempts
                           ---------------------------------------------
                                   number of successful runs
```

This repo is a small, instrumented agent that runs real [Terminal-Bench](https://www.tbench.ai)
tasks in Docker, grades each run with the task's own tests, and reports
cost-per-successful-task per model. Every run is traced to [Arize AX](https://arize.com).

## The finding

10 tasks × 3 models × 3 trials (90 runs). Prices as of 2026-07-07.

| Model | Provider | Pass rate | Mean $/attempt | **$/success** | Retry tax |
|---|---|---|---|---|---|
| gpt-oss-120b | Fireworks (open) | 53% | $0.0166 | **$0.0311** | 1.9× |
| kimi-k2.6 | Fireworks (open) | 77% | $0.0984 | **$0.1283** | 1.3× |
| gpt-5.5 | OpenAI (frontier) | 83% | $0.3233 | **$0.3880** | 1.2× |

Takeaways the metric makes visible:

- **The open small model is ~12× cheaper per success** than the frontier model,
  even though it fails more often. Its per-token cheapness (30–50×) compresses to
  ~12× once you account for its lower success rate and chattier tool use.
- **The mid-tier is the value pick:** kimi is ~3× cheaper per success than gpt-5.5
  at nearly the same reliability (77% vs 83%).
- **"Retry tax"** (attempts needed per success = 1 ÷ pass rate) is the hidden cost
  in one number: the open model needs 1.9 attempts per success, the frontier 1.2.
- **Routing beats any single model.** Sending each task to the cheapest model that
  reliably solves it (≥2/3 trials) yields a blended **$0.11 per successful task,
  solving 9/10 tasks** — cheaper than mid-tier-alone, with frontier-level coverage.
  Easy tasks → gpt-oss; a few mediums → kimi; only the hardest crypto task needs
  gpt-5.5. (One task, `largest-eigenval`, no model solves reliably.)

Frontier models are not automatically better value: gpt-5.5 *failed the trivial
csv-to-parquet task on 2 of 3 trials* — and one `largest-eigenval` success cost
**$3.54**. The traces show exactly where each run went right or wrong.

## What's in this repo

```
run.py                 CLI matrix runner: (task × model × trial), bounded concurrency
cpst/
  tasks.py             load a Terminal-Bench task (instruction, timeouts, paths)
  container.py         per-task Docker lifecycle: build / run / exec / copy / teardown
  agent.py             the agent — OpenAI-compatible loop, one run_terminal_cmd tool,
                       token accounting, token/wall-clock guardrails
  grading.py           faithful port of Terminal-Bench's pytest grading
  runner.py            one run: container → agent → grade → cost → traced result
  report.py            cost-per-successful-task aggregation + summary table
  models.py            model matrix loader + OpenAI-compatible client factory
  tracing.py           Arize AX / OpenInference setup
config/
  models.yaml          model matrix + per-1M-token pricing (single source of truth)
tasks/
  subset.txt           the 10-task subset (each validated against its own oracle)
results/
  benchmark_3trials.jsonl   the headline 90-run study (one row per run)
  benchmark_run1.jsonl      an earlier 2-model run
  subset_validation.log     record of which candidate tasks passed their oracle
scripts/
  summarize.py         render the summary table from any results JSONL
  ax_link.py           build correct Arize AX deep links to traces in a results JSONL
  validate_grading.py  prove grading is faithful on one task (oracle passes, empty fails)
  validate_batch.py    oracle-validate candidate tasks for subset selection
  verify_tool_spans.py check tool spans go red on failure; exec survives binary output
  run_agent_once.py    run one (task, model) end-to-end
  phase2_test.py / phase3_test.py / phase3_verify_spans.py   dev checks
```

Each row in a results JSONL has: task, model, trial, `passed`, `failure_reason`
(`test_failed` / `token_cap` / `timeout` / `max_steps` / `agent_error`), token
counts, dollar `cost_usd`, wall-clock, `steps`, `tool_calls`, and `trace_id`.

## How it works

- **Tasks** come from Terminal-Bench, used *only* as a task source (prompts,
  Docker environments, test scripts). We do **not** use Harbor as the runtime —
  execution and grading are our own, so the demo shows *our* instrumented agent.
- **Agent** is a thin loop over the OpenAI-compatible protocol with a single tool,
  `run_terminal_cmd`, that runs bash inside the task's container via `docker exec`.
  Any provider works by editing `config/models.yaml` (Fireworks, OpenAI, …). The
  thin scaffold keeps cost attributable to the *model*, not the harness.
- **Grading** copies the task's own `tests/` + `run-tests.sh` into the container
  *after* the agent finishes and runs them (pytest; all tests must pass). The agent
  never sees the tests. This reproduces Terminal-Bench's own grading exactly.
- **Guardrails:** a **token-budget cap** (primary, fairness-preserving cutoff) and
  a generous **wall-clock timeout** (safety net). Hitting either is recorded as a
  fail with that reason — distinct from a genuine test failure, so the success
  count stays honest.
- **Observability:** every run emits an OpenInference trace to Arize AX — a root
  `AGENT` span with nested `LLM` and `TOOL` spans, token counts, latency, and an
  ERROR status + failure reason on failure. Tool spans go red on non-zero exit, so
  you can see *where* a run hit friction.

## Setup

Requires **Docker** (running) and **Python 3.12**.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # fill in the keys (see below)

# Task source (read-only, gitignored, ~240 tasks)
git clone --depth 1 https://github.com/laude-institute/terminal-bench.git .tb-src
```

`.env` keys:

| Key | Purpose |
|---|---|
| `FIREWORKS_API_KEY` | Fireworks open models |
| `OPENAI_API_KEY` | frontier comparison model |
| `ARIZE_SPACE_ID`, `ARIZE_API_KEY` | Arize AX tracing |
| `ARIZE_ORG_ID` | (optional) for `scripts/ax_link.py` deep links |
| `ARIZE_PROJECT_NAME` | AX project (default `cost-per-successful-task`) |

Pricing lives in `config/models.yaml` and is the single source of truth for the
Python cost math. Mirror the same per-model numbers into Arize AX's model-cost
settings so the dashboard's cost matches this repo's.

## Usage

Reproduce the headline study:

```bash
python run.py \
  --tasks tasks/subset.txt \
  --models fw-gpt-oss-120b,fw-kimi-k2p6,oai-gpt-5.5 \
  --trials 3 --token-cap 200000 --wall-clock-cap 900 --concurrency 6
```

Inspect results:

```bash
python scripts/summarize.py results/benchmark_3trials.jsonl   # the summary table
python scripts/ax_link.py results/benchmark_3trials.jsonl --failed-only   # AX links
```

Smaller smoke test:

```bash
python run.py --task csv-to-parquet --models fw-gpt-oss-120b --trials 1
```

## Caveats

- **Stateless exec agent.** The agent drives tasks through one-shot `docker exec`
  commands, not a persistent interactive terminal. This fits batch/file-output
  tasks (the large majority) but not tasks that need a live TUI. The subset was
  chosen accordingly.
- **Subset, not the full suite.** 10 of Terminal-Bench's ~240 tasks, picked to sit
  in the band where some models pass and some fail — that's where the metric is
  informative. Each was validated by running its own oracle solution through this
  harness. This is not a leaderboard-comparable score.
- **Prices drift.** `config/models.yaml` prices are from 2026-07-07; re-check them.
- **Small sample.** 3 trials per cell smooths but doesn't eliminate run-to-run
  variance. Treat the numbers as directional.
- **The `ax` CLI (0.8.0)** resolves `traces export` by project *name* buggily —
  pass the project *ID*; and `--limit` must be ≤100.

## Credits

Tasks are from [Terminal-Bench](https://github.com/laude-institute/terminal-bench)
(Apache-2.0), used here only as a task source. Individual tasks may bundle
third-party components under their own licenses — check per-task before any public
redistribution. Tracing via [Arize AX](https://arize.com) and
[OpenInference](https://github.com/Arize-ai/openinference).
