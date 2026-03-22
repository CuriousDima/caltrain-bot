from collections.abc import Sequence
from typing import Literal

import dspy

from caltrain_bot.config import LLMSettings, OllamaSettings, OpenRouterSettings


def build_station_extraction_signature(
    stations: Sequence[str],
) -> type[dspy.Signature]:
    """Build a DSPy signature constrained to the known station names."""
    station_names = tuple(stations)
    if not station_names:
        raise ValueError("At least one station name is required.")

    # Build Literal["a", "b", ...] from DB-loaded station names at runtime.
    # Literal[...] syntax needs the values during annotation construction, so we
    # call __getitem__ explicitly instead of hardcoding the station list.
    station_literal = Literal.__getitem__(station_names)

    # It’s a bit messy. That’s the price I’m willing to pay for the flexibility of defining the signature at runtime.
    # The complexity comes from a dynamic list of stations that we only know after the database is loaded.
    return type(
        "ExtractDepartureAndArrivalStations",
        (dspy.Signature,),
        {
            "__doc__": "Extract departure station and arrival station from the question.",
            "__annotations__": {
                "question": str,
                "departure_station": station_literal,
                "arrival_station": station_literal,
            },
            "question": dspy.InputField(),
            "departure_station": dspy.OutputField(desc="Departure station"),
            "arrival_station": dspy.OutputField(desc="Arrival station"),
        },
    )


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

    def __init__(self, llm_settings: LLMSettings, stations: Sequence[str]):
        # Construct the LM object after validating settings.
        _validate_provider(llm_settings)
        self._lm: dspy.LM = _build_lm(llm_settings)
        # Make the LM available globally for all signatures to use when making predictions.
        dspy.configure(lm=self._lm)
        # Build the station extraction signature and prediction object.
        self._station_signature: type[dspy.Signature] = (
            build_station_extraction_signature(stations)
        )
        self._station_extractor: dspy.Predict = dspy.Predict(self._station_signature)

    def convert(self, question: str) -> dspy.Prediction:
        return self._station_extractor(question=question)
