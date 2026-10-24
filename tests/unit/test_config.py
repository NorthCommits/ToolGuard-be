from pathlib import Path

import pytest
from pydantic import ValidationError

from toolguard.config import Settings

FAKE_GROQ = "fake-groq-value-for-tests"
FAKE_OPENROUTER = "fake-openrouter-value-for-tests"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real environment out of these tests."""
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "SERVICE_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def test_starts_without_keys() -> None:
    settings = Settings(_env_file=None)
    assert settings.groq_api_key is None
    assert settings.openrouter_api_key is None
    assert settings.providers_configured() == []
    assert settings.default_fail_on == "high"


def test_lowercase_names_in_dotenv_are_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"groq_api_key={FAKE_GROQ}\nopenrouter_api_key={FAKE_OPENROUTER}\n")
    settings = Settings(_env_file=env_file)
    assert settings.groq_api_key.get_secret_value() == FAKE_GROQ
    assert settings.openrouter_api_key.get_secret_value() == FAKE_OPENROUTER
    assert settings.providers_configured() == ["groq", "openrouter"]


def test_uppercase_environment_names_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ)
    settings = Settings(_env_file=None)
    assert settings.providers_configured() == ["groq"]


def test_blank_key_counts_as_unset(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("groq_api_key=\nopenrouter_api_key=   \n")
    assert Settings(_env_file=env_file).providers_configured() == []


def test_unknown_dotenv_entries_are_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("something_else=1\n")
    Settings(_env_file=env_file)


def test_secrets_never_appear_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER)
    settings = Settings(_env_file=None)
    for text in (repr(settings), str(settings), settings.model_dump_json()):
        assert FAKE_GROQ not in text
        assert FAKE_OPENROUTER not in text


def test_wildcard_cors_origin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        Settings(_env_file=None, cors_allowed_origins=["*"])


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("tpm_groq", 0),
        ("judge_cache_ttl_days", 0),
        ("ai_request_timeout_seconds", -1),
        ("ai_max_wait_seconds", 0),
        ("ai_default_cooldown_seconds", 0),
        ("judge_model_family", " "),
        ("openrouter_reasoning_effort", "lowest"),
    ],
)
def test_nonsense_ai_settings_are_rejected(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_ai_setting_defaults() -> None:
    settings = Settings(_env_file=None)
    assert (settings.tpm_groq, settings.judge_cache_ttl_days) == (7_200, 30)
    assert settings.openrouter_reasoning_effort == "low"
    assert settings.ai_max_wait_seconds == 20
