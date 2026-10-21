"""Typed domain models shared by all modules (sdd.md section 4)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(StrEnum):
    """Finding severity. Compares by rank, not alphabetically."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(Severity).index(self)

    # StrEnum would otherwise compare as plain strings, where "critical" < "high".
    def __lt__(self, other: object) -> bool:
        return self.rank < Severity(other).rank

    def __le__(self, other: object) -> bool:
        return self.rank <= Severity(other).rank

    def __gt__(self, other: object) -> bool:
        return self.rank > Severity(other).rank

    def __ge__(self, other: object) -> bool:
        return self.rank >= Severity(other).rank


class Category(StrEnum):
    INJECTION = "injection"
    EXFILTRATION = "exfiltration"
    HIDDEN_CHARACTERS = "hidden_characters"
    CROSS_TOOL = "cross_tool"
    EXCESSIVE_PERMISSIONS = "excessive_permissions"
    NAMING = "naming"
    STRUCTURE = "structure"


class FindingSource(StrEnum):
    RULE = "rule"
    JUDGE = "judge"


class ScanStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScanStage(StrEnum):
    QUEUED = "queued"
    INGEST = "ingest"
    RULES = "rules"
    TRIAGE = "triage"
    AI_REVIEW = "ai_review"
    SCORING = "scoring"
    REPORTING = "reporting"
    DONE = "done"


class Verdict(StrEnum):
    PASS = "pass"
    REVIEW = "review"
    FAIL = "fail"


class AIReviewStatus(StrEnum):
    PERFORMED = "performed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    DISABLED = "disabled"


class AIReviewReason(StrEnum):
    DISABLED_BY_REQUEST = "disabled_by_request"
    NO_PROVIDER_KEYS = "no_provider_keys"
    QUOTA_EXHAUSTED = "quota_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    JUDGE_OUTPUT_INVALID = "judge_output_invalid"
    SCAN_TIMEOUT = "scan_timeout"
    REVIEW_CAP_REACHED = "review_cap_reached"
    NO_TOOLS_NEEDED_REVIEW = "no_tools_needed_review"


class Tool(BaseModel):
    """A normalized tool. Text fields hold the original input unchanged, for evidence."""

    model_config = ConfigDict(frozen=True)

    server_name: str
    name: str
    title: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    # True when the input had no schema at all, so a rule can tell it apart from {}.
    input_schema_missing: bool = False
    annotations: dict[str, Any] = Field(default_factory=dict)
    content_hash: str


class Evidence(BaseModel):
    """Where a finding matched. start and end are character (code point) offsets in the field."""

    field: str
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    context: str = ""

    @model_validator(mode="after")
    def _end_after_start(self) -> "Evidence":
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self


class Finding(BaseModel):
    id: str
    source: FindingSource
    rule_id: str
    category: Category
    severity: Severity
    tool_name: str
    evidence: list[Evidence] = Field(default_factory=list)
    explanation: str
    suggested_action: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    review_note: str | None = None


class FindingAnnotation(BaseModel):
    """A judge note on an existing finding. It never removes or downgrades the finding."""

    finding_id: str
    likely_false_positive: bool = False
    note: str = Field(max_length=300)


class JudgeVerdict(BaseModel):
    tool_name: str
    risk_level: Severity
    added_findings: list[Finding] = Field(default_factory=list)
    annotations: list[FindingAnnotation] = Field(default_factory=list)
    reasoning_summary: str = Field(max_length=300)


class ToolResult(BaseModel):
    tool_name: str
    content_hash: str
    highest_severity: Severity | None = None
    finding_count: int = 0
    ai_reviewed: bool = False


class FindingCounts(BaseModel):
    by_severity: dict[Severity, int] = Field(default_factory=dict)
    by_category: dict[Category, int] = Field(default_factory=dict)


class ProviderUsage(BaseModel):
    provider: str
    calls: int = 0
    estimated_tokens: int = 0


class AIUsage(BaseModel):
    providers: list[ProviderUsage] = Field(default_factory=list)
    cache_hits: int = 0


class Versions(BaseModel):
    toolguard: str
    rule_set: str
    prompt: str
    report_schema: str


class ScanReport(BaseModel):
    scan_id: str
    target: str
    started_at: datetime
    finished_at: datetime | None = None
    status: ScanStatus
    verdict: Verdict | None = None
    fail_on: Severity
    tool_count: int
    counts: FindingCounts = Field(default_factory=FindingCounts)
    findings: list[Finding] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    ai_review_status: AIReviewStatus
    ai_review_reason: AIReviewReason | None = None
    ai_review_message: str = ""
    degraded: bool = False
    degraded_reason: AIReviewReason | None = None
    unreviewed_tools: list[str] = Field(default_factory=list)
    ai_usage: AIUsage = Field(default_factory=AIUsage)
    versions: Versions
