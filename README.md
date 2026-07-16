# Cost per successful task

**A benchmark that measures what it actually costs to get a task *done*, not what a token costs.**

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

40 tasks × 9 models × 6 trials (2,160 runs), across four providers. Prices verified
2026-07-07 and 2026-07-14. Every one of the 360 (task, model) cells has exactly 6 trials.

| Model | Provider | Pass rate | Mean $/attempt | **$/success** | Retry tax |
|---|---|---|---|---|---|
| gpt-oss-120b | Fireworks (open) | 33% | $0.0178 | **$0.0541** | 3.0× |
| gemini-3.1-flash-lite | Google | 40% | $0.0255 | **$0.0632** | 2.5× |
| kimi-k2.6 | Fireworks (open) | 42% | $0.1632 | **$0.3839** | 2.4× |
| glm-5.2 | Fireworks (open) | 42% | $0.2083 | **$0.5000** | 2.4× |
| deepseek-v4-pro | Fireworks (open) | 39% | $0.2302 | **$0.5877** | 2.6× |
| gpt-5.5 | OpenAI (frontier) | 67% | $0.4242 | **$0.6363** | 1.5× |
| gpt-5 | OpenAI | 41% | $0.3171 | **$0.7687** | 2.4× |
| claude-sonnet-5 | Anthropic | 49% | $0.4945 | **$1.0144** | 2.1× |
| gemini-3.5-flash | Google | 23% | $0.2877 | **$1.2331** | 4.3× |

Takeaways the metric makes visible:

- **The cheapest model per success has the worst pass rate.** gpt-oss-120b completes
  a task for $0.054 while passing only 33% of the time, roughly **12× cheaper per
  success than gpt-5.5** and **23× cheaper than gemini-3.5-flash**. Cheap tokens plus
  retries beat expensive tokens, even after paying the retry tax three times over.
- **The finding replicated when we made the study harder.** An earlier 10-task,
  3-model, 3-trial study put gpt-oss vs gpt-5.5 at **12.5×**. Quadrupling the tasks,
  tripling the models, doubling the trials, and shifting to a much harder task mix
  moved it to **11.8×**. The gap held.
- **The frontier model earns its price on capability, not value.** gpt-5.5 has the
  best pass rate (67%) and the lowest retry tax (1.5×). It is the most *reliable*
  model here. It still costs ~12× more per unit of completed work.
- **A cheap-sounding name is not a cheap model.** gemini-3.5-flash is the worst value
  in the field: lowest pass rate (23%), highest retry tax (4.3×), and **91% of its
  failures (168 of 184) were `token_cap`**, meaning it spirals until it hits the
  budget ceiling and fails. Its sibling gemini-3.1-flash-lite is the second-cheapest
  per success. "Flash" told you nothing; the metric did.
- **"Retry tax"** (attempts per success = 1 ÷ pass rate) puts the hidden cost in one
  number: the cheapest model needs 3 attempts per success, the frontier 1.5.
- **Cheapest per success is not a drop-in replacement.** gpt-oss-120b reliably solves
  (4 of 6 trials) only **8 of 40 tasks**; gpt-5.5 reliably solves **25 of 40**. The
  open model is cheap partly *because* it only wins the tasks it can win. Cost per
  success and coverage are different questions, and you need both.

### Routing beats every single model, on both axes

Run `python scripts/routing.py results/benchmark_40x6.jsonl`.

| Policy | Reliably solves | **$/success** |
|---|---|---|
| gpt-oss-120b alone | 8/40 | $0.054 |
| gpt-5.5 alone (best single model) | 25/40 | $0.636 |
| **Oracle routing** (cheapest model that reliably solves each task) | **32/40** | **$0.223** |
| **Escalation** (gpt-oss → flash-lite → kimi → gpt-5.5, stop on first pass) | 32.3/40 per pass | **$0.527** |
| Naive escalation through all 9 models | 33.7/40 per pass | $1.195 |

- **Oracle routing solves 28% more tasks than the best single model at ~1/3 the cost**
  ($0.223 vs $0.636). It needs hindsight to pick the model per task, so treat it as
  the bound on what good routing buys, not a deployable policy.
- **Escalation is the deployable version** and still wins on both axes: cheaper than
  gpt-5.5 alone *and* solving more, because most tasks get retired by a model costing
  under $0.03 an attempt and only the genuinely hard ones reach the frontier.
- **Naive "escalate through everything" is a trap.** At $1.195 per success it is worse
  than every single model in the study: you pay the entire ladder on the ~6 tasks that
  nobody solves, and you pay poor-value rungs on the way. Ladder design is the whole
  game; a bad ladder is worse than no routing at all.

Frontier models are not automatically better value. gpt-5.5 passed the trivial
`csv-to-parquet` task only **3 of 6 trials**, and the single most expensive success in
the study was claude-sonnet-5 solving `feal-linear-cryptanalysis` for **$1.35**. The
traces show exactly where each run went right or wrong.

Scope: 37 of 40 tasks were solved by at least one model. Three (`gpt2-codegolf`,
`path-tracing-reverse`, `pcap-to-netflow`) were solved by none; all three pass their
own oracle solution through this harness, so they are legitimately hard rather than
broken. Total spend for the study was $520.45.

## What's in this repo

```
run.py                 CLI matrix runner: (task × model × trial), bounded concurrency,
                       --resume to finish a crashed/stopped matrix without re-running
cpst/
  tasks.py             load a Terminal-Bench task (instruction, timeouts, paths)
  container.py         per-task Docker lifecycle: build / run / exec / copy / teardown
  agent.py             the agent: OpenAI-compatible loop, one run_terminal_cmd tool,
                       token accounting, token/wall-clock guardrails
  grading.py           faithful port of Terminal-Bench's pytest grading
  runner.py            one run: container -> agent -> grade -> cost -> traced result
  report.py            cost-per-successful-task aggregation + summary table
  models.py            model matrix loader + OpenAI-compatible client factory
  tracing.py           Arize AX / OpenInference setup
config/
  models.yaml          model matrix + per-1M-token pricing (single source of truth)
tasks/
  subset40.txt         the 40-task set for the headline study (8 easy / 20 medium /
                       12 hard), a superset of subset.txt, each oracle-validated
  subset.txt           the original 10-task subset (earlier study)
results/
  benchmark_40x6.jsonl      the headline 2,160-run study (one row per run)
  benchmark_3trials.jsonl   the earlier 90-run study (10 tasks × 3 models × 3 trials)
  subset_validation.log     record of which candidate tasks passed their oracle
scripts/
  summarize.py         render the summary table from any results JSONL
  routing.py           oracle + escalation routing analysis over a results JSONL
  run_status.py        live progress / throughput / spend for an in-flight matrix
  stop_run.sh          cleanly stop a long run so it can be --resumed later
  resume_run.sh        resume the remaining cells and merge the final results
  ax_link.py           build correct Arize AX deep links to traces in a results JSONL
  validate_grading.py  prove grading is faithful on one task (oracle passes, empty fails)
  validate_batch.py    oracle-validate candidate tasks for subset selection
  verify_tool_spans.py check tool spans go red on failure; exec survives binary output
  run_agent_once.py    run one (task, model) end-to-end
```

Each row in a results JSONL has: task, model, trial, `passed`, `failure_reason`
(`test_failed` / `token_cap` / `timeout` / `max_steps` / `agent_error`), token
counts, dollar `cost_usd`, wall-clock, `steps`, `tool_calls`, and `trace_id`.

## How it works

- **Tasks** come from Terminal-Bench, used *only* as a task source (prompts,
  Docker environments, test scripts). We do **not** use Harbor as the runtime.
  Execution and grading are our own, so the demo shows *our* instrumented agent.
- **Agent** is a thin loop over the OpenAI-compatible protocol with a single tool,
  `run_terminal_cmd`, that runs bash inside the task's container via `docker exec`.
  Any provider works by editing `config/models.yaml`. Fireworks, OpenAI, Anthropic,
  and Google all run through the same code path via their OpenAI-compatible
  endpoints. The thin scaffold keeps cost attributable to the *model*, not the harness.
- **Grading** copies the task's own `tests/` + `run-tests.sh` into the container
  *after* the agent finishes and runs them (pytest; all tests must pass). The agent
  never sees the tests. This reproduces Terminal-Bench's own grading exactly.
- **Guardrails:** a **token-budget cap** (primary, fairness-preserving cutoff) and
  a generous **wall-clock timeout** (safety net). Hitting either is recorded as a
  fail with that reason, distinct from a genuine test failure, so the success
  count stays honest. Command output is capped per exec so a task that streams
  runaway output cannot exhaust host memory.
- **Observability:** every run emits an OpenInference trace to Arize AX: a root
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
| `OPENAI_API_KEY` | OpenAI comparison models |
| `ANTHROPIC_API_KEY` | Anthropic comparison model |
| `GOOGLE_API_KEY` | Google comparison models |
| `ARIZE_SPACE_ID`, `ARIZE_API_KEY` | Arize AX tracing |
| `ARIZE_ORG_ID` | (optional) for `scripts/ax_link.py` deep links |
| `ARIZE_PROJECT_NAME` | AX project (default `cost-per-successful-task`) |

Pricing lives in `config/models.yaml` and is the single source of truth for the
Python cost math. Mirror the same per-model numbers into Arize AX's model-cost
settings so the dashboard's cost matches this repo's.

## Usage

Reproduce the headline study (2,160 runs; takes many hours and real money):

```bash
python run.py \
  --tasks tasks/subset40.txt \
  --models fw-gpt-oss-120b,fw-kimi-k2p6,fw-deepseek-v4-pro,fw-glm-5p2,oai-gpt-5.5,oai-gpt-5,ant-claude-sonnet-5,goog-gemini-3.5-flash,goog-gemini-3.1-flash-lite \
  --trials 6 --token-cap 200000 --wall-clock-cap 900 --concurrency 12
```

A long matrix is interruptible. `--resume` skips (task, model, trial) cells already
present in `--output` and appends, so a stop or crash costs nothing but the in-flight
cells:

```bash
./scripts/stop_run.sh                              # clean stop, keeps completed cells
nohup ./scripts/resume_run.sh >/dev/null 2>&1 &    # finish the rest, then merge
python scripts/run_status.py                       # progress, throughput, spend
```

Inspect results:

```bash
python scripts/summarize.py results/benchmark_40x6.jsonl          # the summary table
python scripts/ax_link.py results/benchmark_40x6.jsonl --failed-only   # AX links
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
- **Subset, not the full suite.** 40 of Terminal-Bench's ~240 tasks, picked to sit
  in the band where some models pass and some fail, which is where the metric is
  informative. Each was validated by running its own oracle solution through this
  harness. This is not a leaderboard-comparable score.
- **Do not compare pass rates to the earlier 10-task study.** `subset40.txt` is much
  harder (12 hard tasks vs 1), so absolute pass rates are far lower. The
  cost-per-success *ratios* are what replicate.
- **Prices drift.** `config/models.yaml` prices are from 2026-07-07 and 2026-07-14;
  re-check them. Claude Sonnet 5 is billed here at its $3/$15 standard rate, not the
  $2/$10 introductory rate that ran through 2026-08-31.
- **Sample size.** 6 trials × 40 tasks is 240 runs per model, putting the 95%
  confidence interval on a pass rate at roughly ±6%. Enough to rank models by
  cost-per-success with confidence; not enough to split hairs between neighbours
  (kimi-k2.6 and glm-5.2 are a coin flip apart on pass rate).
- **Agent errors.** 2.2% of runs failed on transient provider errors, mostly on
  OpenAI and Anthropic. They are counted as failures, which is slightly unkind to
  those models.

## Credits

Tasks are from [Terminal-Bench](https://github.com/laude-institute/terminal-bench)
(Apache-2.0), used here only as a task source. Individual tasks may bundle
third-party components under their own licenses, so check per-task before any public
redistribution. Tracing via [Arize AX](https://arize.com) and
[OpenInference](https://github.com/Arize-ai/openinference).
