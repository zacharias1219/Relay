"""Command line: python -m relay <command>.

    data      download HumanEvalFix once, check it in the sandbox, freeze the 40-task sample
    models    list the model ids your Groq account can see; flag configured ids that are missing
    smoke     M0 exit test: one call per backend writes a valid trace line; a repeat is a cache hit
    profile   M1: time to first token and tokens/s per backend on 20 fixed prompts
    verify-sandbox  M2 exit test: the verifier fails a known-bad patch and passes a known-good one
    run       run tasks under a policy (resumable; --replay serves only from the cache)
    repro     replay every stored run from the cache, check it matches its trace, rebuild results
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from relay import policies
from relay.backends import OpenAICompatBackend, build_backends, load_dotenv
from relay.cache import Cache, CacheMiss
from relay.config import Config


def _tasks_for(config: Config) -> dict:
    from relay.tasks import load_selected

    return load_selected(config)


def cmd_data(args, config: Config) -> int:
    from relay.tasks import prepare

    out = prepare(config, force_download=args.force)
    print(f"{out['n_total']} tasks in the split, {out['n_eligible']} eligible "
          f"(buggy fails, reference passes); sampled {out['n_tasks']} with seed {out['seed']}.")
    if out["ineligible"]:
        print(f"ineligible: {', '.join(sorted(out['ineligible']))}")
    print(f"wrote {config.path('tasks_file')}")
    return 0


def cmd_models(args, config: Config) -> int:
    groq = {bid: b for bid, b in config.backends.items() if b.get("kind") == "groq"}
    if not groq:
        print("no Groq backends configured")
        return 0
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set (put it in .env or export it)", file=sys.stderr)
        return 2
    first = next(iter(groq.items()))
    client = OpenAICompatBackend(first[0], first[1], api_key=os.environ["GROQ_API_KEY"])
    ids = client.list_models()
    print("Models visible to your Groq account:")
    for m in ids:
        print(f"  {m}")
    bad = 0
    for bid, b in groq.items():
        ok = b["model_id"] in ids
        bad += not ok
        print(f"{'OK     ' if ok else 'MISSING'} {bid}: {b['model_id']}")
    return 1 if bad else 0


def cmd_smoke(args, config: Config) -> int:
    """M0 exit test."""
    from relay.runner import _call, RunContext
    from relay.trace import TraceWriter, new_uid, read_traces, validate_record
    from relay.ratelimit import UsageLedger, build_limiters
    from relay.sandbox import Sandbox

    tasks = None
    if config.synthetic:
        tasks = _tasks_for(config)
    traces_dir = config.path("traces_dir")
    n = len(list(traces_dir.glob("smoke_*.jsonl"))) + 1
    writer = TraceWriter(traces_dir / f"smoke_{n:03d}.jsonl")
    backends = build_backends(config, 0.0, tasks=tasks,
                              limiters=build_limiters(config, UsageLedger(config.path("state_dir"))))
    ids = args.backend or list(backends)
    ctx = RunContext(config, f"smoke_{n:03d}", policies.get("P0"), backends, Cache(config.path("cache_dir")),
                     Sandbox.from_config(config), writer)
    prompt = "Reply with the single word: pong"
    ok = True
    for bid in ids:
        common = {"run_id": ctx.run_id, "task_id": "smoke", "task_run_uid": new_uid(), "policy": "smoke"}
        try:
            for attempt in (1, 2):
                res, rec = _call(ctx, common, bid, "diagnose", attempt, prompt, 8)
                writer.write(rec)
                validate_record(rec | {"schema": 1, "ts": "x"})
                print(f"{bid:<12} attempt {attempt}: cache_hit={rec['cache_hit']} total_ms={rec['total_ms']} "
                      f"tokens={rec['prompt_tokens']}+{rec['completion_tokens']} text={res.text.strip()[:40]!r}")
            if not rec["cache_hit"]:
                ok = False
                print(f"FAIL {bid}: repeat call was not a cache hit")
        except Exception as e:
            ok = False
            print(f"FAIL {bid}: {e}")
    lines = list(read_traces(writer.path))  # re-validates every line on disk
    print(f"{'PASS' if ok else 'FAIL'}: {len(lines)} valid trace lines in {writer.path}")
    return 0 if ok else 1


def cmd_profile(args, config: Config) -> int:
    from relay.profile import run_profile

    tasks = _tasks_for(config) if config.synthetic else None
    path = run_profile(config, args.backend, n=args.n, tasks=tasks)
    print(f"wrote {path}; build the table with: python -m analysis.build")
    return 0


def cmd_verify_sandbox(args, config: Config) -> int:
    """M2 exit test: known-bad fails, known-good passes, on every selected task."""
    from relay.sandbox import Sandbox

    sbx = Sandbox.from_config(config)
    tasks = _tasks_for(config)
    bad_ok = good_ok = 0
    for tid, t in tasks.items():
        bad = sbx.run(t["declaration"] + t["buggy_solution"], t)
        good = sbx.run(t["declaration"] + t["canonical_solution"], t)
        bad_ok += not bad.passed
        good_ok += good.passed
        if bad.passed or not good.passed:
            print(f"  {tid}: buggy passed={bad.passed} reference passed={good.passed}")
    escape = sbx.run("import socket\ndef f():\n    return 1\nsocket.create_connection(('1.1.1.1', 53), timeout=2)\n",
                     {"declaration": "", "test": "def check(f):\n    assert f() == 1\n", "entry_point": "f"})
    print(f"sandbox: {sbx.describe()}")
    print(f"known-bad patches failed: {bad_ok}/{len(tasks)}; known-good passed: {good_ok}/{len(tasks)}; "
          f"network blocked: {not escape.passed}")
    ok = bad_ok == good_ok == len(tasks) and not escape.passed
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_run(args, config: Config) -> int:
    from relay.runner import run_policy

    tasks = _tasks_for(config)
    if args.tasks:
        keep = set(args.tasks.split(","))
        tasks = {k: v for k, v in tasks.items() if k in keep or k.split("/")[-1] in keep}
    pols = list(policies.POLICIES) if args.policy.lower() == "all" else [args.policy.upper()]
    if args.run_id and len(pols) > 1:
        print("--run-id needs a single --policy", file=sys.stderr)
        return 2
    for pid in pols:
        try:
            res = run_policy(config, pid, tasks, run_id=args.run_id, replay=args.replay,
                             temperature=args.temperature, rep=args.rep, limit=args.limit)
        except CacheMiss as e:
            print(f"\nREPLAY FAILED (cache miss): {e}", file=sys.stderr)
            return 3
        if res["status"] != "complete":
            return 4
    return 0


def _final_attempts(path: Path) -> dict[str, list[tuple]]:
    """task_id -> [(stage, attempt, backend, output_hash, passed)] for its completed execution."""
    from relay.trace import read_traces

    recs = list(read_traces(path))
    done_uids = {r["task_run_uid"]: r["task_id"] for r in recs if r["record"] == "task_end"}
    out: dict[str, list[tuple]] = {}
    for r in recs:
        if r["record"] == "call" and r["task_run_uid"] in done_uids:
            v = r.get("verify") or {}
            out.setdefault(r["task_id"], []).append(
                (r["stage"], r["attempt"], r["backend"], r["output_hash"], v.get("passed")))
    for tid in done_uids.values():
        out.setdefault(tid, [])
    return out


def cmd_repro(args, config: Config) -> int:
    """Replay every stored run from the cache (zero API calls) and rebuild every table and figure."""
    from relay.runner import read_meta, run_policy
    from relay.tasks import load_all

    os.environ["RELAY_OFFLINE"] = "1"
    t0 = time.time()
    traces_dir = config.path("traces_dir")
    runs = sorted(p for p in traces_dir.glob("r*.jsonl"))
    if not runs:
        print(f"No experiment traces in {traces_dir}. Run the experiment first (docs/RUNBOOK.md), "
              f"or try the offline demo: make demo-repro", file=sys.stderr)
        return 2
    repro_dir = config.path("repro_dir")
    if repro_dir.exists():
        shutil.rmtree(repro_dir)
    all_tasks = load_all(config.path("data_file"))
    problems = []
    warnings = []
    for path in runs:
        meta = read_meta(path)
        if not meta:
            problems.append(f"{path.name}: no run_meta line")
            continue
        tasks = {tid: all_tasks[tid] for tid in meta["task_ids"]}
        orig = _final_attempts(path)
        tasks = {tid: t for tid, t in tasks.items() if tid in orig}  # only tasks the run completed
        try:
            run_policy(config, meta["policy"], tasks, run_id=meta["run_id"], replay=True,
                       temperature=meta["temperature"], rep=meta.get("rep", 0), out_dir=repro_dir, quiet=True)
        except CacheMiss as e:
            print(f"REPRO FAILED: {path.name}: {e}", file=sys.stderr)
            return 3
        again = _final_attempts(repro_dir / path.name)
        for tid, seq in orig.items():
            seq2 = again.get(tid)
            strip = lambda s: [x[:4] for x in s]  # noqa: E731
            if seq2 is None or strip(seq) != strip(seq2):
                problems.append(f"{path.name} {tid}: replayed call sequence differs from the trace")
            elif [x[4] for x in seq] != [x[4] for x in seq2]:
                warnings.append(f"{path.name} {tid}: a test verdict changed on replay (flaky or timing-bound test)")
        print(f"replayed {path.name} ({meta['policy']}, {len(orig)} tasks): "
              f"{'ok' if not any(p.startswith(path.name) for p in problems) else 'MISMATCH'}")
    replay_s = time.time() - t0
    for w in warnings:
        print(f"warning: {w}")
    stats = {"replay_seconds": round(replay_s, 1), "runs": [p.name for p in runs], "mismatches": problems,
             "verdict_changes": warnings, "api_calls": 0}
    build = config.path("repro_dir").parent
    build.mkdir(parents=True, exist_ok=True)
    (build / "repro_stats.json").write_text(json.dumps(stats, indent=2))
    from analysis.build import build_all

    build_all(config, repro_stats=stats, t_start=t0)
    if problems:
        print("REPRO FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"repro OK: {len(runs)} runs replayed from cache with zero API calls; "
          f"results rebuilt in {time.time() - t0:.1f}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="relay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.environ.get("RELAY_CONFIG", "config.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("data", help="download and freeze the task sample")
    s.add_argument("--force", action="store_true", help="re-download even if the file exists")

    sub.add_parser("models", help="list Groq model ids and check the configured ones")

    s = sub.add_parser("smoke", help="M0 exit test")
    s.add_argument("--backend", action="append")

    s = sub.add_parser("profile", help="M1 latency profile")
    s.add_argument("--backend", action="append")
    s.add_argument("-n", type=int, default=20)

    sub.add_parser("verify-sandbox", help="M2 exit test for the verifier")

    s = sub.add_parser("run", help="run tasks under a policy")
    s.add_argument("--policy", required=True, help="P0, P1, P2, P3 or all")
    s.add_argument("--run-id", help="resume or name a run (default: next free rNNN)")
    s.add_argument("--replay", action="store_true", help="serve only from the cache; fail loudly on a miss")
    s.add_argument("--temperature", type=float, help="override (noise runs use a small value)")
    s.add_argument("--rep", type=int, default=0, help="repetition index for temperature > 0 noise runs")
    s.add_argument("--limit", type=int, help="run at most this many remaining tasks")
    s.add_argument("--tasks", help="comma-separated task ids or numbers (debugging)")

    sub.add_parser("repro", help="replay all runs from cache and rebuild results")

    args = ap.parse_args(argv)
    config = Config.load(args.config)
    handler = {
        "data": cmd_data, "models": cmd_models, "smoke": cmd_smoke, "profile": cmd_profile,
        "verify-sandbox": cmd_verify_sandbox, "run": cmd_run, "repro": cmd_repro,
    }[args.cmd]
    return handler(args, config)
