import numpy as np
import pytest

from evalkit import bootstrap, bootstrap_ratio, paired_bootstrap, paired_bootstrap_ratio, percentile_stat, verdict


def test_constant_has_zero_width():
    iv = bootstrap(np.ones(40), resamples=1000, seed=1)
    assert iv.point == iv.lo == iv.hi == 1.0


def test_mean_interval_covers_point_and_is_seeded():
    x = np.r_[np.ones(30), np.zeros(10)]
    a = bootstrap(x, resamples=2000, seed=7)
    b = bootstrap(x, resamples=2000, seed=7)
    assert a.point == pytest.approx(0.75) and a.lo < 0.75 < a.hi
    assert (a.lo, a.hi) == (b.lo, b.hi)


def test_paired_identical_is_exactly_zero():
    x = np.random.default_rng(0).random(40)
    iv = paired_bootstrap(x, x, resamples=1000)
    assert iv.point == iv.lo == iv.hi == 0.0
    assert verdict(iv).startswith("no detectable difference at n = 40")


def test_paired_is_tighter_than_unpaired_for_correlated_arms():
    rng = np.random.default_rng(3)
    base = rng.random(40) * 10
    other = base + 0.5 + rng.normal(0, 0.05, 40)
    iv = paired_bootstrap(base, other, resamples=2000)
    assert iv.excludes_zero() and verdict(iv, "lower") == "worse than baseline"
    assert verdict(iv, "higher") == "better than baseline"


def test_ratio_and_zero_denominator():
    cost = np.full(4, 2.0)
    iv = bootstrap_ratio(cost, np.array([1, 0, 0, 0]), resamples=2000, seed=0)
    assert iv.point == pytest.approx(8.0)
    assert iv.dropped > 0  # resamples that solved nothing are dropped, and counted
    d = paired_bootstrap_ratio(cost, np.ones(4), cost / 2, np.ones(4), resamples=500)
    assert d.point == pytest.approx(-1.0)


def test_percentile_stat():
    iv = bootstrap(np.arange(100.0), percentile_stat(50), resamples=500)
    assert iv.point == pytest.approx(49.5)
