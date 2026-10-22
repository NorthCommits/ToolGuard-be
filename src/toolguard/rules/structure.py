"""Structure rules (TG-SIZ): unusually large or deep tool definitions."""

from typing import Any

from toolguard.core.models import Category, Evidence, Severity, Tool
from toolguard.rules.base import Hit, Rule, RuleContext
from toolguard.rules.schema import properties
from toolguard.rules.text import MAX_SCAN_CHARS, TextField, describe_field, evidence

MAX_SCHEMA_DEPTH = 10
MAX_PROPERTY_COUNT = 100


def _depth(value: Any) -> int:
    """Nesting depth of JSON containers, computed without recursion."""
    deepest = 0
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            deepest = max(deepest, depth)
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            deepest = max(deepest, depth)
            stack.extend((child, depth + 1) for child in node)
    return deepest


def check_long_description(tool: Tool, ctx: RuleContext) -> Hit | None:
    length = len(tool.description)
    if length <= ctx.description_length_threshold:
        return None
    field = TextField("description", tool.description)
    return Hit(
        evidence=[evidence(field, ctx.description_length_threshold, length)],
        explanation=(
            f"The description is {length} characters long, above the "
            f"{ctx.description_length_threshold} character threshold. Long descriptions are "
            "harder to review and can bury instructions far from the start."
        ),
        suggested_action="Shorten the description to what a caller needs to know.",
    )


def check_unusual_structure(tool: Tool, ctx: RuleContext) -> Hit | None:
    depth = _depth(tool.input_schema)
    count = sum(1 for _ in properties(tool.input_schema))
    reasons = []
    if depth > MAX_SCHEMA_DEPTH:
        reasons.append(f"it is nested {depth} levels deep")
    if count > MAX_PROPERTY_COUNT:
        reasons.append(f"it defines {count} parameters")
    if not reasons:
        return None
    summary = f"depth {depth}, {count} parameters"
    return Hit(
        evidence=[Evidence(field="input_schema", text=summary, start=0, end=0, context=summary)],
        explanation=(
            "The input schema is unusual: " + "; ".join(reasons) + ". Large or deep schemas are "
            "hard to review and can hide text from checks."
        ),
        suggested_action="Simplify the schema, or review it by hand before trusting the tool.",
    )


def check_scan_truncated(tool: Tool, ctx: RuleContext) -> Hit | None:
    """Text past the per-tool scan cap is never checked, so say so instead of staying silent."""
    if ctx.truncated_field is None:
        return None
    # Offset where scanning stopped inside that field (0 when the cap hit before it began).
    stop = next((len(f.text) for f in ctx.fields if f.path == ctx.truncated_field), 0)
    note = f"scan stopped in {ctx.truncated_field} at the {MAX_SCAN_CHARS} character cap"
    return Hit(
        evidence=[
            Evidence(field=ctx.truncated_field, text=note, start=stop, end=stop, context=note)
        ],
        explanation=(
            f"The tool's text is longer than the {MAX_SCAN_CHARS} character scan cap, so part of "
            f"it was not checked, starting in {describe_field(ctx.truncated_field)}. Padding text "
            "to push instructions past a scanner's limit is a known way to hide them."
        ),
        suggested_action=(
            "Review the unscanned text by hand, and ask the publisher why the tool needs this "
            "much text."
        ),
    )


RULES = [
    Rule(
        "TG-SIZ-001",
        Category.STRUCTURE,
        Severity.LOW,
        "Description longer than the configured threshold",
        check_long_description,
    ),
    Rule(
        "TG-SIZ-002",
        Category.STRUCTURE,
        Severity.INFO,
        "Unusual schema depth or structure",
        check_unusual_structure,
    ),
    Rule(
        "TG-SIZ-003",
        Category.STRUCTURE,
        Severity.MEDIUM,
        "Tool text exceeded the scan cap and part of it was not scanned",
        check_scan_truncated,
    ),
]
