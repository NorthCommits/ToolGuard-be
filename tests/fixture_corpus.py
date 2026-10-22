"""Load the fixture corpus, run the rules, and compute the metrics in tests/fixtures/README.md."""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from toolguard.core.models import Severity, Verdict
from toolguard.core.scoring import verdict
from toolguard.ingest.manifest import parse_manifest
from toolguard.rules.registry import run_rules

FIXTURES = Path(__file__).parent / "fixtures"
# The main corpus. heldout/ was written after the rules, to measure overfitting; it is
# reported separately and never counted in the main rates.
FIXTURE_FILES = sorted([*FIXTURES.glob("benign/*.json"), *FIXTURES.glob("malicious/*.json")])
HELDOUT_FILES = sorted(FIXTURES.glob("heldout/*.json"))
FAIL_ON = Severity.HIGH


@dataclass(frozen=True)
class Outcome:
    path: Path
    expected_verdict: Verdict
    actual_verdict: Verdict
    missing: frozenset[tuple[str, str]]
    finding_count: int

    @property
    def benign(self) -> bool:
        return self.path.parent.name == "benign"

    @property
    def flagged(self) -> bool:
        """The verdict is review or fail, even if not every expected finding was reported."""
        return self.actual_verdict is not Verdict.PASS

    @property
    def detected(self) -> bool:
        """Every expected finding reported and the verdict as expected."""
        return not self.missing and self.actual_verdict is self.expected_verdict


@cache
def evaluate(path: Path) -> Outcome:
    raw = path.read_bytes()
    document = json.loads(raw)
    findings = run_rules(parse_manifest(raw, max_bytes=1_000_000, max_tools=500))
    reported = {(f.tool_name, f.rule_id) for f in findings}
    expected = {(e["tool"], e["rule_id"]) for e in document["expected"]["findings"]}
    return Outcome(
        path=path,
        expected_verdict=Verdict(document["expected"]["verdict"]),
        actual_verdict=verdict(findings, FAIL_ON),
        missing=frozenset(expected - reported),
        finding_count=len(findings),
    )


@dataclass(frozen=True)
class Metrics:
    malicious: int
    detected: int
    benign: int
    false_positives: int
    benign_with_findings: int

    @property
    def detection_rate(self) -> float:
        return self.detected / self.malicious if self.malicious else 0.0

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.benign if self.benign else 0.0


def metrics() -> Metrics:
    outcomes = [evaluate(path) for path in FIXTURE_FILES]
    malicious = [o for o in outcomes if not o.benign]
    benign = [o for o in outcomes if o.benign]
    return Metrics(
        malicious=len(malicious),
        detected=sum(o.detected for o in malicious),
        benign=len(benign),
        false_positives=sum(o.actual_verdict is not Verdict.PASS for o in benign),
        benign_with_findings=sum(o.finding_count > 0 for o in benign),
    )


def heldout_outcomes() -> list[Outcome]:
    return [evaluate(path) for path in HELDOUT_FILES]
