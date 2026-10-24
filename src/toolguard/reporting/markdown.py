"""Markdown report following pdd.md section 7. Input is a report already passed through
reporting.safe.neutralize.

Only template text is written unescaped. Scanned values go out in one of three ways:
- code(): an inline code span, for names, paths, and the target;
- fence(): a fenced block, for evidence;
- prose(): escaped text, for explanations and notes.
All three first make the value one line of printable ASCII, so nothing can end a block early.
No scanned value is ever placed in a table.
"""

import re
import string

from toolguard.core.models import (
    AIReviewStatus,
    Evidence,
    Finding,
    OffsetBasis,
    ScanReport,
    Severity,
)
from toolguard.core.scoring import position
from toolguard.rules.text import char_name

GROUP_MIN_TOOLS = 3  # a rule firing on this many tools gets one grouped card
GROUP_TOOLS_SHOWN = 20

DISABLED_SHORT_NOTE = "Rules only: AI review was not used."
METHOD_NOTE = (
    "ToolGuard checked each tool's name, title, description, annotations, and input schema "
    "with deterministic rules for prompt injection, data exfiltration wording, hidden "
    "characters, cross-tool manipulation, excessive permissions, naming problems, and "
    "unusual structure. It does not run the tools, read the server's code, or see what the "
    "tools return at run time. Rules detect known patterns and can miss reworded attacks (on "
    "a held-out set of new attacks they fully detected 2 of 11), so AI review matters for "
    "coverage."
)

_BACKTICKS = re.compile("`+")
# A dot inside a word, as in a domain name. Defanged so no renderer, including ones that link
# bare domains, finds a link; sentence-ending dots are left alone.
_INNER_DOT = re.compile("(?<=[A-Za-z0-9])\\.(?=[A-Za-z0-9])")
_PUNCTUATION = set(string.punctuation)


def render(report: ScanReport) -> str:
    v = report.versions
    lines = [
        "# ToolGuard scan report",
        "",
        f"- Target: {code(report.target)}",
        f"- Scan ID: {code(report.scan_id)}",
        f"- Started: {report.started_at.isoformat()}",
        f"- Finished: {report.finished_at.isoformat() if report.finished_at else 'not finished'}",
        f"- Tools scanned: {report.tool_count}",
        f"- Versions: ToolGuard {code(v.toolguard)}, rule set {code(v.rule_set)}, "
        f"report schema {code(v.report_schema)}, prompt {code(v.prompt or 'none')}",
        "",
        "## Verdict",
        "",
        f"**{str(report.verdict).upper()}**: {report.verdict_message}",
        "",
        f"How it was decided: {report.verdict_basis}",
        "",
    ]
    if report.ai_review_status is AIReviewStatus.DISABLED:
        lines += [DISABLED_SHORT_NOTE, ""]
    lines += _ai_review(report)
    lines += _summary(report)
    lines += _findings(report)
    lines += ["## Tools scanned", ""]
    for result in report.tool_results:
        severity = result.highest_severity or "none"
        lines.append(
            f"- {code(result.tool_name)}: highest severity {severity}, "
            f"{_count(result.finding_count, 'finding')}"
        )
    lines += ["", "## Method", "", METHOD_NOTE, "", report.disclaimer, ""]
    return "\n".join(lines)


def _ai_review(report: ScanReport) -> list[str]:
    cov = report.ai_review_coverage
    reason = f", reason {report.ai_review_reason}" if report.ai_review_reason else ""
    return [
        "## AI review",
        "",
        f"Status: {report.ai_review_status}{reason}.",
        "",
        report.ai_review_message,
        "",
        f"- Tools reviewed by AI: {cov.tools_ai_reviewed}",
        f"- Tools that qualified but were not reviewed: {cov.tools_qualified_not_reviewed}",
        f"- Tools skipped by triage: {cov.tools_skipped_by_triage}",
        f"- Tools checked by rules only: {cov.tools_rules_only}",
        "",
        cov.note,
        "",
    ]


def _summary(report: ScanReport) -> list[str]:
    # Only our own enum words and numbers go in these tables.
    lines = ["## Summary", "", "| Severity | Findings |", "| --- | --- |"]
    for severity in reversed(Severity):
        lines.append(f"| {severity} | {report.counts.by_severity.get(severity, 0)} |")
    lines += ["", "| Category | Findings |", "| --- | --- |"]
    for category, count in report.counts.by_category.items():
        lines.append(f"| {category} | {count} |")
    return [*lines, ""]


def _findings(report: ScanReport) -> list[str]:
    lines = ["## Findings", ""]
    if not report.findings:
        return [*lines, "No findings.", ""]
    grouped = {g.rule_id: g for g in report.finding_groups if g.tool_count >= GROUP_MIN_TOOLS}
    by_id = {f.id: f for f in report.findings}
    shown: set[str] = set()
    for finding in report.findings:  # already ordered by severity, then tool
        group = grouped.get(finding.rule_id)
        if group is None:
            lines += _card(finding)
        elif group.rule_id not in shown:
            shown.add(group.rule_id)
            first = by_id[group.finding_ids[0]]
            lines += [
                f"### {str(group.severity).upper()}: {group.rule_id} on {group.tool_count} tools",
                "",
                f"- Category: {group.category}",
                f"- Rule: {group.rule_id}, source: {first.source}",
                f"- Tools ({group.tool_count}):",
            ]
            lines += [f"  - {code(name)}" for name in group.tool_names[:GROUP_TOOLS_SHOWN]]
            hidden = group.tool_count - GROUP_TOOLS_SHOWN
            if hidden > 0:
                lines.append(f"  - and {hidden} more, listed with every finding in the JSON report")
            lines += ["", f"Example from tool {code(first.tool_name)}:", ""]
            lines += _details(first)
    return lines


def _card(finding: Finding) -> list[str]:
    return [
        f"### {str(finding.severity).upper()}: {finding.rule_id} on tool {code(finding.tool_name)}",
        "",
        f"- Category: {finding.category}",
        f"- Tool: {code(finding.tool_name)} (tool {position(finding) + 1} in the manifest)",
        f"- Rule: {finding.rule_id}, source: {finding.source}",
        "",
        *_details(finding),
    ]


def _details(finding: Finding) -> list[str]:
    lines: list[str] = []
    total = len(finding.evidence)
    for number, ev in enumerate(finding.evidence, 1):
        lines += [
            f"Evidence {number} of {total}, field {code(ev.field)}, {_position(ev)}:",
            "",
            fence(ev.text),
            "",
            "Context:",
            "",
            fence(ev.context),
            "",
        ]
    lines += [f"Explanation: {prose(finding.explanation)}", ""]
    if finding.suggested_action:
        lines += [f"Suggested action: {prose(finding.suggested_action)}", ""]
    if finding.confidence is not None:
        lines += [f"Confidence: {finding.confidence:.2f}", ""]
    if finding.review_note:
        lines += [f"Review note: {prose(finding.review_note)}", ""]
    return lines


def _position(ev: Evidence) -> str:
    if ev.offsets_in is OffsetBasis.SCHEMA_EXCERPT:
        return f"characters {ev.start} to {ev.end} of the schema excerpt"
    if ev.offsets_in is OffsetBasis.NONE:
        return "a note from the rule, with no text position"
    return f"characters {ev.start} to {ev.end}"


def _count(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def flat(text: str) -> str:
    """One line of printable ASCII: anything else is written as its code point."""
    return "".join(
        c if " " <= c <= "~" else (char_name(c) if c.isascii() else f"[U+{ord(c):04X}]")
        for c in text
    )


def code(text: str) -> str:
    """An inline code span whose delimiter is longer than any backtick run inside."""
    text = flat(text)
    ticks = "`" * (max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    # One space on each side is stripped by the parser, and lets text start with a backtick.
    return f"{ticks} {text} {ticks}"


def fence(text: str) -> str:
    """A fenced block whose fence is longer than any backtick run inside."""
    text = flat(text)
    ticks = "`" * max(3, max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    return f"{ticks}\n{text}\n{ticks}"


def prose(text: str) -> str:
    """Escaped text: link triggers defanged, then every ASCII punctuation mark escaped."""
    text = flat(text).replace("://", "[:]//").replace("@", "[@]")
    text = _INNER_DOT.sub("[.]", text)
    return "".join("\\" + c if c in _PUNCTUATION else c for c in text)
