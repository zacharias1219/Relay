"""M1: profile each backend on 20 fixed prompts (time to first token, tokens/s).

Profiling measures latency, so it bypasses the response cache; every call is
still traced to traces/profile_<id>.jsonl, and the analysis builds the profile
table from those traces alone.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from relay.backends import build_backends
from relay.config import Config
from relay.ratelimit import UsageLedger, build_limiters
from relay.trace import TraceWriter

PROFILE_PROMPTS: list[str] = [
    "Write a Python function that returns the n-th Fibonacci number iteratively.",
    "Explain in two sentences what a Python generator is.",
    "Write a Python function that checks whether a string is a palindrome, ignoring case.",
    "Write a Python one-liner that flattens a list of lists.",
    "What is the time complexity of binary search? Answer in one sentence.",
    "Write a Python function that merges two sorted lists into one sorted list.",
    "Fix this code: def add(a, b): return a - b",
    "Write a Python function that counts vowels in a string.",
    "Explain the difference between a list and a tuple in Python in three sentences.",
    "Write a Python function that returns the prime factors of an integer.",
    "Write a Python function that removes duplicates from a list while preserving order.",
    "What does the `yield from` statement do? Answer briefly.",
    "Write a Python function to transpose a matrix given as a list of lists.",
    "Write a Python function that returns True if a number is a perfect square.",
    "Write a Python function that converts a Roman numeral string to an integer.",
    "Explain what a race condition is in one short paragraph.",
    "Write a Python function that computes the greatest common divisor of two integers.",
    "Write a Python function that returns the longest common prefix of a list of strings.",
    "Write a Python function that rotates a list to the right by k steps.",
    "Write a Python function that validates balanced parentheses in a string.",
]

MAX_TOKENS = 256


def next_profile_id(traces_dir: Path) -> str:
    nums = [int(m.group(1)) for p in traces_dir.glob("profile_*.jsonl")
            if (m := re.fullmatch(r"profile_(\d+)\.jsonl", p.name))]
    return f"profile_{(max(nums) + 1 if nums else 1):03d}"


def run_profile(config: Config, backend_ids: list[str] | None = None, n: int = 20, tasks=None) -> Path:
    traces_dir = config.path("traces_dir")
    traces_dir.mkdir(parents=True, exist_ok=True)
    run_id = next_profile_id(traces_dir)
    writer = TraceWriter(traces_dir / f"{run_id}.jsonl")
    backends = build_backends(config, 0.0, tasks=tasks,
                              limiters=build_limiters(config, UsageLedger(config.path("state_dir"))))
    ids = backend_ids or list(backends)
    for bid in ids:
        b = backends[bid]
        for i, prompt in enumerate(PROFILE_PROMPTS[:n]):
            out = b.generate(prompt, config.max_tokens("profile", bid) if
                             (config.backend(bid).get("max_tokens") or {}).get("profile") else MAX_TOKENS)
            r = out.result
            writer.write({
                "record": "profile", "run_id": run_id, "prompt_id": f"p{i:02d}",
                "backend": bid, "backend_kind": b.kind, "model_id": b.model_id,
                "ttft_ms": None if r.ttft_ms is None else round(r.ttft_ms, 1),
                "total_ms": round(r.total_ms, 1), "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens, "tokens_estimated": r.tokens_estimated,
                "finish_reason": r.finish_reason, "reasoning_tokens": r.reasoning_tokens,
                "queue_wait_ms": round(out.queue_wait_ms, 1),
            })
            print(f"  {bid:<12} p{i:02d} ttft={r.ttft_ms or 0:7.1f}ms total={r.total_ms:8.1f}ms "
                  f"out={r.completion_tokens}", file=sys.stderr)
    return writer.path
