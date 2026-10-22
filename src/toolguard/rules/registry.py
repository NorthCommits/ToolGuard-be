"""Rule registry and engine: pick the active rules and turn their hits into findings."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from toolguard.core.models import Finding, FindingSource, Severity, Tool
from toolguard.rules import cross_tool, exfiltration, hidden, injection, naming, schema, structure
from toolguard.rules.base import Rule, RuleContext
from toolguard.rules.text import MAX_EVIDENCE_PER_FINDING, text_fields

# Bump whenever a rule is added, removed, or changes what it reports.
RULESET_VERSION = "1"
DEFAULT_DESCRIPTION_LENGTH_THRESHOLD = 2_000

RULES: tuple[Rule, ...] = (
    *hidden.RULES,
    *injection.RULES,
    *exfiltration.RULES,
    *cross_tool.RULES,
    *schema.RULES,
    *naming.RULES,
    *structure.RULES,
)
RULES_BY_ID: dict[str, Rule] = {rule.id: rule for rule in RULES}


def active_rules(
    disabled: Sequence[str] = (), overrides: Mapping[str, str] | None = None
) -> list[Rule]:
    """The rules to run, after disabling and severity overrides from settings.

    Raises ValueError naming the setting and the unknown rule ID.
    """
    overrides = overrides or {}
    for setting, ids in (("rules_disabled", disabled), ("rules_severity_overrides", overrides)):
        for rule_id in ids:
            if rule_id not in RULES_BY_ID:
                raise ValueError(f"Setting {setting} names an unknown rule ID: {rule_id}")
    return [
        replace(rule, severity=Severity(overrides.get(rule.id, rule.severity)))
        for rule in RULES
        if rule.id not in disabled
    ]


def run_rules(
    tools: Sequence[Tool],
    rules: Sequence[Rule] = RULES,
    *,
    description_length_threshold: int = DEFAULT_DESCRIPTION_LENGTH_THRESHOLD,
) -> list[Finding]:
    """Run every rule on every tool. Finding IDs are scan-local: tool index and rule ID."""
    findings: list[Finding] = []
    for index, tool in enumerate(tools):
        fields, truncated_field = text_fields(tool)
        ctx = RuleContext(
            all_tools=tools,
            fields=fields,
            truncated_field=truncated_field,
            description_length_threshold=description_length_threshold,
        )
        for rule in rules:
            hit = rule.check(tool, ctx)
            if hit is None:
                continue
            findings.append(
                Finding(
                    id=f"{index}:{rule.id}",
                    source=FindingSource.RULE,
                    rule_id=rule.id,
                    category=rule.category,
                    severity=rule.severity,
                    tool_name=tool.name,
                    evidence=hit.evidence[:MAX_EVIDENCE_PER_FINDING],
                    explanation=hit.explanation,
                    suggested_action=hit.suggested_action,
                )
            )
    return findings
