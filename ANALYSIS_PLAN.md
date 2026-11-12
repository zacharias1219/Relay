# Analysis plan

Fixed before the first full run. If anything here changes after real traces
exist, the change goes in the log at the bottom with its date and reason.

## Question

Does routing the stages of a bug-fix task across models solve as many tasks
as sending everything to the large model (P0), for less money and time?

## Units and sample

- Unit: one HumanEvalFix (Python) task. n = 40.
- The 40 tasks are drawn with `experiment.seed` from every task whose buggy
  code fails its tests and whose reference passes them in our sandbox, and
  are frozen in `data/tasks.json`. All four policies run the same 40.
- Primary runs: one run per policy at temperature 0. If budget allows, two
  more runs per policy at temperature 0.2 (`--temperature 0.2 --rep 1|2`) to
  see run-to-run noise. Noise runs never replace primary runs.

## Metrics (per task, then aggregated)

| Metric | Definition |
|---|---|
| Pass rate | tasks whose final patch passes all tests / 40 |
| Cost per solved task | list-price cost of every token spent / tasks solved |
| Latency p50, p95 | task start (first test run of the buggy code) to final verify; model time + test time, queue wait excluded |
| Wall clock p50, p95 | the same with queue wait included |
| Escalation rate | share of tasks where a retry patch ran on a different backend than the first patch |
| Calls per task | model calls made (at most four) |

Cost is list-price equivalent: `prompt_tokens * input_price + completion_tokens
* output_price` per call, prices from `config.yaml` at run time (and stored in
each run's `run_meta`). Local calls cost $0. A call served from the cache
counts its original tokens, cost and timings, so every policy is charged as if
it had made every call it needed.

## Statistics

- 95% percentile bootstrap intervals, 10,000 resamples of tasks, seeded with
  `experiment.seed`.
- Cost per solved task is a ratio of sums; numerator and denominator are
  resampled together. Resamples with zero solved tasks are dropped and counted.
- Each policy is compared with P0 by a **paired** bootstrap: the same task
  indices are drawn for both arms and the interval is on the difference
  (policy minus P0).
- A difference is claimed only when its paired interval excludes zero.
  Otherwise the page says "no detectable difference at n = 40".
- Comparisons reported: pass rate, cost per solved task, cost per task,
  latency p50, wall clock p50. No other comparisons are tested; no p-values.

## Failure analysis (always reported, even if empty)

1. Tasks every policy fails, with the likely reason (from the final test
   output, overridable in `analysis/annotations.yaml`).
2. Tasks where P3 escalated and still failed, with the tokens and list-price
   cost the escalation calls spent.
3. Tasks where a small model passed and the large model failed (P1 vs P0, and
   P3's local first attempt vs P0).
4. Tasks P0 solved that a routed policy did not, with the reason.
5. Patches that pass the tests but are plainly wrong: every unique passing
   patch is compared with the reference on perturbed test inputs; disagreements
   are candidates; only a human confirms one (annotations file).
6. Dataset contamination is stated on the page: the tasks are public, so pass
   rates are inflated for every policy. Policies are compared with each other,
   never with published scores.

## Things that would invalidate a run

- A configured model id that the models endpoint does not list (check before
  every batch with `make models`).
- Any trace line that fails schema validation.
- `make repro` reporting a call-sequence mismatch.

## Change log

- 2026-11-12: plan written; no real traces exist yet.
- 2026-11-12: Groq's free tier offers only GPT-OSS reasoning models, so
  groq-small = gpt-oss-20b and groq-large = gpt-oss-120b, with larger
  per-backend max_tokens and reasoning_effort low. No metric or test changed.
- 2026-11-12: local-small ran on the cloud build container's CPU (no laptop
  available). Recorded in config.yaml and every run_meta; reported on the page.
- 2026-11-12: first live run complete for P0-P3 (see RESULTS.md). The eight
  overfit-probe candidates were reviewed by Claude and dismissed with
  reasons in analysis/annotations.yaml; a human should confirm.
