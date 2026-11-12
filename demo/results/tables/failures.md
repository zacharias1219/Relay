# Failure gallery

## Tasks every policy fails

- none

## P3 escalated and still failed (2 tasks, 2,406 tokens, $0.000818 wasted)

- HumanEvalFix/75: local-small > groq-small > groq-large, 1,262 tokens, the patch raises ZeroDivisionError at runtime
- HumanEvalFix/105: local-small > groq-small > groq-large, 1,144 tokens, the patch runs but still returns wrong answers

## Small model passed, large model failed

- none

## Routing lost to the baseline (task level)

- P1 on HumanEvalFix/6: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/53: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/71: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/83: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/111: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/129: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/135: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P2 on HumanEvalFix/110: the patch runs but still returns wrong answers (path groq-small > groq-large > groq-large)
- P3 on HumanEvalFix/75: the patch raises ZeroDivisionError at runtime (path local-small > groq-small > groq-large)

## Passes the tests but plainly wrong

- none found among 40 probed passing patches
