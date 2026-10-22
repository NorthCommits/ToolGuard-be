"""Run every rule over the calibration manifests and print all findings, for review by eye.

Not part of the pytest pass or fail checks: real servers are expected to produce some findings,
and the point is to judge which are false positives. Run it from the repository root:

    uv run python tests/calibration/run_calibration.py
"""

from collections import Counter
from pathlib import Path

from toolguard.core.models import Severity
from toolguard.core.scoring import verdict
from toolguard.ingest.manifest import parse_manifest
from toolguard.rules.registry import RULES, run_rules

MANIFESTS = sorted((Path(__file__).parent / "manifests").glob("*.json"))


def main() -> None:
    per_rule: Counter[str] = Counter()
    tool_total = 0
    for path in MANIFESTS:
        tools = parse_manifest(path.read_bytes(), max_bytes=1_000_000, max_tools=500)
        tool_total += len(tools)
        findings = run_rules(tools)
        print(
            f"\n== {path.name}: {len(tools)} tools, {len(findings)} findings, "
            f"verdict {verdict(findings, Severity.HIGH)} at fail_on high"
        )
        for finding in findings:
            per_rule[finding.rule_id] += 1
            print(f"  {finding.rule_id} {finding.severity} {finding.tool_name}")
            for evidence in finding.evidence:
                print(f"      {evidence.field} [{evidence.start}:{evidence.end}] {evidence.text!r}")
    print(f"\n{len(MANIFESTS)} manifests, {tool_total} tools")
    for rule in RULES:
        print(f"  {rule.id} ({rule.severity}): {per_rule[rule.id]}")


if __name__ == "__main__":
    main()
