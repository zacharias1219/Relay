# Failure gallery

## Tasks every policy fails

- none

## P3 escalated and still failed (2 tasks, 2,599 tokens, $0.000572 wasted)

- HumanEvalFix/63: local-small > groq-small > groq-large, 1,218 tokens, the patch runs but still returns wrong answers
- HumanEvalFix/87: local-small > groq-small > groq-large, 1,381 tokens, the patch runs but still returns wrong answers

## Small model passed, large model failed

- HumanEvalFix/87: P1 (groq-small); P0 failed because the patch runs but still returns wrong answers
- HumanEvalFix/129: P1 (groq-small), P3 first attempt (local-small); P0 failed because the patch runs but still returns wrong answers

## Routing lost to the baseline (task level)

- P3 on HumanEvalFix/63: the patch runs but still returns wrong answers (path local-small > groq-small > groq-large)

## Passes the tests but plainly wrong

- none found among 96 probed passing patches
