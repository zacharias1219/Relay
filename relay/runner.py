"""Runs tasks under a policy and traces every call and test run.

The runner walks a task through its stages and asks the policy which backend to
use. Every model call passes the cache and the rate limiter; every call and
every test run lands in traces/<run_id>.jsonl. A failed test sends the runner
back to the policy for a retry, which may escalate to a stronger backend.

Runs are resumable: tasks with a `task_end` line are skipped, so a run can be
spread across several days of free-tier budget.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relay import hwinfo, policies, stages
from relay.backends import Backend, build_backends
from relay.cache import Cache, GenResult
from relay.config import Config
from relay.policies import State
from relay.ratelimit import DailyBudgetExhausted, UsageLedger, build_limiters
from relay.sandbox import Sandbox
from relay.trace import TraceWriter, new_uid, read_traces, sha256_text


class TooManyCalls(RuntimeError):
    pass


@dataclass
class RunContext:
    config: Config
    run_id: str
    policy: policies.Policy
    backends: dict[str, Backend]
    cache: Cache
    sandbox: Sandbox
    writer: TraceWriter
    temperature: float = 0.0
    rep: int = 0
    log: Any = field(default=None)

    def say(self, msg: str) -> None:
        if self.log is not False:
            print(msg, file=sys.stderr, flush=True)


@dataclass
class TaskOutcome:
    task_id: str
    passed: bool
    model_calls: int
    backends: list[str]


def next_run_id(traces_dir: Path) -> str:
    nums = [int(m.group(1)) for p in traces_dir.glob("r*.jsonl") if (m := re.fullmatch(r"r(\d+)\.jsonl", p.name))]
    return f"r{(max(nums) + 1 if nums else 1):03d}"


def completed_tasks(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {r["task_id"] for r in read_traces(path, validate=False) if r.get("record") == "task_end"}


def read_meta(path: Path) -> dict | None:
    if not path.exists():
        return None
    for r in read_traces(path, validate=False):
        if r.get("record") == "run_meta":
            return r
    return None


def _call(ctx: RunContext, common: dict, backend_id: str, stage: str, attempt: int,
          prompt: str, max_tokens: int) -> tuple[GenResult, dict]:
    b = ctx.backends[backend_id]

    def do() -> GenResult:
        out = b.generate(prompt, max_tokens)
        r = out.result
        r.queue_wait_ms = round(out.queue_wait_ms, 1)
        return r

    res, hit = ctx.cache.fetch(backend_id, b.model_id, prompt, b.params(max_tokens, ctx.rep), do)
    rec = {
        "record": "call",
        **common,
        "stage": stage,
        "attempt": attempt,
        "backend": backend_id,
        "backend_kind": b.kind,
        "model_id": b.model_id,
        "temperature": ctx.temperature,
        "rep": ctx.rep,
        "queue_wait_ms": round(res.queue_wait_ms, 1),
        "ttft_ms": None if res.ttft_ms is None else round(res.ttft_ms, 1),
        "total_ms": round(res.total_ms, 1),
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "tokens_estimated": res.tokens_estimated,
        "cost_usd_list": ctx.config.cost_usd(backend_id, res.prompt_tokens, res.completion_tokens),
        "cache_hit": hit,
        "output_hash": sha256_text(res.text),
        "prompt_hash": sha256_text(prompt),
        "verify": None,
    }
    return res, rec


def run_task(ctx: RunContext, task: dict) -> TaskOutcome:
    cfg = ctx.config
    max_calls = int(cfg["experiment"]["max_model_calls_per_task"])
    max_out = int(cfg["generation"].get("test_output_max_chars", 1500))
    max_diag = int(cfg["generation"].get("diagnosis_max_chars", 800))
    common = {
        "run_id": ctx.run_id,
        "task_id": task["task_id"],
        "task_run_uid": new_uid(),
        "policy": ctx.policy.id,
    }
    entry = task["entry_point"]
    code0 = stages.buggy_code(task)

    # Verify the buggy code first: its failing output is the diagnose input.
    v0 = stages.verify(ctx.sandbox, code0, task)
    ctx.writer.write({"record": "verify", **common, "stage": "verify", "attempt": 0, "verify": v0.to_dict()})
    test_output = v0.stderr_tail

    calls = 0
    used: list[str] = []
    diagnosis = None
    state = State(attempt=1)

    b = ctx.policy.route("diagnose", state)
    if b:
        res, rec = _call(ctx, common, b, "diagnose", 1, stages.diagnose_prompt(code0, test_output, max_out),
                         cfg.max_tokens("diagnose"))
        calls += 1
        used.append(b)
        diagnosis = res.text.strip() or None
        ctx.writer.write(rec)

    passed = False
    previous = None
    while True:
        b = ctx.policy.route("patch", state)
        if b is None:
            break
        if calls >= max_calls:
            raise TooManyCalls(f"{task['task_id']}: policy {ctx.policy.id} asked for call {calls + 1} > {max_calls}")
        prompt = stages.patch_prompt(code0, test_output, diagnosis, previous, max_out, max_diag)
        res, rec = _call(ctx, common, b, "patch", state.attempt, prompt, cfg.max_tokens("patch"))
        calls += 1
        used.append(b)
        code = stages.extract_code(res.text, entry)
        v = stages.verify(ctx.sandbox, code, task)
        rec["verify"] = v.to_dict()
        ctx.writer.write(rec)
        state.history.append({"backend": b, "passed": v.passed, "status": v.status})
        if v.passed:
            passed = True
            break
        previous = (code or "", v.stderr_tail)
        state.attempt += 1

    ctx.writer.write({
        "record": "task_end", **common, "passed": passed, "model_calls": calls,
        "backends": used, "patch_attempts": state.attempt if passed else state.attempt - 1,
    })
    return TaskOutcome(task["task_id"], passed, calls, used)


def build_context(config: Config, policy_id: str, run_id: str | None = None, replay: bool = False,
                  temperature: float | None = None, rep: int = 0, out_dir: Path | None = None,
                  tasks: dict | None = None, quiet: bool = False) -> RunContext:
    traces_dir = out_dir or config.path("traces_dir")
    traces_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_id or next_run_id(traces_dir)
    temp = float(config["experiment"].get("temperature", 0.0)) if temperature is None else float(temperature)
    if replay:
        os.environ["RELAY_OFFLINE"] = "1"  # belt and braces: network backends refuse to connect
    ledger = UsageLedger(config.path("state_dir"))
    limiters = {} if replay else build_limiters(config, ledger)
    return RunContext(
        config=config,
        run_id=run_id,
        policy=policies.get(policy_id),
        backends=build_backends(config, temp, tasks=tasks, limiters=limiters),
        cache=Cache(config.path("cache_dir"), replay=replay),
        sandbox=Sandbox.from_config(config),
        writer=TraceWriter(traces_dir / f"{run_id}.jsonl"),
        temperature=temp,
        rep=rep,
        log=False if quiet else None,
    )


def run_meta(ctx: RunContext, task_ids: list[str], replay: bool) -> dict:
    cfg = ctx.config
    used = sorted(policies.backends_used(ctx.policy.id))
    meta: dict[str, Any] = {
        "record": "run_meta",
        "run_id": ctx.run_id,
        "policy": ctx.policy.id,
        "policy_name": ctx.policy.name,
        "temperature": ctx.temperature,
        "rep": ctx.rep,
        "synthetic": cfg.synthetic,
        "mode": "replay" if replay else "live",
        "task_ids": task_ids,
        "seed": cfg.seed,
        "git_sha": hwinfo.git_sha(),
        "host": hwinfo.describe(),
        "hardware": cfg.get("hardware", {}),
        "sandbox": ctx.sandbox.describe(),
        "backends": {
            bid: {
                "kind": b["kind"],
                "model_id": b["model_id"],
                "price_per_mtok": b.get("price_per_mtok"),
                "limits": b.get("limits"),
                "quantization": b.get("quantization"),
            }
            for bid, b in cfg.backends.items() if bid in used
        },
    }
    local = [bid for bid in used if cfg.backend(bid).get("kind") == "llamacpp"]
    if local and not replay:
        props = getattr(ctx.backends[local[0]], "server_props", lambda: {})()
        if props:
            meta["llama_cpp"] = props
    return meta


def run_policy(config: Config, policy_id: str, tasks: dict[str, dict], run_id: str | None = None,
               replay: bool = False, temperature: float | None = None, rep: int = 0,
               out_dir: Path | None = None, limit: int | None = None, quiet: bool = False) -> dict:
    ctx = build_context(config, policy_id, run_id, replay, temperature, rep, out_dir, tasks, quiet)
    path = ctx.writer.path
    existing = read_meta(path)
    if existing:
        if existing["policy"] != ctx.policy.id:
            raise SystemExit(f"{path} is a {existing['policy']} run; refusing to append {ctx.policy.id}")
        if float(existing["temperature"]) != ctx.temperature or int(existing.get("rep", 0)) != ctx.rep:
            raise SystemExit(f"{path} was run with temperature={existing['temperature']} rep={existing.get('rep')}")
    task_ids = list(tasks)
    if not existing:
        ctx.writer.write(run_meta(ctx, task_ids, replay))
    done = completed_tasks(path)
    todo = [t for t in task_ids if t not in done]
    if limit is not None:
        todo = todo[:limit]
    ctx.say(f"[{ctx.run_id}] policy {ctx.policy.id} ({ctx.policy.name}): {len(done)} done, {len(todo)} to run"
            f"{' [replay]' if replay else ''}")
    solved = 0
    t0 = time.time()
    for i, tid in enumerate(todo, 1):
        try:
            out = run_task(ctx, tasks[tid])
        except DailyBudgetExhausted as e:
            ctx.say(f"[{ctx.run_id}] stopping: {e}\n  Resume tomorrow with: python -m relay run "
                    f"--policy {ctx.policy.id} --run-id {ctx.run_id}")
            return {"run_id": ctx.run_id, "status": "budget_exhausted", "completed": len(done) + i - 1}
        solved += out.passed
        ctx.say(f"  [{i:>2}/{len(todo)}] {tid:<18} {'PASS' if out.passed else 'fail'}  "
                f"calls={out.model_calls} via {' > '.join(out.backends)}")
    ctx.say(f"[{ctx.run_id}] done: {solved}/{len(todo)} solved this session in {time.time() - t0:.1f}s; "
            f"cache hits {ctx.cache.hits}, misses {ctx.cache.misses}")
    return {"run_id": ctx.run_id, "status": "complete", "path": str(path),
            "completed": len(completed_tasks(path)), "cache_hits": ctx.cache.hits, "cache_misses": ctx.cache.misses}


def dump(obj) -> str:
    return json.dumps(obj, indent=2)
