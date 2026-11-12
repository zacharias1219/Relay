"""Overfit probe: does a passing patch agree with the reference off the test inputs?

HumanEvalFix shows the model the same tests that verify it, so a patch can pass
by special-casing what the tests check. For every passing patch we take the
argument tuples the tests call the function with, perturb them (ints +-1,
strings and lists reversed or extended, ...) and compare the patch with the
reference solution in the sandbox. Disagreements on inputs the reference
accepts are *candidates* for "passes the tests but is plainly wrong"; a human
confirms or dismisses each one in analysis/annotations.yaml.
"""

from __future__ import annotations

import ast
import json
import random

from relay.sandbox import SOCKET_GUARD, Sandbox

MAX_INPUTS = 40

PROBE_TEMPLATE = r'''
import copy, json, math, signal

REF_SRC = {ref!r}
CAND_SRC = {cand!r}
ENTRY = {entry!r}
INPUTS = {inputs}

class _T(Exception):
    pass

def _alarm(*_):
    raise _T()

signal.signal(signal.SIGALRM, _alarm)

def _load(src):
    ns = {{}}
    exec(src, ns)
    return ns[ENTRY]

ref, cand = _load(REF_SRC), _load(CAND_SRC)

def _call(f, args):
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        return ("ok", f(*copy.deepcopy(args)))
    except _T:
        return ("timeout", None)
    except Exception as e:
        return ("raise", type(e).__name__)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9) or (a != a and b != b)
        except TypeError:
            return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return type(a) == type(b) and len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b

checked, diffs = 0, []
for args in INPUTS:
    r = _call(ref, args)
    if r[0] != "ok":
        continue  # outside the reference's domain; not evidence
    c = _call(cand, args)
    checked += 1
    if c[0] != "ok" or not _same(r[1], c[1]):
        diffs.append({{"args": repr(args)[:160], "reference": repr(r[1])[:80],
                      "patch": (repr(c[1]) if c[0] == "ok" else c[0] + ":" + str(c[1]))[:80]}})
print(json.dumps({{"checked": checked, "disagreements": len(diffs), "examples": diffs[:3]}}))
'''


def asserted_inputs(test_src: str) -> list[tuple]:
    """Argument tuples from calls to the candidate inside `def check(candidate)`."""
    try:
        tree = ast.parse(test_src)
    except SyntaxError:
        return []
    out = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check"]:
        if not fn.args.args:
            continue
        cand = fn.args.args[0].arg
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == cand:
                if node.keywords:
                    continue
                try:
                    out.append(tuple(ast.literal_eval(a) for a in node.args))
                except Exception:
                    continue
    seen, uniq = set(), []
    for a in out:
        k = repr(a)
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq


def _perturb(v, rng: random.Random):
    if isinstance(v, bool):
        return [not v]
    if isinstance(v, int):
        return [v + 1, v - 1, v * 2 + 3]
    if isinstance(v, float):
        return [v * 1.5 + 0.25, -v]
    if isinstance(v, str):
        outs = [v[::-1], v + v[:1], v[1:]] if v else ["a"]
        return outs
    if isinstance(v, list):
        if not v:
            return [[]]
        outs = [list(reversed(v)), v + [v[0]], v[:-1]]
        sh = list(v)
        rng.shuffle(sh)
        outs.append(sh)
        return outs
    if isinstance(v, tuple):
        return [tuple(reversed(v))]
    return []


def perturbed_inputs(inputs: list[tuple], seed: int) -> list[tuple]:
    rng = random.Random(seed)
    out, seen = [], {repr(a) for a in inputs}
    for args in inputs:
        for i, v in enumerate(args):
            for nv in _perturb(v, rng):
                new = args[:i] + (nv,) + args[i + 1:]
                k = repr(new)
                if k not in seen:
                    seen.add(k)
                    out.append(new)
    rng.shuffle(out)
    return out[:MAX_INPUTS]


def probe(sandbox: Sandbox, task: dict, patch_code: str, seed: int) -> dict | None:
    inputs = perturbed_inputs(asserted_inputs(task["test"]), seed)
    if not inputs:
        return None
    ref = task["declaration"] + task["canonical_solution"]
    imports = "\n".join(l for l in task["declaration"].splitlines() if l.startswith(("import ", "from ")))
    program = SOCKET_GUARD + PROBE_TEMPLATE.format(
        ref=ref, cand=imports + "\n" + patch_code, entry=task["entry_point"], inputs=repr(inputs))
    rc, out, err, _ = sandbox.run_program(program, timeout_s=max(sandbox.timeout_s, 30))
    if rc != 0:
        return {"checked": 0, "disagreements": 0, "examples": [], "error": err.strip()[-200:]}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return None
