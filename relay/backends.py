"""Backend adapters. Every model call in Relay goes through `Backend.generate`.

    generate(prompt, max_tokens) -> GenResult(text, prompt_tokens,
                                              completion_tokens, ttft_ms, total_ms)

Groq and the llama.cpp server both speak the OpenAI chat-completions protocol,
so one streaming client serves both with a different base URL. Responses are
streamed so time-to-first-token is measured on the client.

`SimBackend` is a deterministic, offline *simulator* used only by the demo
config and the tests. It is labelled synthetic in every trace and on the page.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from relay.cache import GenResult
from relay.ratelimit import ModelLimiter, parse_duration


class BackendError(RuntimeError):
    pass


class OfflineError(BackendError):
    """Raised if a network backend is used while RELAY_OFFLINE=1 (replay)."""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class CallOutcome:
    result: GenResult
    queue_wait_ms: float


class Backend:
    kind = "base"

    def __init__(self, backend_id: str, cfg: dict[str, Any], temperature: float = 0.0):
        self.backend_id = backend_id
        self.cfg = cfg
        self.model_id = cfg["model_id"]
        self.temperature = temperature

    def params(self, max_tokens: int, rep: int = 0) -> dict[str, Any]:
        """Everything besides the prompt that can change the output (the cache key)."""
        p: dict[str, Any] = {"max_tokens": max_tokens, "temperature": self.temperature}
        if self.temperature > 0:
            p["rep"] = rep  # repeated noise runs must not hit each other's cache
        extra = self.cfg.get("extra_params")
        if extra:
            p["extra"] = extra
        return p

    def generate(self, prompt: str, max_tokens: int) -> CallOutcome:
        raise NotImplementedError


class OpenAICompatBackend(Backend):
    """Streaming chat-completions client for Groq and llama.cpp's server."""

    kind = "openai"

    def __init__(
        self,
        backend_id: str,
        cfg: dict[str, Any],
        temperature: float = 0.0,
        limiter: ModelLimiter | None = None,
        api_key: str | None = None,
        max_retries: int = 6,
        backoff_s: float = 2.0,
        session=None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        super().__init__(backend_id, cfg, temperature)
        self.kind = cfg.get("kind", "openai")
        self.base_url = cfg["base_url"].rstrip("/")
        self.limiter = limiter
        self.api_key = api_key
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.timeout_s = float(cfg.get("timeout_s", 120))
        self._session = session
        self._sleep = sleep

    @property
    def session(self):
        if os.environ.get("RELAY_OFFLINE") == "1":
            raise OfflineError(f"{self.backend_id}: network call attempted while RELAY_OFFLINE=1")
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _body(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.temperature == 0:
            body["seed"] = 0
        body.update(self.cfg.get("extra_params") or {})
        return body

    def generate(self, prompt: str, max_tokens: int) -> CallOutcome:
        prompt_est = estimate_tokens(prompt)
        est = self.limiter.reservation(prompt_est, max_tokens) if self.limiter else prompt_est + max_tokens
        queue_ms = 0.0
        attempt = 0
        while True:
            if self.limiter:
                queue_ms += self.limiter.acquire(est)
                queue_ms += self.limiter.wait_penalty()
            try:
                result, headers = self._stream_once(prompt, max_tokens)
            except _RetryableError as e:
                attempt += 1
                if attempt > self.max_retries:
                    raise BackendError(f"{self.backend_id}: giving up after {attempt} attempts: {e}") from e
                wait = e.retry_after if e.retry_after is not None else self.backoff_s * (2 ** (attempt - 1))
                if self.limiter:
                    self.limiter.settle(est, 0)  # the rejected request still counted against RPM
                    self.limiter.penalize(wait)
                    queue_ms += self.limiter.wait_penalty()
                else:
                    self._sleep(wait)
                    queue_ms += wait * 1000.0
                continue
            if self.limiter:
                self.limiter.settle(est, result.prompt_tokens + result.completion_tokens)
                self.limiter.observe_headers(headers)
                if not result.tokens_estimated:
                    self.limiter.learn(prompt_est, max_tokens, result.prompt_tokens, result.completion_tokens)
            return CallOutcome(result, queue_ms)

    def _stream_once(self, prompt: str, max_tokens: int) -> tuple[GenResult, dict[str, str]]:
        url = f"{self.base_url}/chat/completions"
        t0 = time.perf_counter()
        try:
            resp = self.session.post(
                url, headers=self._headers(), data=json.dumps(self._body(prompt, max_tokens)),
                stream=True, timeout=self.timeout_s,
            )
        except OfflineError:
            raise
        except Exception as e:  # connection errors are retryable
            raise _RetryableError(f"request failed: {e}") from e
        headers = dict(resp.headers)
        if resp.status_code == 429:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            raise _RetryableError("429 rate limited", parse_duration(ra) if ra else None)
        if resp.status_code >= 500:
            raise _RetryableError(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise BackendError(f"{self.backend_id}: HTTP {resp.status_code}: {resp.text[:500]}")

        ttft_ms: float | None = None
        parts: list[str] = []
        usage: dict[str, Any] | None = None
        finish: str | None = None
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise BackendError(f"{self.backend_id}: stream error {chunk['error']}")
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content") or ""
                thinking = delta.get("reasoning") or delta.get("reasoning_content") or ""
                if (piece or thinking) and ttft_ms is None:
                    ttft_ms = (time.perf_counter() - t0) * 1000.0
                if piece:
                    parts.append(piece)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
            u = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
            if u:
                usage = u
        total_ms = (time.perf_counter() - t0) * 1000.0
        text = "".join(parts)
        if usage and usage.get("prompt_tokens") is not None:
            pt, ct, est = int(usage["prompt_tokens"]), int(usage.get("completion_tokens") or 0), False
        else:
            pt, ct, est = estimate_tokens(prompt), estimate_tokens(text), True
        rt = None
        if usage:
            details = usage.get("completion_tokens_details") or {}
            rt = details.get("reasoning_tokens", usage.get("reasoning_tokens"))
        return GenResult(text, pt, ct, ttft_ms, total_ms, est, finish_reason=finish,
                         reasoning_tokens=None if rt is None else int(rt)), headers

    def list_models(self) -> list[str]:
        r = self.session.get(f"{self.base_url}/models", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))

    def server_props(self) -> dict[str, Any]:
        """llama.cpp's /props (build info, model path). Empty dict if unavailable."""
        try:
            root = self.base_url[: -len("/v1")] if self.base_url.endswith("/v1") else self.base_url
            r = self.session.get(f"{root}/props", timeout=5)
            if r.status_code == 200:
                d = r.json()
                keep = ("build_info", "model_path", "total_slots", "chat_template")
                out = {k: d.get(k) for k in keep if k in d}
                if "chat_template" in out and out["chat_template"]:
                    out["chat_template"] = out["chat_template"][:80] + "..."
                return out
        except Exception:
            pass
        return {}


class _RetryableError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Offline simulator (demo and tests only)
# ---------------------------------------------------------------------------


def _u01(*parts: Any) -> float:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


class SimBackend(Backend):
    """A deterministic stand-in for a model, used to exercise the pipeline offline.

    It is an *oracle simulator*: it recognises the task from the code in the
    prompt and returns the reference fix with probability `skill` (plus
    `diagnosis_boost` when a diagnosis is present, scaled by the bug type's
    `difficulty`), otherwise the buggy code back, unchanged or with a cosmetic
    edit. Timings come from configured TTFT and tokens-per-second, with jitter.
    Nothing it produces says anything about a real model.
    """

    kind = "sim"

    def __init__(self, backend_id: str, cfg: dict[str, Any], tasks: dict[str, dict], temperature: float = 0.0):
        super().__init__(backend_id, cfg, temperature)
        self.sim = cfg.get("sim") or {}
        self.tasks = list(tasks.values())

    def _find_task(self, prompt: str) -> dict | None:
        # Match on the buggy code itself: HumanEval reuses some function names.
        for t in self.tasks:
            if (t["declaration"] + t["buggy_solution"]).strip("\n") in prompt:
                return t
        return None

    def generate(self, prompt: str, max_tokens: int) -> CallOutcome:
        task = self._find_task(prompt)
        ph = hashlib.sha256(prompt.encode()).hexdigest()
        is_diagnose = "root cause" in prompt
        if task is None:
            text = "I could not find the function."
        elif is_diagnose:
            phrasing = ["looks like", "is probably", "seems to be"][int(_u01("phr", self.backend_id, ph) * 3)]
            text = (
                f"The bug in `{task['entry_point']}` {phrasing} {task.get('bug_type', 'a logic error')}, "
                f"which produces {task.get('failure_symptoms', 'incorrect output')} ({self.model_id})."
            )
        else:
            skill = float(self.sim.get("skill", 0.5))
            if "Diagnosis:" in prompt:
                skill += float(self.sim.get("diagnosis_boost", 0.0))
            difficulty = (self.sim.get("difficulty") or {}).get(task.get("bug_type", ""), 0.0)
            skill = min(0.98, max(0.02, skill - float(difficulty)))
            u = _u01(self.backend_id, self.model_id, task["task_id"], ph, self.temperature)
            if u < skill:
                code = task["declaration"] + task["canonical_solution"]
            else:
                code = task["declaration"] + task["buggy_solution"]
                if _u01("cosmetic", ph) < 0.5:
                    code = code.rstrip() + "\n"
            text = "```python\n" + code.strip("\n") + "\n```"
        pt = estimate_tokens(prompt)
        rt = None
        if self.sim.get("reasoning_tokens"):  # mimic a reasoning model: hidden tokens, billed as output
            rt = int(float(self.sim["reasoning_tokens"]) * (0.5 + _u01("rt", self.backend_id, ph)))
        ct = min(max_tokens, estimate_tokens(text) + (rt or 0))
        ttft = float(self.sim.get("ttft_ms", 300)) * (0.7 + 0.6 * _u01("ttft", self.backend_id, ph))
        tps = float(self.sim.get("tokens_per_s", 100)) * (0.8 + 0.4 * _u01("tps", self.backend_id, ph))
        total = ttft + ct / tps * 1000.0
        q = 0.0
        qcfg = self.sim.get("queue_ms")
        if qcfg and _u01("queue", self.backend_id, ph) < float(qcfg.get("p", 0)):
            q = float(qcfg.get("ms", 0)) * (0.5 + _u01("qms", ph))
        return CallOutcome(GenResult(text, pt, ct, round(ttft, 1), round(total, 1), True, finish_reason="stop",
                                     reasoning_tokens=rt), round(q, 1))


def build_backends(config, temperature: float, tasks: dict[str, dict] | None = None, limiters=None) -> dict[str, Backend]:
    out: dict[str, Backend] = {}
    limiters = limiters or {}
    rl = config["ratelimit"]
    for bid, b in config.backends.items():
        kind = b.get("kind")
        if kind == "sim":
            out[bid] = SimBackend(bid, b, tasks or {}, temperature)
        elif kind in ("groq", "llamacpp", "openai"):
            key = None
            if kind == "groq":
                key = os.environ.get(b.get("api_key_env", "GROQ_API_KEY"))
            out[bid] = OpenAICompatBackend(
                bid, b, temperature, limiter=limiters.get(bid), api_key=key,
                max_retries=int(rl.get("max_retries", 6)), backoff_s=float(rl.get("backoff_s", 2.0)),
            )
        else:
            raise ValueError(f"backend {bid}: unknown kind {kind!r}")
    return out


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so the key never has to be typed into a command."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
