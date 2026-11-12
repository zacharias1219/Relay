"""Percentile bootstrap, plain and paired.

Conventions
-----------
* Inputs are per-task arrays aligned by task (index i is the same task in
  every array). Paired functions resample the *same* task indices for both
  arms, so the interval is on the per-task difference, not two separate CIs.
* Ratio statistics (cost per solved task) resample numerator and denominator
  together; resamples where the denominator is zero are dropped and counted.
* All randomness comes from numpy's PCG64 seeded by the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

import numpy as np

Stat = Callable[[np.ndarray], np.ndarray]  # (B, n) -> (B,)


@dataclass
class Interval:
    point: float | None
    lo: float | None
    hi: float | None
    n: int
    resamples: int
    level: float
    dropped: int = 0  # resamples discarded (e.g. zero denominator)

    def excludes_zero(self) -> bool:
        return self.lo is not None and self.hi is not None and (self.lo > 0 or self.hi < 0)

    def to_dict(self) -> dict:
        return asdict(self)


def _idx(n: int, resamples: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(resamples, n))


def _ci(samples: np.ndarray, level: float) -> tuple[float | None, float | None]:
    samples = samples[np.isfinite(samples)]
    if samples.size == 0:
        return None, None
    a = (1 - level) / 2
    lo, hi = np.quantile(samples, [a, 1 - a])
    return float(lo), float(hi)


def _f(x) -> float | None:
    x = float(x)
    return x if np.isfinite(x) else None


def mean_stat(m: np.ndarray) -> np.ndarray:
    return m.mean(axis=-1)


def percentile_stat(q: float) -> Stat:
    def stat(m: np.ndarray) -> np.ndarray:
        return np.percentile(m, q, axis=-1, method="linear")

    stat.__name__ = f"p{q:g}"
    return stat


def bootstrap(values, stat: Stat = mean_stat, resamples: int = 10_000, level: float = 0.95,
              seed: int = 0) -> Interval:
    x = np.asarray(values, dtype=float)
    n = x.size
    if n == 0:
        return Interval(None, None, None, 0, resamples, level)
    point = stat(x[None, :])[0]
    boots = stat(x[_idx(n, resamples, seed)])
    lo, hi = _ci(boots, level)
    return Interval(_f(point), lo, hi, n, resamples, level)


def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    s_den = den.sum(axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(s_den > 0, num.sum(axis=-1) / np.where(s_den > 0, s_den, 1), np.nan)


def bootstrap_ratio(num, den, resamples: int = 10_000, level: float = 0.95, seed: int = 0) -> Interval:
    """CI for sum(num) / sum(den), e.g. total cost / tasks solved."""
    a, b = np.asarray(num, float), np.asarray(den, float)
    n = a.size
    if n == 0:
        return Interval(None, None, None, 0, resamples, level)
    point = _ratio(a[None, :], b[None, :])[0]
    idx = _idx(n, resamples, seed)
    boots = _ratio(a[idx], b[idx])
    lo, hi = _ci(boots, level)
    return Interval(_f(point), lo, hi, n, resamples, level, dropped=int(np.isnan(boots).sum()))


def paired_bootstrap(base, other, stat: Stat = mean_stat, resamples: int = 10_000, level: float = 0.95,
                     seed: int = 0) -> Interval:
    """CI for stat(other) - stat(base) with tasks resampled jointly."""
    a, b = np.asarray(base, float), np.asarray(other, float)
    if a.shape != b.shape:
        raise ValueError("paired arrays must be aligned by task")
    n = a.size
    if n == 0:
        return Interval(None, None, None, 0, resamples, level)
    point = stat(b[None, :])[0] - stat(a[None, :])[0]
    idx = _idx(n, resamples, seed)
    boots = stat(b[idx]) - stat(a[idx])
    lo, hi = _ci(boots, level)
    return Interval(_f(point), lo, hi, n, resamples, level)


def paired_bootstrap_ratio(base_num, base_den, other_num, other_den, resamples: int = 10_000,
                           level: float = 0.95, seed: int = 0) -> Interval:
    """CI for ratio(other) - ratio(base), e.g. the change in cost per solved task."""
    an, ad = np.asarray(base_num, float), np.asarray(base_den, float)
    bn, bd = np.asarray(other_num, float), np.asarray(other_den, float)
    n = an.size
    if n == 0:
        return Interval(None, None, None, 0, resamples, level)
    point = _ratio(bn[None, :], bd[None, :])[0] - _ratio(an[None, :], ad[None, :])[0]
    idx = _idx(n, resamples, seed)
    boots = _ratio(bn[idx], bd[idx]) - _ratio(an[idx], ad[idx])
    lo, hi = _ci(boots, level)
    return Interval(_f(point), lo, hi, n, resamples, level, dropped=int(np.isnan(boots).sum()))


def verdict(diff: Interval, better: str = "higher") -> str:
    """'higher' or 'lower' is better. Only claim a difference if the CI excludes zero."""
    if diff.point is None or diff.lo is None:
        return "not enough data"
    if not diff.excludes_zero():
        return f"no detectable difference at n = {diff.n}"
    up = diff.lo > 0
    good = up if better == "higher" else not up
    return "better than baseline" if good else "worse than baseline"
