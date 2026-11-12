import pytest

from relay.ratelimit import DailyBudgetExhausted, ModelLimiter, UsageLedger, parse_duration


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.slept += s


def test_rpm_bucket_throttles():
    c = Clock()
    lim = ModelLimiter("m", rpm=6, safety=1.0, clock=c, sleep=c.sleep)
    waits = [lim.acquire(0) for _ in range(7)]
    assert all(w == 0 for w in waits[:6])
    assert waits[6] == pytest.approx(10_000, rel=1e-6)  # one request per 10 s


def test_tpm_bucket_throttles():
    c = Clock()
    lim = ModelLimiter("m", tpm=600, safety=1.0, clock=c, sleep=c.sleep)
    assert lim.acquire(600) == 0
    assert lim.acquire(300) == pytest.approx(30_000, rel=1e-6)


def test_settle_returns_unused_tokens():
    c = Clock()
    lim = ModelLimiter("m", tpm=600, safety=1.0, clock=c, sleep=c.sleep)
    lim.acquire(600)
    lim.settle(600, 100)  # only 100 were really used
    assert lim.acquire(400) == 0


def test_daily_budget(tmp_path):
    led = UsageLedger(tmp_path, clock_date=lambda: "2026-11-12")
    lim = ModelLimiter("m", rpd=2, safety=1.0, ledger=led)
    for _ in range(2):
        lim.acquire(1)
        lim.settle(1, 1)
    with pytest.raises(DailyBudgetExhausted):
        lim.acquire(1)
    # persisted: a new process on the same day sees the usage
    lim2 = ModelLimiter("m", rpd=2, safety=1.0, ledger=UsageLedger(tmp_path, clock_date=lambda: "2026-11-12"))
    with pytest.raises(DailyBudgetExhausted):
        lim2.acquire(1)
    lim3 = ModelLimiter("m", rpd=2, safety=1.0, ledger=UsageLedger(tmp_path, clock_date=lambda: "2026-11-13"))
    assert lim3.acquire(1) == 0


def test_429_penalty_counts_as_queue_wait():
    c = Clock()
    lim = ModelLimiter("m", rpm=100, clock=c, sleep=c.sleep)
    lim.penalize(7.5)
    assert lim.wait_penalty() == pytest.approx(7500)


@pytest.mark.parametrize("s,v", [("7.66s", 7.66), ("2m59.56s", 179.56), ("1h2m3s", 3723), ("120ms", 0.12), ("3", 3.0)])
def test_parse_duration(s, v):
    assert parse_duration(s) == pytest.approx(v)


def test_usage_model_cold_start_then_learns(tmp_path):
    from relay.ratelimit import UsageModel

    um = UsageModel(tmp_path, quantile=0.9, margin=1.25, min_observations=3)
    assert um.reservation("m", 100, 3072) == 100 + 128 + 3072  # cold: full budget
    for ct in (300, 320, 400):
        um.observe("m", 100, 3072, prompt_tokens=170, completion_tokens=ct)
    r = um.reservation("m", 100, 3072)
    assert r == 100 + 70 + int(400 * 1.25) + 1  # learned overhead + p90 completion x margin
    assert r < 1000
    # budgets are learned separately per max_tokens (stage), and persist across processes
    assert UsageModel(tmp_path).reservation("m", 100, 1024) == 100 + 128 + 1024
    assert UsageModel(tmp_path).reservation("m", 100, 3072) == r


def test_usage_model_never_exceeds_max_tokens(tmp_path):
    from relay.ratelimit import UsageModel

    um = UsageModel(None, min_observations=1)
    um.observe("m", 10, 200, 10, 200)
    assert um.reservation("m", 10, 200) == 10 + 0 + 200


def test_limiter_uses_learned_reservation():
    from relay.ratelimit import UsageModel

    lim = ModelLimiter("m", tpm=6000, usage_model=UsageModel(None, min_observations=1))
    assert lim.reservation(100, 3072) == 100 + 128 + 3072
    lim.learn(100, 3072, 160, 250)
    assert lim.reservation(100, 3072) == 100 + 60 + int(250 * 1.25) + 1
    assert ModelLimiter("m", tpm=6000).reservation(100, 3072) == 3172  # no model: old rule
