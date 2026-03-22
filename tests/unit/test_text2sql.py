import pytest

from caltrain_bot.config import OllamaSettings, OpenRouterSettings, Provider
from caltrain_bot.text2sql import _validate_provider


@pytest.mark.parametrize(
    "settings",
    [
        OpenRouterSettings(
            provider=Provider.OPENROUTER,
            api_key="test-api-key",
            model="openai/gpt-4o-mini",
        ),
        OllamaSettings(
            provider=Provider.OLLAMA,
            api_base="http://localhost:11434",
            model="llama3.2",
        ),
    ],
)
def test_validate_provider_accepts_supported_settings(settings):
    _validate_provider(settings)


@pytest.mark.parametrize("api_key", ["", "   ", "\n\t"])
def test_validate_provider_rejects_blank_openrouter_api_key(api_key):
    settings = OpenRouterSettings(
        provider=Provider.OPENROUTER,
        api_key=api_key,
        model="openai/gpt-4o-mini",
    )

    with pytest.raises(
        ValueError, match="OPENROUTER_API_KEY environment variable is required."
    ):
        _validate_provider(settings)
