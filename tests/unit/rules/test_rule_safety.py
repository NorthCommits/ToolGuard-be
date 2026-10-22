"""Rules must stay fast on hostile input and must never raise on odd input.

The timing test is the real guard against catastrophic regex backtracking: it runs every rule
on inputs built to trigger it. The static check of pattern text is only a heuristic that
catches the most common dangerous shape early; passing it proves nothing on its own.
"""

import re
import time
from types import ModuleType

import pytest
from rule_helpers import make_tool, prop

from toolguard.rules import cross_tool, exfiltration, hidden, injection, naming, schema, structure
from toolguard.rules import text as text_module
from toolguard.rules.registry import RULES, run_rules
from toolguard.rules.text import MAX_SCAN_CHARS

# Generous so a slow CI runner does not flake; a backtracking pattern takes seconds or more.
TIME_BUDGET_SECONDS = 1.0
SIZE = 200_000

HOSTILE_TEXT = {
    "letters": "a" * SIZE,
    "spaces": " " * SIZE,
    "when_tool_used": "when the x tool is used always " * (SIZE // 31),
    "angle_brackets": "<" * SIZE,
    "override_prefix": "ignore all previous " * (SIZE // 20),
    "call_words": "call the " * (SIZE // 9),
    "at_signs": "a@" * (SIZE // 2),
    "url_prefix": "http://" * (SIZE // 7),
    "ssh_paths": "~/.ssh read " * (SIZE // 12),
    "near_base64": ("A" * 119 + "!") * (SIZE // 120),
    "zero_width": "\u200b" * SIZE,
    "dots": ". " * (SIZE // 2),
}


def hostile_tools() -> list[tuple[str, object]]:
    cases: list[tuple[str, object]] = [
        (name, make_tool(description=value)) for name, value in HOSTILE_TEXT.items()
    ]
    wide = {"type": "object", "properties": {f"p{i}": prop() for i in range(5_000)}}
    cases.append(("wide_schema", make_tool(schema=wide)))
    # Just under the ingestion nesting limit (MAX_NESTING_DEPTH, 64): two levels per step.
    deep: dict = prop()
    for _ in range(31):
        deep = {"type": "object", "properties": {"x": deep}}
    cases.append(("deep_schema", make_tool(schema=deep)))
    cases.append(("long_name", make_tool(name="n" * 10_000)))
    return cases


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.id)
def test_rules_are_fast_on_hostile_input(rule) -> None:
    for case, tool in hostile_tools():
        started = time.perf_counter()
        run_rules([tool], [rule])
        elapsed = time.perf_counter() - started
        assert elapsed < TIME_BUDGET_SECONDS, f"{rule.id} took {elapsed:.2f}s on {case}"


def test_many_tools_in_one_scan_stay_fast() -> None:
    tools = [make_tool(name=f"tool_number_{i}", description="Returns a value.") for i in range(500)]
    started = time.perf_counter()
    run_rules(tools)
    assert time.perf_counter() - started < 10 * TIME_BUDGET_SECONDS


# A quantified group that itself ends in an unbounded quantifier, such as (a+)+ or (?:x*)*.
NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*]\)[+*{]")
MODULES: list[ModuleType] = [
    cross_tool,
    exfiltration,
    hidden,
    injection,
    naming,
    schema,
    structure,
    text_module,
]


def test_no_nested_unbounded_quantifiers_heuristic() -> None:
    patterns = [
        (module.__name__, name, value.pattern)
        for module in MODULES
        for name, value in vars(module).items()
        if isinstance(value, re.Pattern)
    ]
    assert patterns
    offenders = [(m, n) for m, n, p in patterns if NESTED_QUANTIFIER.search(p)]
    assert not offenders


ODD_TOOLS = [
    make_tool(name="", description=""),
    make_tool(name="x", description="\ud800 lone \udfff surrogates \U0010ffff\x00\x7f"),
    make_tool(
        name="emoji_\U0001f600", title="\u202e\u2066", description="\u0645\u0631\u062d\u0628\u0627"
    ),
    make_tool(
        schema={
            "type": 7,
            "properties": {
                "a": None,
                "b": 5,
                "c": [],
                "d": {"type": ["string", None], "enum": [None, 1, {"x": "y"}], "default": {}},
                "e": True,
                "f": {"properties": "not a dict", "items": [1, 2], "anyOf": "x"},
                "": {"description": 42},
            },
            "additionalProperties": "yes",
            "required": "everything",
        },
        annotations={"readOnlyHint": "yes", "destructiveHint": None, "title": 5},
    ),
    make_tool(description="x" * (MAX_SCAN_CHARS * 2)),
]


@pytest.mark.parametrize("tool", ODD_TOOLS, ids=range(len(ODD_TOOLS)))
def test_rules_never_raise_on_odd_input(tool) -> None:
    run_rules([tool, tool])
