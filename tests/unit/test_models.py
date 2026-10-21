from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from toolguard.core.models import (
    AIReviewStatus,
    Evidence,
    JudgeVerdict,
    ScanReport,
    ScanStatus,
    Severity,
    Versions,
)


def test_severity_compares_by_rank_not_alphabet() -> None:
    assert Severity.CRITICAL > Severity.HIGH
    assert Severity.INFO < Severity.LOW < Severity.MEDIUM < Severity.HIGH < Severity.CRITICAL
    assert Severity.HIGH >= Severity.HIGH
    assert max([Severity.HIGH, Severity.CRITICAL, Severity.LOW]) is Severity.CRITICAL
    assert sorted([Severity.HIGH, Severity.INFO, Severity.CRITICAL]) == [
        Severity.INFO,
        Severity.HIGH,
        Severity.CRITICAL,
    ]


def test_severity_compares_with_plain_strings() -> None:
    assert Severity.HIGH >= "medium"
    assert Severity.HIGH < "critical"


def test_judge_risk_level_must_be_a_severity() -> None:
    with pytest.raises(ValidationError):
        JudgeVerdict(tool_name="t", risk_level="severe", reasoning_summary="x")
    verdict = JudgeVerdict(tool_name="t", risk_level="info", reasoning_summary="x")
    assert verdict.risk_level is Severity.INFO


def test_evidence_end_cannot_precede_start() -> None:
    with pytest.raises(ValidationError):
        Evidence(field="description", text="x", start=5, end=2)


def test_scan_report_round_trips_through_json() -> None:
    report = ScanReport(
        scan_id="s1",
        target="manifest",
        started_at=datetime(2026, 10, 21, tzinfo=UTC),
        status=ScanStatus.COMPLETED,
        fail_on=Severity.HIGH,
        tool_count=0,
        ai_review_status=AIReviewStatus.DISABLED,
        versions=Versions(toolguard="0.1.0", rule_set="0", prompt="0", report_schema="1"),
    )
    assert ScanReport.model_validate_json(report.model_dump_json()) == report
