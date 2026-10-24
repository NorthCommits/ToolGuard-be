"""Golden files: both report formats for fixed fixtures, with the scan ID, timestamps, and
ToolGuard version pinned. The example reports in docs/examples are produced the same way.
Regenerate both with UPDATE_GOLDEN=1 and review the diff."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fixture_corpus import FIXTURES, scan
from report_safety import assert_inert_markdown, assert_safe_json

from toolguard.reporting import json_report, markdown

ROOT = Path(__file__).parents[2]
GOLDEN = ROOT / "tests/golden"
EXAMPLES = ROOT / "docs/examples"
PINNED_TIME = datetime(2026, 1, 1, tzinfo=UTC)
# Output file stem -> fixture.
CASES = {
    GOLDEN / "malicious_bidi_controls": "malicious/bidi_controls",
    GOLDEN / "malicious_weak_schemas": "malicious/weak_schemas",
    GOLDEN / "benign_clean_basic": "benign/clean_basic",
    EXAMPLES / "hidden_instructions": "malicious/hidden_instructions",
}


def pinned_reports(fixture: str) -> tuple[str, str]:
    """The JSON and Markdown reports of a fixture, with run-specific values pinned."""
    report = scan((FIXTURES / f"{fixture}.json").read_bytes())
    report = report.model_copy(
        update={
            "scan_id": "0" * 32,
            "started_at": PINNED_TIME,
            "finished_at": PINNED_TIME,
            "versions": report.versions.model_copy(update={"toolguard": "pinned"}),
        }
    )
    return json_report.render(report), markdown.render(report)


@pytest.mark.parametrize("stem", CASES, ids=lambda p: str(p.relative_to(ROOT)))
def test_reports_match_golden_files(stem: Path) -> None:
    json_text, markdown_text = pinned_reports(CASES[stem])
    assert_safe_json(json_text)
    assert_inert_markdown(markdown_text)
    for path, text in (
        (stem.with_suffix(".json"), json_text),
        (stem.with_suffix(".md"), markdown_text),
    ):
        if os.environ.get("UPDATE_GOLDEN") == "1":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="ascii")
        # Reading as ASCII also fails on any non-ASCII byte.
        assert path.read_text(encoding="ascii") == text, f"{path.name} differs from golden"
        assert not any(word in text.lower() for word in ("api_key", "gsk_", "sk-or", "token"))
