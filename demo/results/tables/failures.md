# Failure gallery

## Tasks every policy fails

- none

## P3 escalated and still failed (4 tasks, 5,143 tokens, $0.00144 wasted)

- HumanEvalFix/97: local-small > groq-small > groq-large, 1,094 tokens, the patch runs but still returns wrong answers
- HumanEvalFix/111: local-small > groq-small > groq-large, 1,324 tokens, the patch runs but still returns wrong answers
- HumanEvalFix/126: local-small > groq-small > groq-large, 1,564 tokens, the patch runs but still returns wrong answers
- HumanEvalFix/159: local-small > groq-small > groq-large, 1,161 tokens, the patch runs but still returns wrong answers

## Small model passed, large model failed

- HumanEvalFix/96: P1 (groq-small), P3 first attempt (local-small); P0 failed because the patch runs but still returns wrong answers

## Routing lost to the baseline (task level)

- P1 on HumanEvalFix/4: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/62: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/77: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/83: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/155: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/159: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P1 on HumanEvalFix/163: the patch runs but still returns wrong answers (path groq-small > groq-small > groq-small)
- P2 on HumanEvalFix/8: the patch runs but still returns wrong answers (path groq-small > groq-large > groq-large)
- P2 on HumanEvalFix/126: the patch runs but still returns wrong answers (path groq-small > groq-large > groq-large)
- P2 on HumanEvalFix/155: the patch runs but still returns wrong answers (path groq-small > groq-large > groq-large)
- P3 on HumanEvalFix/97: the patch runs but still returns wrong answers (path local-small > groq-small > groq-large)
- P3 on HumanEvalFix/111: the patch runs but still returns wrong answers (path local-small > groq-small > groq-large)
- P3 on HumanEvalFix/126: the patch runs but still returns wrong answers (path local-small > groq-small > groq-large)
- P3 on HumanEvalFix/159: the patch runs but still returns wrong answers (path local-small > groq-small > groq-large)

## Passes the tests but plainly wrong

- none found among 40 probed passing patches
