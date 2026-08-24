from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import dspy
from dspy.signatures.signature import Signature, ensure_signature
from loguru import logger

from caltrain_bot.config import OpenRouterSettings


class QuestionsClassifier(dspy.Signature):
    """Classify whether a question is asking about train schedules or not."""

    question: str = dspy.InputField()
    is_schedule_question: bool = dspy.OutputField(
        desc="Whether the question is about train schedules"
    )


class ReActWithDatetime(dspy.Module):
    def __init__(self, signature: str | type[Signature], tools: list[Any], max_iterations: int = 10) -> None:
        super().__init__()
        base_signature = ensure_signature(signature)
        self.signature = base_signature.append(
            "current_datetime",
            dspy.InputField(desc="Current date and time in the America/Los_Angeles timezone formatted as ISO 8601."),
        )
        self.react = dspy.ReAct(signature=self.signature, tools=tools, max_iters=max_iterations)

    def forward(self, **input_args: Any) -> dspy.Prediction:
        input_args["current_datetime"] = datetime.now(
            ZoneInfo("America/Los_Angeles")
        ).isoformat()
        return self.react(**input_args)

    async def aforward(self, **input_args: Any) -> dspy.Prediction:
        input_args["current_datetime"] = datetime.now(
            ZoneInfo("America/Los_Angeles")
        ).isoformat()
        return await self.react.aforward(**input_args)


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


def datetime_calculator(
    start_time: str,
    delta_minutes: int,
) -> str:
    """Calculate a new datetime by adding minutes (including negative values) to a given start time."""
    start_dt = datetime.fromisoformat(start_time)
    new_dt = start_dt + timedelta(minutes=delta_minutes)
    return new_dt.isoformat()


class QuestionAnalyzer:
    """Analyzes the user's question and extracts the departure station, arrival station, and departure time."""

    def __init__(self, llm_settings: OpenRouterSettings, stations: Sequence[str]):
        # Construct the LM object after validating settings.
        if not llm_settings.api_key.strip():
            raise ValueError("OPENROUTER_API_KEY environment variable is required.")
        self._lm: dspy.LM = dspy.LM(
            f"openrouter/{llm_settings.model}",
            api_key=llm_settings.api_key,
        )
        # Make the LM available globally for all signatures to use when making predictions.
        dspy.configure(lm=self._lm)

        self._question_classifier: dspy.Predict = dspy.Predict(QuestionsClassifier)
        self._stations_departure_time_extractor = ReActWithDatetime(
            build_station_extraction_signature(stations),
            tools=[datetime_calculator],
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
