import pytest

from caltrain_bot.config import load_settings


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", " vendor/model ")
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("STATS_PORT", raising=False)
    monkeypatch.delenv("USAGE_DB_PATH", raising=False)


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


def test_load_settings_runs_only_telegram_by_default(required_env):
    settings = load_settings()

    assert settings.telegram_bot_token == "telegram-token"
    assert settings.web_port is None
    assert settings.stats_port is None


def test_load_settings_allows_the_web_page_without_telegram(required_env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("WEB_PORT", " 8080 ")

    settings = load_settings()

    assert settings.telegram_bot_token is None
    assert settings.web_port == 8080


def test_load_settings_requires_at_least_one_frontend(required_env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN.*WEB_PORT"):
        load_settings()


@pytest.mark.parametrize("value", ["http", "0", "70000", "-1", "80.5"])
def test_load_settings_rejects_an_invalid_web_port(required_env, monkeypatch, value):
    monkeypatch.setenv("WEB_PORT", value)

    with pytest.raises(ValueError, match="WEB_PORT"):
        load_settings()


def test_load_settings_keeps_usage_statistics_next_to_the_data(required_env):
    path = load_settings().usage_db_path

    assert path.name == "usage.db"
    assert path.parent == load_settings().gtfs_file_path.parent


def test_load_settings_reads_the_usage_db_path(required_env, monkeypatch, tmp_path):
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "stats.db"))

    assert load_settings().usage_db_path == tmp_path / "stats.db"


def test_load_settings_reads_the_statistics_port(required_env, monkeypatch):
    monkeypatch.setenv("WEB_PORT", "8080")
    monkeypatch.setenv("STATS_PORT", " 8081 ")

    settings = load_settings()

    assert settings.web_port == 8080
    assert settings.stats_port == 8081


def test_load_settings_serves_no_statistics_page_by_default(required_env, monkeypatch):
    monkeypatch.setenv("WEB_PORT", "8080")
    monkeypatch.setenv("STATS_PORT", "  ")

    assert load_settings().stats_port is None


@pytest.mark.parametrize("value", ["http", "0", "70000", "-1", "80.5"])
def test_load_settings_rejects_an_invalid_statistics_port(
    required_env, monkeypatch, value
):
    monkeypatch.setenv("WEB_PORT", "8080")
    monkeypatch.setenv("STATS_PORT", value)

    with pytest.raises(ValueError, match="STATS_PORT must be a port number"):
        load_settings()


def test_load_settings_keeps_the_statistics_port_off_the_public_port(
    required_env, monkeypatch
):
    monkeypatch.setenv("WEB_PORT", "8080")
    monkeypatch.setenv("STATS_PORT", "8080")

    with pytest.raises(ValueError, match="STATS_PORT must differ from WEB_PORT"):
        load_settings()


@pytest.mark.parametrize("telegram_bot_token", ["telegram-token", ""])
def test_load_settings_requires_the_web_page_for_the_statistics_page(
    required_env, monkeypatch, telegram_bot_token
):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", telegram_bot_token)
    monkeypatch.setenv("STATS_PORT", "8081")

    with pytest.raises(ValueError, match="STATS_PORT is set but WEB_PORT is not"):
        load_settings()
