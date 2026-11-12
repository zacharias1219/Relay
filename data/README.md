# Data

`humanevalfix_python.jsonl` is the Python split of HumanEvalPack
(HumanEvalFix), from Muennighoff et al., "OctoPack: Instruction Tuning Code
Large Language Models" (2023), released under the MIT licence by the
BigCode project. `SOURCE.json` records the URL it was downloaded from and its
sha256. `tasks.json` is the seeded 40-task sample, plus the eligibility check
(the buggy code must fail its tests and the reference must pass them in the
Relay sandbox).

Regenerate with `make data` (add `--force` via `python -m relay data --force`
to re-download).

MIT License, Copyright (c) 2023 BigCode Project. Permission is hereby granted,
free of charge, to any person obtaining a copy of this data and associated
documentation files, to deal in it without restriction, including without
limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies, subject to including this copyright and
permission notice in all copies or substantial portions. THE DATA IS PROVIDED
"AS IS", WITHOUT WARRANTY OF ANY KIND.
