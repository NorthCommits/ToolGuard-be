"""Shared fixtures, and the fixture corpus metrics printed at the end of every pytest run."""

import pytest
from fixture_corpus import heldout_outcomes, metrics

from toolguard.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings with a fresh database file and no .env, provider keys, or rule overrides.

    A file, not ":memory:", because every connection to ":memory:" is a new empty database.
    """
    return Settings(
        _env_file=None,
        database_path=tmp_path / "test.db",
        groq_api_key=None,
        openrouter_api_key=None,
        rules_disabled=[],
        rules_severity_overrides={},
    )


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    result = metrics()
    terminalreporter.write_sep("-", "fixture corpus metrics")
    terminalreporter.write_line(
        f"detection rate: {result.detection_rate:.0%} ({result.detected}/{result.malicious} "
        "malicious fixtures)"
    )
    terminalreporter.write_line(
        f"false positive rate: {result.false_positive_rate:.0%} ({result.false_positives}/"
        f"{result.benign} benign fixtures not pass)"
    )
    terminalreporter.write_line(
        f"benign fixtures with any finding (information only): "
        f"{result.benign_with_findings}/{result.benign}"
    )

    heldout = heldout_outcomes()
    terminalreporter.write_sep("-", "held-out hostile set (reported only, not asserted)")
    terminalreporter.write_line(
        f"fully detected: {sum(o.detected for o in heldout)}/{len(heldout)}, "
        f"flagged review or fail: {sum(o.flagged for o in heldout)}/{len(heldout)}"
    )
    for outcome in heldout:
        if not outcome.detected:
            missing = ", ".join(sorted(rule for _, rule in outcome.missing)) or "none"
            terminalreporter.write_line(
                f"  {outcome.path.stem}: verdict {outcome.actual_verdict}, missing {missing}"
            )
