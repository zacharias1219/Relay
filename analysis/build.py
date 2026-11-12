"""Build results.json, tables and figures from traces. Never calls a model API.

    python -m analysis.build [--config config.yaml]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from analysis import figures, fmt
from analysis.data import Run, load_profiles, load_runs, select_primary
from analysis.failures import gallery, load_annotations
from evalkit import (bootstrap, bootstrap_ratio, paired_bootstrap, paired_bootstrap_ratio, percentile_stat,
                     verdict)
from relay import policies as policy_defs
from relay.config import Config

HIGHER_BETTER = {"pass_rate": "higher"}


def _order(ids):
    return sorted(ids, key=lambda s: int(s.split("/")[-1]))


def _arrays(run: Run, tids: list[str]) -> dict[str, np.ndarray]:
    rows = [run.tasks[t] for t in tids]
    return {
        "pass": np.array([r.passed for r in rows], float),
        "cost": np.array([r.cost for r in rows], float),
        "latency": np.array([r.latency_ms for r in rows], float),
        "wall": np.array([r.wall_ms for r in rows], float),
        "escalated": np.array([r.escalated for r in rows], float),
        "calls": np.array([r.n_calls for r in rows], float),
    }


def policy_summary(run: Run, B: int, level: float, seed: int, config: Config) -> dict:
    tids = _order(run.tasks)
    a = _arrays(run, tids)
    p = policy_defs.get(run.policy)
    kw = dict(resamples=B, level=level, seed=seed)
    tok: dict[str, dict] = defaultdict(lambda: {"prompt": 0, "completion": 0, "calls": 0, "cost": 0.0})
    for r in run.tasks.values():
        for c in r.calls:
            d = tok[c["backend"]]
            d["prompt"] += c["prompt_tokens"]
            d["completion"] += c["completion_tokens"]
            d["calls"] += 1
            d["cost"] += c["cost_usd_list"]
    tokens_by_backend = {
        b: {**d, "total": d["prompt"] + d["completion"],
            "display": {"prompt": fmt.integer(d["prompt"]), "completion": fmt.integer(d["completion"]),
                        "total": fmt.integer(d["prompt"] + d["completion"]), "calls": fmt.integer(d["calls"]),
                        "cost": fmt.usd(d["cost"])}}
        for b, d in sorted(tok.items())
    }
    model = sum(r.model_ms for r in run.tasks.values())
    test = sum(r.test_ms for r in run.tasks.values())
    queue = sum(r.queue_ms for r in run.tasks.values())
    solved = int(a["pass"].sum())
    total_cost = float(a["cost"].sum())
    n = len(tids)
    hits = sum(c["cache_hit"] for r in run.tasks.values() for c in r.calls)
    ncalls = sum(r.n_calls for r in run.tasks.values())
    return {
        "id": run.policy, "name": p.name, "description": p.description, "run_id": run.run_id,
        "temperature": run.temperature, "n": n, "n_display": str(n),
        "complete": run.complete, "n_expected": len(run.meta["task_ids"]),
        "solved": solved, "solved_display": f"{solved}/{n}",
        "pass_rate": fmt.interval(bootstrap(a["pass"], **kw).to_dict(), fmt.pct),
        "cost_per_solved": fmt.interval(bootstrap_ratio(a["cost"], a["pass"], **kw).to_dict(), fmt.usd),
        "cost_per_task": fmt.interval(bootstrap(a["cost"], **kw).to_dict(), fmt.usd),
        "latency_p50": fmt.interval(bootstrap(a["latency"], percentile_stat(50), **kw).to_dict(), fmt.secs),
        "latency_p95": fmt.interval(bootstrap(a["latency"], percentile_stat(95), **kw).to_dict(), fmt.secs),
        "wall_p50": fmt.interval(bootstrap(a["wall"], percentile_stat(50), **kw).to_dict(), fmt.secs),
        "wall_p95": fmt.interval(bootstrap(a["wall"], percentile_stat(95), **kw).to_dict(), fmt.secs),
        "escalation_rate": fmt.interval(bootstrap(a["escalated"], **kw).to_dict(), fmt.pct),
        "calls_per_task": fmt.interval(bootstrap(a["calls"], **kw).to_dict(), fmt.num),
        "total_cost": total_cost, "total_cost_display": fmt.usd(total_cost),
        "tokens_by_backend": tokens_by_backend,
        "latency_sum_display": fmt.secs(model + test), "wall_sum_display": fmt.secs(model + test + queue),
        "time_split": {"model_ms": model, "test_ms": test, "queue_ms": queue,
                       "model_ms_display": fmt.secs(model), "test_ms_display": fmt.secs(test),
                       "queue_ms_display": fmt.secs(queue)},
        "cache_hits": hits, "calls": ncalls,
        "cache_hits_display": f"{hits}/{ncalls}",
        "discarded_partial_records": run.discarded_partial,
    }


def paired_vs_baseline(primary: dict[str, Run], baseline: str, B: int, level: float, seed: int) -> list[dict]:
    if baseline not in primary:
        return []
    base = primary[baseline]
    out = []
    kw = dict(resamples=B, level=level, seed=seed)
    for pid, run in primary.items():
        if pid == baseline:
            continue
        tids = _order(set(base.tasks) & set(run.tasks))
        if not tids:
            continue
        a, b = _arrays(base, tids), _arrays(run, tids)
        metrics = [
            ("pass_rate", "pass rate", paired_bootstrap(a["pass"], b["pass"], **kw), fmt.pts, "higher"),
            ("cost_per_solved", "cost per solved task",
             paired_bootstrap_ratio(a["cost"], a["pass"], b["cost"], b["pass"], **kw), lambda v: fmt.usd(v, signed=True), "lower"),
            ("cost_per_task", "cost per task", paired_bootstrap(a["cost"], b["cost"], **kw),
             lambda v: fmt.usd(v, signed=True), "lower"),
            ("latency_p50", "latency p50", paired_bootstrap(a["latency"], b["latency"], percentile_stat(50), **kw),
             lambda v: fmt.secs(v, signed=True), "lower"),
            ("wall_p50", "wall clock p50", paired_bootstrap(a["wall"], b["wall"], percentile_stat(50), **kw),
             lambda v: fmt.secs(v, signed=True), "lower"),
        ]
        for key, label, iv, f, better in metrics:
            d = iv.to_dict()
            out.append({
                "policy": pid, "baseline": baseline, "metric": key, "label": label, "better": better,
                "n": len(tids), "diff": fmt.interval(d, f), "verdict": verdict(iv, better),
                "dropped_resamples": d.get("dropped", 0),
            })
    return out


def profile_table(profiles: list[dict]) -> list[dict]:
    latest: dict[str, str] = {}
    for r in profiles:
        latest[r["backend"]] = max(latest.get(r["backend"], ""), r["run_id"])
    rows = []
    for b in sorted(latest):
        recs = [r for r in profiles if r["backend"] == b and r["run_id"] == latest[b]]
        ttft = [r["ttft_ms"] for r in recs if r["ttft_ms"] is not None]
        tps = [r["completion_tokens"] / ((r["total_ms"] - r["ttft_ms"]) / 1000.0) for r in recs
               if r["ttft_ms"] is not None and r["total_ms"] > r["ttft_ms"] and r["completion_tokens"] > 1]

        def q(v, p):
            return float(np.percentile(v, p)) if v else None

        rows.append({
            "backend": b, "model_id": recs[0]["model_id"], "kind": recs[0]["backend_kind"], "run_id": latest[b],
            "n": len(recs), "n_display": str(len(recs)),
            "ttft_p50": q(ttft, 50), "ttft_p25": q(ttft, 25), "ttft_p75": q(ttft, 75),
            "tps_p50": q(tps, 50), "tps_p25": q(tps, 25), "tps_p75": q(tps, 75),
            "display": {
                "ttft_p50": fmt.ms(q(ttft, 50)), "ttft_iqr": f"{fmt.ms(q(ttft, 25))} to {fmt.ms(q(ttft, 75))}",
                "tps_p50": fmt.num(q(tps, 50), 1), "tps_iqr": f"{fmt.num(q(tps, 25), 1)} to {fmt.num(q(tps, 75), 1)}",
            },
            "date": recs[0]["ts"][:10],
        })
    return rows


def hardware(runs: list[Run], config: Config) -> dict:
    hw = dict(config.get("hardware") or {})
    local_meta = next((r.meta for r in runs if "local-small" in r.meta.get("backends", {})), None)
    meta = local_meta or (runs[0].meta if runs else {})
    groq_days = sorted({c["ts"][:10] for r in runs for t in r.tasks.values() for c in t.calls
                        if c["backend_kind"] == "groq" and not c["cache_hit"]})
    lb = config.backends.get("local-small", {})
    return {
        "laptop_model": (meta.get("hardware") or {}).get("laptop_model", hw.get("laptop_model")),
        "llama_cpp_build": ((meta.get("llama_cpp") or {}).get("build_info")
                            or (meta.get("hardware") or {}).get("llama_cpp_build") or hw.get("llama_cpp_build")),
        "host": meta.get("host", {}),
        "ram_display": f"{meta.get('host', {}).get('ram_gb')} GB" if meta.get("host", {}).get("ram_gb") else "n/a",
        "local_model": lb.get("model_id"), "quantization": lb.get("quantization"),
        "llama_cpp_props": meta.get("llama_cpp", {}),
        "sandbox": meta.get("sandbox", {}),
        "groq_run_dates": groq_days,
        "groq_run_dates_display": (f"{groq_days[0]} to {groq_days[-1]}" if len(groq_days) > 1
                                   else (groq_days[0] if groq_days else "n/a (no live Groq calls in these traces)")),
    }


def noise(runs: list[Run], primary: dict[str, Run]) -> list[dict]:
    by = defaultdict(list)
    for r in runs:
        by[r.policy].append(r)
    out = []
    for pid, rs in sorted(by.items()):
        if len(rs) < 2:
            continue
        rows = []
        for r in sorted(rs, key=lambda r: (r.temperature, r.meta.get("rep", 0))):
            n = len(r.tasks)
            s = sum(t.passed for t in r.tasks.values())
            rows.append({"run_id": r.run_id, "temperature": r.temperature, "rep": r.meta.get("rep", 0),
                         "solved": s, "n": n, "pass_rate": s / n if n else None,
                         "display": fmt.pct(s / n if n else None), "primary": primary.get(pid) is r})
        rates = [x["pass_rate"] for x in rows if x["pass_rate"] is not None]
        spread = max(rates) - min(rates) if rates else None
        out.append({"policy": pid, "runs": rows, "spread": spread, "spread_display": fmt.pts(spread).lstrip("+")})
    return out


def task_grid(primary: dict[str, Run], tasks_meta: dict) -> list[dict]:
    ids = _order(set().union(*(set(r.tasks) for r in primary.values()))) if primary else []
    out = []
    for tid in ids:
        per = {}
        for pid, run in primary.items():
            row = run.tasks.get(tid)
            if row is None:
                continue
            fv = row.final_verify or {}
            per[pid] = {
                "passed": row.passed, "calls": row.n_calls,
                "path": [c["backend"] for c in row.calls],
                "path_display": " > ".join(f"{c['stage']}@{c['backend']}" for c in row.calls),
                "escalated": row.escalated, "status": fv.get("status", "none"),
                "cost": row.cost, "cost_display": fmt.usd(row.cost),
                "latency_ms": row.latency_ms, "latency_display": fmt.secs(row.latency_ms),
                "wall_ms": row.wall_ms, "wall_display": fmt.secs(row.wall_ms),
                "queue_display": fmt.secs(row.queue_ms),
                "tokens": row.tokens, "tokens_display": fmt.integer(row.tokens),
                "timeline": row.timeline(),
            }
        m = tasks_meta.get(tid, {})
        out.append({"task_id": tid, "entry_point": m.get("entry_point"), "bug_type": m.get("bug_type"),
                    "per_policy": per})
    return out


def _outputs_by_hash(cache_dir: Path) -> dict[str, str]:
    from relay.cache import Cache
    from relay.trace import sha256_text

    out = {}
    for e in Cache(cache_dir).iter_entries():
        t = e["response"]["text"]
        out[sha256_text(t)] = t
    return out


def run_probes(primary: dict[str, Run], tasks_meta: dict, config: Config) -> dict:
    from analysis.overfit import probe
    from relay.sandbox import Sandbox
    from relay.stages import extract_code

    if not config["analysis"].get("overfit_probe", True):
        return {}
    texts = _outputs_by_hash(config.path("cache_dir"))
    sbx = Sandbox.from_config(config)
    seen: dict[tuple[str, str], dict] = {}
    for run in primary.values():
        for tid, row in run.tasks.items():
            if not row.passed:
                continue
            last = row.patch_calls[-1]
            ch = last["verify"]["code_hash"]
            if (tid, ch) in seen or tid not in tasks_meta:
                continue
            text = texts.get(last["output_hash"])
            if text is None:
                seen[(tid, ch)] = None
                continue
            code = extract_code(text, tasks_meta[tid]["entry_point"])
            seen[(tid, ch)] = probe(sbx, tasks_meta[tid], code, seed=config.seed) if code else None
    return seen


def write_tables(results: dict, tables_dir: Path) -> None:
    tables_dir.mkdir(parents=True, exist_ok=True)
    pol = results["policies"]
    ci = results["config"]["ci_level_display"]
    lines = [f"| Policy | Solved | Pass rate ({ci} CI) | Cost per solved ({ci} CI) | Latency p50 | Latency p95 | "
             f"Wall p50 | Escalation | Calls/task |",
             "|---|---|---|---|---|---|---|---|---|"]
    rows_csv = []
    for p in pol:
        lines.append(
            f"| {p['id']} {p['name']} | {p['solved_display']} | {p['pass_rate']['display']} {p['pass_rate']['display_ci']} | "
            f"{p['cost_per_solved']['display']} {p['cost_per_solved']['display_ci']} | "
            f"{p['latency_p50']['display']} {p['latency_p50']['display_ci']} | "
            f"{p['latency_p95']['display']} {p['latency_p95']['display_ci']} | "
            f"{p['wall_p50']['display']} {p['wall_p50']['display_ci']} | "
            f"{p['escalation_rate']['display']} | {p['calls_per_task']['display']} |")
        rows_csv.append({
            "policy": p["id"], "run_id": p["run_id"], "n": p["n"], "solved": p["solved"],
            **{f"{k}_{s}": p[k][s] for k in ("pass_rate", "cost_per_solved", "latency_p50", "latency_p95",
                                              "wall_p50", "wall_p95", "escalation_rate", "calls_per_task")
               for s in ("point", "lo", "hi")},
            "total_cost_usd_list": p["total_cost"],
        })
    (tables_dir / "policies.md").write_text("\n".join(lines) + "\n")
    _csv(tables_dir / "policies.csv", rows_csv)

    lines = ["| Comparison | Metric | Difference | 95% CI | Verdict |", "|---|---|---|---|---|"]
    for d in results["paired"]:
        lines.append(f"| {d['policy']} vs {d['baseline']} | {d['label']} | {d['diff']['display']} | "
                     f"{d['diff']['display_ci']} | {d['verdict']} |")
    (tables_dir / "paired.md").write_text("\n".join(lines) + "\n")
    _csv(tables_dir / "paired.csv", [{"policy": d["policy"], "baseline": d["baseline"], "metric": d["metric"],
                                       "point": d["diff"]["point"], "lo": d["diff"]["lo"], "hi": d["diff"]["hi"],
                                       "verdict": d["verdict"]} for d in results["paired"]])

    lines = ["| Policy | Backend | Calls | Prompt tokens | Completion tokens | List-price cost |", "|---|---|---|---|---|---|"]
    for p in pol:
        for b, t in p["tokens_by_backend"].items():
            lines.append(f"| {p['id']} | {b} | {t['display']['calls']} | {t['display']['prompt']} | "
                         f"{t['display']['completion']} | {t['display']['cost']} |")
    lines += ["", "| Policy | Model time | Test time | Queue wait | Total list-price cost |", "|---|---|---|---|---|"]
    for p in pol:
        ts = p["time_split"]
        lines.append(f"| {p['id']} | {ts['model_ms_display']} | {ts['test_ms_display']} | {ts['queue_ms_display']} | "
                     f"{p['total_cost_display']} |")
    (tables_dir / "cost_latency.md").write_text("\n".join(lines) + "\n")

    if results["profile"]:
        lines = ["| Backend | Model | n | TTFT p50 | TTFT IQR | Tokens/s p50 | Tokens/s IQR |", "|---|---|---|---|---|---|---|"]
        for r in results["profile"]:
            d = r["display"]
            lines.append(f"| {r['backend']} | {r['model_id']} | {r['n']} | {d['ttft_p50']} | {d['ttft_iqr']} | "
                         f"{d['tps_p50']} | {d['tps_iqr']} |")
        (tables_dir / "profile.md").write_text("\n".join(lines) + "\n")

    f = results["failures"]
    lines = ["# Failure gallery", "", "## Tasks every policy fails", ""]
    lines += [f"- {x['task_id']} ({x['bug_type']}): {x['likely_reason']}" for x in f["all_fail"]] or ["- none"]
    lines += ["", f"## P3 escalated and still failed ({f['p3_escalated_failed']['count']} tasks, "
                  f"{f['p3_escalated_failed']['tokens_display']} tokens, {f['p3_escalated_failed']['cost_display']} wasted)", ""]
    lines += [f"- {x['task_id']}: {x['path']}, {x['escalation_tokens_display']} tokens, {x['reason']}"
              for x in f["p3_escalated_failed"]["tasks"]] or ["- none"]
    lines += ["", "## Small model passed, large model failed", ""]
    lines += [f"- {x['task_id']}: {', '.join(x['small_winners'])}; P0 failed because {x['large_failure']['reason']}"
              for x in f["small_beats_large"]] or ["- none"]
    lines += ["", "## Routing lost to the baseline (task level)", ""]
    lines += [f"- {x['policy']} on {x['task_id']}: {x['reason']} (path {x['path']})" for x in f["routing_losses"]] or ["- none"]
    lines += ["", "## Passes the tests but plainly wrong", ""]
    pw = f["plainly_wrong"]
    lines += [f"- confirmed: {x['task_id']} ({x['policy']}): {x['note']}" for x in pw["confirmed"]]
    lines += [f"- candidate: {x['task_id']} ({', '.join(x['policies'])}): disagrees with the reference on "
              f"{x['display']} perturbed inputs" for x in pw["candidates"]]
    if not pw["confirmed"] and not pw["candidates"]:
        lines.append(f"- none found among {pw['probed_display']} probed passing patches")
    (tables_dir / "failures.md").write_text("\n".join(lines) + "\n")


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def headline(results: dict) -> list[str]:
    """Plain-language findings, each built only from numbers already in results."""
    out = []
    for d in results["paired"]:
        if d["metric"] not in ("pass_rate", "cost_per_solved", "latency_p50"):
            continue
        if d["verdict"].startswith("no detectable"):
            out.append(f"{d['policy']} vs {d['baseline']}, {d['label']}: no detectable difference at n = {d['n']} "
                       f"(paired difference {d['diff']['display']}, {results['config']['ci_level_display']} CI "
                       f"{d['diff']['display_ci']}).")
        else:
            out.append(f"{d['policy']} vs {d['baseline']}, {d['label']}: {d['verdict']} "
                       f"(paired difference {d['diff']['display']}, CI {d['diff']['display_ci']}).")
    return out


def build_all(config: Config, repro_stats: dict | None = None, t_start: float | None = None,
              quiet: bool = False) -> dict:
    t0 = t_start or time.time()
    from relay.tasks import load_all

    traces_dir = config.path("traces_dir")
    runs = load_runs(traces_dir)
    profiles = load_profiles(traces_dir)
    primary = select_primary(runs, float(config["experiment"].get("temperature", 0.0)))
    a = config["analysis"]
    B, level, seed = int(a["bootstrap_resamples"]), float(a["ci_level"]), config.seed
    baseline = a.get("baseline_policy", "P0")
    all_tasks = load_all(config.path("data_file")) if config.path("data_file").exists() else {}
    tasks_meta = {tid: {"entry_point": t["entry_point"], "bug_type": t.get("bug_type"),
                        "declaration": t["declaration"], "canonical_solution": t["canonical_solution"],
                        "buggy_solution": t["buggy_solution"], "test": t["test"]} for tid, t in all_tasks.items()}

    synthetic = any(r.meta.get("synthetic") for r in runs) or config.synthetic
    pol = [policy_summary(r, B, level, seed, config) for r in primary.values()]
    paired = paired_vs_baseline(primary, baseline, B, level, seed)
    probes = run_probes(primary, tasks_meta, config)
    annotations = load_annotations(config.path("annotations"))
    fails = gallery(primary, tasks_meta, baseline, annotations, probes)
    grid = task_grid(primary, tasks_meta)
    # Small enough to embed: buggy and reference code for tasks in the gallery
    gallery_ids = ({x["task_id"] for x in fails["all_fail"]} | {x["task_id"] for x in fails["routing_losses"]}
                   | {x["task_id"] for x in fails["small_beats_large"]}
                   | {x["task_id"] for x in fails["p3_escalated_failed"]["tasks"]}
                   | {x["task_id"] for x in fails["plainly_wrong"]["candidates"]})
    code = {tid: {"buggy": tasks_meta[tid]["declaration"] + tasks_meta[tid]["buggy_solution"],
                  "reference": tasks_meta[tid]["declaration"] + tasks_meta[tid]["canonical_solution"]}
            for tid in gallery_ids if tid in tasks_meta}
    n_tasks = int(config["experiment"]["n_tasks"])
    results = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "synthetic": synthetic,
        "config": {
            "path": os.path.relpath(config["_path"], config.root),
            "seed": config.seed, "n_tasks": n_tasks, "n_tasks_display": str(n_tasks),
            "bootstrap_resamples": B, "bootstrap_resamples_display": fmt.integer(B),
            "ci_level": level, "ci_level_display": fmt.pct(level, 0),
            "baseline": baseline, "replay_default_policy": a.get("replay_default_policy", "P3"),
            "max_calls": int(config["experiment"]["max_model_calls_per_task"]),
            "max_calls_display": str(config["experiment"]["max_model_calls_per_task"]),
            "sandbox_timeout_display": f"{config['sandbox']['timeout_s']} s",
            "backends": {b: {"kind": v["kind"], "model_id": v["model_id"], "role": v.get("role"),
                             "runs_on": v.get("runs_on"), "price_per_mtok": v.get("price_per_mtok"),
                             "price_display": (f"{fmt.usd(v['price_per_mtok']['input'])} in / "
                                               f"{fmt.usd(v['price_per_mtok']['output'])} out per 1M tokens")
                             if v.get("price_per_mtok") else "n/a",
                             "limits": v.get("limits"),
                             "limits_display": (", ".join(f"{fmt.integer(x)} {k.upper()}" for k, x in v["limits"].items())
                                                if v.get("limits") else "none (local)")}
                         for b, v in config.backends.items()},
            "policies": {pid: {"name": p.name, "description": p.description} for pid, p in policy_defs.POLICIES.items()},
            "groq_sample_limits_display": "30 RPM, 1,000 RPD, 8,000 TPM, 200,000 TPD",
        },
        "runs": [{"run_id": r.run_id, "policy": r.policy, "temperature": r.temperature, "rep": r.meta.get("rep", 0),
                  "tasks_completed": len(r.tasks), "tasks_expected": len(r.meta["task_ids"]),
                  "tasks_display": f"{len(r.tasks)}/{len(r.meta['task_ids'])}", "mode": r.meta.get("mode"),
                  "synthetic": r.meta.get("synthetic", False), "git_sha": r.meta.get("git_sha"),
                  "primary": primary.get(r.policy) is r} for r in runs],
        "policies": pol,
        "paired": paired,
        "tasks": grid,
        "failures": fails,
        "task_code": code,
        "profile": profile_table(profiles),
        "noise": noise(runs, primary),
        "hardware": hardware(runs, config),
        "figures": {},
        "repro": {},
    }
    results["headline"] = headline(results)

    figs = figures.write_all(results, config.path("figures_dir"))
    rj = config.path("results_json")
    results["figures"] = {k: os.path.relpath(p, rj.parent) for k, p in figs.items()}
    write_tables(results, config.path("tables_dir"))

    elapsed = time.time() - t0
    rs = repro_stats or {}
    results["repro"] = {
        "command": "make repro" if not synthetic else "make demo-repro",
        "replay_seconds": rs.get("replay_seconds"),
        "total_seconds": round(elapsed, 1),
        "total_display": f"{elapsed:.0f} s" if elapsed >= 10 else f"{elapsed:.1f} s",
        "api_calls": rs.get("api_calls", 0),
        "mismatches": rs.get("mismatches", []),
        "verdict_changes": rs.get("verdict_changes", []),
        "from_replay": bool(repro_stats),
    }
    rj.parent.mkdir(parents=True, exist_ok=True)
    rj.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    if not quiet:
        print(f"wrote {rj} ({len(runs)} runs, {len(primary)} primary, {len(profiles)} profile calls) "
              f"tables -> {config.path('tables_dir')}, figures -> {config.path('figures_dir')}")
        for h in results["headline"]:
            print("  " + h)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.environ.get("RELAY_CONFIG", "config.yaml"))
    args = ap.parse_args(argv)
    build_all(Config.load(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
