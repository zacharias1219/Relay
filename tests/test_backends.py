import json

import pytest

from relay.backends import BackendError, OpenAICompatBackend, OfflineError
from relay.ratelimit import ModelLimiter

CFG = {"kind": "groq", "base_url": "https://example.invalid/openai/v1", "model_id": "m-small", "timeout_s": 5}


class Resp:
    def __init__(self, status, lines=(), headers=None, text=""):
        self.status_code = status
        self._lines = lines
        self.headers = headers or {}
        self.text = text

    def iter_lines(self, decode_unicode=True):
        yield from self._lines


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []

    def post(self, url, headers, data, stream, timeout):
        self.bodies.append(json.loads(data))
        return self.responses.pop(0)


def sse(*chunks):
    return [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]


OK = Resp(200, sse(
    {"choices": [{"delta": {"role": "assistant"}}]},
    {"choices": [{"delta": {"content": "```python\n"}}]},
    {"choices": [{"delta": {"content": "x = 1\n```"}}]},
    {"choices": [], "x_groq": {"usage": {"prompt_tokens": 42, "completion_tokens": 7}}},
))


def test_streaming_text_usage_and_ttft():
    s = Session([OK])
    b = OpenAICompatBackend("groq-small", CFG, session=s)
    out = b.generate("fix it", 64)
    assert out.result.text == "```python\nx = 1\n```"
    assert (out.result.prompt_tokens, out.result.completion_tokens) == (42, 7)
    assert not out.result.tokens_estimated
    assert out.result.ttft_ms is not None and out.result.ttft_ms <= out.result.total_ms
    body = s.bodies[0]
    assert body["temperature"] == 0 and body["stream"] and body["model"] == "m-small"


def test_429_waits_retry_after_and_counts_queue():
    slept = []
    s = Session([Resp(429, headers={"retry-after": "3"}), OK])
    b = OpenAICompatBackend("groq-small", CFG, session=s, sleep=slept.append)
    out = b.generate("fix it", 64)
    assert slept == [3.0] and out.queue_wait_ms == pytest.approx(3000)


def test_429_with_limiter_is_queue_wait():
    t = [0.0]
    lim = ModelLimiter("m", rpm=100, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    s = Session([Resp(429, headers={"retry-after": "2"}), OK])
    out = OpenAICompatBackend("groq-small", CFG, limiter=lim, session=s).generate("p", 8)
    assert out.queue_wait_ms == pytest.approx(2000)


def test_client_error_is_not_retried():
    s = Session([Resp(400, text="bad model")])
    with pytest.raises(BackendError):
        OpenAICompatBackend("groq-small", CFG, session=s).generate("p", 8)


def test_usage_missing_falls_back_to_estimate():
    s = Session([Resp(200, sse({"choices": [{"delta": {"content": "hello world"}}]}))])
    out = OpenAICompatBackend("local-small", {**CFG, "kind": "llamacpp"}, session=s).generate("p" * 40, 8)
    assert out.result.tokens_estimated and out.result.prompt_tokens == 10


def test_offline_guard(monkeypatch):
    monkeypatch.setenv("RELAY_OFFLINE", "1")
    with pytest.raises(OfflineError):
        OpenAICompatBackend("groq-small", CFG).generate("p", 8)
