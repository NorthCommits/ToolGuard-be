"""Settings loaded from environment variables and a local .env file (sdd.md section 3)."""

from pathlib import Path
from typing import Literal

from pydantic import PositiveFloat, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SeverityName = Literal["info", "low", "medium", "high", "critical"]


class Settings(BaseSettings):
    """All runtime settings. Provider keys are optional: without them scans run rules-only."""

    # Names are matched case-insensitively (pydantic-settings default), so the
    # lowercase names in .env and uppercase environment variables both work.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    primary_model: str = "openai/gpt-oss-120b"
    fallback_models: list[str] = [
        "nvidia/nemotron-3.5-super-vl:free",
        "google/gemma-4-26b-a4b-it:free",
    ]

    # About 90 percent of the free-tier limits read in October 2026.
    daily_budget_groq_requests: int = 900
    daily_budget_groq_tokens: int = 180_000
    daily_budget_openrouter_requests: int = 45
    rpm_groq: int = 30
    rpm_openrouter: int = 20
    # Used until Groq's headers report the real tokens-per-minute bucket, and always as a cap.
    tpm_groq: PositiveInt = 7_200

    judge_max_tools_per_call: int = 5
    judge_max_tools_per_scan: int = 10
    judge_max_input_tokens: int = 6_000
    # Output allowance per tool, including reasoning tokens; calibrated in M5.
    judge_output_tokens_per_tool: int = 250
    judge_reasoning_effort: Literal["low", "medium", "high"] = "low"
    # Lowest efforts vary per OpenRouter model, and a reply can be all reasoning (spike 04).
    openrouter_reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = "low"
    # Cache key part; change it to invalidate cached verdicts when the primary model changes.
    judge_model_family: str = "gpt-oss"
    judge_cache_ttl_days: PositiveInt = 30
    ai_request_timeout_seconds: PositiveFloat = 60.0
    # An attempt waits for a rate limit only this long; otherwise the caller moves on.
    ai_max_wait_seconds: PositiveFloat = 20.0
    # Cooldown after a 429 that carries no usable retry-after or reset header.
    ai_default_cooldown_seconds: PositiveFloat = 60.0
    default_fail_on: SeverityName = "high"

    ingest_max_bytes: int = 1_000_000
    ingest_timeout_seconds: float = 15.0
    ingest_max_redirects: int = 3
    ingest_max_tools: int = 500
    description_length_threshold: int = 2_000
    # Read from the environment only, never from API requests. IDs are checked by
    # toolguard.rules.registry.active_rules at startup.
    rules_disabled: list[str] = []
    rules_severity_overrides: dict[str, SeverityName] = {}

    database_path: Path = Path("toolguard.db")
    service_access_token: SecretStr | None = None
    cors_allowed_origins: list[str] = []
    api_max_page_size: int = 200
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    @field_validator("groq_api_key", "openrouter_api_key", "service_access_token", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        """Treat an empty or whitespace-only value (for example `groq_api_key=`) as not set."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("judge_model_family")
    @classmethod
    def _family_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("judge_model_family must not be empty")
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _no_wildcard_origin(cls, origins: list[str]) -> list[str]:
        if "*" in origins:
            raise ValueError("cors_allowed_origins must list exact origins; '*' is not allowed")
        return origins

    def providers_configured(self) -> list[str]:
        """Names of providers that have a key set. Empty means AI review is disabled."""
        return [
            name
            for name, key in (("groq", self.groq_api_key), ("openrouter", self.openrouter_api_key))
            if key is not None
        ]
