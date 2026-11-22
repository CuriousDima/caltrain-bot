import asyncio
from datetime import datetime
from typing import get_args

import dspy
import pytest
from dspy.utils import DummyLM

from caltrain_bot import question_analysis
from caltrain_bot.config import OpenRouterSettings
from caltrain_bot.question_analysis import (
    CALTRAIN_TIMEZONE,
    CaltrainScheduleHelper,
    ScheduleQuestion,
    UnsupportedQuestion,
    build_caltrain_schedule_helper,
    build_lm,
    build_schedule_question_signature,
    describe_upcoming_dates,
)

STATIONS = ("san francisco", "palo alto")
REFERENCE_DATETIME = "2026-08-24T15:00:00-07:00"


class StubPredict(dspy.Module):
    """Stands in for the single LLM-backed predictor."""

    def __init__(self, prediction: dspy.Prediction) -> None:
        super().__init__()
        self.prediction = prediction
        self.calls: list[dict[str, str]] = []

    def forward(self, **kwargs: str) -> dspy.Prediction:
        self.calls.append(kwargs)
        return self.prediction

    async def aforward(self, **kwargs: str) -> dspy.Prediction:
        return self.forward(**kwargs)


def _helper(**prediction: object) -> tuple[CaltrainScheduleHelper, StubPredict]:
    helper = CaltrainScheduleHelper(STATIONS)
    stub = StubPredict(dspy.Prediction(**prediction))
    helper.analyze = stub
    return helper, stub


def test_signature_limits_stations_to_the_known_names():
    signature = build_schedule_question_signature(STATIONS)

    assert set(signature.input_fields) == {
        "question",
        "reference_datetime",
        "upcoming_dates",
    }
    assert set(signature.output_fields) == {
        "is_schedule_question",
        "departure_station",
        "arrival_station",
        "departure_time",
    }
    station_type, none_type = get_args(
        signature.output_fields["departure_station"].annotation
    )
    assert get_args(station_type) == STATIONS
    assert none_type is type(None)


def test_signature_requires_stations():
    with pytest.raises(ValueError, match="At least one station"):
        build_schedule_question_signature(())


def test_describe_upcoming_dates_names_each_weekday():
    upcoming_dates = describe_upcoming_dates(REFERENCE_DATETIME).split(", ")

    assert upcoming_dates[:3] == [
        "Monday 2026-08-24 (today)",
        "Tuesday 2026-08-25 (tomorrow)",
        "Wednesday 2026-08-26",
    ]
    assert upcoming_dates[-1] == "Sunday 2026-09-06"
    assert len(upcoming_dates) == 14


def test_helper_analyzes_a_question_with_a_single_call():
    helper, stub = _helper(
        is_schedule_question=True,
        departure_station="San Francisco",
        arrival_station="palo alto",
        departure_time="2026-08-24T19:00",
    )

    result = helper(
        question="San Francisco to Palo Alto after 7pm",
        reference_datetime=REFERENCE_DATETIME,
    )

    assert result == ScheduleQuestion(
        departure_station="san francisco",
        arrival_station="palo alto",
        departure_time=datetime(2026, 8, 24, 19, 0, tzinfo=CALTRAIN_TIMEZONE),
    )
    assert stub.calls == [
        {
            "question": "San Francisco to Palo Alto after 7pm",
            "reference_datetime": REFERENCE_DATETIME,
            "upcoming_dates": describe_upcoming_dates(REFERENCE_DATETIME),
        }
    ]


def test_helper_supports_async_calls():
    helper, stub = _helper(
        is_schedule_question=True,
        departure_station="san francisco",
        arrival_station="palo alto",
        departure_time="2026-08-24T19:00",
    )

    result = asyncio.run(
        helper.acall(question="SF to Palo Alto", reference_datetime=REFERENCE_DATETIME)
    )

    assert isinstance(result, ScheduleQuestion)
    assert len(stub.calls) == 1


def test_helper_keeps_an_explicit_utc_offset():
    helper, _ = _helper(
        is_schedule_question=True,
        departure_station="san francisco",
        arrival_station="palo alto",
        departure_time="2026-08-24T19:00:00-07:00",
    )

    result = helper(question="SF to PA at 7", reference_datetime=REFERENCE_DATETIME)

    assert isinstance(result, ScheduleQuestion)
    assert result.departure_time == datetime.fromisoformat("2026-08-24T19:00:00-07:00")


def test_helper_rejects_an_unsupported_question():
    helper, _ = _helper(
        is_schedule_question=False,
        departure_station=None,
        arrival_station=None,
        departure_time=None,
    )

    result = helper(
        question="How much is a ticket?", reference_datetime=REFERENCE_DATETIME
    )

    assert result == UnsupportedQuestion()


@pytest.mark.parametrize("missing_time", [None, "TBD", "null", ""])
def test_helper_uses_reference_datetime_when_departure_time_is_missing(missing_time):
    helper, _ = _helper(
        is_schedule_question=True,
        departure_station="san francisco",
        arrival_station="palo alto",
        departure_time=missing_time,
    )

    result = helper(
        question="San Francisco to Palo Alto?", reference_datetime=REFERENCE_DATETIME
    )

    assert result == ScheduleQuestion(
        departure_station="san francisco",
        arrival_station="palo alto",
        departure_time=datetime.fromisoformat(REFERENCE_DATETIME),
        departure_time_stated=False,
    )


@pytest.mark.parametrize(
    ("arrival_station", "departure_time"),
    [
        ("unknown", "2026-08-24T19:00"),
        (None, "2026-08-24T19:00"),
        ("san francisco", "2026-08-24T19:00"),
        ("palo alto", "seven-ish"),
        # Valid ISO 8601, yet outside what a datetime can hold once it is
        # moved to Caltrain time. Only a prompt injection produces these.
        ("palo alto", "0001-01-01T00:00+14:00"),
        ("palo alto", "9999-12-31T23:59-12:00"),
    ],
)
def test_helper_rejects_an_incomplete_or_invalid_lookup(
    arrival_station, departure_time
):
    helper, _ = _helper(
        is_schedule_question=True,
        departure_station="san francisco",
        arrival_station=arrival_station,
        departure_time=departure_time,
    )

    result = helper(
        question="What trains leave San Francisco at 7pm?",
        reference_datetime=REFERENCE_DATETIME,
    )

    assert result == UnsupportedQuestion()


def test_helper_parses_a_model_response_end_to_end():
    helper = CaltrainScheduleHelper(STATIONS)
    lm = DummyLM(
        [
            {
                "is_schedule_question": "True",
                "departure_station": "palo alto",
                "arrival_station": "san francisco",
                "departure_time": "2026-08-25T08:30",
            }
        ]
    )

    with dspy.context(lm=lm):
        result = helper(
            question="Palo Alto to SF tomorrow at 8:30am",
            reference_datetime=REFERENCE_DATETIME,
        )

    assert result == ScheduleQuestion(
        departure_station="palo alto",
        arrival_station="san francisco",
        departure_time=datetime(2026, 8, 25, 8, 30, tzinfo=CALTRAIN_TIMEZONE),
    )


def test_build_lm_configures_a_fast_failing_openrouter_model():
    lm = build_lm(OpenRouterSettings(api_key="test-key", model="vendor/model"))

    assert lm.model == "openrouter/vendor/model"
    assert lm.cache is False
    assert lm.num_retries == question_analysis.LLM_NUM_RETRIES
    assert lm.kwargs["timeout"] == question_analysis.LLM_TIMEOUT_SECONDS
    assert lm.kwargs["max_tokens"] == question_analysis.LLM_MAX_TOKENS
    assert lm.kwargs["extra_body"] == {}


def test_build_lm_passes_optional_openrouter_settings():
    lm = build_lm(
        OpenRouterSettings(
            api_key="test-key",
            model="vendor/model",
            fallback_model="other/model",
            reasoning_effort="none",
        )
    )

    assert lm.kwargs["extra_body"] == {
        "reasoning": {"effort": "none"},
        "models": ["other/model"],
    }


def test_build_lm_requires_an_api_key():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_lm(OpenRouterSettings(api_key=" ", model="vendor/model"))


def test_builder_works_without_an_optimized_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(
        question_analysis, "OPTIMIZED_PROGRAM_PATH", tmp_path / "missing.json"
    )

    helper = build_caltrain_schedule_helper(
        OpenRouterSettings(api_key="test-key", model="test-model"), STATIONS
    )

    assert (
        helper.analyze.signature.instructions
        == question_analysis.SCHEDULE_QUESTION_INSTRUCTIONS.strip()
    )


def test_builder_loads_the_optimized_prompt(monkeypatch, tmp_path):
    optimized = CaltrainScheduleHelper(STATIONS)
    optimized.analyze.signature = optimized.analyze.signature.with_instructions(
        "Optimized instructions."
    )
    program_path = tmp_path / "program.json"
    optimized.save(str(program_path))
    monkeypatch.setattr(question_analysis, "OPTIMIZED_PROGRAM_PATH", program_path)

    helper = build_caltrain_schedule_helper(
        OpenRouterSettings(api_key="test-key", model="test-model"), STATIONS
    )

    assert helper.analyze.signature.instructions == "Optimized instructions."
    station_type, _ = get_args(
        helper.analyze.signature.output_fields["arrival_station"].annotation
    )
    assert get_args(station_type) == STATIONS
