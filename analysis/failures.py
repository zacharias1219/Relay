"""The failure gallery, derived from traces (plus optional human annotations)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from analysis import fmt
from analysis.data import Run, TaskRow

_EXC = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Exit|Interrupt))\b", re.MULTILINE)


def classify(verify: dict | None) -> tuple[str, str]:
    """(code, plain-language likely reason) for a failed verify."""
    if not verify:
        return "no_patch", "no patch was attempted"
    st = verify.get("status")
    if st == "no_code":
        return "no_code", "the reply had no Python code block (format failure or truncated output)"
    if st == "timeout":
        return "timeout", "the patch ran past the 10 s limit (likely an infinite loop)"
    err = verify.get("stderr_tail") or ""
    excs = _EXC.findall(err)
    exc = excs[-1] if excs else None
    if exc in ("SyntaxError", "IndentationError"):
        return "syntax", "the patch does not parse"
    if exc == "NameError":
        return "name", "the patch uses an undefined name (missing import or helper)"
    if exc == "AssertionError" or st == "failed":
        return "wrong_answer", "the patch runs but still returns wrong answers"
    if exc:
        return "runtime", f"the patch raises {exc} at runtime"
    return "error", "the patch crashed"


def load_annotations(path: Path) -> dict:
    if not path.exists():
        return {"failure_reasons": {}, "plainly_wrong": [], "dismissed": []}
    d = yaml.safe_load(path.read_text()) or {}
    d.setdefault("failure_reasons", {})
    d.setdefault("plainly_wrong", [])
    d.setdefault("dismissed", [])
    d["failure_reasons"] = d["failure_reasons"] or {}
    d["plainly_wrong"] = d["plainly_wrong"] or []
    d["dismissed"] = d["dismissed"] or []
    return d


def _task_cost_tokens(row: TaskRow) -> dict:
    return {"tokens": row.tokens, "tokens_display": fmt.integer(row.tokens),
            "cost": row.cost, "cost_display": fmt.usd(row.cost)}


def gallery(primary: dict[str, Run], tasks_meta: dict[str, dict], baseline: str, annotations: dict,
            probes: dict[tuple[str, str], dict]) -> dict:
    pids = list(primary)
    common = set.intersection(*(set(r.tasks) for r in primary.values())) if primary else set()
    order = sorted(common, key=lambda s: int(s.split("/")[-1]))
    reasons_override = annotations["failure_reasons"]

    def reason_for(tid: str, row: TaskRow) -> dict:
        code, text = classify(row.final_verify)
        if tid in reasons_override:
            text = str(reasons_override[tid])
            code = "annotated"
        return {"code": code, "reason": text, "status": (row.final_verify or {}).get("status", "none"),
                "stderr_tail": ((row.final_verify or {}).get("stderr_tail") or "")[-600:]}

    all_fail = []
    for tid in order:
        if all(not primary[p].tasks[tid].passed for p in pids):
            per = {p: reason_for(tid, primary[p].tasks[tid]) for p in pids}
            codes = {v["code"] for v in per.values()}
            all_fail.append({
                "task_id": tid,
                "bug_type": tasks_meta.get(tid, {}).get("bug_type", ""),
                "likely_reason": reasons_override.get(tid) or (next(iter(per.values()))["reason"] if len(codes) == 1
                                                               else "mixed: " + "; ".join(sorted({v['reason'] for v in per.values()}))),
                "per_policy": per,
            })

    # P3 escalated and still failed: what did the escalation spend for nothing?
    p3 = []
    p3_tokens = p3_cost = 0
    if "P3" in primary:
        for tid in sorted(primary["P3"].tasks, key=lambda s: int(s.split("/")[-1])):
            row = primary["P3"].tasks[tid]
            if row.escalated and not row.passed:
                esc_calls = [c for c in row.patch_calls[1:]]
                tok = sum(c["prompt_tokens"] + c["completion_tokens"] for c in esc_calls)
                cost = sum(c["cost_usd_list"] for c in esc_calls)
                p3_tokens += tok
                p3_cost += cost
                p3.append({"task_id": tid, "path": " > ".join(c["backend"] for c in row.patch_calls),
                           "escalation_tokens": tok, "escalation_tokens_display": fmt.integer(tok),
                           "escalation_cost": cost, "escalation_cost_display": fmt.usd(cost),
                           **reason_for(tid, row)})

    # Small passed where large failed
    small_beats_large = []
    if baseline in primary:
        base = primary[baseline]
        for tid in order:
            if base.tasks[tid].passed:
                continue
            winners = []
            if "P1" in primary and primary["P1"].tasks[tid].passed:
                winners.append("P1 (groq-small)")
            if "P3" in primary:
                pc = primary["P3"].tasks[tid].patch_calls
                if pc and pc[0]["backend"] == "local-small" and (pc[0].get("verify") or {}).get("passed"):
                    winners.append("P3 first attempt (local-small)")
            if winners:
                small_beats_large.append({"task_id": tid, "bug_type": tasks_meta.get(tid, {}).get("bug_type", ""),
                                          "small_winners": winners,
                                          "large_failure": reason_for(tid, base.tasks[tid])})

    # Routing loses to the baseline, task by task
    losses = []
    if baseline in primary:
        base = primary[baseline]
        for p in pids:
            if p == baseline:
                continue
            for tid in order:
                if base.tasks[tid].passed and not primary[p].tasks[tid].passed:
                    row = primary[p].tasks[tid]
                    losses.append({"policy": p, "task_id": tid,
                                   "bug_type": tasks_meta.get(tid, {}).get("bug_type", ""),
                                   "path": " > ".join(c["backend"] for c in row.calls),
                                   **reason_for(tid, row), **_task_cost_tokens(row)})

    # Passes the tests but plainly wrong
    confirmed = []
    for item in annotations["plainly_wrong"]:
        confirmed.append({"task_id": item.get("task_id"), "policy": item.get("policy"),
                          "note": item.get("note", ""), "code_hash": item.get("code_hash")})
    dismissed = {(d.get("task_id"), d.get("code_hash")) for d in annotations["dismissed"]}
    candidates = []
    for (tid, code_hash), res in sorted(probes.items()):
        if not res or res.get("disagreements", 0) == 0 or (tid, code_hash) in dismissed:
            continue
        pols = sorted(p for p in pids if tid in primary[p].tasks and
                      (primary[p].tasks[tid].final_verify or {}).get("code_hash") == code_hash
                      and primary[p].tasks[tid].passed)
        candidates.append({"task_id": tid, "code_hash": code_hash, "policies": pols,
                           "checked": res["checked"], "disagreements": res["disagreements"],
                           "display": f"{res['disagreements']}/{res['checked']}",
                           "examples": res.get("examples", [])})
    probed = len([r for r in probes.values() if r])

    return {
        "all_fail": all_fail,
        "p3_escalated_failed": {"tasks": p3, "tokens": p3_tokens, "tokens_display": fmt.integer(p3_tokens),
                                "cost": p3_cost, "cost_display": fmt.usd(p3_cost), "count": len(p3)},
        "small_beats_large": small_beats_large,
        "routing_losses": losses,
        "plainly_wrong": {"confirmed": confirmed, "candidates": candidates, "probed_patches": probed,
                          "probed_display": fmt.integer(probed)},
    }
