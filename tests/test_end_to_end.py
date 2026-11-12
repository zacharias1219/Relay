"""Runner -> traces -> replay -> analysis on the offline simulator."""

import json
import shutil

import pytest

from relay.cache import CacheMiss
from relay.runner import run_policy
from relay.trace import read_traces


def _subset(tasks, n=4):
    return dict(list(tasks.items())[:n])


def test_run_trace_replay_analyse(demo_config, tasks):
    sub = _subset(tasks)
    for pid in ("P0", "P1", "P2", "P3"):
        res = run_policy(demo_config, pid, sub, quiet=True)
        assert res["status"] == "complete" and res["completed"] == len(sub)

    traces = sorted(demo_config.path("traces_dir").glob("r*.jsonl"))
    assert len(traces) == 4
    for p in traces:
        recs = list(read_traces(p))  # every line validates
        assert recs[0]["record"] == "run_meta" and recs[0]["synthetic"] is True
        ends = [r for r in recs if r["record"] == "task_end"]
        assert len(ends) == len(sub) and all(e["model_calls"] <= 4 for e in ends)
        assert any(r["record"] == "verify" and r["attempt"] == 0 for r in recs)

    # resume: rerunning a complete run does nothing
    res = run_policy(demo_config, "P3", sub, run_id="r004", quiet=True)
    assert res["cache_misses"] == 0

    # replay reproduces the exact call sequence with zero backend calls
    from relay.cli import _final_attempts
    out = demo_config.path("repro_dir")
    run_policy(demo_config, "P3", sub, run_id="r004", replay=True, out_dir=out, quiet=True)
    a = _final_attempts(demo_config.path("traces_dir") / "r004.jsonl")
    b = _final_attempts(out / "r004.jsonl")
    assert a == b

    from analysis.build import build_all
    r = build_all(demo_config, quiet=True)
    assert r["synthetic"] is True
    assert [p["id"] for p in r["policies"]] == ["P0", "P1", "P2", "P3"]
    assert {d["policy"] for d in r["paired"]} == {"P1", "P2", "P3"}
    for p in r["policies"]:
        assert p["n"] == len(sub) and p["pass_rate"]["display"].endswith("%")
    assert json.loads(demo_config.path("results_json").read_text())["policies"]
    assert (demo_config.path("tables_dir") / "policies.md").exists()
    assert (demo_config.path("figures_dir") / "pass_vs_cost.svg").read_text().startswith("<svg")


def test_replay_miss_fails_loudly(demo_config, tasks):
    sub = _subset(tasks, 2)
    run_policy(demo_config, "P1", sub, quiet=True)
    shutil.rmtree(demo_config.path("cache_dir"))
    with pytest.raises(CacheMiss):
        run_policy(demo_config, "P1", sub, run_id="r001", replay=True,
                   out_dir=demo_config.path("repro_dir"), quiet=True)


def test_interrupted_task_is_rerun_and_partial_lines_ignored(demo_config, tasks):
    from analysis.data import load_run
    from relay.trace import TraceWriter

    sub = _subset(tasks, 2)
    run_policy(demo_config, "P0", sub, quiet=True)
    path = demo_config.path("traces_dir") / "r001.jsonl"
    # simulate a crash mid-task: a stray call line from an execution that never ended
    recs = list(read_traces(path))
    stray = dict(next(r for r in recs if r["record"] == "call"))
    stray["task_run_uid"] = "crashed"
    stray.pop("schema"), stray.pop("ts")
    TraceWriter(path).write(stray)
    run = load_run(path)
    assert run.discarded_partial == 1 and len(run.tasks) == 2
