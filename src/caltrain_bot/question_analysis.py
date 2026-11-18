from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

import dspy
from loguru import logger

from caltrain_bot.config import OpenRouterSettings

OPTIMIZED_PROGRAM_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "schedule_question_program.json"
)

CALTRAIN_TIMEZONE = ZoneInfo("America/Los_Angeles")

# One short structured answer per question: fail fast and retry rather than
# leave the user waiting on a stuck request.
LLM_TIMEOUT_SECONDS = 20
LLM_NUM_RETRIES = 2
LLM_MAX_TOKENS = 1000

_UPCOMING_DAYS = 14
_MISSING_TIME_VALUES = {"", "none", "null", "not specified", "tbd", "unknown"}


@dataclass(frozen=True)
class UnsupportedQuestion:
    pass


@dataclass(frozen=True)
class ScheduleQuestion:
    departure_station: str
    arrival_station: str
    departure_time: datetime


QuestionAnalysisResult = UnsupportedQuestion | ScheduleQuestion


SCHEDULE_QUESTION_INSTRUCTIONS = """\
Decide whether a message is a self-contained Caltrain schedule lookup and, if it is, extract the route and the departure time.

## is_schedule_question
True only when the message asks which trains run from one specific Caltrain station to another specific, different Caltrain station, optionally at or around a departure time. Otherwise false. In particular, answer false when:
- the departure station or the arrival station is missing, is not a Caltrain station (SFO airport, BART stations, Oakland, ...), is ambiguous ("near my office", "somewhere in Silicon Valley", "San Mateo or Hillsdale"), or depends on context the message does not contain ("from here", "from there", "the return train");
- the direction is not stated ("between X and Y") or both stations are the same;
- the message asks to arrive by a deadline, for the first or last train of the day, for a round trip, or for a trip with more than two stations;
- the message is about fares, tickets, refunds, delays, alerts, cancellations, platforms, parking, bikes, pets, amenities, accessibility, or anything other than the timetable.
When false, set every other output to null.

## Stations
Map aliases, abbreviations, and misspellings to the canonical station, for example: "SF", "San Fran", "the city", "4th and King" -> san francisco; "Diridon", "San Jose" -> san jose diridon; "Cal Ave" -> california avenue; "Mt View" -> mountain view; "RWC" -> redwood city; "South City", "SSF", "South SF" -> south san francisco; "22nd St" -> 22nd street; "B'way" -> broadway.

## departure_time
The local wall-clock time the user wants to leave, formatted as YYYY-MM-DDTHH:MM (24-hour, no UTC offset). Null when the message states no time or asks for the next train.
- Use the stated time exactly; do not round it and do not shift it by travel time. "After 6 PM" and "around 6 PM" both mean 18:00.
- Take the calendar date from `upcoming_dates`; never work out weekdays yourself. With no day stated, use today. "This <weekday>" and a bare weekday name mean the first such day after today; "next <weekday>" means the first such day after today as well, or the one a week later when today is that weekday. An explicit calendar date wins.
- A time without AM/PM takes it from context ("morning", "tonight", "after dinner", "this afternoon"). 12:00 AM to 12:59 AM is 00:00 to 00:59. "Midnight tonight" and a post-midnight time mentioned for tonight are on tomorrow's date.
- "In N minutes/hours", "N minutes from now", or "N minutes ago": add or subtract from `reference_datetime`, carrying over the hour and the date.
"""


def build_schedule_question_signature(
    stations: Sequence[str],
) -> type[dspy.Signature]:
    """Build the DSPy signature that restricts stations to the known names."""
    station_names = tuple(stations)
    if not station_names:
        raise ValueError("At least one station name is required.")

    # The station list is only known at runtime, so the Literal is built dynamically.
    station_type: Any = Literal.__getitem__(station_names)
    optional_station: Any = Optional.__getitem__(station_type)

    return type(
        "AnalyzeScheduleQuestion",
        (dspy.Signature,),
        {
            "__doc__": SCHEDULE_QUESTION_INSTRUCTIONS,
            "__annotations__": {
                "question": str,
                "reference_datetime": str,
                "upcoming_dates": str,
                "is_schedule_question": bool,
                "departure_station": optional_station,
                "arrival_station": optional_station,
                "departure_time": str | None,
            },
            "question": dspy.InputField(desc="Message from the user"),
            "reference_datetime": dspy.InputField(
                desc="Current local date and time in America/Los_Angeles (ISO 8601)"
            ),
            "upcoming_dates": dspy.InputField(
                desc="Weekday and calendar date of today and the following days"
            ),
            "is_schedule_question": dspy.OutputField(
                desc="Whether the message is a supported schedule lookup"
            ),
            "departure_station": dspy.OutputField(
                desc="Canonical station the trip starts from; null if unsupported"
            ),
            "arrival_station": dspy.OutputField(
                desc="Canonical station the trip ends at; null if unsupported"
            ),
            "departure_time": dspy.OutputField(
                desc="Local departure time as YYYY-MM-DDTHH:MM; null if not stated"
            ),
        },
    )


def describe_upcoming_dates(reference_datetime: str) -> str:
    """List the weekday of today and the following days so the model never has to derive one."""
    today = datetime.fromisoformat(reference_datetime).date()
    labels = {0: " (today)", 1: " (tomorrow)"}
    return ", ".join(
        f"{day:%A %Y-%m-%d}{labels.get(offset, '')}"
        for offset in range(_UPCOMING_DAYS)
        for day in (today + timedelta(days=offset),)
    )


class CaltrainScheduleHelper(dspy.Module):
    """Turn a question into a schedule lookup with a single LLM call."""

    def __init__(self, stations: Sequence[str]) -> None:
        super().__init__()
        self.analyze = dspy.Predict(build_schedule_question_signature(stations))
        self._stations_by_normalized_name = {
            self._normalize_station(station): station for station in stations
        }

    @staticmethod
    def _normalize_station(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            normalized = value.strip()
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
        else:
            return None
        # The model answers in Caltrain wall time without a UTC offset.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CALTRAIN_TIMEZONE)
        return parsed

    def _canonical_station(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return self._stations_by_normalized_name.get(self._normalize_station(value))

    def _resolve_departure_time(
        self, value: object, reference_datetime: str
    ) -> datetime | None:
        if value is None or (
            isinstance(value, str) and value.strip().casefold() in _MISSING_TIME_VALUES
        ):
            value = reference_datetime
        return self._parse_datetime(value)

    def _to_result(
        self, analysis: dspy.Prediction, question: str, reference_datetime: str
    ) -> QuestionAnalysisResult:
        logger.info(f"Question analysis for {question!r}: {analysis}")

        if not getattr(analysis, "is_schedule_question", False):
            logger.warning(f"Not a schedule question: {question!r}")
            return UnsupportedQuestion()

        departure_station = self._canonical_station(
            getattr(analysis, "departure_station", None)
        )
        arrival_station = self._canonical_station(
            getattr(analysis, "arrival_station", None)
        )
        departure_time = self._resolve_departure_time(
            getattr(analysis, "departure_time", None), reference_datetime
        )

        if (
            departure_station is None
            or arrival_station is None
            or departure_time is None
            or departure_station == arrival_station
        ):
            logger.warning(
                f"Question did not produce a complete, valid schedule lookup: {question!r}"
            )
            return UnsupportedQuestion()

        return ScheduleQuestion(
            departure_station=departure_station,
            arrival_station=arrival_station,
            departure_time=departure_time,
        )

    def forward(self, question: str, reference_datetime: str) -> QuestionAnalysisResult:
        analysis = self.analyze(
            question=question,
            reference_datetime=reference_datetime,
            upcoming_dates=describe_upcoming_dates(reference_datetime),
        )
        return self._to_result(analysis, question, reference_datetime)

    async def aforward(
        self, question: str, reference_datetime: str
    ) -> QuestionAnalysisResult:
        analysis = await self.analyze.acall(
            question=question,
            reference_datetime=reference_datetime,
            upcoming_dates=describe_upcoming_dates(reference_datetime),
        )
        return self._to_result(analysis, question, reference_datetime)


def build_lm(llm_settings: OpenRouterSettings) -> dspy.LM:
    if not llm_settings.api_key.strip():
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    extra_body: dict[str, Any] = {}
    if llm_settings.reasoning_effort:
        # Unset means the model's default, which is the most accurate setting.
        extra_body["reasoning"] = {"effort": llm_settings.reasoning_effort}
    if llm_settings.fallback_model:
        # OpenRouter retries with these models when the primary one fails or is
        # no longer served.
        extra_body["models"] = [llm_settings.fallback_model]

    return dspy.LM(
        f"openrouter/{llm_settings.model}",
        api_key=llm_settings.api_key,
        max_tokens=LLM_MAX_TOKENS,
        timeout=LLM_TIMEOUT_SECONDS,
        num_retries=LLM_NUM_RETRIES,
        extra_body=extra_body,
        # Every prompt embeds the current time, so cached answers are never reused.
        cache=False,
    )


def build_caltrain_schedule_helper(
    llm_settings: OpenRouterSettings, stations: Sequence[str]
) -> CaltrainScheduleHelper:
    dspy.configure(lm=build_lm(llm_settings))

    program = CaltrainScheduleHelper(stations)
    if OPTIMIZED_PROGRAM_PATH.exists():
        program.load(str(OPTIMIZED_PROGRAM_PATH))
        logger.info(f"Loaded optimized prompt from: {OPTIMIZED_PROGRAM_PATH}")
    return program
