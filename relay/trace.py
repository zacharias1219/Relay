"""JSONL trace writer and schema.

One JSON line per model call or verify step, appended to traces/<run_id>.jsonl.
Two bookkeeping record types frame the calls: `run_meta` (first line of a run)
and `task_end` (closes one task). Analysis recomputes every metric from the
`call` and `verify` lines; `task_end` exists so interrupted runs can resume.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

_NUM = (int, float)
_OPT_NUM = (int, float, type(None))
_OPT_STR = (str, type(None))

# field -> allowed types. Every listed field is required on that record type.
CALL_FIELDS: dict[str, tuple] = {
    "record": (str,),
    "schema": (int,),
    "ts": (str,),
    "run_id": (str,),
    "task_id": (str,),
    "task_run_uid": (str,),
    "policy": (str,),
    "stage": (str,),
    "attempt": (int,),
    "backend": (str,),
    "backend_kind": (str,),
    "model_id": (str,),
    "temperature": _NUM,
    "rep": (int,),
    "queue_wait_ms": _NUM,
    "ttft_ms": _OPT_NUM,
    "total_ms": _NUM,
    "prompt_tokens": (int,),
    "completion_tokens": (int,),
    "tokens_estimated": (bool,),
    "cost_usd_list": _NUM,
    "cache_hit": (bool,),
    "output_hash": (str,),
    "verify": (dict, type(None)),
}

VERIFY_FIELDS: dict[str, tuple] = {
    "record": (str,),
    "schema": (int,),
    "ts": (str,),
    "run_id": (str,),
    "task_id": (str,),
    "task_run_uid": (str,),
    "policy": (str,),
    "stage": (str,),
    "attempt": (int,),
    "verify": (dict,),
}

VERIFY_RESULT_FIELDS: dict[str, tuple] = {
    "passed": (bool,),
    "status": (str,),
    "test_ms": _NUM,
    "code_hash": _OPT_STR,
    "stderr_tail": (str,),
}

TASK_END_FIELDS: dict[str, tuple] = {
    "record": (str,),
    "schema": (int,),
    "ts": (str,),
    "run_id": (str,),
    "task_id": (str,),
    "task_run_uid": (str,),
    "policy": (str,),
    "passed": (bool,),
    "model_calls": (int,),
}

RUN_META_FIELDS: dict[str, tuple] = {
    "record": (str,),
    "schema": (int,),
    "ts": (str,),
    "run_id": (str,),
    "policy": (str,),
    "temperature": _NUM,
    "rep": (int,),
    "synthetic": (bool,),
}

PROFILE_FIELDS: dict[str, tuple] = {
    "record": (str,),
    "schema": (int,),
    "ts": (str,),
    "run_id": (str,),
    "prompt_id": (str,),
    "backend": (str,),
    "backend_kind": (str,),
    "model_id": (str,),
    "ttft_ms": _OPT_NUM,
    "total_ms": _NUM,
    "prompt_tokens": (int,),
    "completion_tokens": (int,),
    "tokens_estimated": (bool,),
}

SCHEMAS = {
    "call": CALL_FIELDS,
    "verify": VERIFY_FIELDS,
    "task_end": TASK_END_FIELDS,
    "run_meta": RUN_META_FIELDS,
    "profile": PROFILE_FIELDS,
}

STAGES = {"diagnose", "patch", "verify"}


class TraceError(ValueError):
    pass


def validate_record(rec: dict[str, Any]) -> None:
    kind = rec.get("record")
    if kind not in SCHEMAS:
        raise TraceError(f"unknown record type {kind!r}")
    for field, types in SCHEMAS[kind].items():
        if field not in rec:
            raise TraceError(f"{kind} record missing field {field!r}")
        val = rec[field]
        # bool is an int subclass; refuse bools where numbers are expected
        if isinstance(val, bool) and bool not in types:
            raise TraceError(f"{kind}.{field}: bool where {types} expected")
        if not isinstance(val, types):
            raise TraceError(f"{kind}.{field}: {type(val).__name__} not in {types}")
    if kind in ("call", "verify"):
        if rec["stage"] not in STAGES:
            raise TraceError(f"bad stage {rec['stage']!r}")
        v = rec.get("verify")
        if v is not None:
            for field, types in VERIFY_RESULT_FIELDS.items():
                if field not in v:
                    raise TraceError(f"verify result missing {field!r}")
                if not isinstance(v[field], types) or (isinstance(v[field], bool) and bool not in types):
                    raise TraceError(f"verify.{field}: bad type {type(v[field]).__name__}")
    if kind == "call" and rec["stage"] == "verify":
        raise TraceError("a call record cannot have stage 'verify'")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TraceWriter:
    """Append-only, line-buffered JSONL writer that validates every record."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rec: dict[str, Any]) -> dict[str, Any]:
        rec = {"schema": SCHEMA_VERSION, "ts": now_iso(), **rec}
        validate_record(rec)
        line = json.dumps(rec, sort_keys=False, separators=(",", ":"), ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return rec


def read_traces(path: str | os.PathLike, validate: bool = True) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:  # a torn final line from a crash
                raise TraceError(f"{path}:{lineno}: invalid JSON ({e})") from e
            if validate:
                try:
                    validate_record(rec)
                except TraceError as e:
                    raise TraceError(f"{path}:{lineno}: {e}") from e
            yield rec


def new_uid() -> str:
    return hashlib.sha1(f"{time.time_ns()}-{os.getpid()}-{os.urandom(4).hex()}".encode()).hexdigest()[:12]
