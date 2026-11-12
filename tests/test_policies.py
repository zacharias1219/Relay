import pytest

from relay.policies import POLICIES, State


def path(pid):
    p = POLICIES[pid]
    diag = p.route("diagnose", State(1))
    patches = []
    a = 1
    while (b := p.route("patch", State(a))) is not None:
        patches.append(b)
        a += 1
    return diag, patches


def test_routing_table_matches_spec():
    assert path("P0") == ("groq-large", ["groq-large", "groq-large"])
    assert path("P1") == ("groq-small", ["groq-small", "groq-small"])
    assert path("P2") == ("groq-small", ["groq-large", "groq-large"])
    assert path("P3") == (None, ["local-small", "groq-small", "groq-large"])


@pytest.mark.parametrize("pid", list(POLICIES))
def test_never_more_than_four_calls(pid):
    diag, patches = path(pid)
    assert (diag is not None) + len(patches) <= 4
