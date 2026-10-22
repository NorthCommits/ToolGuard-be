"""Scan verdict from findings (sdd.md section 14). Deduplication and counts come in M3."""

from collections.abc import Sequence

from toolguard.core.models import Finding, Severity, Verdict


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
