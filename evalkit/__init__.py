"""evalkit v0: bootstrap confidence intervals and paired comparisons.

Each task is one unit. With n = 40, intervals are wide; that is the honest
answer, not a bug. Every function takes per-task arrays, resamples task
indices with replacement, and returns percentile intervals.
"""

from evalkit.bootstrap import (  # noqa: F401
    Interval,
    bootstrap,
    bootstrap_ratio,
    paired_bootstrap,
    paired_bootstrap_ratio,
    percentile_stat,
    verdict,
)
