"""Budget ledger and rate limiting (sdd.md sections 9.3 and 10).

Every model attempt reserves before its one HTTP request and settles after it. Reserving runs
in one BEGIN IMMEDIATE transaction, so concurrent attempts in threads, tasks, or processes
sharing the database cannot overcommit: open reservations count at their estimate.

Our own counters always apply. Groq's quota headers apply on top when a snapshot exists.
Cooldowns come only from real 429 or 402 responses and their headers, never from scanned text.
Times are UTC seconds since the epoch, from an injectable clock.

Per process only: the provider-disabled flag (after a 401) and the cached OpenRouter key info.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from toolguard.config import Settings
from toolguard.storage import db

GROQ = "groq"
OPENROUTER = "openrouter"
MINUTE = 60.0
DAY = 86_400.0
# A reservation older than the request timeout plus this is treated as abandoned.
ABANDON_MARGIN_SECONDS = 30.0
SANE_MAX_COUNT = 10_000_000


class RefusalReason(StrEnum):
    PROVIDER_DISABLED = "provider_disabled"
    COOLING_DOWN = "cooling_down"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TOO_LARGE = "too_large"  # the estimate can never fit the per-minute token limit
    PAYMENT_REQUIRED = "payment_required"


@dataclass(frozen=True)
class Refusal:
    reason: RefusalReason
    retry_at: float | None = None


@dataclass(frozen=True)
class Reservation:
    id: int
    provider: str
    model: str
    estimated_tokens: int


@dataclass(frozen=True)
class QuotaSnapshot:
    """Groq x-ratelimit-* headers: requests are per day, tokens per minute."""

    limit_requests: int
    remaining_requests: int
    reset_requests: float
    limit_tokens: int
    remaining_tokens: int
    reset_tokens: float


@dataclass(frozen=True)
class KeyInfo:
    """OpenRouter free_model_daily_requests: counts only, nothing that identifies the account."""

    used: int
    limit: int | None
    remaining: int | None


class _Usage(NamedTuple):
    requests: int
    tokens: int
    oldest: float | None


def sane_count(value: object) -> int | None:
    """A non-negative integer of plausible size, or None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= SANE_MAX_COUNT else None


def next_utc_midnight(now: float) -> float:
    return (now // DAY + 1) * DAY


class Ledger:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.clock = clock
        self.disabled: set[str] = set()
        self.key_info: KeyInfo | None = None
        self.key_info_read_at: float | None = None

    def disable(self, provider: str) -> None:
        """After a 401: the key is invalid, so stop using this provider in this process."""
        self.disabled.add(provider)

    def reserve(self, provider: str, model: str, estimated_tokens: int) -> Reservation | Refusal:
        now = self.clock()
        if provider in self.disabled:
            return Refusal(RefusalReason.PROVIDER_DISABLED)
        with db.connect(self.settings.database_path) as conn, db.transaction(conn):
            stale = now - self.settings.ai_request_timeout_seconds - ABANDON_MARGIN_SECONDS
            conn.execute(
                "UPDATE ai_calls SET status = 'abandoned' "
                "WHERE status = 'reserved' AND reserved_at < ?",
                (stale,),
            )
            quota = conn.execute(
                "SELECT * FROM provider_quota WHERE provider = ?", (provider,)
            ).fetchone()
            if quota and quota["cooldown_until"] and quota["cooldown_until"] > now:
                return Refusal(RefusalReason.COOLING_DOWN, quota["cooldown_until"])
            check = self._check_groq if provider == GROQ else self._check_openrouter
            refusal = check(conn, quota, now, estimated_tokens)
            if refusal:
                return refusal
            cursor = conn.execute(
                "INSERT INTO ai_calls (provider, model, status, reserved_at, requests, "
                "estimated_tokens) VALUES (?, ?, 'reserved', ?, 1, ?)",
                (provider, model, now, estimated_tokens),
            )
        return Reservation(cursor.lastrowid, provider, model, estimated_tokens)

    def settle(
        self,
        reservation: Reservation,
        *,
        outcome: str,
        requests: int,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        snapshot: QuotaSnapshot | None = None,
        cooldown_until: float | None = None,
    ) -> None:
        """Record what an attempt really used. Unknown tokens keep the estimate."""
        now = self.clock()
        with db.connect(self.settings.database_path) as conn, db.transaction(conn):
            conn.execute(
                "UPDATE ai_calls SET status = 'settled', finished_at = ?, outcome = ?, "
                "requests = ?, input_tokens = ?, output_tokens = ? WHERE id = ?",
                (now, outcome, max(1, requests), input_tokens, output_tokens, reservation.id),
            )
            if snapshot is not None or cooldown_until is not None:
                _ensure_quota_row(conn, reservation.provider)
            if snapshot is not None:
                conn.execute(
                    "UPDATE provider_quota SET observed_at = ?, limit_requests = ?, "
                    "remaining_requests = ?, reset_requests = ?, limit_tokens = ?, "
                    "remaining_tokens = ?, reset_tokens = ? WHERE provider = ?",
                    (
                        now,
                        snapshot.limit_requests,
                        snapshot.remaining_requests,
                        snapshot.reset_requests,
                        snapshot.limit_tokens,
                        snapshot.remaining_tokens,
                        snapshot.reset_tokens,
                        reservation.provider,
                    ),
                )
            if cooldown_until is not None:
                conn.execute(
                    "UPDATE provider_quota SET cooldown_until = "
                    "max(coalesce(cooldown_until, 0), ?) WHERE provider = ?",
                    (cooldown_until, reservation.provider),
                )

    def record_reported(self, used: int, limit: int | None) -> None:
        """OpenRouter's own daily counter, read occasionally as a cross-check."""
        with db.connect(self.settings.database_path) as conn, db.transaction(conn):
            _ensure_quota_row(conn, OPENROUTER)
            conn.execute(
                "UPDATE provider_quota SET reported_daily_used = ?, reported_daily_limit = ?, "
                "reported_at = ? WHERE provider = ?",
                (used, limit, self.clock(), OPENROUTER),
            )

    def _check_groq(self, conn, quota, now: float, estimate: int) -> Refusal | None:
        s = self.settings
        if estimate > s.tpm_groq:
            return Refusal(RefusalReason.TOO_LARGE)
        minute = _usage(conn, GROQ, now - MINUTE, include_abandoned=False)
        day = _usage(conn, GROQ, now - DAY, include_abandoned=True)
        if minute.requests + 1 > s.rpm_groq or minute.tokens + estimate > s.tpm_groq:
            return Refusal(RefusalReason.RATE_LIMITED, (minute.oldest or now) + MINUTE)
        if (
            day.requests + 1 > s.daily_budget_groq_requests
            or day.tokens + estimate > s.daily_budget_groq_tokens
        ):
            return Refusal(RefusalReason.BUDGET_EXHAUSTED, (day.oldest or now) + DAY)
        if quota is None or quota["observed_at"] is None:
            return None  # no snapshot yet (first call or a fresh database): own counters only

        # The snapshot does not include attempts reserved after it was observed.
        observed = quota["observed_at"]
        elapsed = max(0.0, now - observed)
        since = _usage(conn, GROQ, observed, include_abandoned=True)
        limit_r = quota["limit_requests"]
        available = min(limit_r, quota["remaining_requests"] + elapsed * limit_r / DAY)
        available -= since.requests
        keep = max(0, limit_r - s.daily_budget_groq_requests)
        if available - 1 < keep:
            wait = (keep + 1 - available) * DAY / limit_r
            return Refusal(RefusalReason.BUDGET_EXHAUSTED, now + wait)
        # After a minute the token bucket has refilled; our own minute counter covers the rest.
        if elapsed < MINUTE:
            limit_t = quota["limit_tokens"]
            tokens = min(limit_t, quota["remaining_tokens"] + elapsed * limit_t / MINUTE)
            tokens -= since.tokens
            if estimate > tokens:
                wait = (estimate - tokens) * MINUTE / limit_t
                return Refusal(RefusalReason.RATE_LIMITED, now + wait)
        return None

    def _check_openrouter(self, conn, quota, now: float, estimate: int) -> Refusal | None:
        s = self.settings
        minute = _usage(conn, OPENROUTER, now - MINUTE, include_abandoned=False)
        if minute.requests + 1 > s.rpm_openrouter:
            return Refusal(RefusalReason.RATE_LIMITED, (minute.oldest or now) + MINUTE)
        # The free-model counter is one pool for all free models, per UTC day.
        day_start = now // DAY * DAY
        used = _usage(conn, OPENROUTER, day_start - 0.001, include_abandoned=True).requests
        budget = s.daily_budget_openrouter_requests
        reported_at = quota["reported_at"] if quota else None
        if reported_at is not None and reported_at >= day_start:
            # The larger of our count and the reported one, plus our calls since the reading.
            after = _usage(conn, OPENROUTER, reported_at, include_abandoned=True).requests
            used = max(used, (quota["reported_daily_used"] or 0) + after)
            reported_limit = quota["reported_daily_limit"]
            if reported_limit:  # a reported limit may lower our budget, never raise it
                budget = min(budget, reported_limit)
        if used + 1 > budget:
            return Refusal(RefusalReason.BUDGET_EXHAUSTED, next_utc_midnight(now))
        return None


def _ensure_quota_row(conn, provider: str) -> None:
    conn.execute(
        "INSERT INTO provider_quota (provider) VALUES (?) ON CONFLICT DO NOTHING", (provider,)
    )


def _usage(conn, provider: str, since: float, *, include_abandoned: bool) -> _Usage:
    """Requests and tokens of attempts reserved strictly after a time, so a row leaves a
    window exactly when the window has passed it. Tokens are actual once
    settled with known usage, the estimate otherwise. Abandoned reservations still count for
    daily totals but no longer hold a per-minute window."""
    statuses = ("reserved", "settled", "abandoned")[: 3 if include_abandoned else 2]
    row = conn.execute(
        "SELECT coalesce(sum(requests), 0), "
        "coalesce(sum(CASE WHEN status = 'settled' AND input_tokens IS NOT NULL "
        "THEN input_tokens + coalesce(output_tokens, 0) ELSE estimated_tokens END), 0), "
        "min(reserved_at) FROM ai_calls WHERE provider = ? AND "
        "reserved_at > ? "
        f"AND status IN ({', '.join('?' * len(statuses))})",
        (provider, since, *statuses),
    ).fetchone()
    return _Usage(row[0], row[1], row[2])
