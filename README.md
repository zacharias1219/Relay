# Relay

Relay splits one agentic task into stages, routes each stage to a model on
Groq or on your own laptop, and measures, with paired bootstrap confidence
intervals, whether routing beats sending everything to one big model.

It is a small, honest routing lab: one workload (HumanEvalFix bug fixes with
real tests), three backends, four routing policies, a sandboxed test runner as
the verifier, one JSON trace line per call, and a results page built only from
those traces.

> **The Groq constraint, stated plainly.** Groq serves every model on its own
> chip, so Relay routes across *models* and across *local versus hosted*, not
> across chip vendors. Free-tier limits apply per organisation and per model,
> so spreading stages across models also spreads the daily budget. Free usage
> costs nothing, so cost is reported as the **list-price equivalent**: tokens
> used times each model's published price.

### Backends

| Backend | Model | Notes |
|---|---|---|
| local-small | a 3B to 8B instruct GGUF on llama.cpp (default label `qwen2.5-coder-7b-instruct-q4_k_m`) | cheapest first attempt, $0 list price |
| groq-small | `openai/gpt-oss-20b` | reasoning model, `reasoning_effort: low` |
| groq-large | `openai/gpt-oss-120b` | reasoning model, `reasoning_effort: low` |

Both Groq models are reasoning models: their hidden reasoning is billed as
completion tokens and counts against `max_tokens`, so they get larger
per-backend budgets in `config.yaml`, and every trace line records
`finish_reason` and `reasoning_tokens`. A reply cut off mid-reasoning shows up
in the failure gallery as "hit max_tokens", not as a wrong fix.

## Status

**First live run complete (2026-11-12).** All four policies ran on all 40
tasks with GPT-OSS 20B and 120B on Groq and Qwen2.5-Coder-7B on llama.cpp.
Findings, with caveats, are in [RESULTS.md](RESULTS.md); in short: every
routed policy matched P0's pass rate (no detectable difference at n = 40) and
was cheaper per solved task, and P3 was about a sixth of P0's cost but much
slower, because its "local" model ran on a cloud CPU at 4 tokens/s.
`make repro` rebuilds every number from the committed traces with zero API
calls. To rerun P3 on a real laptop, follow [docs/RUNBOOK.md](docs/RUNBOOK.md).

What *is* in the repo is an offline **synthetic demo**: the same runner,
cache, traces, sandbox, statistics and results page, driven by a deterministic
simulator instead of real models (`config.demo.yaml`). It exists so the
pipeline can be run and tested end to end without a key. Its numbers say
nothing about any real model, and the page and every trace say so.

## Quick start

```bash
make setup
```

```bash
make test
```

```bash
make demo-repro
```

```bash
make serve
```

Then open http://localhost:8000/replay/?results=demo-results.json for the
demo page. `make demo-repro` replays the committed demo traces from the
committed cache (zero API calls), checks each replayed call against its
trace, re-runs every test in the sandbox, and rebuilds every table and figure.

## How it works

```
Task ──> Runner ──> Router (policy) ──> Cache + rate limiter ──> local-small | groq-small | groq-large
           │  ▲                               │
     patch │  │ pass/fail                     │ every call
           ▼  │                               ▼
         Sandbox ─────────────────────────> Trace file (JSONL) ──> analysis ──> results.json ──> replay page
```

Stages, each a plain function (`relay/stages.py`):

1. **Diagnose**: buggy code + failing test output -> a short cause in plain text.
2. **Patch**: code + test output (+ diagnosis) -> the full corrected function.
3. **Verify**: not a model. Run the tests in the sandbox.
4. **Retry**: on failure, the failed patch and its test output go back for another patch, possibly on a stronger backend.

A task never makes more than four model calls.

| Policy | Diagnose | Patch | On failure |
|---|---|---|---|
| P0 all-large | groq-large | groq-large | retry on groq-large |
| P1 all-small | groq-small | groq-small | retry on groq-small |
| P2 by stage | groq-small | groq-large | retry on groq-large |
| P3 cheap-then-verify | skipped | local-small | verify, then groq-small, then groq-large |

A policy is a function `route(stage, state) -> backend_id` (`relay/policies.py`).

## Repo layout

```
relay/
  backends.py     adapters: Groq and llama.cpp (one streaming OpenAI-compatible client), offline simulator
  stages.py       diagnose, patch, verify, retry; prompts; code extraction
  policies.py     P0 to P3
  sandbox.py      test runner: fresh process, temp dir, timeout, rlimits, no network (or Docker)
  ratelimit.py    token buckets per model (RPM, TPM) + persisted daily ledger (RPD, TPD), 429 retry-after
  cache.py        response cache keyed by sha256(model id, prompt, params); --replay mode
  trace.py        JSONL writer and schema
  runner.py       runs tasks under a policy; resumable across days
  tasks.py        HumanEvalFix download, sandbox eligibility check, seeded 40-task sample
  profile.py      M1 latency profile (20 fixed prompts)
  cli.py          python -m relay {data,models,smoke,profile,verify-sandbox,run,repro}
evalkit/          bootstrap CI, ratio bootstrap, paired bootstrap, verdicts
analysis/         results.json, tables, SVG figures, failure gallery, overfit probe, page checker
replay/           static results page and race replay; reads results.json only
data/             HumanEvalFix Python split (MIT) and the frozen task sample
traces/ cache/    real experiment traces and responses (empty until you run it)
demo/             synthetic demo traces, cache, tables and figures
config.yaml       model ids, prices, limits, seed: the only place model ids live
config.demo.yaml  the same config with simulated backends
ANALYSIS_PLAN.md  metrics, statistics and failure analysis, fixed before the first run
docs/RUNBOOK.md   step-by-step milestones M0 to M5 for the real experiment
```

## Trace format

One JSON line per model call or verify step, appended to `traces/<run_id>.jsonl`:

```json
{"record":"call","schema":1,"ts":"2026-11-12T09:16:58.101+00:00","run_id":"r004","task_id":"HumanEvalFix/12",
 "task_run_uid":"30fd447d5eb6","policy":"P3","stage":"patch","attempt":1,"backend":"local-small",
 "backend_kind":"llamacpp","model_id":"qwen2.5-coder-7b-instruct-q4_k_m","temperature":0.0,"rep":0,
 "queue_wait_ms":0.0,"ttft_ms":310.0,"total_ms":2150.0,"prompt_tokens":612,"completion_tokens":140,
 "tokens_estimated":false,"cost_usd_list":0.0,"cache_hit":false,"output_hash":"ab12...","prompt_hash":"9f3c...",
 "verify":{"passed":false,"status":"failed","test_ms":48.0,"code_hash":"cf6b...","stderr_tail":"..."}}
```

(values illustrative). Each run starts with a `run_meta` line (policy,
temperature, task ids, model ids, prices, limits, host, sandbox, llama.cpp
build) and each task ends with a `task_end` line. A `verify` line at attempt 0
records the buggy code's failing test run, which is the diagnose input.
`queue_wait_ms` (time in our own rate limiter or honouring retry-after) is
kept apart from `total_ms`, so latency can be reported with and without it.

## Measurement rules

- **Model ids live in `config.yaml` only** and are recorded in every trace line. Run `make models` before each batch: Groq retires and renames models.
- **Temperature 0** for primary runs. Noise runs use `--temperature 0.2 --rep N`, which changes the cache key.
- **Cache**: every response is stored under a hash of model id, prompt and parameters. `--replay` serves only from the cache and fails loudly on a miss; replay also sets `RELAY_OFFLINE=1`, which makes every network backend refuse to connect.
- **Statistics**: n = 40 tasks, 95% percentile bootstrap with 10,000 resamples, paired against P0. A difference is claimed only when the paired interval excludes zero. See [ANALYSIS_PLAN.md](ANALYSIS_PLAN.md).
- **Sandbox**: each patch runs in a fresh process in a temp dir with a 10 s timeout and rlimits on memory, CPU, file size and open files; on Linux it also runs in an empty network namespace (`unshare -rn`) when the kernel allows, with a socket guard as a second layer. `sandbox.mode: docker` uses `docker run --network none --read-only`. On plain Windows use WSL or Docker.

## The results page

`replay/index.html` is a static page that reads `results.json` and nothing
else. It shows, in order: the problem, the Groq constraint and the
architecture; the policy table with intervals; the paired comparison against
P0; the race replay (two lanes, P0 against a chosen policy, drawn from trace
timings); the design tradeoffs rejected; the failure gallery; cost and latency
with hardware specs; and how to reproduce it. `make check-page` renders it in
headless Chromium and fails if any number on the page is missing from
`results.json` (needs `pip install playwright`).

## Data

HumanEvalFix is the Python split of HumanEvalPack (Muennighoff et al., 2023,
*OctoPack*), MIT licence. `make data` downloads it once (Hugging Face first,
then the OctoPack GitHub mirror), records the source and sha256 in
`data/SOURCE.json`, checks every task in the sandbox, and freezes a seeded
sample of 40 in `data/tasks.json`. These tasks are public and models have
likely seen them, so pass rates are inflated for every policy; compare
policies with each other, not with published scores.

## Licence

Apache 2.0 (see `LICENSE`). The dataset in `data/` is MIT (see `data/README.md`).
