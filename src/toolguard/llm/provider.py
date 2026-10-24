"""Model layer (sdd.md section 9): the ordered model list, quota headers, and one attempt.

An attempt is one HTTP request to one model, reserved with the ledger before and settled after.
The judge (M5) runs its agent with output retries set to 0, so a validation failure is never a
second unreserved request inside an attempt; the judge's loop starts a new attempt instead.

Provider details stay here: Groq's SDK takes an httpx client and the OpenAI SDK used for
OpenRouter takes an httpx2 client; both get a response hook that reads quota headers. Results
carry only a kind, a status code, and times. Provider error text, which can quote the request,
never reaches a result, a log line, or the database.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

import httpx
import httpx2
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelTimeoutError,
    UnexpectedModelBehavior,
)
from pydantic_ai.models import Model
from pydantic_ai.models.groq import GroqModel, GroqModelSettings
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.groq import GroqProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

from toolguard.config import Settings
from toolguard.llm.budget import (
    DAY,
    GROQ,
    OPENROUTER,
    KeyInfo,
    Ledger,
    QuotaSnapshot,
    Refusal,
    RefusalReason,
    sane_count,
)

logger = logging.getLogger(__name__)

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
KEY_INFO_MIN_INTERVAL_SECONDS = 600.0
MAX_HEADER_DURATION = 7 * DAY
QUOTA_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
    "retry-after",
)
_DURATION_PART = re.compile(r"([0-9]{1,9}(?:\.[0-9]{1,9})?)(ms|d|h|m|s)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3_600.0, "d": DAY}
_SECONDS = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,9})?")

T = TypeVar("T")


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    RATE_LIMITED = "rate_limited"  # 429
    PAYMENT_REQUIRED = "payment_required"  # 402
    AUTH = "auth"  # 401: the provider is disabled for this process
    FORBIDDEN = "forbidden"  # 403: may be a moderation block on this one input
    SERVER_ERROR = "server_error"  # 5xx
    HTTP_ERROR = "http_error"  # any other status
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_OUTPUT = "invalid_output"
    ERROR = "error"  # not a provider failure; re-raised after settling


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    name: str
    model: Model
    settings: ModelSettings


@dataclass(frozen=True)
class Succeeded(Generic[T]):
    value: T
    requests: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Refused:
    reason: RefusalReason
    retry_at: float | None = None


@dataclass(frozen=True)
class Failed:
    kind: Outcome
    status_code: int | None = None
    cooldown_until: float | None = None


AttemptResult = Succeeded[T] | Refused | Failed


@dataclass
class _Seen:
    """What the response hook saw during one attempt: counts and quota headers only."""

    responses: int = 0
    headers: dict[str, str] = field(default_factory=dict)


_ATTEMPT: ContextVar[_Seen | None] = ContextVar("toolguard_attempt", default=None)


async def _on_response(response: httpx.Response | httpx2.Response) -> None:
    seen = _ATTEMPT.get()
    if seen is not None:
        seen.responses += 1
        seen.headers = {k: response.headers[k] for k in QUOTA_HEADERS if k in response.headers}


def build_chain(
    settings: Settings,
    *,
    groq_transport: httpx.AsyncBaseTransport | None = None,
    openrouter_transport: httpx2.AsyncBaseTransport | None = None,
) -> list[ModelSpec]:
    """Groq primary first, then the OpenRouter fallbacks; providers without a key are skipped.
    Transports are for tests only."""
    common: dict[str, Any] = {
        "temperature": 0.0,
        "max_retries": 0,  # SDK retries would hide real requests from the ledger
        "timeout": settings.ai_request_timeout_seconds,
    }
    hooks = {"response": [_on_response]}
    chain: list[ModelSpec] = []
    if settings.groq_api_key is not None:
        client = httpx.AsyncClient(
            timeout=settings.ai_request_timeout_seconds, event_hooks=hooks, transport=groq_transport
        )
        provider = GroqProvider(
            api_key=settings.groq_api_key.get_secret_value(), http_client=client
        )
        chain.append(
            ModelSpec(
                GROQ,
                settings.primary_model,
                GroqModel(settings.primary_model, provider=provider),
                GroqModelSettings(**common, groq_reasoning_effort=settings.judge_reasoning_effort),
            )
        )
    if settings.openrouter_api_key is not None:
        client2 = httpx2.AsyncClient(
            timeout=settings.ai_request_timeout_seconds,
            event_hooks=hooks,
            transport=openrouter_transport,
        )
        provider2 = OpenRouterProvider(
            api_key=settings.openrouter_api_key.get_secret_value(), http_client=client2
        )
        for name in settings.fallback_models:
            chain.append(
                ModelSpec(
                    OPENROUTER,
                    name,
                    OpenRouterModel(name, provider=provider2),
                    OpenRouterModelSettings(
                        **common,
                        openrouter_reasoning={"effort": settings.openrouter_reasoning_effort},
                    ),
                )
            )
    return chain


def parse_duration(text: str | None) -> float | None:
    """Seconds from a duration such as 10m4.8s, 592ms, 2m59.56s, or 1h2m. None if invalid."""
    if not text or len(text) > 64:
        return None
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(number + unit for number, unit in parts) != text:
        return None
    seconds = sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)
    return seconds if seconds <= MAX_HEADER_DURATION else None


def parse_retry_after(text: str | None) -> float | None:
    """retry-after in seconds, if it is a plausible number; dates and garbage give None."""
    if not text or not _SECONDS.fullmatch(text.strip()):
        return None
    seconds = float(text)
    return seconds if 0 < seconds <= DAY else None


def parse_snapshot(headers: dict[str, str]) -> QuotaSnapshot | None:
    """All six Groq quota headers, each valid, or None so our own counters are used."""

    def count(name: str) -> int | None:
        value = headers.get(name, "").strip()
        ok = value.isascii() and value.isdigit() and len(value) <= 9
        return sane_count(int(value)) if ok else None

    limit_r, remaining_r = count(QUOTA_HEADERS[0]), count(QUOTA_HEADERS[1])
    limit_t, remaining_t = count(QUOTA_HEADERS[3]), count(QUOTA_HEADERS[4])
    reset_r = parse_duration(headers.get(QUOTA_HEADERS[2]))
    reset_t = parse_duration(headers.get(QUOTA_HEADERS[5]))
    values = (limit_r, remaining_r, reset_r, limit_t, remaining_t, reset_t)
    if any(v is None for v in values) or not limit_r or not limit_t:
        return None
    if remaining_r > limit_r or remaining_t > limit_t:
        return None
    return QuotaSnapshot(limit_r, remaining_r, reset_r, limit_t, remaining_t, reset_t)


def cooldown_after_429(headers: dict[str, str], now: float, default: float) -> float:
    """retry-after if valid, else the reset of an exhausted bucket, else the default."""
    retry_after = parse_retry_after(headers.get("retry-after"))
    if retry_after is not None:
        return now + retry_after
    snapshot = parse_snapshot(headers)
    if snapshot is not None:
        resets = [
            reset
            for remaining, reset in (
                (snapshot.remaining_requests, snapshot.reset_requests),
                (snapshot.remaining_tokens, snapshot.reset_tokens),
            )
            if remaining == 0
        ]
        if resets:
            return now + max(resets)
    return now + default


async def run_attempt(
    ledger: Ledger,
    spec: ModelSpec,
    estimated_tokens: int,
    call: Callable[[ModelSpec], Awaitable[Any]],
) -> AttemptResult:
    """Reserve, make one call, classify the outcome, and settle.

    call runs the agent with spec.model and spec.settings and returns its run result (output
    and usage). It must allow no output retries. A short rate-limit wait (up to
    ai_max_wait_seconds) is waited out once; anything longer is returned as Refused.
    """
    settings = ledger.settings
    reserved = await asyncio.to_thread(ledger.reserve, spec.provider, spec.name, estimated_tokens)
    if isinstance(reserved, Refusal) and reserved.reason in (
        RefusalReason.RATE_LIMITED,
        RefusalReason.COOLING_DOWN,
    ):
        wait = (reserved.retry_at or 0) - ledger.clock()
        if 0 < wait <= settings.ai_max_wait_seconds:
            await asyncio.sleep(wait)
            reserved = await asyncio.to_thread(
                ledger.reserve, spec.provider, spec.name, estimated_tokens
            )
    if isinstance(reserved, Refusal):
        _log(spec, reserved.reason, None, 0.0, 0, estimated_tokens, None, None)
        return Refused(reserved.reason, reserved.retry_at)

    seen = _Seen()
    token = _ATTEMPT.set(seen)
    started = time.monotonic()
    status: int | None = None
    try:
        result = await call(spec)
    except (ModelAPIError, UnexpectedModelBehavior) as exc:
        outcome, status = _classify(exc)
        result = None
    except BaseException:
        _ATTEMPT.reset(token)
        await asyncio.to_thread(
            ledger.settle, reserved, outcome=Outcome.ERROR, requests=seen.responses
        )
        raise
    _ATTEMPT.reset(token)

    now = ledger.clock()
    snapshot = parse_snapshot(seen.headers)
    cooldown: float | None = None
    if result is not None:
        outcome = Outcome.SUCCEEDED
        usage = result.usage
        requests = max(seen.responses, usage.requests)
        input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
    else:
        requests, input_tokens, output_tokens = seen.responses, None, None
        if outcome is Outcome.RATE_LIMITED:
            cooldown = cooldown_after_429(seen.headers, now, settings.ai_default_cooldown_seconds)
        elif outcome is Outcome.PAYMENT_REQUIRED:
            # Credits do not come back by themselves; wait at least until the next UTC day.
            cooldown = max(now + settings.ai_default_cooldown_seconds, (now // DAY + 1) * DAY)
        elif outcome is Outcome.AUTH:
            ledger.disable(spec.provider)
            logger.error("%s rejected the API key; check the configured key", spec.provider)
    await asyncio.to_thread(
        ledger.settle,
        reserved,
        outcome=outcome,
        requests=requests,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        snapshot=snapshot,
        cooldown_until=cooldown,
    )
    duration = time.monotonic() - started
    _log(spec, outcome, status, duration, requests, estimated_tokens, input_tokens, output_tokens)

    if outcome is Outcome.SUCCEEDED:
        return Succeeded(result.output, requests, input_tokens, output_tokens)
    if outcome is Outcome.PAYMENT_REQUIRED:
        return Refused(RefusalReason.PAYMENT_REQUIRED, cooldown)
    return Failed(outcome, status, cooldown)


def _classify(exc: BaseException) -> tuple[Outcome, int | None]:
    if isinstance(exc, UnexpectedModelBehavior):
        return Outcome.INVALID_OUTPUT, None
    if isinstance(exc, ModelTimeoutError):
        return Outcome.TIMEOUT, None
    if isinstance(exc, ModelConnectionError):
        return Outcome.CONNECTION, None
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) else None
    kinds = {429: Outcome.RATE_LIMITED, 402: Outcome.PAYMENT_REQUIRED, 401: Outcome.AUTH}
    if status in kinds:
        return kinds[status], status
    if status == 403:
        return Outcome.FORBIDDEN, status
    if status is not None and status >= 500:
        return Outcome.SERVER_ERROR, status
    return Outcome.HTTP_ERROR, status


def _log(
    spec: ModelSpec,
    outcome: str,
    status: int | None,
    duration: float,
    requests: int,
    estimated: int,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    # Never the exception, the prompt, the response, or the key.
    logger.info(
        "ai attempt provider=%s model=%s outcome=%s status=%s duration_ms=%d requests=%d "
        "estimated_tokens=%d input_tokens=%s output_tokens=%s",
        spec.provider,
        spec.name,
        outcome,
        status,
        duration * 1000,
        requests,
        estimated,
        input_tokens,
        output_tokens,
    )


async def read_openrouter_key_info(
    ledger: Ledger, *, transport: httpx2.AsyncBaseTransport | None = None
) -> KeyInfo | None:
    """OpenRouter's free-model daily counter, read at most once per interval per process.

    It costs no completion quota but is still a request, so callers use it at startup and from
    the budget endpoint, never before each attempt. Only the three counts are kept.
    """
    now = ledger.clock()
    last = ledger.key_info_read_at
    if last is not None and now - last < KEY_INFO_MIN_INTERVAL_SECONDS:
        return ledger.key_info
    key = ledger.settings.openrouter_api_key
    if key is None:
        return None
    ledger.key_info_read_at = now
    try:
        async with httpx2.AsyncClient(
            timeout=ledger.settings.ai_request_timeout_seconds, transport=transport
        ) as client:
            response = await client.get(
                OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key.get_secret_value()}"}
            )
        if response.status_code != 200:
            logger.warning("openrouter key info unavailable: status %s", response.status_code)
            return ledger.key_info
        counts = response.json()["data"]["free_model_daily_requests"]
        used = sane_count(counts.get("used"))
        limit = sane_count(counts.get("limit")) or None
        remaining = sane_count(counts.get("remaining"))
    except Exception as exc:
        logger.warning("openrouter key info unavailable: %s", type(exc).__name__)
        return ledger.key_info
    if used is None:
        logger.warning("openrouter key info unavailable: no usable counts")
        return ledger.key_info
    ledger.key_info = KeyInfo(used, limit, remaining)
    await asyncio.to_thread(ledger.record_reported, used, limit)
    return ledger.key_info
