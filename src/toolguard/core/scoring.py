"""Aggregation and scoring (sdd.md section 14): deduplication, per-tool results, counts,
groups, and the verdict. Everything here is deterministic given findings and fail_on."""

from collections.abc import Sequence

from toolguard.core.models import (
    Category,
    Finding,
    FindingCounts,
    FindingGroup,
    Severity,
    Tool,
    ToolResult,
    Verdict,
)

# pdd.md section 7.4.
VERDICT_MESSAGES = {
    Verdict.PASS: (
        "No issues at or above the chosen threshold were detected by ToolGuard's checks. "
        "This is not a guarantee of safety."
    ),
    Verdict.REVIEW: "Some findings need human review before this server is trusted.",
    Verdict.FAIL: "Findings at or above the chosen threshold were detected.",
}


def verdict(findings: Sequence[Finding], fail_on: Severity) -> Verdict:
    """fail at or above fail_on; review when the highest is at least medium; pass otherwise."""
    if not findings:
        return Verdict.PASS
    highest = max(finding.severity for finding in findings)
    if highest >= fail_on:
        return Verdict.FAIL
    if highest >= Severity.MEDIUM:
        return Verdict.REVIEW
    return Verdict.PASS


def verdict_basis(findings: Sequence[Finding], fail_on: Severity) -> str:
    """The rule that decided the verdict, in words, so the result is reproducible."""
    highest = max((f.severity for f in findings), default=None)
    result = verdict(findings, fail_on)
    if result is Verdict.FAIL:
        return f"fail: at least one finding is at or above the fail_on threshold ({fail_on})."
    shown = highest or "none"
    if result is Verdict.REVIEW:
        return (
            f"review: no finding reaches the fail_on threshold ({fail_on}), and the highest "
            f"severity ({shown}) is medium or above."
        )
    return (
        f"pass: no finding reaches the fail_on threshold ({fail_on}), and the highest "
        f"severity ({shown}) is below medium."
    )


def position(finding: Finding) -> int:
    """Tool index of a finding, from its scan-local ID "index:rule_id"."""
    return int(finding.id.split(":", 1)[0])


def aggregate(findings: Sequence[Finding], tools: Sequence[Tool]) -> list[Finding]:
    """Deduplicated findings with fingerprints, ordered by severity, then tool, then rule.

    Rule findings are never merged across rule IDs; one finding per rule per tool already
    exists, so deduplication only guards the ID. Merging across sources comes with the judge.
    """
    unique: dict[str, Finding] = {}
    for finding in findings:
        kept = unique.get(finding.id)
        if kept is None or finding.severity > kept.severity:
            unique[finding.id] = finding
    marked = [
        f.model_copy(update={"fingerprint": f"{tools[position(f)].content_hash}:{f.rule_id}"})
        for f in unique.values()
    ]
    return sorted(marked, key=lambda f: (-f.severity.rank, position(f), f.rule_id))


def tool_results(tools: Sequence[Tool], findings: Sequence[Finding]) -> list[ToolResult]:
    results = [ToolResult(tool_name=t.name, content_hash=t.content_hash) for t in tools]
    for finding in findings:
        result = results[position(finding)]
        result.finding_count += 1
        if result.highest_severity is None or finding.severity > result.highest_severity:
            result.highest_severity = finding.severity
    return results


def counts(findings: Sequence[Finding]) -> FindingCounts:
    """Counts with every severity and category present, zero when nothing matched."""
    result = FindingCounts(
        by_severity=dict.fromkeys(Severity, 0), by_category=dict.fromkeys(Category, 0)
    )
    for finding in findings:
        result.by_severity[finding.severity] += 1
        result.by_category[finding.category] += 1
    return result


def groups(findings: Sequence[Finding]) -> list[FindingGroup]:
    """One group per rule ID, by severity then rule ID. Tools keep the findings' order."""
    by_rule: dict[str, FindingGroup] = {}
    for finding in findings:
        group = by_rule.setdefault(
            finding.rule_id,
            FindingGroup(
                rule_id=finding.rule_id,
                category=finding.category,
                severity=finding.severity,
                tool_count=0,
                tool_names=[],
                finding_ids=[],
            ),
        )
        group.severity = max(group.severity, finding.severity)
        group.tool_count += 1
        group.tool_names.append(finding.tool_name)
        group.finding_ids.append(finding.id)
    return sorted(by_rule.values(), key=lambda g: (-g.severity.rank, g.rule_id))
