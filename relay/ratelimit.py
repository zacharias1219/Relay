"""Token-bucket rate limiting, one limiter per Groq model.

Groq's free-tier limits apply per organisation and per model, so each backend
gets its own limiter with four budgets: requests and tokens per minute
(continuous token buckets) and requests and tokens per day (a ledger persisted
on disk so separate processes on the same day share it).

Time spent waiting here is *queue wait*. It is reported apart from model time,
so the model is never blamed for our own throttle.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class DailyBudgetExhausted(RuntimeError):
    """The per-day request or token budget for a model is used up."""


@dataclass
class Bucket:
    capacity: float
    refill_per_s: float
    level: float = field(default=-1.0)
    updated: float = 0.0

    def __post_init__(self):
        if self.level < 0:
            self.level = self.capacity

    def refill(self, now: float) -> None:
        if self.updated:
            self.level = min(self.capacity, self.level + (now - self.updated) * self.refill_per_s)
        self.updated = now

    def wait_for(self, amount: float, now: float) -> float:
        """Seconds until `amount` is available (0 if it is now)."""
        self.refill(now)
        amount = min(amount, self.capacity)  # an oversized request waits for a full bucket
        if self.level >= amount:
            return 0.0
        return (amount - self.level) / self.refill_per_s

    def take(self, amount: float, now: float) -> None:
        self.refill(now)
        self.level -= min(amount, self.capacity)


class UsageLedger:
    """Per-day (UTC) usage per model, persisted as JSON under the state dir."""

    def __init__(self, state_dir: str | os.PathLike | None, clock_date: Callable[[], str] | None = None):
        self.state_dir = Path(state_dir) if state_dir else None
        self._date = clock_date or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self._mem: dict[str, dict[str, dict[str, int]]] = {}

    def _file(self, day: str) -> Path | None:
        return self.state_dir / f"usage-{day}.json" if self.state_dir else None

    def _load(self, day: str) -> dict[str, dict[str, int]]:
        if day in self._mem:
            return self._mem[day]
        data: dict[str, dict[str, int]] = {}
        f = self._file(day)
        if f and f.exists():
            data = json.loads(f.read_text())
        self._mem[day] = data
        return data

    def get(self, model: str) -> dict[str, int]:
        return dict(self._load(self._date()).get(model, {"requests": 0, "tokens": 0}))

    def add(self, model: str, requests: int, tokens: int) -> None:
        day = self._date()
        data = self._load(day)
        m = data.setdefault(model, {"requests": 0, "tokens": 0})
        m["requests"] += requests
        m["tokens"] += tokens
        f = self._file(day)
        if f:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, f)


class UsageModel:
    """Learns how many tokens calls really use, so the limiter reserves that, not max_tokens.

    Reserving `prompt + max_tokens` before every call is safe but, for reasoning
    models with large budgets, wildly pessimistic: a 3,072-token patch budget
    typically uses a few hundred. This keeps a window of recent completion sizes
    per (model, max_tokens) and of how far the chars/4 prompt estimate falls
    short of the real prompt count (chat templates add tokens), and reserves a
    high quantile of each plus a margin. Until it has `min_observations` it
    reserves the full max_tokens. Any shortfall is repaid by `settle()` after the
    call, and Groq's own 429 + retry-after remains the backstop.
    """

    def __init__(self, state_dir: str | os.PathLike | None = None, quantile: float = 0.9,
                 margin: float = 1.25, min_observations: int = 3, window: int = 50,
                 default_prompt_overhead: int = 128):
        self.path = Path(state_dir) / "usage-model.json" if state_dir else None
        self.quantile = quantile
        self.margin = margin
        self.min_obs = min_observations
        self.window = window
        self.default_overhead = default_prompt_overhead
        self.data: dict[str, dict[str, list[int]]] = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except (ValueError, OSError):
                self.data = {}

    @staticmethod
    def _key(model: str, max_tokens: int) -> str:
        return f"{model}|{max_tokens}"

    def _q(self, xs: list[int]) -> float:
        s = sorted(xs)
        i = min(len(s) - 1, max(0, int(round(self.quantile * (len(s) - 1)))))
        return float(s[i])

    def reservation(self, model: str, prompt_est: int, max_tokens: int) -> int:
        d = self.data.get(self._key(model, max_tokens), {})
        comp, over = d.get("completion", []), d.get("prompt_excess", [])
        overhead = max(0.0, self._q(over)) if len(over) >= self.min_obs else self.default_overhead
        if len(comp) < self.min_obs:
            completion = max_tokens
        else:
            completion = min(max_tokens, int(self._q(comp) * self.margin) + 1)
        return int(prompt_est + overhead + completion)

    def observe(self, model: str, prompt_est: int, max_tokens: int, prompt_tokens: int,
                completion_tokens: int) -> None:
        d = self.data.setdefault(self._key(model, max_tokens), {"completion": [], "prompt_excess": []})
        d["completion"] = (d["completion"] + [int(completion_tokens)])[-self.window:]
        d["prompt_excess"] = (d["prompt_excess"] + [int(prompt_tokens) - int(prompt_est)])[-self.window:]
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            os.replace(tmp, self.path)


class ModelLimiter:
    def __init__(
        self,
        model: str,
        rpm: float | None = None,
        tpm: float | None = None,
        rpd: float | None = None,
        tpd: float | None = None,
        safety: float = 0.85,
        ledger: UsageLedger | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        usage_model: UsageModel | None = None,
    ):
        self.model = model
        self.usage_model = usage_model
        self.clock = clock
        self.sleep = sleep
        self.ledger = ledger or UsageLedger(None)
        self.rpd = rpd * safety if rpd else None
        self.tpd = tpd * safety if tpd else None
        self.req_bucket = Bucket(rpm * safety, rpm * safety / 60.0) if rpm else None
        self.tok_bucket = Bucket(tpm * safety, tpm * safety / 60.0) if tpm else None
        self.blocked_until = 0.0

    def _check_daily(self, est_tokens: int) -> None:
        used = self.ledger.get(self.model)
        if self.rpd is not None and used["requests"] + 1 > self.rpd:
            raise DailyBudgetExhausted(
                f"{self.model}: daily request budget used ({used['requests']} of {self.rpd:.0f} after safety margin)"
            )
        if self.tpd is not None and used["tokens"] + est_tokens > self.tpd:
            raise DailyBudgetExhausted(
                f"{self.model}: daily token budget used ({used['tokens']} + ~{est_tokens} > {self.tpd:.0f} after safety margin)"
            )

    def reservation(self, prompt_est: int, max_tokens: int) -> int:
        """Tokens to reserve before a call: learned from usage, or prompt + max_tokens."""
        if self.usage_model is None:
            return prompt_est + max_tokens
        return self.usage_model.reservation(self.model, prompt_est, max_tokens)

    def learn(self, prompt_est: int, max_tokens: int, prompt_tokens: int, completion_tokens: int) -> None:
        if self.usage_model is not None:
            self.usage_model.observe(self.model, prompt_est, max_tokens, prompt_tokens, completion_tokens)

    def acquire(self, est_tokens: int) -> float:
        """Block until one request of ~est_tokens may go out. Returns ms waited."""
        self._check_daily(est_tokens)
        waited = 0.0
        while True:
            now = self.clock()
            waits = [max(0.0, self.blocked_until - now)]
            if self.req_bucket:
                waits.append(self.req_bucket.wait_for(1, now))
            if self.tok_bucket:
                waits.append(self.tok_bucket.wait_for(est_tokens, now))
            w = max(waits)
            if w <= 1e-9:
                break
            self.sleep(w)
            waited += w
        now = self.clock()
        if self.req_bucket:
            self.req_bucket.take(1, now)
        if self.tok_bucket:
            self.tok_bucket.take(est_tokens, now)
        return waited * 1000.0

    def settle(self, est_tokens: int, actual_tokens: int) -> None:
        """Correct the token bucket once the real usage is known, and log it."""
        if self.tok_bucket:
            now = self.clock()
            self.tok_bucket.refill(now)
            self.tok_bucket.level = min(self.tok_bucket.capacity, self.tok_bucket.level + est_tokens - actual_tokens)
        self.ledger.add(self.model, 1, actual_tokens)

    def penalize(self, retry_after_s: float) -> None:
        """A 429 arrived: hold every request to this model for retry_after_s."""
        self.blocked_until = max(self.blocked_until, self.clock() + max(0.0, retry_after_s))

    def wait_penalty(self) -> float:
        """Sleep out any 429 penalty. Returns ms waited."""
        w = max(0.0, self.blocked_until - self.clock())
        if w > 0:
            self.sleep(w)
        return w * 1000.0

    def observe_headers(self, headers: dict[str, str]) -> None:
        """Respect Groq's x-ratelimit-* headers when they are tighter than our buckets."""
        h = {k.lower(): v for k, v in headers.items()}
        rem_tok = h.get("x-ratelimit-remaining-tokens")
        reset_tok = h.get("x-ratelimit-reset-tokens")
        if rem_tok is not None and self.tok_bucket:
            try:
                self.tok_bucket.refill(self.clock())
                self.tok_bucket.level = min(self.tok_bucket.level, float(rem_tok) * 0.95)
            except ValueError:
                pass
        rem_req = h.get("x-ratelimit-remaining-requests")
        reset_req = h.get("x-ratelimit-reset-requests")
        if rem_req is not None and reset_req and _safe_float(rem_req) == 0:
            self.penalize(parse_duration(reset_req))
        if rem_tok is not None and reset_tok and _safe_float(rem_tok) == 0:
            self.penalize(parse_duration(reset_tok))


def _safe_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_duration(s: str | None) -> float:
    """Parse Groq-style durations: '7.66s', '2m59.56s', '1h2m3s', '120ms', or plain seconds."""
    if not s:
        return 0.0
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, num = 0.0, ""
    i = 0
    while i < len(s):
        c = s[i]
        if c.isdigit() or c == ".":
            num += c
            i += 1
            continue
        if s.startswith("ms", i):
            total += float(num or 0) / 1000.0
            i += 2
        elif c == "h":
            total += float(num or 0) * 3600
            i += 1
        elif c == "m":
            total += float(num or 0) * 60
            i += 1
        elif c == "s":
            total += float(num or 0)
            i += 1
        else:
            i += 1
        num = ""
    if num:
        total += float(num)
    return total


def build_limiters(config, ledger: UsageLedger | None = None) -> dict[str, ModelLimiter]:
    rl = config["ratelimit"]
    safety = float(rl.get("safety", 0.85))
    res = rl.get("reserve") or {}
    usage_model = None
    if res.get("mode", "observed") == "observed":
        state = ledger.state_dir if ledger is not None else None
        usage_model = UsageModel(state, quantile=float(res.get("quantile", 0.9)),
                                 margin=float(res.get("margin", 1.25)),
                                 min_observations=int(res.get("min_observations", 3)),
                                 window=int(res.get("window", 50)))
    out = {}
    for bid, b in config.backends.items():
        lim = b.get("limits")
        if not lim:
            continue
        out[bid] = ModelLimiter(
            model=b["model_id"],
            rpm=lim.get("rpm"),
            tpm=lim.get("tpm"),
            rpd=lim.get("rpd"),
            tpd=lim.get("tpd"),
            safety=safety,
            ledger=ledger,
            usage_model=usage_model,
        )
    return out
