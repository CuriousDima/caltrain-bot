# We use DSPy to convert natural language questions to SQL queries.
# DSPy is a bet on writing code instead of strings.

from __future__ import annotations

from urllib import error, request

import dspy

from caltrain_bot.config import LLMSettings, OllamaSettings, OpenRouterSettings

OLLAMA_TIMEOUT_SECONDS = 5


def _normalize_ollama_api_base(api_base: str) -> str:
    normalized = api_base.strip().rstrip("/")
    if normalized.endswith("/api"):
        return normalized[: -len("/api")]
    return normalized


def _build_ollama_tags_url(api_base: str) -> str:
    return f"{_normalize_ollama_api_base(api_base)}/api/tags"


def _validate_ollama(settings: OllamaSettings) -> None:
    tags_url: str = _build_ollama_tags_url(settings.api_base)
    try:
        with request.urlopen(tags_url, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ConnectionError(
                    f"Failed to connect to Ollama at {tags_url}: HTTP {response.status}"
                )
    except error.URLError as exc:
        raise ConnectionError(
            f"Failed to connect to Ollama at {tags_url}: {exc}"
        ) from exc


def _validate_openrouter(settings: OpenRouterSettings) -> None:
    if not settings.api_key.strip():
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")


def _validate_provider(settings: LLMSettings) -> None:
    if isinstance(settings, OllamaSettings):
        _validate_ollama(settings)
        return

    _validate_openrouter(settings)


def _build_lm(settings: LLMSettings) -> dspy.LM:
    if isinstance(settings, OllamaSettings):
        return dspy.LM(
            f"ollama_chat/{settings.model}",
            api_base=_normalize_ollama_api_base(settings.api_base),
            api_key="",
        )

    return dspy.LM(
        f"openrouter/{settings.model}",
        api_key=settings.api_key,
    )


def _build_configured_lm(settings: LLMSettings) -> dspy.LM:
    _validate_provider(settings)
    lm = _build_lm(settings)
    dspy.configure(lm=lm)
    return lm


class Text2SqlConvertor:
    """Prepare and hold the DSPy LM used for text-to-SQL conversion."""

    def __init__(self, llm_settings: LLMSettings):
        self._lm = _build_configured_lm(llm_settings)

    def convert(self, question: str) -> str:
        _ = question
        return "dummy text"
