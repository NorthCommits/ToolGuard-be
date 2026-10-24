"""Load the fixture corpus, run the full pipeline, and compute the metrics in
tests/fixtures/README.md."""

import json
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from toolguard.config import Settings
from toolguard.core.models import ScanReport, ScanStatus, Severity, Verdict
from toolguard.core.pipeline import create_scan, run_scan
from toolguard.reporting.safe import NAME_CHARS, neutral
from toolguard.storage.db import get_scan

FIXTURES = Path(__file__).parent / "fixtures"
# The main corpus. heldout/ was written after the rules, to measure overfitting; it is
# reported separately and never counted in the main rates.
FIXTURE_FILES = sorted([*FIXTURES.glob("benign/*.json"), *FIXTURES.glob("malicious/*.json")])
HELDOUT_FILES = sorted(FIXTURES.glob("heldout/*.json"))
FAIL_ON = Severity.HIGH

# One database for the whole run, removed when the interpreter exits.
_DATABASE = tempfile.TemporaryDirectory()
SETTINGS = Settings(
    _env_file=None,
    database_path=Path(_DATABASE.name) / "corpus.db",
    groq_api_key=None,
    openrouter_api_key=None,
    rules_disabled=[],
    rules_severity_overrides={},
)


def scan(raw: bytes, fail_on: Severity = FAIL_ON) -> ScanReport:
    """Run the pipeline without AI and return the stored, neutralized report."""
    scan_id = create_scan(SETTINGS, fail_on=fail_on)
    run_scan(SETTINGS, scan_id, raw)
    row = get_scan(SETTINGS.database_path, scan_id)
    assert row["status"] == ScanStatus.COMPLETED, row["failure_code"]
    return ScanReport.model_validate_json(row["report_json"])


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
    report = scan(raw)
    # Report names are neutralized, so expected names are compared in the same form.
    reported = {(f.tool_name, f.rule_id) for f in report.findings}
    expected = {
        (neutral(e["tool"], NAME_CHARS), e["rule_id"]) for e in document["expected"]["findings"]
    }
    return Outcome(
        path=path,
        expected_verdict=Verdict(document["expected"]["verdict"]),
        actual_verdict=report.verdict,
        missing=frozenset(expected - reported),
        finding_count=len(report.findings),
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
