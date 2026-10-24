import asyncio
import threading

import pytest
from llm_helpers import T0, Clock

from toolguard.llm.budget import (
    DAY,
    GROQ,
    OPENROUTER,
    Ledger,
    QuotaSnapshot,
    Refusal,
    RefusalReason,
    Reservation,
    next_utc_midnight,
)
from toolguard.storage import db


def ledger(settings, clock=None, **update) -> Ledger:
    return Ledger(settings.model_copy(update=update), clock or Clock())


def ok(result) -> Reservation:
    assert isinstance(result, Reservation), result
    return result


def refused(result, reason: RefusalReason) -> Refusal:
    assert isinstance(result, Refusal) and result.reason is reason, result
    return result


def snapshot(remaining_requests=993, remaining_tokens=7_880) -> QuotaSnapshot:
    return QuotaSnapshot(1_000, remaining_requests, 604.8, 8_000, remaining_tokens, 0.9)


# Groq: our own counters


def test_groq_requests_per_minute(settings) -> None:
    clock = Clock()
    led = ledger(settings, clock)
    for _ in range(settings.rpm_groq):
        ok(led.reserve(GROQ, "m", 10))
    result = refused(led.reserve(GROQ, "m", 10), RefusalReason.RATE_LIMITED)
    assert result.retry_at == T0 + 60
    clock.advance(60)
    ok(led.reserve(GROQ, "m", 10))


def test_groq_tokens_per_minute_and_too_large(settings) -> None:
    led = ledger(settings)
    refused(led.reserve(GROQ, "m", settings.tpm_groq + 1), RefusalReason.TOO_LARGE)
    ok(led.reserve(GROQ, "m", 7_000))
    refused(led.reserve(GROQ, "m", 300), RefusalReason.RATE_LIMITED)


def test_settled_actual_tokens_replace_the_estimate(settings) -> None:
    led = ledger(settings)
    first = ok(led.reserve(GROQ, "m", 7_000))
    led.settle(first, outcome="succeeded", requests=1, input_tokens=100, output_tokens=20)
    ok(led.reserve(GROQ, "m", 7_000))


def test_groq_daily_budgets(settings) -> None:
    clock = Clock()
    led = ledger(settings, clock, daily_budget_groq_requests=2, daily_budget_groq_tokens=10_000)
    ok(led.reserve(GROQ, "m", 10))
    clock.advance(61)
    ok(led.reserve(GROQ, "m", 10))
    clock.advance(61)
    result = refused(led.reserve(GROQ, "m", 10), RefusalReason.BUDGET_EXHAUSTED)
    assert result.retry_at == T0 + DAY

    led = ledger(settings, Clock(T0 + 10 * DAY), daily_budget_groq_tokens=1_000)
    ok(led.reserve(GROQ, "m", 900))
    refused(led.reserve(GROQ, "m", 200), RefusalReason.BUDGET_EXHAUSTED)


# Groq: header snapshot


def test_snapshot_keeps_the_request_reserve_and_refills(settings) -> None:
    clock = Clock()
    led = ledger(settings, clock)  # daily budget 900 of 1000 keeps 100 in reserve
    first = ok(led.reserve(GROQ, "m", 10))
    led.settle(first, outcome="succeeded", requests=1, snapshot=snapshot(remaining_requests=101))
    clock.advance(61)
    ok(led.reserve(GROQ, "m", 10))  # 101 available, 100 kept
    clock.advance(1)
    result = refused(led.reserve(GROQ, "m", 10), RefusalReason.BUDGET_EXHAUSTED)
    # One request refills every 86.4 seconds; 62 seconds have passed since the snapshot.
    assert result.retry_at == pytest.approx(clock.now + 86.4 - 62, abs=0.01)
    clock.now = result.retry_at
    ok(led.reserve(GROQ, "m", 10))


def test_snapshot_tokens_per_minute(settings) -> None:
    clock = Clock()
    led = ledger(settings, clock)
    first = ok(led.reserve(GROQ, "m", 10))
    led.settle(
        first,
        outcome="succeeded",
        requests=1,
        input_tokens=5,
        output_tokens=5,
        snapshot=snapshot(remaining_tokens=500),
    )
    result = refused(led.reserve(GROQ, "m", 1_000), RefusalReason.RATE_LIMITED)
    assert result.retry_at == pytest.approx(clock.now + 500 * 60 / 8_000)
    clock.advance(4)
    ok(led.reserve(GROQ, "m", 1_000))


def test_restart_keeps_usage_snapshot_and_cooldown_but_not_disabling(settings) -> None:
    clock = Clock()
    led = ledger(settings, clock, rpm_groq=1)
    first = ok(led.reserve(GROQ, "m", 10))
    led.settle(first, outcome="rate_limited", requests=1, cooldown_until=T0 + 30)
    led.disable(OPENROUTER)

    restarted = ledger(settings, clock, rpm_groq=1)
    refused(restarted.reserve(GROQ, "m", 10), RefusalReason.COOLING_DOWN)
    clock.advance(31)
    refused(restarted.reserve(GROQ, "m", 10), RefusalReason.RATE_LIMITED)  # log survived
    clock.advance(30)
    ok(restarted.reserve(GROQ, "m", 10))
    ok(restarted.reserve(OPENROUTER, "m", 10))  # disabling is per process


def test_restart_without_snapshot_uses_own_counters(settings) -> None:
    clock = Clock()
    ok(ledger(settings, clock, daily_budget_groq_requests=1).reserve(GROQ, "m", 10))
    clock.advance(120)
    restarted = ledger(settings, clock, daily_budget_groq_requests=1)
    refused(restarted.reserve(GROQ, "m", 10), RefusalReason.BUDGET_EXHAUSTED)


def test_cooldown_keeps_the_later_time(settings) -> None:
    led = ledger(settings)
    first = ok(led.reserve(GROQ, "m", 10))
    led.settle(first, outcome="rate_limited", requests=1, cooldown_until=T0 + 100)
    with db.connect(settings.database_path) as conn, db.transaction(conn):
        conn.execute("UPDATE provider_quota SET cooldown_until = NULL")
    second = ok(led.reserve(GROQ, "m", 10))
    led.settle(second, outcome="rate_limited", requests=1, cooldown_until=T0 + 50)
    led.settle(second, outcome="rate_limited", requests=1, cooldown_until=T0 + 20)
    assert refused(led.reserve(GROQ, "m", 10), RefusalReason.COOLING_DOWN).retry_at == T0 + 50


# Abandoned reservations


def test_abandoned_reservations_leave_the_minute_window_but_count_daily(settings) -> None:
    clock = Clock()
    led = ledger(
        settings, clock, rpm_groq=1, daily_budget_groq_requests=2, ai_request_timeout_seconds=5
    )
    ok(led.reserve(GROQ, "m", 10))  # never settled: a crash or a hang
    clock.advance(10)
    refused(led.reserve(GROQ, "m", 10), RefusalReason.RATE_LIMITED)
    clock.advance(26)  # older than the 5 second timeout plus the 30 second margin
    ok(led.reserve(GROQ, "m", 10))
    clock.advance(61)
    refused(led.reserve(GROQ, "m", 10), RefusalReason.BUDGET_EXHAUSTED)
    with db.connect(settings.database_path) as conn:
        statuses = [r[0] for r in conn.execute("SELECT status FROM ai_calls ORDER BY id")]
    # The second reservation has now hung past the timeout as well.
    assert statuses == ["abandoned", "abandoned"]


# Concurrency


def test_concurrent_reservations_from_threads_never_overcommit(settings) -> None:
    led = ledger(settings)
    results: list = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(5):
            result = led.reserve(GROQ, "m", 10)
            with lock:
                results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(isinstance(r, Reservation) for r in results) == settings.rpm_groq


def test_concurrent_reservations_from_tasks_never_overcommit(settings) -> None:
    led = ledger(settings)

    async def main() -> list:
        tasks = [asyncio.to_thread(led.reserve, OPENROUTER, "m", 10) for _ in range(40)]
        return await asyncio.gather(*tasks)

    results = asyncio.run(main())
    assert sum(isinstance(r, Reservation) for r in results) == settings.rpm_openrouter


# OpenRouter


def test_openrouter_daily_budget_resets_at_utc_midnight(settings) -> None:
    clock = Clock(next_utc_midnight(T0) - 120)
    led = ledger(settings, clock, daily_budget_openrouter_requests=1)
    ok(led.reserve(OPENROUTER, "m", 10))
    result = refused(led.reserve(OPENROUTER, "m", 10), RefusalReason.BUDGET_EXHAUSTED)
    assert result.retry_at == next_utc_midnight(T0)
    clock.now = result.retry_at
    ok(led.reserve(OPENROUTER, "m", 10))


@pytest.mark.parametrize(
    ("used", "limit", "allowed"),
    [
        (0, None, 5),  # nothing reported beyond our own count
        (3, None, 2),  # a larger reported count wins, plus our calls after the reading
        (0, 2, 2),  # a lower reported limit lowers the budget
        (0, 1_000, 5),  # a higher reported limit never raises our budget
    ],
)
def test_openrouter_reported_counter_cross_check(settings, used, limit, allowed) -> None:
    clock = Clock()
    led = ledger(settings, clock, daily_budget_openrouter_requests=5)
    led.record_reported(used, limit)
    granted = 0
    for _ in range(10):
        clock.advance(61)
        granted += isinstance(led.reserve(OPENROUTER, "m", 10), Reservation)
    assert granted == allowed


def test_reported_counter_from_an_earlier_day_is_ignored(settings) -> None:
    clock = Clock()
    led = ledger(settings, clock, daily_budget_openrouter_requests=5)
    led.record_reported(5, 5)
    refused(led.reserve(OPENROUTER, "m", 10), RefusalReason.BUDGET_EXHAUSTED)
    clock.now = next_utc_midnight(clock.now) + 1
    ok(led.reserve(OPENROUTER, "m", 10))


def test_disabled_provider_is_refused(settings) -> None:
    led = ledger(settings)
    led.disable(GROQ)
    refused(led.reserve(GROQ, "m", 10), RefusalReason.PROVIDER_DISABLED)
    ok(led.reserve(OPENROUTER, "m", 10))
