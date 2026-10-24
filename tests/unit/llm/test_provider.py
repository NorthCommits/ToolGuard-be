import asyncio
import logging
import socket

import httpx
import httpx2
import pytest
from llm_helpers import (
    GROQ_HEADERS,
    GROQ_KEY,
    OPENROUTER_KEY,
    T0,
    Clock,
    FakeGroq,
    FakeOpenRouter,
    call,
    keyed,
)

from toolguard.llm import provider
from toolguard.llm.budget import (
    DAY,
    GROQ,
    OPENROUTER,
    Ledger,
    RefusalReason,
    Reservation,
    next_utc_midnight,
)
from toolguard.llm.provider import (
    Failed,
    KeyInfo,
    Outcome,
    Refused,
    Succeeded,
    build_chain,
    parse_duration,
    parse_retry_after,
    parse_snapshot,
    read_openrouter_key_info,
    run_attempt,
)
from toolguard.storage import db


def attempt(settings, fake, clock=None, *, which=GROQ, estimate=500, led=None):
    led = led or Ledger(settings, clock or Clock())
    kwargs = {"groq_transport" if which == GROQ else "openrouter_transport": fake.transport}
    spec = next(s for s in build_chain(settings, **kwargs) if s.provider == which)
    return asyncio.run(run_attempt(led, spec, estimate, call)), led


def database_text(settings) -> str:
    with db.connect(settings.database_path) as conn:
        return "\n".join(conn.iterdump())


# Header parsing


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("10m4.8s", 604.8),
        ("592ms", 0.592),
        ("2m59.56s", 179.56),
        ("1h2m3s", 3_723.0),
        ("2d", 2 * DAY),
        ("0s", 0.0),
    ],
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize(
    "text",
    ["", None, "abc", "10x", "-5s", "5", "1e9s", "10m 4s", "8d", "99999999d", "\u0661\u0660s"],
)
def test_parse_duration_rejects_garbage(text) -> None:
    assert parse_duration(text) is None


@pytest.mark.parametrize(
    ("text", "seconds"), [("7", 7.0), ("2.5", 2.5), ("0", None), ("-3", None), ("100000", None)]
)
def test_parse_retry_after(text: str, seconds) -> None:
    assert parse_retry_after(text) == seconds
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None


def test_parse_snapshot() -> None:
    snap = parse_snapshot(GROQ_HEADERS)
    assert (snap.limit_requests, snap.remaining_requests, snap.reset_requests) == (1000, 993, 604.8)
    assert (snap.limit_tokens, snap.remaining_tokens, snap.reset_tokens) == (8000, 7880, 0.9)


@pytest.mark.parametrize(
    "change",
    [
        {"x-ratelimit-remaining-requests": None},
        {"x-ratelimit-remaining-requests": "-1"},
        {"x-ratelimit-remaining-requests": "2000"},  # more than the limit
        {"x-ratelimit-limit-tokens": "99999999999999"},
        {"x-ratelimit-limit-tokens": "0"},
        {"x-ratelimit-reset-tokens": "soon"},
        {"x-ratelimit-reset-requests": "1000d"},
        {"x-ratelimit-limit-requests": "\u0661\u0660"},
    ],
)
def test_malformed_headers_give_no_snapshot(change) -> None:
    headers = {**GROQ_HEADERS, **change}
    headers = {k: v for k, v in headers.items() if v is not None}
    assert parse_snapshot(headers) is None


# The chain


def test_chain_order_and_settings(settings) -> None:
    assert build_chain(settings) == []
    chain = build_chain(keyed(settings))
    assert [(s.provider, s.name) for s in chain] == [
        (GROQ, settings.primary_model),
        *((OPENROUTER, name) for name in settings.fallback_models),
    ]
    groq, fallback = chain[0], chain[1]
    assert groq.settings["max_retries"] == 0 and groq.settings["temperature"] == 0
    assert groq.settings["groq_reasoning_effort"] == "low"
    assert fallback.settings["max_retries"] == 0
    assert fallback.settings["openrouter_reasoning"] == {"effort": "low"}
    only_openrouter = keyed(settings, groq_api_key=None)
    assert [s.provider for s in build_chain(only_openrouter)] == [OPENROUTER] * 2


# Attempts


def test_groq_success_reads_headers_and_settles(settings) -> None:
    fake = FakeGroq(GROQ_HEADERS)
    result, _ = attempt(keyed(settings), fake)
    assert isinstance(result, Succeeded) and result.value.level == 3
    assert (result.requests, result.input_tokens, result.output_tokens) == (1, 100, 20)
    body = fake.bodies[0]
    assert body["reasoning_effort"] == "low" and body["temperature"] == 0
    with db.connect(settings.database_path) as conn:
        call_row = dict(conn.execute("SELECT * FROM ai_calls").fetchone())
        quota = dict(conn.execute("SELECT * FROM provider_quota").fetchone())
    assert call_row["status"] == "settled" and call_row["outcome"] == "succeeded"
    assert (call_row["input_tokens"], call_row["output_tokens"]) == (100, 20)
    assert (quota["remaining_requests"], quota["reset_requests"]) == (993, 604.8)
    assert quota["cooldown_until"] is None


def test_openrouter_success_through_httpx2(settings) -> None:
    fake = FakeOpenRouter(200)
    result, _ = attempt(keyed(settings), fake, which=OPENROUTER)
    assert isinstance(result, Succeeded)
    assert fake.bodies[0]["reasoning"] == {"effort": "low"}


def test_invalid_output_is_one_request_and_a_failed_attempt(settings) -> None:
    def bad(request):
        from llm_helpers import completion

        return httpx.Response(200, json=completion(request, arguments='{"level": "x"}'))

    fake = FakeGroq(bad, bad)
    result, _ = attempt(keyed(settings), fake)
    assert result == Failed(Outcome.INVALID_OUTPUT)
    assert len(fake.bodies) == 1  # no validation retry inside an attempt


def test_429_sets_cooldown_from_retry_after_without_sdk_retry(settings) -> None:
    clock = Clock()
    fake = FakeGroq(lambda r: httpx.Response(429, headers={"retry-after": "7"}, json={"error": {}}))
    result, led = attempt(keyed(settings), fake, clock)
    assert result == Failed(Outcome.RATE_LIMITED, 429, T0 + 7)
    assert len(fake.bodies) == 1
    assert led.reserve(GROQ, "m", 10).reason is RefusalReason.COOLING_DOWN


@pytest.mark.parametrize(
    ("headers", "cooldown"),
    [
        ({**GROQ_HEADERS, "x-ratelimit-remaining-requests": "0"}, 604.8),
        ({"retry-after": "soon", "x-ratelimit-reset-requests": "-1"}, 60.0),
        ({"retry-after": "99999999"}, 60.0),
    ],
)
def test_429_cooldown_falls_back_to_headers_then_default(settings, headers, cooldown) -> None:
    fake = FakeGroq(lambda r: httpx.Response(429, headers=headers, json={}))
    result, _ = attempt(keyed(settings), fake)
    assert result.cooldown_until == pytest.approx(T0 + cooldown)


def test_401_disables_the_provider_for_this_process(settings) -> None:
    fake = FakeGroq(401, 200)
    result, led = attempt(keyed(settings), fake)
    assert result == Failed(Outcome.AUTH, 401)
    again, _ = attempt(keyed(settings), fake, led=led)
    assert again == Refused(RefusalReason.PROVIDER_DISABLED)
    assert len(fake.bodies) == 1


def test_403_from_hostile_input_does_not_change_provider_state(settings) -> None:
    """A crafted description may trigger a moderation block; it must not switch AI off."""
    fake = FakeGroq(403, 200)
    result, led = attempt(keyed(settings), fake)
    assert result == Failed(Outcome.FORBIDDEN, 403)
    assert GROQ not in led.disabled
    with db.connect(settings.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM provider_quota").fetchone()[0] == 0
    again, _ = attempt(keyed(settings), fake, led=led)
    assert isinstance(again, Succeeded)


def test_402_is_a_refusal_with_a_long_cooldown(settings) -> None:
    fake = FakeOpenRouter(402)
    result, led = attempt(keyed(settings), fake, which=OPENROUTER)
    assert result == Refused(RefusalReason.PAYMENT_REQUIRED, next_utc_midnight(T0))
    assert OPENROUTER not in led.disabled
    assert led.reserve(OPENROUTER, "m", 10).reason is RefusalReason.COOLING_DOWN


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (500, Failed(Outcome.SERVER_ERROR, 500)),
        (418, Failed(Outcome.HTTP_ERROR, 418)),
        (httpx.ReadTimeout, Failed(Outcome.TIMEOUT)),
        (httpx.ConnectError, Failed(Outcome.CONNECTION)),
    ],
)
def test_other_failures_have_no_cooldown(settings, reply, expected) -> None:
    result, led = attempt(keyed(settings), FakeGroq(reply))
    assert result == expected
    with db.connect(settings.database_path) as conn:
        row = conn.execute("SELECT requests, outcome FROM ai_calls").fetchone()
    assert tuple(row) == (1, expected.kind)
    assert isinstance(led.reserve(GROQ, "m", 10), Reservation)


def test_openrouter_timeout_through_httpx2(settings) -> None:
    result, _ = attempt(keyed(settings), FakeOpenRouter(httpx2.ReadTimeout), which=OPENROUTER)
    assert result == Failed(Outcome.TIMEOUT)


def test_error_bodies_and_keys_never_leak(settings, caplog) -> None:
    """Provider error text can quote the key or the scanned request; none of it is kept."""
    caplog.set_level(logging.DEBUG)
    results = []
    led = Ledger(keyed(settings), Clock())
    for reply in (429, 403, 500, 401):
        result, _ = attempt(keyed(settings), FakeGroq(reply), led=led)
        results.append(result)
        led.disabled.clear()
        with db.connect(settings.database_path) as conn, db.transaction(conn):
            conn.execute("DELETE FROM provider_quota")
    stored = database_text(settings)
    for text in (GROQ_KEY, OPENROUTER_KEY, "SCANNED-TEXT", "ignore rules"):
        assert text not in repr(results)
        assert text not in caplog.text
        assert text not in stored


def test_short_rate_limit_waits_once_and_long_one_is_refused(settings, monkeypatch) -> None:
    clock = Clock()
    led = Ledger(keyed(settings, rpm_groq=1), clock)
    led.reserve(GROQ, "m", 10)
    clock.advance(45)  # the window frees in 15 seconds, under the 20 second wait limit
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(provider.asyncio, "sleep", fake_sleep)
    result, _ = attempt(keyed(settings, rpm_groq=1), FakeGroq(200), led=led)
    assert isinstance(result, Succeeded) and waited == [pytest.approx(15)]

    clock.advance(1)  # now the window frees in 59 seconds: too long to wait
    result, _ = attempt(keyed(settings, rpm_groq=1), FakeGroq(200), led=led)
    assert result == Refused(RefusalReason.RATE_LIMITED, pytest.approx(clock.now + 59))


def test_unexpected_exception_is_settled_and_raised(settings) -> None:
    led = Ledger(keyed(settings), Clock())
    spec = build_chain(keyed(settings), groq_transport=FakeGroq().transport)[0]

    async def broken(spec):
        raise KeyError("bug")

    with pytest.raises(KeyError):
        asyncio.run(run_attempt(led, spec, 10, broken))
    with db.connect(settings.database_path) as conn:
        assert conn.execute("SELECT status, outcome FROM ai_calls").fetchone()[:] == (
            "settled",
            "error",
        )


def test_network_guard_blocks_real_hosts() -> None:
    with pytest.raises(RuntimeError):
        socket.getaddrinfo("api.groq.com", 443)


# OpenRouter key info


def key_info_transport(status: int = 200, payload=None):
    seen: list[str] = []

    def handler(request):
        seen.append(request.headers["authorization"])
        body = payload or {
            "data": {"free_model_daily_requests": {"used": 7, "limit": 50, "remaining": 43}}
        }
        return httpx2.Response(status, json=body)

    return httpx2.MockTransport(handler), seen


def test_key_info_is_read_and_cached(settings, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    clock = Clock()
    led = Ledger(keyed(settings), clock)
    transport, seen = key_info_transport()
    info = asyncio.run(read_openrouter_key_info(led, transport=transport))
    assert info == KeyInfo(7, 50, 43)
    clock.advance(60)
    assert asyncio.run(read_openrouter_key_info(led, transport=transport)) == info
    assert len(seen) == 1  # the minimum interval keeps the second read local
    with db.connect(settings.database_path) as conn:
        row = conn.execute("SELECT reported_daily_used, reported_daily_limit FROM provider_quota")
        assert tuple(row.fetchone()) == (7, 50)
    assert OPENROUTER_KEY not in caplog.text and OPENROUTER_KEY not in database_text(settings)


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (401, None),
        (200, {"data": {"free_model_daily_requests": {"used": -4}}}),
        (200, {"data": {"free_model_daily_requests": {"used": "many"}}}),
        (200, {"unexpected": True}),
    ],
)
def test_bad_key_info_is_ignored(settings, status, payload) -> None:
    led = Ledger(keyed(settings), Clock())
    transport, _ = key_info_transport(status, payload)
    assert asyncio.run(read_openrouter_key_info(led, transport=transport)) is None
    with db.connect(settings.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM provider_quota").fetchone()[0] == 0


def test_key_info_needs_a_key(settings) -> None:
    led = Ledger(settings, Clock())
    assert asyncio.run(read_openrouter_key_info(led)) is None
