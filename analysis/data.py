"""Load traces and turn them into per-task rows. Reads traces only, never the API."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from relay.trace import read_traces


@dataclass
class TaskRow:
    task_id: str
    passed: bool
    calls: list[dict]
    verifies: list[dict]       # stage 'verify' records (the attempt-0 run of the buggy code)
    end: dict

    # derived ------------------------------------------------------------
    @property
    def patch_calls(self) -> list[dict]:
        return [c for c in self.calls if c["stage"] == "patch"]

    @property
    def cost(self) -> float:
        return sum(c["cost_usd_list"] for c in self.calls)

    @property
    def model_ms(self) -> float:
        return sum(c["total_ms"] for c in self.calls)

    @property
    def test_ms(self) -> float:
        t = sum(v["verify"]["test_ms"] for v in self.verifies)
        t += sum((c.get("verify") or {}).get("test_ms", 0.0) for c in self.calls)
        return t

    @property
    def queue_ms(self) -> float:
        return sum(c["queue_wait_ms"] for c in self.calls)

    @property
    def latency_ms(self) -> float:
        """Task start to final verify, queue wait excluded."""
        return self.model_ms + self.test_ms

    @property
    def wall_ms(self) -> float:
        return self.latency_ms + self.queue_ms

    @property
    def n_calls(self) -> int:
        return len(self.calls)

    @property
    def tokens(self) -> int:
        return sum(c["prompt_tokens"] + c["completion_tokens"] for c in self.calls)

    @property
    def first_patch_backend(self) -> str | None:
        p = self.patch_calls
        return p[0]["backend"] if p else None

    @property
    def escalated(self) -> bool:
        """Left its first backend: a retry patch ran on a different backend than the first patch."""
        p = self.patch_calls
        return any(c["backend"] != p[0]["backend"] for c in p[1:])

    @property
    def final_verify(self) -> dict | None:
        p = self.patch_calls
        return p[-1].get("verify") if p else None

    def tokens_by_backend(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for c in self.calls:
            d = out.setdefault(c["backend"], {"prompt": 0, "completion": 0, "calls": 0})
            d["prompt"] += c["prompt_tokens"]
            d["completion"] += c["completion_tokens"]
            d["calls"] += 1
        return out

    def timeline(self) -> list[dict]:
        """Sequential events for the race replay, all durations straight from the trace."""
        ev: list[dict] = []
        t = 0.0

        def add(kind: str, dur: float, **kw):
            nonlocal t
            if dur <= 0 and kind == "queue":
                return
            ev.append({"t": round(t, 1), "dur": round(dur, 1), "kind": kind, **kw})
            t += dur

        for v in self.verifies:
            add("test", v["verify"]["test_ms"], stage="verify", attempt=v["attempt"], passed=v["verify"]["passed"])
        for c in self.calls:
            add("queue", c["queue_wait_ms"], stage=c["stage"], attempt=c["attempt"], backend=c["backend"])
            add("model", c["total_ms"], stage=c["stage"], attempt=c["attempt"], backend=c["backend"],
                cost=c["cost_usd_list"], tokens=c["prompt_tokens"] + c["completion_tokens"],
                cache_hit=c["cache_hit"])
            v = c.get("verify")
            if v is not None:
                add("test", v["test_ms"], stage="verify", attempt=c["attempt"], passed=v["passed"], status=v["status"])
        return ev


@dataclass
class Run:
    run_id: str
    path: Path
    meta: dict
    tasks: dict[str, TaskRow] = field(default_factory=dict)
    discarded_partial: int = 0

    @property
    def policy(self) -> str:
        return self.meta["policy"]

    @property
    def temperature(self) -> float:
        return float(self.meta["temperature"])

    @property
    def complete(self) -> bool:
        return set(self.meta["task_ids"]) <= set(self.tasks)


def load_run(path: Path) -> Run:
    recs = list(read_traces(path))
    meta = next((r for r in recs if r["record"] == "run_meta"), None)
    if meta is None:
        raise ValueError(f"{path}: no run_meta line")
    ends = {r["task_run_uid"]: r for r in recs if r["record"] == "task_end"}
    # If a task ran twice (an interrupted attempt then a resume), keep the execution that finished last.
    final_uid: dict[str, str] = {}
    for r in recs:
        if r["record"] == "task_end":
            final_uid[r["task_id"]] = r["task_run_uid"]
    keep = set(final_uid.values())
    run = Run(meta["run_id"], path, meta)
    by_uid: dict[str, dict[str, list]] = {}
    for r in recs:
        if r["record"] in ("call", "verify"):
            if r["task_run_uid"] not in keep:
                run.discarded_partial += 1
                continue
            d = by_uid.setdefault(r["task_run_uid"], {"calls": [], "verifies": []})
            (d["calls"] if r["record"] == "call" else d["verifies"]).append(r)
    for tid, uid in final_uid.items():
        d = by_uid.get(uid, {"calls": [], "verifies": []})
        end = ends[uid]
        row = TaskRow(tid, bool(end["passed"]), d["calls"], d["verifies"], end)
        fv = row.final_verify
        derived = bool(fv and fv["passed"])
        if derived != row.passed:
            raise ValueError(f"{path} {tid}: task_end.passed={row.passed} but final verify says {derived}")
        run.tasks[tid] = row
    return run


def load_runs(traces_dir: Path) -> list[Run]:
    paths = sorted(p for p in traces_dir.glob("r*.jsonl") if re.fullmatch(r"r\d+\.jsonl", p.name))
    return [load_run(p) for p in paths]


def select_primary(runs: list[Run], base_temperature: float = 0.0) -> dict[str, Run]:
    """Per policy: the temperature-0 run with the most completed tasks (latest wins ties)."""
    best: dict[str, Run] = {}
    for r in runs:
        if r.temperature != base_temperature:
            continue
        cur = best.get(r.policy)
        if cur is None or len(r.tasks) >= len(cur.tasks):
            best[r.policy] = r
    return dict(sorted(best.items()))


def load_profiles(traces_dir: Path) -> list[dict]:
    out = []
    for p in sorted(traces_dir.glob("profile_*.jsonl")):
        out.extend(r for r in read_traces(p) if r["record"] == "profile")
    return out
