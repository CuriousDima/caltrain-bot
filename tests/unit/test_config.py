import pytest

from caltrain_bot.config import load_settings


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", " vendor/model ")
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_REASONING_EFFORT", raising=False)


def test_load_settings_reads_the_llm_settings(required_env):
    settings = load_settings()

    assert settings.llm.api_key == "openrouter-key"
    assert settings.llm.model == "vendor/model"
    assert settings.llm.fallback_model is None
    assert settings.llm.reasoning_effort is None


@pytest.mark.parametrize(
    ("value", "expected"), [("other/model", "other/model"), ("  ", None)]
)
def test_load_settings_reads_the_optional_fallback_model(
    required_env, monkeypatch, value, expected
):
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL", value)

    assert load_settings().llm.fallback_model == expected


@pytest.mark.parametrize(("value", "expected"), [("none", "none"), ("", None)])
def test_load_settings_reads_the_optional_reasoning_effort(
    required_env, monkeypatch, value, expected
):
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", value)

    assert load_settings().llm.reasoning_effort == expected


def test_load_settings_requires_the_model(required_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "")

    with pytest.raises(ValueError, match="OPENROUTER_MODEL"):
        load_settings()
