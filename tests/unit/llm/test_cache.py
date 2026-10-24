import pytest
from llm_helpers import T0

from toolguard.core.models import JudgeVerdict
from toolguard.llm import cache
from toolguard.llm.budget import DAY, GROQ, OPENROUTER
from toolguard.llm.cache import CachePut
from toolguard.storage import db

VERDICT = JudgeVerdict(tool_name="t", risk_level="info", reasoning_summary="No risk signals.")
HASH = "a" * 64


def put(settings, *, provider=GROQ, model=None, version="test-v1", now=T0, content_hash=HASH):
    return cache.put(
        settings,
        content_hash,
        version,
        VERDICT,
        provider=provider,
        model=model or settings.primary_model,
        now=now,
    )


def test_round_trip_keeps_provider_and_model(settings) -> None:
    assert put(settings) is CachePut.STORED
    hit = cache.get(settings, HASH, "test-v1", now=T0 + 1)
    assert hit.verdict == VERDICT
    assert (hit.provider, hit.model, hit.created_at) == (GROQ, settings.primary_model, T0)


def test_miss_on_other_hash_prompt_version_or_family(settings) -> None:
    put(settings)
    assert cache.get(settings, "b" * 64, "test-v1", now=T0) is None
    assert cache.get(settings, HASH, "test-v2", now=T0) is None
    other_family = settings.model_copy(update={"judge_model_family": "other"})
    assert cache.get(other_family, HASH, "test-v1", now=T0) is None


@pytest.mark.parametrize(
    ("provider", "model"),
    [(OPENROUTER, "nvidia/nemotron-3.5-super-vl:free"), (GROQ, "some-other-groq-model")],
)
def test_only_primary_model_verdicts_are_stored(settings, provider, model) -> None:
    assert put(settings, provider=provider, model=model) is CachePut.NOT_PRIMARY_MODEL
    assert cache.get(settings, HASH, "test-v1", now=T0) is None


def test_entries_expire_and_are_purged(settings) -> None:
    put(settings)
    ttl = settings.judge_cache_ttl_days * DAY
    assert cache.get(settings, HASH, "test-v1", now=T0 + ttl - 1) is not None
    assert cache.get(settings, HASH, "test-v1", now=T0 + ttl + 1) is None
    put(settings, content_hash="c" * 64, now=T0 + ttl + 1)
    with db.connect(settings.database_path) as conn:
        hashes = [r[0] for r in conn.execute("SELECT content_hash FROM judge_cache")]
    assert hashes == ["c" * 64]


def test_corrupt_entry_is_a_miss(settings) -> None:
    put(settings)
    with db.connect(settings.database_path) as conn, db.transaction(conn):
        conn.execute('UPDATE judge_cache SET verdict_json = \'{"risk_level": "severe"}\'')
    assert cache.get(settings, HASH, "test-v1", now=T0) is None
