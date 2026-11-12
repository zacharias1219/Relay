"""Routing policies P0 to P3. A policy is a function route(stage, state) -> backend_id.

`state` carries the patch attempt number (1 = first patch). A policy returns
None to skip a stage (P3 skips diagnose) or to stop retrying.

    Policy              Diagnose     Patch        On failure
    P0 all-large        groq-large   groq-large   retry on groq-large
    P1 all-small        groq-small   groq-small   retry on groq-small
    P2 by stage         groq-small   groq-large   retry on groq-large
    P3 cheap-then-verify skipped     local-small  verify, then groq-small, then groq-large

P3 can trust its escalation signal because the verifier is the real test suite:
escalation fires only on a real failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class State:
    attempt: int = 1                      # patch attempt about to run
    history: list[dict] = field(default_factory=list)


Route = Callable[[str, State], "str | None"]


def p0_all_large(stage: str, state: State) -> str | None:
    if stage == "diagnose":
        return "groq-large"
    return "groq-large" if state.attempt <= 2 else None


def p1_all_small(stage: str, state: State) -> str | None:
    if stage == "diagnose":
        return "groq-small"
    return "groq-small" if state.attempt <= 2 else None


def p2_by_stage(stage: str, state: State) -> str | None:
    if stage == "diagnose":
        return "groq-small"
    return "groq-large" if state.attempt <= 2 else None


P3_LADDER = ("local-small", "groq-small", "groq-large")


def p3_cheap_then_verify(stage: str, state: State) -> str | None:
    if stage == "diagnose":
        return None
    i = state.attempt - 1
    return P3_LADDER[i] if i < len(P3_LADDER) else None


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    route: Route
    description: str


POLICIES: dict[str, Policy] = {
    "P0": Policy("P0", "all-large", p0_all_large, "diagnose and patch on groq-large; retry on groq-large"),
    "P1": Policy("P1", "all-small", p1_all_small, "diagnose and patch on groq-small; retry on groq-small"),
    "P2": Policy("P2", "by stage", p2_by_stage, "diagnose on groq-small, patch on groq-large; retry on groq-large"),
    "P3": Policy("P3", "cheap-then-verify", p3_cheap_then_verify,
                 "no diagnosis; patch on local-small, verify, then escalate to groq-small, then groq-large"),
}


def get(policy_id: str) -> Policy:
    try:
        return POLICIES[policy_id.upper()]
    except KeyError:
        raise KeyError(f"unknown policy {policy_id!r}; choose from {', '.join(POLICIES)}") from None


def backends_used(policy_id: str) -> set[str]:
    p = get(policy_id)
    out = set()
    for a in range(1, 6):
        for stage in ("diagnose", "patch"):
            b = p.route(stage, State(attempt=a))
            if b:
                out.add(b)
    return out
