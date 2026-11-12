"""Replay recorded Groq calls through the rate limiter under both reservation rules.

For each live run, walk its calls in order on a simulated clock: wait for the
limiter, then advance the clock by the call's recorded model time and the
task's recorded test time. Token counts and prompts come from the traces and
the cache. This answers "how much queue wait would the observed-usage rule have
saved on this exact workload?" without spending any API budget.

    python -m analysis.limiter_sim [--config config.yaml]
"""

from __future__ import annotations

import argparse
import os

from analysis.data import load_runs
from relay.backends import estimate_tokens
from relay.cache import Cache
from relay.config import Config
from relay.ratelimit import ModelLimiter, UsageModel


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def simulate(config: Config, mode: str, safety: float | None = None) -> dict[str, float]:
    """Total queue wait in seconds per run."""
    prompts = {}
    from relay.trace import sha256_text
    for e in Cache(config.path("cache_dir")).iter_entries():
        prompts[sha256_text(e["request"]["prompt"])] = e["request"]["prompt"]
    safety = float(config["ratelimit"].get("safety", 0.85)) if safety is None else safety
    res = config["ratelimit"].get("reserve") or {}
    um = UsageModel(None, float(res.get("quantile", 0.9)), float(res.get("margin", 1.25)),
                    int(res.get("min_observations", 3))) if mode == "observed" else None
    out = {}
    for run in sorted(load_runs(config.path("traces_dir")), key=lambda r: r.run_id):
        clock = Clock()
        lims = {}
        for bid, b in config.backends.items():
            if b.get("limits"):
                lim = b["limits"]
                lims[bid] = ModelLimiter(b["model_id"], rpm=lim.get("rpm"), tpm=lim.get("tpm"), safety=safety,
                                         clock=clock, sleep=clock.sleep, usage_model=um)
        wait = 0.0
        for tid in sorted(run.tasks, key=lambda s: int(s.split("/")[-1])):
            row = run.tasks[tid]
            for v in row.verifies:
                clock.t += v["verify"]["test_ms"] / 1000
            for c in row.calls:
                lim = lims.get(c["backend"])
                if lim is not None and not c["cache_hit"]:  # cache hits never reached the API
                    pe = estimate_tokens(prompts.get(c["prompt_hash"], ""))
                    mt = config.max_tokens(c["stage"], c["backend"])
                    est = lim.reservation(pe, mt)
                    w = lim.acquire(est) / 1000.0
                    wait += w
                    lim.settle(est, c["prompt_tokens"] + c["completion_tokens"])
                    lim.learn(pe, mt, c["prompt_tokens"], c["completion_tokens"])
                clock.t += c["total_ms"] / 1000
                if c.get("verify"):
                    clock.t += c["verify"]["test_ms"] / 1000
        out[f"{run.run_id} {run.policy}"] = wait
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.environ.get("RELAY_CONFIG", "config.yaml"))
    cfg = Config.load(ap.parse_args(argv).config)
    recorded = {f"{r.run_id} {r.policy}": sum(t.queue_ms for t in r.tasks.values()) / 1000
                for r in load_runs(cfg.path("traces_dir"))}
    old, new = simulate(cfg, "max_tokens"), simulate(cfg, "observed")
    full = simulate(cfg, "observed", safety=1.0)
    print("Total queue wait per run, in seconds. 'recorded' is what the live run logged;")
    print("the simulated columns replay the same calls on a simulated clock.\n")
    print(f"{'run':<9} {'recorded':>9} {'old rule':>9} {'observed':>9} {'observed, no safety margin':>27}")
    for k in old:
        print(f"{k:<9} {recorded[k]:>9.1f} {old[k]:>9.1f} {new[k]:>9.1f} {full[k]:>27.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
