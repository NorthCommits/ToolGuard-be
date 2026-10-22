"""Small builders for rule tests."""

from typing import Any

from toolguard.core.models import Finding, Tool
from toolguard.ingest.manifest import build_tool
from toolguard.rules.registry import RULES_BY_ID, run_rules

EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
MISSING = object()


def make_tool(
    name: str = "sample_tool",
    description: str = "",
    schema: Any = EMPTY_SCHEMA,
    annotations: dict[str, Any] | None = None,
    title: str | None = None,
) -> Tool:
    return build_tool(
        "test-server",
        name,
        title,
        description,
        None if schema is MISSING else schema,
        annotations,
    )


def prop(**fields: Any) -> dict[str, Any]:
    """A property schema with a type and description unless overridden."""
    return {"type": "string", "description": "A value.", **fields}


def schema_with(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "additionalProperties": False}


def findings(rule_id: str, *tools: Tool, **kwargs: Any) -> list[Finding]:
    """Findings of one rule for the first tool, with all tools as the scan."""
    found = run_rules(list(tools), [RULES_BY_ID[rule_id]], **kwargs)
    return [f for f in found if f.id.startswith("0:")]


def fires(rule_id: str, *tools: Tool, **kwargs: Any) -> bool:
    return bool(findings(rule_id, *tools, **kwargs))
