"""Hash cache for model responses, with a replay mode that never calls out.

Every response is stored under sha256(model id, prompt, parameters). In replay
mode the cache serves hits only and raises CacheMiss on anything else, so a
replayed run can never make an API call by accident.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from relay.trace import now_iso, sha256_text


class CacheMiss(RuntimeError):
    """Raised in replay mode when a request has no cached response."""


@dataclass
class GenResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    ttft_ms: float | None
    total_ms: float
    tokens_estimated: bool = False
    queue_wait_ms: float = 0.0  # time spent in our own rate limiter / retry-after

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GenResult":
        return cls(
            text=d["text"],
            prompt_tokens=int(d["prompt_tokens"]),
            completion_tokens=int(d["completion_tokens"]),
            ttft_ms=d.get("ttft_ms"),
            total_ms=float(d["total_ms"]),
            tokens_estimated=bool(d.get("tokens_estimated", False)),
            queue_wait_ms=float(d.get("queue_wait_ms", 0.0)),
        )


def request_key(model_id: str, prompt: str, params: dict[str, Any]) -> str:
    payload = json.dumps({"model_id": model_id, "prompt": prompt, "params": params}, sort_keys=True, ensure_ascii=False)
    return sha256_text(payload)


class Cache:
    def __init__(self, root: str | os.PathLike, replay: bool = False):
        self.root = Path(root)
        self.replay = replay
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> GenResult | None:
        p = self._path(key)
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            entry = json.load(f)
        return GenResult.from_dict(entry["response"])

    def put(self, key: str, request: dict[str, Any], result: GenResult) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {"key": key, "created_at": now_iso(), "request": request, "response": result.to_dict()}
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)

    def fetch(
        self,
        backend_id: str,
        model_id: str,
        prompt: str,
        params: dict[str, Any],
        call: Callable[[], GenResult],
    ) -> tuple[GenResult, bool]:
        """Return (result, cache_hit). `call` runs only on a miss outside replay."""
        key = request_key(model_id, prompt, params)
        hit = self.get(key)
        if hit is not None:
            self.hits += 1
            return hit, True
        if self.replay:
            raise CacheMiss(
                f"replay mode: no cached response for backend={backend_id} model={model_id} "
                f"key={key[:16]}... (prompt sha256 {sha256_text(prompt)[:16]}...). "
                "Replay serves only from the cache; run without --replay to make the call."
            )
        self.misses += 1
        result = call()
        self.put(key, {"backend": backend_id, "model_id": model_id, "params": params, "prompt": prompt}, result)
        return result, False

    def iter_entries(self):
        if not self.root.exists():
            return
        for p in sorted(self.root.glob("*/*.json")):
            with open(p, encoding="utf-8") as f:
                yield json.load(f)
