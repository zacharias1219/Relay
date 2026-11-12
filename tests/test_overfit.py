from analysis.overfit import perturbed_inputs, probe, asserted_inputs
from relay.sandbox import Sandbox

TASK = {
    "declaration": "def add(a, b):\n",
    "canonical_solution": "    return a + b\n",
    "test": "def check(add):\n    assert add(1, 2) == 3\n    assert add(5, 5) == 10\ncheck(add)\n",
    "entry_point": "add",
}


def test_inputs_from_asserts():
    assert asserted_inputs(TASK["test"]) == [(1, 2), (5, 5)]
    assert perturbed_inputs([(1, 2)], seed=0)


def test_probe_flags_a_hardcoded_patch():
    hard = "def add(a, b):\n    return {(1, 2): 3, (5, 5): 10}.get((a, b), 0)\n"
    res = probe(Sandbox(), TASK, hard, seed=0)
    assert res["disagreements"] > 0


def test_probe_passes_a_real_fix():
    res = probe(Sandbox(), TASK, "def add(a, b):\n    return b + a\n", seed=0)
    assert res["checked"] > 0 and res["disagreements"] == 0
