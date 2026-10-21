"""Every fixture must ingest cleanly and carry a well-formed expected block."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from toolguard.core.models import Verdict
from toolguard.ingest.manifest import parse_manifest

FIXTURES = Path(__file__).parents[1] / "fixtures"
FIXTURE_FILES = sorted(FIXTURES.glob("*/*.json"))


class ExpectedFinding(BaseModel):
    tool: str
    rule_id: str = Field(pattern=r"^TG-[A-Z]{3}-\d{3}$")


class Expected(BaseModel):
    verdict: Verdict
    findings: list[ExpectedFinding]


def test_corpus_has_both_folders() -> None:
    folders = {path.parent.name for path in FIXTURE_FILES}
    assert folders == {"benign", "malicious"}


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_fixture_ingests_and_expectations_are_valid(path: Path) -> None:
    raw = path.read_bytes()
    tools = parse_manifest(raw, max_bytes=1_000_000, max_tools=500)
    document = json.loads(raw)
    expected = Expected.model_validate(document["expected"])
    assert document["notes"].strip()

    tool_names = {tool.name for tool in tools}
    assert {finding.tool for finding in expected.findings} <= tool_names

    if path.parent.name == "benign":
        assert expected.verdict is Verdict.PASS
        assert expected.findings == []
    else:
        assert expected.verdict in (Verdict.REVIEW, Verdict.FAIL)
        assert expected.findings
