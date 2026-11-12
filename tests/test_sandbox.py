from relay.sandbox import Sandbox

T = {"declaration": "", "test": "def check(f):\n    assert f(2) == 4\n", "entry_point": "f"}


def test_known_good_and_known_bad_on_real_tasks(tasks):
    sbx = Sandbox(timeout_s=10)
    for t in list(tasks.values())[:5]:
        assert not sbx.run(t["declaration"] + t["buggy_solution"], t).passed
        assert sbx.run(t["declaration"] + t["canonical_solution"], t).passed


def test_statuses():
    sbx = Sandbox(timeout_s=2)
    assert sbx.run("def f(x):\n    return x * 2\n", T).status == "passed"
    r = sbx.run("def f(x):\n    return x + 3\n", T)
    assert r.status == "failed" and "AssertionError" in r.stderr_tail
    assert sbx.run("def f(x:\n", T).status == "error"
    assert sbx.run("def f(x):\n    while True: pass\n", T).status == "timeout"
    assert sbx.run(None, T).status == "no_code"


def test_check_call_is_appended_when_missing():
    # 'def check(f)' contains the text 'check(f)'; the call must still be appended
    assert Sandbox().run("def f(x):\n    return 0\n", T).status == "failed"


def test_network_is_blocked():
    code = "import socket\ndef f(x):\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n    return x * 2\n"
    r = Sandbox(timeout_s=5).run(code, T)
    assert not r.passed


def test_yaml_off_means_off():
    assert Sandbox(network=False).network == "off"
    assert Sandbox(network="off").network == "off"
