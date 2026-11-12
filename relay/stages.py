"""The stages of one bug-fix task. Each stage is a plain function.

1. diagnose  buggy code + failing test output -> short cause in plain text
2. patch     code + test output (+ diagnosis) -> full corrected function
3. verify    not a model: run the tests in the sandbox
4. retry     a patch whose prompt carries the failed attempt and its output
"""

from __future__ import annotations

import ast
import re

from relay.sandbox import Sandbox, VerifyResult

FENCE = "```"


def buggy_code(task: dict) -> str:
    return (task["declaration"] + task["buggy_solution"]).strip("\n")


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def diagnose_prompt(code: str, test_output: str, max_out: int = 1500) -> str:
    return (
        "A Python function fails its tests.\n\n"
        f"Code:\n{FENCE}python\n{code}\n{FENCE}\n\n"
        f"Test output:\n{FENCE}\n{_clip(test_output, max_out)}\n{FENCE}\n\n"
        "In at most three sentences, state the root cause of the bug. Do not write code."
    )


def patch_prompt(
    code: str,
    test_output: str,
    diagnosis: str | None = None,
    previous: tuple[str, str] | None = None,
    max_out: int = 1500,
    max_diag: int = 800,
) -> str:
    """`previous` is (failed_patch_code, its_test_output) for a retry."""
    p = (
        "Fix the bug in this Python function.\n\n"
        f"Code:\n{FENCE}python\n{code}\n{FENCE}\n\n"
        f"Test output:\n{FENCE}\n{_clip(test_output, max_out)}\n{FENCE}\n"
    )
    if diagnosis:
        p += f"\nDiagnosis: {_clip(diagnosis, max_diag)}\n"
    if previous:
        prev_code, prev_out = previous
        p += (
            "\nA previous fix was tried and still fails.\n"
            f"Previous fix:\n{FENCE}python\n{prev_code or '(no code was produced)'}\n{FENCE}\n"
            f"Its test output:\n{FENCE}\n{_clip(prev_out, max_out)}\n{FENCE}\n"
        )
    p += (
        "\nReturn the complete corrected function, with any imports it needs, "
        "in a single ```python code block. No explanation."
    )
    return p


_BLOCK = re.compile(r"```[ \t]*(?:python|py|Python)?[ \t]*\n(.*?)```", re.DOTALL)


def extract_code(text: str, entry_point: str | None = None) -> str | None:
    """Pull the corrected function out of a model reply.

    Prefers a fenced block that defines `entry_point`; falls back to the first
    fenced block, then to the whole reply if it parses as Python. Returns None
    when nothing usable is found (verify then records status `no_code`).
    """
    if not text:
        return None
    blocks = [b.strip("\n") for b in _BLOCK.findall(text)]
    if not blocks:
        # an unterminated fence (output cut at max_tokens)
        m = re.search(r"```[ \t]*(?:python|py)?[ \t]*\n(.*)$", text, re.DOTALL)
        if m:
            blocks = [m.group(1).strip("\n")]
    if entry_point:
        for b in blocks:
            if re.search(rf"def\s+{re.escape(entry_point)}\s*\(", b):
                return b
    if blocks:
        return blocks[0]
    try:
        ast.parse(text)
        if entry_point and f"def {entry_point}" in text:
            return text.strip("\n")
    except SyntaxError:
        pass
    return None


def verify(sandbox: Sandbox, code: str | None, task: dict) -> VerifyResult:
    return sandbox.run(code, task)
