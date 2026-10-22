"""Fixture regression: real rule output compared with each fixture's expected block."""

import pytest
from fixture_corpus import FIXTURE_FILES, evaluate, metrics

from toolguard.core.models import Verdict

# PRD section 11 targets.
MIN_DETECTION_RATE = 0.90
MAX_FALSE_POSITIVE_RATE = 0.10


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_fixture_matches_expectations(path) -> None:
    outcome = evaluate(path)
    if outcome.benign:
        assert outcome.actual_verdict is Verdict.PASS
    else:
        assert not outcome.missing, f"expected findings not reported: {sorted(outcome.missing)}"
        assert outcome.actual_verdict is outcome.expected_verdict


def test_prd_targets() -> None:
    result = metrics()
    assert result.detection_rate >= MIN_DETECTION_RATE
    assert result.false_positive_rate < MAX_FALSE_POSITIVE_RATE
