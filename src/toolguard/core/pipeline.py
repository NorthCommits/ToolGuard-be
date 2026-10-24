"""Scan pipeline without AI (sdd.md section 16): ingest, rules, scoring, reporting.

Triage and AI review do not exist until M5, so they are skipped and the report says AI review
was disabled. A failure in any stage marks the scan failed with a stable code and a generic
message; current_stage stays at the stage that failed.
"""

import logging
import sqlite3
from importlib.metadata import version

from toolguard.config import Settings
from toolguard.core import scoring
from toolguard.core.errors import (
    InternalError,
    InvalidInput,
    PayloadTooLarge,
    Problem,
    StorageError,
)
from toolguard.core.models import (
    AIReviewCoverage,
    AIReviewReason,
    AIReviewStatus,
    ScanReport,
    ScanStage,
    ScanStatus,
    Severity,
    Versions,
)
from toolguard.ingest.manifest import parse_manifest
from toolguard.reporting import json_report
from toolguard.reporting.safe import TARGET_CHARS, neutralize
from toolguard.rules.registry import RULESET_VERSION, active_rules, run_rules
from toolguard.storage import db

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "A result without findings is not a guarantee of safety: ToolGuard only detects the "
    "patterns its checks cover."
)
COVERAGE_NOTE = "Tools not reviewed by AI were checked by rules only."
AI_DISABLED_MESSAGES = {
    AIReviewReason.DISABLED_BY_REQUEST: (
        "AI review was not used for this scan (turned off for this scan). Findings come from "
        "deterministic rules only."
    ),
    AIReviewReason.NO_PROVIDER_KEYS: (
        "AI review was not used for this scan (no AI provider keys are configured). Findings "
        "come from deterministic rules only."
    ),
}
GENERIC_FAILURES = {
    "storage_error": "The scan results could not be stored.",
    "internal_error": "The scan failed because of an internal error.",
}


def create_scan(settings: Settings, target: str = "manifest", fail_on: str | None = None) -> str:
    """Record a new pending scan and return its ID. target is untrusted text."""
    if not target.strip() or len(target) > TARGET_CHARS:
        raise InvalidInput(
            "The target is invalid.",
            [Problem(field="target", reason=f"must be 1 to {TARGET_CHARS} characters")],
        )
    return db.create_scan(
        settings.database_path,
        target=target,
        target_type="manifest",
        fail_on=Severity(fail_on or settings.default_fail_on),
    )


def run_scan(settings: Settings, scan_id: str, raw: bytes, *, ai_review: bool = False) -> None:
    """Run a created scan to completed or failed. Stage failures do not raise.

    Raises InternalError if AI review is requested while provider keys are configured: there
    is no AI stage yet, and a report must never look as if AI review ran.
    """
    path = settings.database_path
    if ai_review and settings.providers_configured():
        db.fail_scan(path, scan_id, InternalError.code, GENERIC_FAILURES["internal_error"])
        raise InternalError("AI review was requested, but the AI review stage does not exist.")
    reason = AIReviewReason.NO_PROVIDER_KEYS if ai_review else AIReviewReason.DISABLED_BY_REQUEST

    stage = ScanStage.INGEST
    try:
        scan = db.get_scan(path, scan_id)
        if scan is None:
            raise InternalError("Unknown scan ID.")
        started_at = db.now()
        db.start_scan(path, scan_id, started_at)
        tools = parse_manifest(
            raw, max_bytes=settings.ingest_max_bytes, max_tools=settings.ingest_max_tools
        )

        stage = ScanStage.RULES
        db.set_stage(path, scan_id, stage)
        rules = active_rules(settings.rules_disabled, settings.rules_severity_overrides)
        findings = run_rules(
            tools, rules, description_length_threshold=settings.description_length_threshold
        )

        stage = ScanStage.SCORING
        db.set_stage(path, scan_id, stage)
        findings = scoring.aggregate(findings, tools)
        fail_on = Severity(scan["fail_on"])
        verdict = scoring.verdict(findings, fail_on)
        report = ScanReport(
            scan_id=scan_id,
            target=scan["target"],
            started_at=started_at,
            finished_at=db.now(),
            status=ScanStatus.COMPLETED,
            verdict=verdict,
            verdict_message=scoring.VERDICT_MESSAGES[verdict],
            verdict_basis=scoring.verdict_basis(findings, fail_on),
            fail_on=fail_on,
            tool_count=len(tools),
            counts=scoring.counts(findings),
            findings=findings,
            finding_groups=scoring.groups(findings),
            tool_results=scoring.tool_results(tools, findings),
            ai_review_status=AIReviewStatus.DISABLED,
            ai_review_reason=reason,
            ai_review_message=AI_DISABLED_MESSAGES[reason],
            ai_review_coverage=AIReviewCoverage(tools_rules_only=len(tools), note=COVERAGE_NOTE),
            versions=Versions(
                toolguard=version("toolguard"),
                rule_set=RULESET_VERSION,
                report_schema=json_report.REPORT_SCHEMA_VERSION,
            ),
            disclaimer=DISCLAIMER,
        )

        stage = ScanStage.REPORTING
        db.set_stage(path, scan_id, stage)
        safe = neutralize(report, tools)
        db.complete_scan(path, scan_id, tools, findings, safe, json_report.render(safe))
    except Exception as exc:
        code, message = _failure(exc)
        # Type name only: exception text can quote scanned content.
        logger.error("scan %s failed in stage %s: %s", scan_id, stage, type(exc).__name__)
        db.fail_scan(path, scan_id, code, message)


def _failure(exc: Exception) -> tuple[str, str]:
    """Stable code and generic message. Ingest messages never quote the input."""
    if isinstance(exc, InvalidInput | PayloadTooLarge):
        return exc.code, exc.message
    if isinstance(exc, sqlite3.Error | StorageError):
        return StorageError.code, GENERIC_FAILURES[StorageError.code]
    return InternalError.code, GENERIC_FAILURES[InternalError.code]
