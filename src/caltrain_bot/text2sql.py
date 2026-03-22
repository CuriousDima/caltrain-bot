# We use DSPy to convert natural language questions to SQL queries.
# DSPy is a bet on writing code instead of strings.

import dspy

from caltrain_bot.config import LLMSettings, OllamaSettings, OpenRouterSettings


def _validate_provider(settings: LLMSettings) -> None:
    match settings:
        case OpenRouterSettings():
            if not settings.api_key.strip():
                raise ValueError("OPENROUTER_API_KEY environment variable is required.")
        case OllamaSettings():
            # No need to validate OllamaSettings.
            # The bot will not be able to connect to Ollama and naturally fail with connection error.
            pass
        case _:
            raise ValueError(f"Unsupported LLM provider: {settings.provider}")


def _build_lm(settings: LLMSettings) -> dspy.LM:
    match settings:
        case OpenRouterSettings():
            return dspy.LM(
                f"openrouter/{settings.model}",
                api_key=settings.api_key,
            )
        case OllamaSettings():
            return dspy.LM(
                f"ollama_chat/{settings.model}",
                api_base=settings.api_base,
            )
        case _:
            raise ValueError(f"Unsupported LLM provider: {settings.provider}")


class Text2SqlConvertor:
    """Prepare and hold the DSPy LM used for text-to-SQL conversion."""

    def __init__(self, llm_settings: LLMSettings):
        _validate_provider(llm_settings)
        self._lm = _build_lm(llm_settings)
        dspy.configure(lm=self._lm)

    def convert(self, question: str) -> str:
        response = self._lm(question)
        return response
