"""Neutralize scanned text for reports (pdd.md section 7.6).

Every string that comes from a scan (target, tool names, field paths, evidence, explanations,
notes) passes through here before it reaches a report: hidden and control characters are
shown by name, and anything shortened carries a visible "[... N more characters]" marker,
where N counts characters of the original text. Markup escaping is format-specific and lives
in the renderers.
"""

import json
from collections.abc import Sequence
from typing import Any

from toolguard.core.models import Evidence, Finding, OffsetBasis, ScanReport, Tool
from toolguard.core.scoring import position
from toolguard.rules.text import TextField, char_name, is_hidden, json_evidence, visible
from toolguard.rules.text import evidence as rule_evidence

TARGET_CHARS = 500
NAME_CHARS = 100
PATH_CHARS = 200
PROSE_CHARS = 600
EVIDENCE_CHARS = 200
CONTEXT_CHARS = 40  # original characters on each side of the match

_MISSING = object()


def omitted(count: int) -> str:
    return f"[... {count} more characters]" if count > 0 else ""


def neutral(text: str, limit: int) -> str:
    """visible(text), cut to at most limit characters plus a marker when longer."""
    out: list[str] = []
    size = 0
    for index, char in enumerate(text):
        piece = char_name(char) if is_hidden(char) else char
        size += len(piece)
        if size > limit:
            return "".join(out) + omitted(len(text) - index)
        out.append(piece)
    return "".join(out)


def neutral_evidence(tool: Tool, ev: Evidence) -> Evidence:
    """Evidence rebuilt from the stored tool text, so shortening is marked with exact counts.

    The rule's excerpt is only trusted as a hint: the original is used when recomputing it
    gives the same excerpt. Offsets are never changed; they always refer to the original text.
    """
    value = _resolve(tool, ev.field)
    field = neutral(ev.field, PATH_CHARS)
    basis = OffsetBasis.FIELD_TEXT
    if (
        isinstance(value, str)
        and ev.start < ev.end <= len(value)
        and rule_evidence(TextField(ev.field, value), ev.start, ev.end).text == ev.text
    ):
        text = neutral(value[ev.start : ev.end], EVIDENCE_CHARS)
        left_start = max(0, ev.start - CONTEXT_CHARS)
        right_end = min(len(value), ev.end + CONTEXT_CHARS)
        context = (
            omitted(left_start)
            + visible(value[left_start : ev.start])
            + text
            + visible(value[ev.end : right_end])
            + omitted(len(value) - right_end)
        )
    elif value is not _MISSING and json_evidence(ev.field, value).text == ev.text:
        # Structural evidence: compact JSON of a schema value; offsets cover that JSON text.
        text = context = neutral(_compact(value), EVIDENCE_CHARS)
        basis = OffsetBasis.SCHEMA_EXCERPT
    else:
        # A note written by the rule, for example "(no input schema)".
        text = neutral(ev.text, EVIDENCE_CHARS)
        context = neutral(ev.context, EVIDENCE_CHARS + 2 * CONTEXT_CHARS)
        basis = OffsetBasis.NONE
    return Evidence(
        field=field, text=text, start=ev.start, end=ev.end, context=context, offsets_in=basis
    )


def neutral_finding(tool: Tool, finding: Finding) -> Finding:
    return finding.model_copy(
        update={
            "tool_name": neutral(finding.tool_name, NAME_CHARS),
            "evidence": [neutral_evidence(tool, ev) for ev in finding.evidence],
            "explanation": neutral(finding.explanation, PROSE_CHARS),
            "suggested_action": neutral(finding.suggested_action, PROSE_CHARS),
            "review_note": (
                None if finding.review_note is None else neutral(finding.review_note, PROSE_CHARS)
            ),
        }
    )


def neutralize(report: ScanReport, tools: Sequence[Tool]) -> ScanReport:
    """The report with every scanned string neutralized. This is what gets stored and shown."""
    return report.model_copy(
        update={
            "target": neutral(report.target, TARGET_CHARS),
            "findings": [neutral_finding(tools[position(f)], f) for f in report.findings],
            "finding_groups": [
                g.model_copy(update={"tool_names": [neutral(n, NAME_CHARS) for n in g.tool_names]})
                for g in report.finding_groups
            ],
            "tool_results": [
                r.model_copy(update={"tool_name": neutral(r.tool_name, NAME_CHARS)})
                for r in report.tool_results
            ],
            "unreviewed_tools": [neutral(n, NAME_CHARS) for n in report.unreviewed_tools],
        }
    )


def _compact(value: Any) -> str:
    # Same serialization as rules.text.json_evidence, without its cut.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _resolve(tool: Tool, path: str) -> Any:
    """The value an evidence field path points at, or _MISSING. Paths are the ones built by
    rules.text: name, title, description, annotations/..., input_schema/... (JSON Pointer
    escaped segments), and ".../<name> (property name)" for a property name itself."""
    suffix = " (property name)"
    if path.endswith(suffix):
        return _unescape(path[: -len(suffix)].rsplit("/", 1)[-1])
    head, _, rest = path.partition("/")
    if head in ("name", "title", "description") and not rest:
        return getattr(tool, head)
    roots = {"annotations": tool.annotations, "input_schema": tool.input_schema}
    if head not in roots:
        return _MISSING
    node: Any = roots[head]
    for segment in rest.split("/") if rest else ():
        key = _unescape(segment)
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return _MISSING
    return node


def _unescape(segment: str) -> str:
    return segment.replace("~1", "/").replace("~0", "~")
