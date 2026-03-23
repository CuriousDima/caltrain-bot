from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

import dspy
from loguru import logger

from caltrain_bot.config import LLMSettings, OllamaSettings, OpenRouterSettings
from caltrain_bot.time_utils import get_current_server_datetime, get_server_timezone


class QuestionsClassifier(dspy.Signature):
    """Classify whether a question is asking about train schedules or not."""

    question: str = dspy.InputField()
    is_schedule_question: bool = dspy.OutputField(
        desc="Whether the question is about train schedules"
    )


# also a signature, but it needs to be built dynamically after we load station names from the database
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
                "departure_time": datetime,
            },
            "question": dspy.InputField(),
            "departure_station": dspy.OutputField(desc="Departure station"),
            "arrival_station": dspy.OutputField(desc="Arrival station"),
            "departure_time": dspy.OutputField(desc="Approximate departure time"),
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


def _configure_observability(tracking_url: str | None) -> None:
    if tracking_url is None:
        return

    tracking_url = tracking_url.strip()
    if not tracking_url:
        return

    import mlflow

    mlflow.set_tracking_uri(tracking_url)
    mlflow.dspy.autolog()


def get_current_datetime() -> str:
    """Get the current server-local date and time as an ISO 8601 string."""
    return get_current_server_datetime().isoformat()


def datetime_calculator(
    start_time: str,
    delta_minutes: int,
) -> str:
    """Calculate a new server-local datetime from a starting timestamp."""
    start_dt = datetime.fromisoformat(start_time)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=get_server_timezone())
    new_dt = start_dt + timedelta(minutes=delta_minutes)
    return new_dt.isoformat()


class QuestionAnalyzer:
    """Analyzes the user's question and extracts the departure station, arrival station, and departure time."""

    def __init__(
        self,
        llm_settings: LLMSettings,
        stations: Sequence[str],
        mlflow_tracking_url: str | None = None,
    ):
        # Construct the LM object after validating settings.
        _validate_provider(llm_settings)
        self._lm: dspy.LM = _build_lm(llm_settings)
        _configure_observability(mlflow_tracking_url)
        # Make the LM available globally for all signatures to use when making predictions.
        dspy.configure(lm=self._lm)

        self._question_classifier: dspy.Predict = dspy.Predict(QuestionsClassifier)
        self._stations_departure_time_extractor: dspy.ReAct = dspy.ReAct(
            build_station_extraction_signature(stations),
            tools=[get_current_datetime, datetime_calculator],
        )

    def is_schedule_question(self, question: str) -> bool:
        logger.info(f"Classifying question:\n{question}")
        prediction = self._question_classifier(question=question)
        logger.info(f"Classification verdict: {prediction}")
        return prediction.is_schedule_question

    def extract_stations_and_departure_time(self, question: str) -> dspy.Prediction:
        logger.info(
            f"Extracting stations and departure time from question:\n{question}"
        )
        r = self._stations_departure_time_extractor(question=question)
        logger.info(f"Extraction result: {r}")
        return r
