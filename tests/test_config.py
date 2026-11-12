from relay.config import Config
from analysis.failures import classify


def test_gpt_oss_backends_get_their_own_budgets():
    c = Config.load("config.yaml")
    assert c.backend("groq-large")["model_id"] == "openai/gpt-oss-120b"
    assert c.backend("groq-small")["model_id"] == "openai/gpt-oss-20b"
    assert c.max_tokens("patch", "groq-large") > c.max_tokens("patch", "local-small")
    assert c.max_tokens("patch") == c.max_tokens("patch", "local-small")


def test_truncation_is_named_in_the_failure_reason():
    v = {"passed": False, "status": "no_code", "test_ms": 0, "code_hash": None, "stderr_tail": ""}
    code, why = classify(v, {"finish_reason": "length", "reasoning_tokens": 900})
    assert code == "truncated" and "reasoning" in why
    assert classify(v, {"finish_reason": "stop"})[0] == "no_code"
