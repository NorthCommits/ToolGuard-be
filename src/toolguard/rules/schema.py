"""Schema and hint rules (TG-SCH): parameters that accept too much, and misleading hints.

Annotations are claims made by the server, not facts. Per the MCP specification (2026-07-28),
readOnlyHint defaults to false and destructiveHint defaults to true, and clients must not rely
on hints from untrusted servers. TG-SCH-004 reports a destructive-sounding tool that gives no
hint; TG-SCH-005 reports a hint that contradicts what the tool appears to do.
"""

import re
from collections.abc import Iterator
from itertools import islice
from typing import Any

from toolguard.core.models import Category, Evidence, Severity, Tool
from toolguard.rules.base import Hit, Rule, RuleContext
from toolguard.rules.text import MAX_EVIDENCE_PER_FINDING, TextField, evidence, json_evidence

MAX_PROPERTIES = 2_000  # stop walking a pathological schema; TG-SIZ-002 reports its size

# Names whose text is run or evaluated (TG-SCH-001, medium). cmd is kept as an abbreviation.
# "function" added after calibration (Playwright browser_evaluate takes JavaScript).
EXECUTION_NAMES = frozenset(
    ["command", "cmd", "shell", "script", "code", "eval", "exec", "expression", "sql", "function"]
)
# Names whose text points at a resource to open or fetch (TG-SCH-006, low). "query" is not in
# either set: a free-text search query is normal (calibration on two publishers, M2).
LOCATOR_NAMES = frozenset(
    [
        "path",
        "filepath",
        "file",
        "filename",
        "dir",
        "directory",
        "folder",
        "url",
        "uri",
        "endpoint",
        "host",
    ]
)
DESTRUCTIVE_WORDS = frozenset(
    [
        "delete",
        "remove",
        "drop",
        "destroy",
        "purge",
        "wipe",
        "erase",
        "truncate",
        "overwrite",
        "terminate",
        "kill",
        "reset",
        "uninstall",
        "revoke",
        "rm",
        "del",
    ]
)
# Words that mean the tool changes something, so readOnlyHint true would be a false claim.
MODIFYING_WORDS = DESTRUCTIVE_WORDS | frozenset(
    [
        "write",
        "create",
        "update",
        "insert",
        "upload",
        "send",
        "post",
        "put",
        "move",
        "rename",
        "append",
        "modify",
        "edit",
        "patch",
        "save",
        "set",
        "publish",
        "deploy",
    ]
)
DESTRUCTIVE_VERB = re.compile(
    r"\b(?:deletes?|removes?|drops?|destroys?|purges?|wipes?|erases?|truncates?|overwrites?"
    r"|terminates?|permanently)\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_TYPED_BY = ("type", "$ref", "anyOf", "oneOf", "allOf", "enum", "const")


def name_tokens(name: str) -> list[tuple[str, int, int]]:
    """Lowercase words in a tool or parameter name, with their positions."""
    return [(m.group(0).lower(), m.start(), m.end()) for m in _TOKEN.finditer(name)]


def properties(schema: dict[str, Any]) -> Iterator[tuple[str, str, Any]]:
    """(name, path, property schema) for every property, nested ones included."""
    stack: list[tuple[str, Any]] = [("input_schema", schema)]
    seen = 0
    while stack and seen < MAX_PROPERTIES:
        path, node = stack.pop()
        if not isinstance(node, dict):
            continue
        props = node.get("properties")
        if isinstance(props, dict):
            for name, sub in props.items():
                seen += 1
                sub_path = f"{path}/properties/{str(name).replace('~', '~0').replace('/', '~1')}"
                yield str(name), sub_path, sub
                stack.append((sub_path, sub))
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                stack.append((f"{path}/{key}", node[key]))
        for key in ("anyOf", "oneOf", "allOf"):
            if isinstance(node.get(key), list):
                stack.extend((f"{path}/{key}/{i}", s) for i, s in enumerate(node[key]))


def _object_schemas(schema: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    yield "input_schema", schema
    for _, path, sub in properties(schema):
        if isinstance(sub, dict):
            yield path, sub


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:3])
    return shown + (f" and {len(items) - 3} more" if len(items) > 3 else "")


def _matches(name: str, words: frozenset[str]) -> bool:
    """A word of the name is in words, singular or plural (paths, urls, commands)."""
    return any(
        token in words or (token.endswith("s") and token[:-1] in words)
        for token, _, _ in name_tokens(name)
    )


def _is_constrained(node: dict[str, Any]) -> bool:
    return any(key in node for key in ("enum", "const", "pattern"))


def _accepts_free_text(sub: Any) -> bool:
    """A string (or untyped) value, or an array of them, with no enum, const, or pattern."""
    node = sub if isinstance(sub, dict) else {}
    kind = node.get("type")
    if kind == "array" or (isinstance(kind, list) and "array" in kind):
        items = node.get("items") if isinstance(node.get("items"), dict) else {}
        return not _is_constrained(node) and _accepts_free_text(items)
    is_text = kind is None or kind == "string" or (isinstance(kind, list) and "string" in kind)
    return is_text and not _is_constrained(node)


def _free_form(tool: Tool, words: frozenset[str]) -> tuple[list[Evidence], list[str]]:
    found: list[Evidence] = []
    names: list[str] = []
    for name, path, sub in properties(tool.input_schema):
        if _matches(name, words) and _accepts_free_text(sub):
            if len(found) < MAX_EVIDENCE_PER_FINDING:
                found.append(json_evidence(path, sub))
            names.append(name)
    return found, names


def check_free_form(tool: Tool, ctx: RuleContext) -> Hit | None:
    found, names = _free_form(tool, EXECUTION_NAMES)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The parameter named {_names(names)} accepts any text that looks like it will be run "
            "or evaluated, such as a command, code, or SQL."
        ),
        suggested_action=(
            "Restrict the parameter with an enum or a pattern, and validate it on the server."
        ),
    )


def check_free_form_locator(tool: Tool, ctx: RuleContext) -> Hit | None:
    found, names = _free_form(tool, LOCATOR_NAMES)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The parameter named {_names(names)} accepts any path, file name, or address, so "
            "the tool can be pointed at anything the server can reach."
        ),
        suggested_action=(
            "Check that the server confines the value, for example to allowed folders or hosts, "
            "and describe the limit in the schema with a pattern or enum where possible."
        ),
    )


def check_missing_type_or_description(tool: Tool, ctx: RuleContext) -> Hit | None:
    if tool.input_schema_missing:
        return Hit(
            evidence=[Evidence(field="input_schema", text="(no input schema)", start=0, end=0)],
            explanation=(
                "The tool has no input schema, so nothing describes or limits what it accepts."
            ),
            suggested_action="Add an input schema with a type and description for each parameter.",
        )
    found: list[Evidence] = []
    names: list[str] = []
    for name, path, sub in properties(tool.input_schema):
        node = sub if isinstance(sub, dict) else {}
        description = node.get("description")
        has_description = isinstance(description, str) and description.strip()
        if not any(key in node for key in _TYPED_BY) or not has_description:
            if len(found) < MAX_EVIDENCE_PER_FINDING:
                found.append(json_evidence(path, sub))
            names.append(name)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The parameter named {_names(names)} has no type or no description, so the AI model "
            "has to guess what to send."
        ),
        suggested_action="Give every parameter a type and a short description.",
    )


def check_additional_properties(tool: Tool, ctx: RuleContext) -> Hit | None:
    open_schemas = (
        (path, node)
        for path, node in _object_schemas(tool.input_schema)
        if node.get("additionalProperties") is True or node.get("additionalProperties") == {}
    )
    found = [
        json_evidence(f"{path}/additionalProperties", node["additionalProperties"])
        for path, node in islice(open_schemas, MAX_EVIDENCE_PER_FINDING)
    ]
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            "The schema explicitly accepts parameters it does not define, so callers can pass "
            "anything alongside the documented inputs."
        ),
        suggested_action="Set additionalProperties to false unless extra fields are required.",
    )


def _behavior_evidence(tool: Tool, words: frozenset[str]) -> list[Evidence]:
    """Where the tool's name or first sentence suggests the given kind of behavior."""
    field = TextField("name", tool.name)
    found = [
        evidence(field, start, end)
        for token, start, end in name_tokens(tool.name)
        if token in words
    ]
    if not found:
        # Both word sets include the destructive words, so a destructive verb near the start
        # of the description counts for either.
        opening = TextField("description", tool.description[:300])
        match = DESTRUCTIVE_VERB.search(opening.text)
        if match:
            found.append(evidence(opening, match.start(), match.end()))
    return found


def _hint(tool: Tool, key: str) -> bool | None:
    value = tool.annotations.get(key)
    return value if isinstance(value, bool) else None


def check_destructive_without_hint(tool: Tool, ctx: RuleContext) -> Hit | None:
    if _hint(tool, "readOnlyHint") is not None or _hint(tool, "destructiveHint") is not None:
        return None
    found = _behavior_evidence(tool, DESTRUCTIVE_WORDS)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The tool {tool.name} sounds destructive but declares no readOnlyHint or "
            "destructiveHint, so clients cannot tell from its metadata whether it changes data."
        ),
        suggested_action="Set destructiveHint to true so clients can ask for confirmation.",
    )


def _name_evidence(tool: Tool, words: frozenset[str]) -> list[Evidence]:
    field = TextField("name", tool.name)
    return [
        evidence(field, start, end)
        for token, start, end in name_tokens(tool.name)
        if token in words
    ]


def _hint_contradiction(tool: Tool, words: frozenset[str], claims: str) -> Hit | None:
    """Name words show the behavior; description verbs alone never decide (calibration, M2)."""
    found = _name_evidence(tool, words)
    if not found:
        return None
    found.append(json_evidence("annotations", tool.annotations))
    return Hit(
        evidence=found,
        explanation=(
            f"The tool {tool.name} declares {claims}, but its name says it changes or deletes "
            "data. A client that trusts the hint may skip asking the user first."
        ),
        suggested_action=(
            "Treat the hint as false. Correct it on the server, and confirm what the tool really "
            "does before connecting it."
        ),
    )


def check_contradicting_hint(tool: Tool, ctx: RuleContext) -> Hit | None:
    """Destructive name word with readOnlyHint true or destructiveHint false (high)."""
    if _hint(tool, "readOnlyHint") is True:
        return _hint_contradiction(tool, DESTRUCTIVE_WORDS, "readOnlyHint true")
    if _hint(tool, "destructiveHint") is False:
        return _hint_contradiction(tool, DESTRUCTIVE_WORDS, "destructiveHint false")
    return None


def check_read_only_modifier(tool: Tool, ctx: RuleContext) -> Hit | None:
    """Modifying, non-destructive name word with readOnlyHint true (medium)."""
    if _hint(tool, "readOnlyHint") is not True or _name_evidence(tool, DESTRUCTIVE_WORDS):
        return None
    return _hint_contradiction(tool, MODIFYING_WORDS - DESTRUCTIVE_WORDS, "readOnlyHint true")


RULES = [
    Rule(
        "TG-SCH-001",
        Category.EXCESSIVE_PERMISSIONS,
        Severity.MEDIUM,
        "Unconstrained execution-like parameters such as command, shell, script, code, or sql",
        check_free_form,
    ),
    Rule(
        "TG-SCH-002",
        Category.EXCESSIVE_PERMISSIONS,
        Severity.LOW,
        "Missing type or description on parameters, or no input schema at all",
        check_missing_type_or_description,
    ),
    Rule(
        "TG-SCH-003",
        Category.EXCESSIVE_PERMISSIONS,
        Severity.LOW,
        "Schema accepts arbitrary additional properties",
        check_additional_properties,
    ),
    Rule(
        "TG-SCH-004",
        Category.EXCESSIVE_PERMISSIONS,
        Severity.LOW,
        "Destructive-sounding tool without a destructive or read-only hint",
        check_destructive_without_hint,
    ),
    Rule(
        "TG-SCH-005",
        Category.EXCESSIVE_PERMISSIONS,
        Severity.HIGH,
        "Hint that contradicts a destructive tool name",
        check_contradicting_hint,
    ),
    Rule(
        "TG-SCH-006",
        Category.EXCESSIVE_PERMISSIONS,
        Severity.LOW,
        "Unconstrained locator parameters such as path, file, url, or host",
        check_free_form_locator,
    ),
    Rule(
        "TG-SCH-007",
        Category.EXCESSIVE_PERMISSIONS,
        Severity.MEDIUM,
        "Read-only hint on a tool whose name says it writes or changes data",
        check_read_only_modifier,
    ),
]
