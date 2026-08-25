from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import dspy
from loguru import logger

from caltrain_bot.config import OpenRouterSettings

OPTIMIZED_PROGRAM_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "prog_caltrain_schedule_helper_gepa_medium"
)


@dataclass(frozen=True)
class UnsupportedQuestion:
    pass


@dataclass(frozen=True)
class ScheduleQuestion:
    departure_station: str
    arrival_station: str
    departure_time: datetime


QuestionAnalysisResult = UnsupportedQuestion | ScheduleQuestion


class QuestionsClassifier(dspy.Signature):
    """Classify whether a question contains a self-contained Caltrain schedule lookup.

    A supported question identifies both departure and arrival stations. A departure
    time is optional. Reject questions that omit either station or depend on prior
    conversational context.
    """

    question: str = dspy.InputField()
    is_schedule_question: bool = dspy.OutputField(
        desc=(
            "True only when both departure and arrival stations can be resolved "
            "from this question; the departure time may be omitted"
        )
    )


# also a signature, but it needs to be built dynamically after we load station names from the database
def build_station_extraction_signature(
    stations: Sequence[str],
) -> type[dspy.Signature]:
    """Build a DSPy signature that describes the known station names."""
    station_names = tuple(stations)
    if not station_names:
        raise ValueError("At least one station name is required.")

    known_stations = ", ".join(station_names)

    return type(
        "ExtractDepartureAndArrivalStations",
        (dspy.Signature,),
        {
            "__doc__": (
                "Extract a Caltrain route and optional departure time. Return null "
                "for a station that cannot be resolved from the question. Return "
                "null for the departure time when the question does not specify one."
            ),
            "__annotations__": {
                "question": str,
                "reference_datetime": str,
                "departure_station": str | None,
                "arrival_station": str | None,
                "departure_time": datetime | str | None,
            },
            "question": dspy.InputField(),
            "reference_datetime": dspy.InputField(
                desc=(
                    "Current date and time in the America/Los_Angeles timezone "
                    "formatted as ISO 8601"
                )
            ),
            "departure_station": dspy.OutputField(
                desc=f"Canonical departure station from: {known_stations}; null if missing"
            ),
            "arrival_station": dspy.OutputField(
                desc=f"Canonical arrival station from: {known_stations}; null if missing"
            ),
            "departure_time": dspy.OutputField(
                desc=(
                    "Approximate departure time as an ISO 8601 datetime; null when "
                    "the question omits a time"
                )
            ),
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


class CaltrainScheduleHelper(dspy.Module):
    """Classify a question and extract a schedule lookup when applicable."""

    def __init__(
        self,
        question_classifier: dspy.Module,
        stations_departure_time_extractor: dspy.Module,
        stations: Sequence[str],
    ) -> None:
        super().__init__()
        self._question_classifier = question_classifier
        self._stations_departure_time_extractor = stations_departure_time_extractor
        self._stations_by_normalized_name = {
            self._normalize_station(station): station for station in stations
        }

    @staticmethod
    def _normalize_station(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None

        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _canonical_station(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return self._stations_by_normalized_name.get(self._normalize_station(value))

    def _resolve_departure_time(
        self, value: object, reference_datetime: str
    ) -> datetime | None:
        if value is None or (
            isinstance(value, str)
            and value.strip().casefold()
            in {"", "none", "null", "not specified", "tbd", "unknown"}
        ):
            value = reference_datetime
        return self._parse_datetime(value)

    def forward(self, question: str, reference_datetime: str) -> QuestionAnalysisResult:
        logger.info(f"Classifying question:\n{question}")
        classification = self._question_classifier(question=question)
        logger.info(f"Classification verdict: {classification}")

        if not classification.is_schedule_question:
            logger.warning(
                f"Question is not a schedule question. Original question: {question}"
            )
            return UnsupportedQuestion()

        logger.info(
            f"Extracting stations and departure time from question:\n{question}"
        )
        extraction = self._stations_departure_time_extractor(
            question=question, reference_datetime=reference_datetime
        )
        logger.info(f"Extraction result: {extraction}")

        departure_station = self._canonical_station(
            getattr(extraction, "departure_station", None)
        )
        arrival_station = self._canonical_station(
            getattr(extraction, "arrival_station", None)
        )
        departure_time = self._resolve_departure_time(
            getattr(extraction, "departure_time", None), reference_datetime
        )

        if (
            departure_station is None
            or arrival_station is None
            or departure_time is None
        ):
            logger.warning(
                "Question did not produce a complete, valid schedule lookup. "
                f"Original question: {question}"
            )
            return UnsupportedQuestion()

        return ScheduleQuestion(
            departure_station=departure_station,
            arrival_station=arrival_station,
            departure_time=departure_time,
        )


def build_caltrain_schedule_helper(
    llm_settings: OpenRouterSettings, stations: Sequence[str]
) -> CaltrainScheduleHelper:
    if not llm_settings.api_key.strip():
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    lm = dspy.LM(
        f"openrouter/{llm_settings.model}",
        api_key=llm_settings.api_key,
    )
    dspy.configure(lm=lm)

    # This is a trusted, repository-owned pickle created with
    # `save(..., save_program=True)`.
    program = dspy.load(str(OPTIMIZED_PROGRAM_PATH), allow_pickle=True)
    if not isinstance(program, CaltrainScheduleHelper):
        raise TypeError(
            "Optimized program must deserialize to CaltrainScheduleHelper, "
            f"got {type(program).__name__}."
        )

    expected_stations = {program._normalize_station(station) for station in stations}
    optimized_stations = set(program._stations_by_normalized_name)
    if optimized_stations != expected_stations:
        raise ValueError(
            "Optimized program station list does not match the current GTFS stations."
        )

    return program
