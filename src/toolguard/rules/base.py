"""Types shared by all rules. A rule is a pure function of one tool plus read-only scan context."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from toolguard.core.models import Category, Evidence, Severity, Tool
from toolguard.rules.text import TextField


@dataclass(frozen=True)
class RuleContext:
    """Read-only inputs a rule may use besides the tool itself."""

    all_tools: Sequence[Tool]
    fields: Sequence[TextField]  # the tool's text to scan, already capped
    truncated_field: str | None  # first field the scan cap cut short; later text is unscanned
    description_length_threshold: int


@dataclass(frozen=True)
class Hit:
    """What a rule found. The registry turns it into a Finding."""

    evidence: list[Evidence]
    explanation: str
    suggested_action: str


Check = Callable[[Tool, RuleContext], Hit | None]


@dataclass(frozen=True)
class Rule:
    id: str
    category: Category
    severity: Severity
    description: str
    check: Check
