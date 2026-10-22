"""Cross-tool rules (TG-XTL): a tool that tries to steer how other tools are used.

Both rules only fire when the text targets a different tool. "Call this before create_event"
refers to the tool itself and is legitimate documentation.
"""

import re
from collections.abc import Iterator

from toolguard.core.models import Category, Evidence, Severity, Tool
from toolguard.rules.base import Hit, Rule, RuleContext
from toolguard.rules.text import TextField, View, evidence, windows

_I = re.IGNORECASE

# "the fetch tool", "`send_email` function": a named tool, captured.
TOOL_REFERENCE = re.compile(
    r"\b([A-Za-z_][\w.-]{0,63})[`'\"]?\s{1,3}(?:tool|function|command)\b", _I
)
CALL_TARGET = re.compile(
    r"\b(?:call|invoke|use|run|execute|trigger)\s{1,3}(?:the\s{1,3})?[`'\"]?([A-Za-z_][\w.-]{0,63})",
    _I,
)
WORD = re.compile(r"[A-Za-z_][\w.-]{0,63}")
WHEN_USED = re.compile(
    r"\b(?:when|whenever|every\s{1,3}time|each\s{1,3}time|any\s{1,3}time)\b|\binstead\s{1,3}of\b",
    _I,
)
USAGE_DIRECTIVE = re.compile(
    r"\b(?:always|must|never|instead|replace|override|redirect|change|add|set|include|only)\b", _I
)
# "all mail tools in this session must ...": a rule for other tools as a group.
TOOLS_POLICY = re.compile(
    r"\b(?:all|every|any|other)\s{1,3}(?:[a-z-]{1,30}\s{1,3}){0,2}?tools?\b", _I
)
# Policy wording needs a modal, not just a verb such as "add": "all mail tools must ...".
POLICY_DIRECTIVE = re.compile(
    r"\b(?:must|shall|should|always|never|are\s{1,3}required\s{1,3}to|have\s{1,3}to)\b", _I
)
SEQUENCE = re.compile(
    r"\b(?:before|after|first|then|afterwards|beforehand|prior\s{1,3}to|followed\s{1,3}by)\b", _I
)

# Words that refer to the tool itself or to nothing in particular.
_NOT_A_TARGET = frozenset(
    [
        "this",
        "that",
        "it",
        "its",
        "the",
        "a",
        "an",
        "any",
        "each",
        "every",
        "other",
        "same",
        "which",
        "your",
        "our",
        "these",
        "those",
        "them",
        "tool",
        "tools",
        "function",
    ]
)


def _other_tool_names(tool: Tool, ctx: RuleContext) -> set[str]:
    return {t.name.casefold() for t in ctx.all_tools if t.name != tool.name}


def _is_other_tool(word: str, window: str, tool: Tool, others: set[str]) -> bool:
    folded = word.casefold()
    if folded in _NOT_A_TARGET or folded == tool.name.casefold():
        return False
    if folded in others:
        return True
    return any(m.group(1).casefold() == folded for m in TOOL_REFERENCE.finditer(window))


def _referenced_other_tool(window: str, tool: Tool, others: set[str]) -> re.Match[str] | None:
    for match in TOOL_REFERENCE.finditer(window):
        if _is_other_tool(match.group(1), window, tool, others):
            return match
    for match in WORD.finditer(window):
        if match.group(0).casefold() in others:
            return match
    return None


def _windows(ctx: RuleContext) -> Iterator[tuple[TextField, View, int, int]]:
    for field in ctx.fields:
        for view in field.views:
            for start, end in windows(view.text):
                yield field, view, start, end


def check_directs_other_tools(tool: Tool, ctx: RuleContext) -> Hit | None:
    others = _other_tool_names(tool, ctx)
    found: list[Evidence] = []
    for field, view, start, end in _windows(ctx):
        window = view.text[start:end]
        if WHEN_USED.search(window) and USAGE_DIRECTIVE.search(window):
            match = _referenced_other_tool(window, tool, others)
        elif POLICY_DIRECTIVE.search(window):
            match = TOOLS_POLICY.search(window)
        else:
            match = None
        if match:
            found.append(evidence(field, *view.span(start + match.start(), start + match.end())))
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            "This tool's text tells the AI model how another tool must behave. A tool should "
            "describe itself only; changing other tools is how a server shadows or hijacks them."
        ),
        suggested_action=(
            "Remove the instructions about other tools, and check what the other tool would do "
            "if they were followed."
        ),
    )


def check_calls_other_tool(tool: Tool, ctx: RuleContext) -> Hit | None:
    others = _other_tool_names(tool, ctx)
    found: list[Evidence] = []
    for field, view, start, end in _windows(ctx):
        window = view.text[start:end]
        if not SEQUENCE.search(window):
            continue
        for match in CALL_TARGET.finditer(window):
            if _is_other_tool(match.group(1), window, tool, others):
                found.append(
                    evidence(field, *view.span(start + match.start(), start + match.end()))
                )
                break
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            "This tool's text tells the AI model to call another tool before or after it. That "
            "can chain tools together in ways the user did not ask for."
        ),
        suggested_action=(
            "Check why the other tool must be called, and remove the instruction if it is not "
            "needed for this tool to work."
        ),
    )


RULES = [
    Rule(
        "TG-XTL-001",
        Category.CROSS_TOOL,
        Severity.MEDIUM,
        "Description that directs how other tools must be used",
        check_directs_other_tools,
    ),
    Rule(
        "TG-XTL-002",
        Category.CROSS_TOOL,
        Severity.MEDIUM,
        "Instruction to call another tool before or after this one",
        check_calls_other_tool,
    ),
]
