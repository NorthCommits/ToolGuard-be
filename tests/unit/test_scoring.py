import pytest

from toolguard.core import scoring
from toolguard.core.models import Category, Finding, FindingSource, Severity, Verdict
from toolguard.core.scoring import verdict
from toolguard.ingest.manifest import build_tool

TOOLS = [build_tool("s", name) for name in ("a", "b", "c")]


def finding(
    severity: Severity,
    index: int = 0,
    rule_id: str = "TG-TST-001",
    category: Category = Category.STRUCTURE,
) -> Finding:
    return Finding(
        id=f"{index}:{rule_id}",
        source=FindingSource.RULE,
        rule_id=rule_id,
        category=category,
        severity=severity,
        tool_name=TOOLS[index].name,
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


@pytest.mark.parametrize("fail_on", list(Severity))
def test_verdict_basis_names_the_deciding_rule(fail_on: Severity) -> None:
    findings = [finding(Severity.MEDIUM)]
    basis = scoring.verdict_basis(findings, fail_on)
    assert basis.startswith(f"{verdict(findings, fail_on)}:")
    assert f"({fail_on})" in basis
    assert scoring.verdict_basis([], fail_on).startswith("pass:")


def test_aggregate_dedupes_by_id_and_never_merges_rules() -> None:
    findings = [
        finding(Severity.LOW, 0, "TG-AAA-001"),
        finding(Severity.HIGH, 0, "TG-AAA-001"),  # same ID: the higher severity is kept
        finding(Severity.HIGH, 0, "TG-BBB-001", Category.STRUCTURE),  # same category, kept
    ]
    result = scoring.aggregate(findings, TOOLS)
    assert [(f.id, f.severity) for f in result] == [
        ("0:TG-AAA-001", Severity.HIGH),
        ("0:TG-BBB-001", Severity.HIGH),
    ]


def test_aggregate_orders_and_fingerprints() -> None:
    findings = [
        finding(Severity.LOW, 0, "TG-AAA-001"),
        finding(Severity.HIGH, 2, "TG-AAA-002"),
        finding(Severity.HIGH, 1, "TG-AAA-003"),
        finding(Severity.HIGH, 1, "TG-AAA-001"),
    ]
    result = scoring.aggregate(findings, TOOLS)
    assert [f.id for f in result] == [
        "1:TG-AAA-001",
        "1:TG-AAA-003",
        "2:TG-AAA-002",
        "0:TG-AAA-001",
    ]
    assert result[0].fingerprint == f"{TOOLS[1].content_hash}:TG-AAA-001"


def test_tool_results_counts_and_groups() -> None:
    findings = scoring.aggregate(
        [
            finding(Severity.LOW, 0, "TG-AAA-001"),
            finding(Severity.LOW, 2, "TG-AAA-001"),
            finding(Severity.MEDIUM, 2, "TG-BBB-001", Category.INJECTION),
        ],
        TOOLS,
    )
    results = scoring.tool_results(TOOLS, findings)
    assert [(r.tool_name, r.highest_severity, r.finding_count) for r in results] == [
        ("a", Severity.LOW, 1),
        ("b", None, 0),
        ("c", Severity.MEDIUM, 2),
    ]

    counts = scoring.counts(findings)
    assert set(counts.by_severity) == set(Severity) and set(counts.by_category) == set(Category)
    assert counts.by_severity[Severity.LOW] == 2 and counts.by_severity[Severity.HIGH] == 0
    assert counts.by_category[Category.INJECTION] == 1

    groups = scoring.groups(findings)
    assert [(g.rule_id, g.tool_count, g.tool_names) for g in groups] == [
        ("TG-BBB-001", 1, ["c"]),
        ("TG-AAA-001", 2, ["a", "c"]),
    ]
    assert groups[1].finding_ids == ["0:TG-AAA-001", "2:TG-AAA-001"]
