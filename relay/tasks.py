"""Workload: bug-fix tasks from HumanEvalFix (Python split of HumanEvalPack).

`python -m relay data` downloads the split once, records its sha256 and source,
checks every task in the sandbox (the buggy code must fail its tests and the
reference must pass them), and samples `n_tasks` eligible tasks with the
experiment seed. The sample is frozen in data/tasks.json and committed.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from relay.sandbox import Sandbox

REQUIRED = ("task_id", "declaration", "buggy_solution", "canonical_solution", "test", "entry_point")


def relay_task_id(source_id: str) -> str:
    """'Python/12' -> 'HumanEvalFix/12'."""
    return "HumanEvalFix/" + source_id.split("/")[-1]


def download(sources: list[str], dest: Path) -> dict:
    import requests

    errors = []
    for url in sources:
        try:
            r = requests.get(url, timeout=60)
            if r.status_code != 200:
                errors.append(f"{url}: HTTP {r.status_code}")
                continue
            body = r.content
            rows = [json.loads(line) for line in body.decode().splitlines() if line.strip()]
            if not rows or any(k not in rows[0] for k in REQUIRED):
                errors.append(f"{url}: unexpected format")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            return {"url": url, "sha256": hashlib.sha256(body).hexdigest(), "rows": len(rows), "license": "MIT"}
        except Exception as e:  # network refusals, DNS, etc.
            errors.append(f"{url}: {e}")
    raise RuntimeError("could not download HumanEvalFix from any source:\n  " + "\n  ".join(errors))


def load_all(data_file: Path) -> dict[str, dict]:
    out = {}
    with open(data_file, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                row["source_id"] = row["task_id"]
                row["task_id"] = relay_task_id(row["task_id"])
                out[row["task_id"]] = row
    return out


def check_eligible(tasks: dict[str, dict], sandbox: Sandbox) -> dict[str, dict]:
    """Keep tasks whose buggy code fails and whose reference passes in our sandbox."""
    report = {}
    for tid, t in tasks.items():
        bug = sandbox.run(t["declaration"] + t["buggy_solution"], t)
        ref = sandbox.run(t["declaration"] + t["canonical_solution"], t)
        report[tid] = {"buggy_fails": not bug.passed, "reference_passes": ref.passed}
    return report


def select(eligible_ids: list[str], n: int, seed: int) -> list[str]:
    ids = sorted(eligible_ids, key=lambda s: int(s.split("/")[-1]))
    rng = random.Random(seed)
    picked = rng.sample(ids, n)
    return sorted(picked, key=lambda s: int(s.split("/")[-1]))


def load_selected(config) -> dict[str, dict]:
    data_file = config.path("data_file")
    tasks_file = config.path("tasks_file")
    if not data_file.exists() or not tasks_file.exists():
        raise FileNotFoundError(
            f"{data_file} or {tasks_file} is missing. Run `python -m relay data` first."
        )
    sel = json.loads(tasks_file.read_text())
    all_tasks = load_all(data_file)
    return {tid: all_tasks[tid] for tid in sel["task_ids"]}


def prepare(config, force_download: bool = False) -> dict:
    data_file = config.path("data_file")
    tasks_file = config.path("tasks_file")
    source_file = data_file.parent / "SOURCE.json"
    if force_download or not data_file.exists():
        info = download(config["dataset"]["sources"], data_file)
        source_file.write_text(json.dumps(info, indent=2) + "\n")
    tasks = load_all(data_file)
    sandbox = Sandbox.from_config(config)
    report = check_eligible(tasks, sandbox)
    eligible = [tid for tid, r in report.items() if r["buggy_fails"] and r["reference_passes"]]
    n = int(config["experiment"]["n_tasks"])
    picked = select(eligible, n, config.seed)
    out = {
        "seed": config.seed,
        "n_tasks": n,
        "n_total": len(tasks),
        "n_eligible": len(eligible),
        "ineligible": {tid: r for tid, r in report.items() if tid not in eligible},
        "data_sha256": hashlib.sha256(data_file.read_bytes()).hexdigest(),
        "task_ids": picked,
    }
    tasks_file.write_text(json.dumps(out, indent=2) + "\n")
    return out
