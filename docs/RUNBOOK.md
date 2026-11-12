# Runbook: running the real experiment

Three weekends. Do not start a milestone until the previous exit test passes.
Everything below runs on your laptop; nothing needs a GPU.

## 0. Setup (once)

```bash
python3 -m venv .venv && . .venv/bin/activate
```

```bash
make setup
```

```bash
cp .env.example .env    # then paste your Groq key into .env
```

Local model: build or download [llama.cpp](https://github.com/ggml-org/llama.cpp)
and start its server with a 3B to 8B instruct GGUF, for example:

```bash
llama-server -m qwen2.5-coder-7b-instruct-q4_k_m.gguf --port 8080 -c 8192
```

Then edit `config.yaml`:

- `backends.local-small.model_id` and `quantization` to match the GGUF you loaded
- `backends.groq-*.model_id` to ids your account lists (`make models`); the
  defaults are `openai/gpt-oss-20b` and `openai/gpt-oss-120b`
- `extra_params.reasoning_effort` (low by default) and the per-backend
  `max_tokens`; if `results/tables/failures.md` shows "hit max_tokens"
  failures, raise `max_tokens.patch` for that backend
- `price_per_mtok` from groq.com/pricing, and `limits` from your console limits page
- `hardware.laptop_model` and `hardware.llama_cpp_build`

## M0 (weekend 1): plumbing

```bash
make models
```

```bash
make smoke
```

Exit test: `smoke` prints PASS: one call per backend wrote a valid trace line
and the repeat call was a cache hit.

## M1 (weekend 1): profile

```bash
make profile && make analyze
```

Exit test: `results/tables/profile.md` has a median and IQR of time to first
token and tokens per second for each backend. If the local model is too weak
or too slow to matter, swap it now, before building P3 around it.

## M2 (weekend 2): sandbox, P0 and P1 on 10 tasks

```bash
make verify-sandbox
```

```bash
python -m relay run --policy P0 --limit 10
```

```bash
python -m relay run --policy P1 --limit 10
```

Exit test: `verify-sandbox` prints PASS (every known-bad patch fails, every
known-good one passes, and the network is blocked). On Windows use WSL or set
`sandbox.mode: docker`.

## M3 (weekend 2 and after): all policies, all 40 tasks

Runs resume where they stopped. When a model's daily budget runs out the
runner stops cleanly and prints the resume command; run it again the next day.

```bash
make run-all
```

Rough budget: the GPT-OSS models spend extra completion tokens on reasoning,
so expect more than the spec's 3,000 to 4,000 tokens per task; check the first
day's usage in `state/usage-<date>.json` and adjust. At 4,000 tokens per task, a 200,000-token daily
limit covers about 50 task runs per model per day. Because limits are per
model, P1 and P3 mostly draw on the small model's budget and P0/P2 on the
large model's, so the policies can share a day.

Exit test: every policy has 40 complete tasks (`make analyze` shows 40/40 for
each run on the Reproduce table).

Optional noise runs:

```bash
python -m relay run --policy P3 --temperature 0.2 --rep 1
```

## M4 (weekend 3): statistics and repro

```bash
make analyze
```

Read `results/tables/failures.md`. For each overfit-probe candidate, read the
patch (find it in `cache/` by its output hash) and record your verdict in
`analysis/annotations.yaml`. Override any automatic failure reason there too.

Commit `traces/`, `cache/`, `data/`, `replay/results.json` and `results/`.
Then from a fresh clone:

```bash
make repro
```

Exit test: `make repro` replays every run from the cache with zero API calls,
reports no mismatches, and rebuilds every table and figure.

## M5 (weekend 3): the page

```bash
make serve
```

Open http://localhost:8000/replay/ and read every section.

```bash
make check-page
```

Exit test: `check-page` prints PASS: every number on the page exists in
`replay/results.json`.
