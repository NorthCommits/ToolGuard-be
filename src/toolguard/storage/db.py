"""SQLite storage for scans, tools, and findings (sdd.md section 15).

Every operation opens its own short-lived connection, so threads never share one, and every
write is one short transaction. Scanned text is stored as received; it is never executed or
shown from here.

Migrations: MIGRATIONS is an ordered tuple of statement lists, tracked by PRAGMA user_version.
All missing ones run in one explicit transaction. Statements run one by one with execute(),
not executescript(), because executescript() commits any open transaction first.
"""

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from toolguard.core.errors import StorageError
from toolguard.core.models import Finding, ScanReport, ScanStage, ScanStatus, Severity, Tool
from toolguard.core.scoring import position

BUSY_TIMEOUT_SECONDS = 5.0

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """CREATE TABLE scans (
            id TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            target_type TEXT NOT NULL,
            status TEXT NOT NULL,
            current_stage TEXT NOT NULL,
            fail_on TEXT NOT NULL,
            verdict TEXT,
            ai_review_status TEXT,
            ai_review_reason TEXT,
            degraded_reason TEXT,
            failure_code TEXT,
            failure_message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            report_json TEXT,
            versions TEXT
        )""",
        """CREATE TABLE tools (
            id INTEGER PRIMARY KEY,
            scan_id TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            server_name TEXT NOT NULL,
            name TEXT NOT NULL,
            title TEXT,
            content_hash TEXT NOT NULL,
            description TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            schema_missing INTEGER NOT NULL,
            annotations_json TEXT NOT NULL,
            UNIQUE (scan_id, position)
        )""",
        """CREATE TABLE findings (
            scan_id TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            id TEXT NOT NULL,
            tool_id INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            explanation TEXT NOT NULL,
            suggested_action TEXT NOT NULL,
            confidence REAL,
            review_note TEXT,
            fingerprint TEXT NOT NULL,
            PRIMARY KEY (scan_id, id)
        )""",
        "CREATE INDEX scans_created_at ON scans (created_at)",
        "CREATE INDEX tools_content_hash ON tools (content_hash)",
        "CREATE INDEX findings_severity ON findings (scan_id, severity)",
        "CREATE INDEX findings_category ON findings (scan_id, category)",
        "CREATE INDEX findings_tool ON findings (tool_id)",
        "CREATE INDEX findings_fingerprint ON findings (fingerprint)",
    ),
    # M4: budget ledger and judge cache. Times are UTC seconds since the epoch.
    (
        """CREATE TABLE ai_calls (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            reserved_at REAL NOT NULL,
            finished_at REAL,
            outcome TEXT,
            requests INTEGER NOT NULL,
            estimated_tokens INTEGER NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER
        )""",
        "CREATE INDEX ai_calls_provider_time ON ai_calls (provider, reserved_at)",
        """CREATE TABLE provider_quota (
            provider TEXT PRIMARY KEY,
            observed_at REAL,
            limit_requests INTEGER,
            remaining_requests INTEGER,
            reset_requests REAL,
            limit_tokens INTEGER,
            remaining_tokens INTEGER,
            reset_tokens REAL,
            cooldown_until REAL,
            reported_daily_used INTEGER,
            reported_daily_limit INTEGER,
            reported_at REAL
        )""",
        """CREATE TABLE judge_cache (
            content_hash TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model_family TEXT NOT NULL,
            verdict_json TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (content_hash, prompt_version, model_family)
        )""",
        "CREATE INDEX judge_cache_created_at ON judge_cache (created_at)",
    ),
)

_SURROGATE = re.compile("[\ud800-\udfff]")


def now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection with foreign keys on and the schema migrated. Autocommit mode, so
    transactions are always explicit."""
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        # Both pragmas are ignored inside a transaction, so they run first.
        conn.execute("PRAGMA foreign_keys = ON")
        _use_wal(conn)
        migrate(conn)
        yield conn
    finally:
        conn.close()


def _use_wal(conn: sqlite3.Connection) -> None:
    """Switch the file to WAL, which persists, so readers and a writer can overlap.

    The switch needs an exclusive lock and does not wait on the busy timeout. If another
    connection holds a lock, keep the current mode: correctness does not depend on WAL, and a
    later connection switches it.
    """
    if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
        return
    with suppress(sqlite3.OperationalError):
        conn.execute("PRAGMA journal_mode = WAL")


def migrate(conn: sqlite3.Connection) -> None:
    """Apply missing migrations in one transaction. On any error nothing is applied."""
    if _version(conn) == len(MIGRATIONS):
        return
    with transaction(conn):
        version = _version(conn)  # again under the write lock, in case another thread migrated
        for statements in MIGRATIONS[version:]:
            for statement in statements:
                conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")


def _version(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise StorageError("The database schema is newer than this version of ToolGuard.")
    return version


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def _write(path: Path) -> Iterator[sqlite3.Connection]:
    with connect(path) as conn, transaction(conn):
        yield conn


def _text(value: str | None) -> str | None:
    """Text for a TEXT column. Lone surrogates (valid in JSON escapes, not in UTF-8) become
    U+FFFD, one character for one, so evidence offsets still hold."""
    return None if value is None else _SURROGATE.sub("\ufffd", value)


def _json(value: Any) -> str:
    # ensure_ascii keeps lone surrogates exact, as escapes.
    return json.dumps(value, ensure_ascii=True)


def create_scan(path: Path, *, target: str, target_type: str, fail_on: Severity) -> str:
    scan_id = uuid.uuid4().hex
    with _write(path) as conn:
        conn.execute(
            "INSERT INTO scans (id, target, target_type, status, current_stage, fail_on, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                _text(target),
                target_type,
                ScanStatus.PENDING,
                ScanStage.QUEUED,
                fail_on,
                now().isoformat(),
            ),
        )
    return scan_id


def start_scan(path: Path, scan_id: str, started_at: datetime) -> None:
    with _write(path) as conn:
        conn.execute(
            "UPDATE scans SET status = ?, current_stage = ?, started_at = ? WHERE id = ?",
            (ScanStatus.RUNNING, ScanStage.INGEST, started_at.isoformat(), scan_id),
        )


def set_stage(path: Path, scan_id: str, stage: ScanStage) -> None:
    with _write(path) as conn:
        conn.execute("UPDATE scans SET current_stage = ? WHERE id = ?", (stage, scan_id))


def complete_scan(
    path: Path,
    scan_id: str,
    tools: Sequence[Tool],
    findings: Sequence[Finding],
    report: ScanReport,
    report_json: str,
) -> None:
    """Write tools, findings, and the report in one transaction."""
    with _write(path) as conn:
        tool_ids = [
            conn.execute(
                "INSERT INTO tools (scan_id, position, server_name, name, title, content_hash, "
                "description, schema_json, schema_missing, annotations_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    index,
                    _text(tool.server_name),
                    _text(tool.name),
                    _text(tool.title),
                    tool.content_hash,
                    _text(tool.description),
                    _json(tool.input_schema),
                    tool.input_schema_missing,
                    _json(tool.annotations),
                ),
            ).lastrowid
            for index, tool in enumerate(tools)
        ]
        conn.executemany(
            "INSERT INTO findings (scan_id, id, tool_id, source, rule_id, category, severity, "
            "evidence_json, explanation, suggested_action, confidence, review_note, fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    scan_id,
                    f.id,
                    tool_ids[position(f)],
                    f.source,
                    f.rule_id,
                    f.category,
                    f.severity,
                    _json([e.model_dump() for e in f.evidence]),
                    _text(f.explanation),
                    _text(f.suggested_action),
                    f.confidence,
                    _text(f.review_note),
                    f.fingerprint,
                )
                for f in findings
            ],
        )
        conn.execute(
            "UPDATE scans SET status = ?, current_stage = ?, verdict = ?, ai_review_status = ?, "
            "ai_review_reason = ?, degraded_reason = ?, finished_at = ?, report_json = ?, "
            "versions = ? WHERE id = ?",
            (
                ScanStatus.COMPLETED,
                ScanStage.DONE,
                report.verdict,
                report.ai_review_status,
                report.ai_review_reason,
                report.degraded_reason,
                report.finished_at.isoformat() if report.finished_at else None,
                report_json,
                report.versions.model_dump_json(),
                scan_id,
            ),
        )


def fail_scan(path: Path, scan_id: str, code: str, message: str) -> None:
    """Mark a scan failed. current_stage is left at the stage that failed."""
    with _write(path) as conn:
        conn.execute(
            "UPDATE scans SET status = ?, failure_code = ?, failure_message = ?, "
            "finished_at = ? WHERE id = ?",
            (ScanStatus.FAILED, code, message, now().isoformat(), scan_id),
        )


def get_scan(path: Path, scan_id: str) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
    return dict(row) if row else None
