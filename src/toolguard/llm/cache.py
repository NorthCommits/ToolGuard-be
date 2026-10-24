"""Judge verdict cache (sdd.md section 12).

Key: tool content hash, judge prompt version, and the configured model family. Entries expire
after judge_cache_ttl_days. Changing the prompt version or judge_model_family changes the key,
so older entries are never read again.

Until M5 decides otherwise, only verdicts from the configured primary model are stored, so a
fallback model's verdict is never served later as if the primary model had produced it.
"""

import time
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from toolguard.config import Settings
from toolguard.core.models import JudgeVerdict
from toolguard.llm.budget import DAY, GROQ
from toolguard.storage import db


class CachePut(StrEnum):
    STORED = "stored"
    NOT_PRIMARY_MODEL = "not_primary_model"


@dataclass(frozen=True)
class CachedVerdict:
    verdict: JudgeVerdict
    provider: str
    model: str
    created_at: float


def get(
    settings: Settings, content_hash: str, prompt_version: str, now: float | None = None
) -> CachedVerdict | None:
    now = time.time() if now is None else now
    with db.connect(settings.database_path) as conn:
        row = conn.execute(
            "SELECT * FROM judge_cache WHERE content_hash = ? AND prompt_version = ? "
            "AND model_family = ?",
            (content_hash, prompt_version, settings.judge_model_family),
        ).fetchone()
    if row is None or row["created_at"] < now - settings.judge_cache_ttl_days * DAY:
        return None
    try:
        verdict = JudgeVerdict.model_validate_json(row["verdict_json"])
    except ValidationError:
        return None
    return CachedVerdict(verdict, row["provider"], row["model"], row["created_at"])


def put(
    settings: Settings,
    content_hash: str,
    prompt_version: str,
    verdict: JudgeVerdict,
    *,
    provider: str,
    model: str,
    now: float | None = None,
) -> CachePut:
    if provider != GROQ or model != settings.primary_model:
        return CachePut.NOT_PRIMARY_MODEL
    now = time.time() if now is None else now
    with db.connect(settings.database_path) as conn, db.transaction(conn):
        conn.execute(
            "DELETE FROM judge_cache WHERE created_at < ?",
            (now - settings.judge_cache_ttl_days * DAY,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO judge_cache (content_hash, prompt_version, model_family, "
            "verdict_json, provider, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                content_hash,
                prompt_version,
                settings.judge_model_family,
                verdict.model_dump_json(),
                provider,
                model,
                now,
            ),
        )
    return CachePut.STORED
