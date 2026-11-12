from relay import stages

REPLY = """Here is the fix:

```python
def add(a, b):
    return a + b
```
"""


def test_extract_fenced():
    assert stages.extract_code(REPLY, "add").startswith("def add")


def test_extract_prefers_block_defining_entry_point():
    text = "```python\nimport math\n```\n\n```python\ndef add(a, b):\n    return a + b\n```"
    assert "def add" in stages.extract_code(text, "add")


def test_extract_unterminated_fence():
    assert stages.extract_code("```python\ndef add(a, b):\n    return a + b\n", "add").startswith("def add")


def test_extract_bare_code_and_nothing():
    assert stages.extract_code("def add(a, b):\n    return a + b\n", "add")
    assert stages.extract_code("I think the bug is the plus sign.", "add") is None


def test_prompts_carry_inputs():
    d = stages.diagnose_prompt("CODE", "OUT")
    assert "CODE" in d and "OUT" in d and "root cause" in d
    p = stages.patch_prompt("CODE", "OUT", "DIAG", ("PREV", "PREVOUT"))
    for s in ("CODE", "OUT", "DIAG", "PREV", "PREVOUT", "```python"):
        assert s in p
    assert "Diagnosis" not in stages.patch_prompt("CODE", "OUT")
