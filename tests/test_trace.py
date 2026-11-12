import pytest

from relay.trace import TraceError, TraceWriter, read_traces, validate_record

CALL = {
    "record": "call", "schema": 1, "ts": "2026-11-12T00:00:00Z", "run_id": "r007", "task_id": "HumanEvalFix/12",
    "task_run_uid": "abc", "policy": "P3", "stage": "patch", "attempt": 1, "backend": "local-small",
    "backend_kind": "llamacpp", "model_id": "m", "temperature": 0.0, "rep": 0, "queue_wait_ms": 0,
    "ttft_ms": 310, "total_ms": 2150, "prompt_tokens": 612, "completion_tokens": 140, "tokens_estimated": False,
    "cost_usd_list": 0.0, "cache_hit": False, "output_hash": "ab12",
    "verify": {"passed": False, "status": "failed", "test_ms": 480, "code_hash": "x", "stderr_tail": ""},
}


def test_spec_example_validates():
    validate_record(CALL)


@pytest.mark.parametrize("field", ["run_id", "backend", "total_ms", "cache_hit", "output_hash"])
def test_missing_field_rejected(field):
    rec = dict(CALL)
    del rec[field]
    with pytest.raises(TraceError):
        validate_record(rec)


def test_wrong_types_rejected():
    with pytest.raises(TraceError):
        validate_record({**CALL, "prompt_tokens": "612"})
    with pytest.raises(TraceError):
        validate_record({**CALL, "total_ms": True})
    with pytest.raises(TraceError):
        validate_record({**CALL, "stage": "think"})
    with pytest.raises(TraceError):
        validate_record({**CALL, "verify": {"passed": False}})


def test_writer_roundtrip(tmp_path):
    w = TraceWriter(tmp_path / "r001.jsonl")
    rec = {k: v for k, v in CALL.items() if k not in ("schema", "ts")}
    w.write(rec)
    w.write(rec)
    lines = list(read_traces(w.path))
    assert len(lines) == 2 and lines[0]["task_id"] == "HumanEvalFix/12"
