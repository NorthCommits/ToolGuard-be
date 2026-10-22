import pytest

from toolguard.core.models import Category, Finding, FindingSource, Severity, Verdict
from toolguard.core.scoring import verdict


def finding(severity: Severity) -> Finding:
    return Finding(
        id="0:TG-TST-001",
        source=FindingSource.RULE,
        rule_id="TG-TST-001",
        category=Category.STRUCTURE,
        severity=severity,
        tool_name="t",
        explanation="x",
    )


@pytest.mark.parametrize(
    ("severities", "fail_on", "expected"),
    [
        ([], Severity.HIGH, Verdict.PASS),
        ([Severity.INFO, Severity.LOW], Severity.HIGH, Verdict.PASS),
        ([Severity.MEDIUM], Severity.HIGH, Verdict.REVIEW),
        ([Severity.LOW, Severity.HIGH], Severity.HIGH, Verdict.FAIL),
        ([Severity.CRITICAL], Severity.HIGH, Verdict.FAIL),
        ([Severity.HIGH], Severity.CRITICAL, Verdict.REVIEW),
        ([Severity.MEDIUM], Severity.MEDIUM, Verdict.FAIL),
        ([Severity.LOW], Severity.LOW, Verdict.FAIL),
    ],
)
def test_verdict(severities: list[Severity], fail_on: Severity, expected: Verdict) -> None:
    assert verdict([finding(s) for s in severities], fail_on) is expected
