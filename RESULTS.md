# Results: first live run (2026-11-12)

All four policies ran on all 40 HumanEvalFix tasks at temperature 0, one run
each. Every number below comes from `replay/results.json`, which
`make repro` rebuilds from the committed traces and cache with zero API calls.
The full page: `make serve`, then open http://localhost:8000/replay/

## Setup

| Backend | Model | Where it ran |
|---|---|---|
| local-small | Qwen2.5-Coder-7B-Instruct, Q4_K_M GGUF (sha256 `509287f7...`) | **the Claude Code cloud container's CPU**: 4 vCPU Intel Xeon @ 2.10GHz, 15.7 GB RAM, no GPU, llama-cpp-python 0.3.36. Not a laptop. |
| groq-small | `openai/gpt-oss-20b`, reasoning_effort low | Groq free tier |
| groq-large | `openai/gpt-oss-120b`, reasoning_effort low | Groq free tier |

Profile (M1, 20 fixed prompts): time to first token 318 ms (120B), 348 ms
(20B), 714 ms (local). Generation speed 479 tokens/s (120B), 810 tokens/s
(20B), **4.2 tokens/s** (local on CPU).

## Policy table

| Policy | Solved | Pass rate (95% CI) | Cost per solved task (95% CI) | Latency p50 | Wall clock p50 |
|---|---|---|---|---|---|
| P0 all-large | 36/40 | 90.0% [80.0%, 97.5%] | $0.000361 [$0.000274, $0.000495] | 1.50 s | 7.66 s |
| P1 all-small | 38/40 | 95.0% [87.5%, 100.0%] | $0.000148 [$0.000126, $0.000177] | 1.25 s | 7.57 s |
| P2 by stage | 38/40 | 95.0% [87.5%, 100.0%] | $0.000235 [$0.000189, $0.000295] | 1.42 s | 4.79 s |
| P3 cheap-then-verify | 38/40 | 95.0% [87.5%, 100.0%] | $0.0000593 [$0.0000269, $0.000103] | 17.31 s | 17.31 s |

## What the paired comparison against P0 says

- **Pass rate: no detectable difference at n = 40 for any policy.** Each
  routed policy solved 2 more tasks than P0 (+5.0 pts), but every paired
  interval touches or crosses zero.
- **Cost per solved task: every routed policy is cheaper, and the intervals
  exclude zero.** P1 −$0.000212, P2 −$0.000126, P3 −$0.000301 per solved
  task. P3 costs about a sixth of P0 per fix: the free local model solved 27 of
  40 tasks on its first try, so only 13 tasks escalated to groq-small and 3 to
  groq-large.
- **Latency: P3 is clearly slower** (+15.81 s at the median, CI
  [+13.59 s, +19.97 s]), because the local model generates 4.2 tokens/s on
  this CPU against 479-810 on Groq. P1 and P2 show no detectable latency
  difference from P0.

## Where routing loses, and other failures

- **Routing lost to the baseline on one task:** P3 failed HumanEvalFix/63
  (P0 solved it) after escalating through all three models; the final patch
  runs but returns wrong answers.
- **P3 escalated and still failed** on 2 tasks (63 and 87), spending 2,599
  tokens ($0.000572 list price) on escalation calls that bought nothing.
- **The small model beat the large one** on HumanEvalFix/87 and /129: the
  20B solved both, the 120B failed both. On /129 the local 7B also solved it
  on its first try.
- **No task defeated every policy.**
- **Passes the tests but plainly wrong:** the overfit probe flagged 8 patches
  on 3 tasks (6, 111, 129). All 8 disagree with the reference only on
  perturbed inputs that break the task's preconditions (unbalanced
  parentheses, a non-square grid, a leading space). They are dismissed in
  `analysis/annotations.yaml` with reasons, as Claude's review; a human
  should confirm. The probe needs precondition-aware perturbations.
- **Contamination:** pass rates of 90-95% are high for every policy. These
  tasks are public and these models have very likely seen them. Compare the
  policies with each other, not with published scores.

## Caveats you should know before quoting any of this

1. **"Local" is a cloud CPU, not a laptop.** P3's latency would be very
   different on a laptop with Apple silicon or a GPU. Its cost and pass-rate
   results do not depend on where the local model runs.
2. **Wall clock is dominated by the free tier's tokens-per-minute limit.**
   Groq calls waited 294 s (P0), 223 s (P1) and 143 s (P2) in total in
   Relay's token bucket, against 57-70 s of model time. Queue wait is logged
   apart from latency so it is never blamed on the models.

   This run reserved prompt + max_tokens (3,072 for a patch) before each call.
   That rule has since been replaced by reserving from observed usage
   (`ratelimit.reserve` in config.yaml). Replaying the recorded calls through
   both rules on a simulated clock (`make limiter-sim`) shows the old rule was
   not the main cost, because unused reservations were already refunded after
   each call:

   | Run | Recorded wait | Simulated, old rule | Simulated, observed usage | Observed usage, no 15% safety margin |
   |---|---|---|---|---|
   | P0 | 293.8 s | 285.3 s | 262.6 s | 203.3 s |
   | P1 | 222.9 s | 257.1 s | 233.6 s | 180.6 s |
   | P2 | 142.7 s | 118.5 s | 94.6 s | 62.5 s |

   The new rule saves about 23 s per run. Most of the wait is the limit
   itself: these calls averaged about 450-490 tokens each (on P0, 47% of
   completion tokens were hidden reasoning), and 8,000 tokens a minute at an
   85% safety margin allows about 113 tokens a second. Running at 100% of the
   limit would save more, at the price of more 429s.
3. **Local token counts are estimates.** The llama-cpp-python server does not
   report usage while streaming; those trace lines carry
   `tokens_estimated: true`. Local list-price cost is $0 either way.
4. **One run per policy, n = 40.** Intervals are wide. The noise runs at
   temperature 0.2 were not done.
5. **Prices and limits were set from memory** (Groq's pricing page was not
   reachable from the build machine): $0.075/$0.30 per million tokens
   (20B) and $0.15/$0.60 (120B). If they differ, edit `config.yaml` and run
   `make analyze`; costs are recomputed from token counts.

## Budget actually used

Groq, for M0-M3 combined: 124 requests and 58,534 tokens on gpt-oss-20b, 164
requests and 82,360 tokens on gpt-oss-120b, all within one day's free tier.
List-price equivalent of all four policies' runs: about $0.03.
