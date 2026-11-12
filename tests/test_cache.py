import pytest

from relay.cache import Cache, CacheMiss, GenResult, request_key


def _res():
    return GenResult("hello", 10, 2, 50.0, 120.0)


def test_miss_then_hit(tmp_path):
    c = Cache(tmp_path)
    calls = []
    r1, hit1 = c.fetch("b", "m", "p", {"max_tokens": 8}, lambda: calls.append(1) or _res())
    r2, hit2 = c.fetch("b", "m", "p", {"max_tokens": 8}, lambda: calls.append(1) or _res())
    assert (hit1, hit2) == (False, True) and len(calls) == 1
    assert r2.text == "hello" and r2.total_ms == 120.0


def test_key_covers_model_prompt_params():
    k = request_key("m", "p", {"t": 0})
    assert k != request_key("m2", "p", {"t": 0})
    assert k != request_key("m", "p2", {"t": 0})
    assert k != request_key("m", "p", {"t": 0.2})


def test_replay_fails_loudly_on_miss(tmp_path):
    c = Cache(tmp_path, replay=True)
    with pytest.raises(CacheMiss):
        c.fetch("b", "m", "p", {}, lambda: pytest.fail("replay must never call the backend"))


def test_replay_serves_hits(tmp_path):
    Cache(tmp_path).fetch("b", "m", "p", {}, _res)
    r, hit = Cache(tmp_path, replay=True).fetch("b", "m", "p", {}, lambda: pytest.fail("no call"))
    assert hit and r.text == "hello"
