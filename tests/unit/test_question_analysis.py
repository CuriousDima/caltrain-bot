from datetime import datetime

import dspy
import pytest

from caltrain_bot.config import OpenRouterSettings
from caltrain_bot.question_analysis import (
    CaltrainScheduleHelper,
    OPTIMIZED_PROGRAM_PATH,
    ScheduleQuestion,
    UnsupportedQuestion,
    build_caltrain_schedule_helper,
    build_station_extraction_signature,
)

STATIONS = ("san francisco", "palo alto")


class StubModule(dspy.Module):
    def __init__(self, prediction: dspy.Prediction) -> None:
        super().__init__()
        self.prediction = prediction
        self.questions: list[str] = []
        self.reference_datetimes: list[str | None] = []

    def forward(
        self, question: str, reference_datetime: str | None = None
    ) -> dspy.Prediction:
        self.questions.append(question)
        self.reference_datetimes.append(reference_datetime)
        return self.prediction


def test_station_extraction_signature_accepts_reference_datetime():
    signature = build_station_extraction_signature(STATIONS)

    assert set(signature.input_fields) == {"question", "reference_datetime"}


def test_builder_loads_the_optimized_program(monkeypatch):
    classifier = StubModule(dspy.Prediction(is_schedule_question=False))
    extractor = StubModule(dspy.Prediction())
    optimized_program = CaltrainScheduleHelper(classifier, extractor, STATIONS)
    load_calls: list[tuple[str, bool]] = []

    def fake_load(path: str, allow_pickle: bool):
        load_calls.append((path, allow_pickle))
        return optimized_program

    monkeypatch.setattr(dspy, "load", fake_load)

    result = build_caltrain_schedule_helper(
        OpenRouterSettings(api_key="test-key", model="test-model"),
        STATIONS,
    )

    assert result is optimized_program
    assert load_calls == [(str(OPTIMIZED_PROGRAM_PATH), True)]


def test_builder_rejects_an_optimized_program_with_stale_stations(monkeypatch):
    classifier = StubModule(dspy.Prediction(is_schedule_question=False))
    extractor = StubModule(dspy.Prediction())
    optimized_program = CaltrainScheduleHelper(
        classifier, extractor, ("san francisco",)
    )
    monkeypatch.setattr(dspy, "load", lambda *_args, **_kwargs: optimized_program)

    with pytest.raises(ValueError, match="station list does not match"):
        build_caltrain_schedule_helper(
            OpenRouterSettings(api_key="test-key", model="test-model"),
            STATIONS,
        )


def test_helper_does_not_extract_an_unsupported_question():
    classifier = StubModule(dspy.Prediction(is_schedule_question=False))
    extractor = StubModule(dspy.Prediction())
    helper = CaltrainScheduleHelper(classifier, extractor, STATIONS)

    result = helper(
        question="How much is a ticket?",
        reference_datetime="2026-08-24T15:00:00-07:00",
    )

    assert result == UnsupportedQuestion()
    assert classifier.questions == ["How much is a ticket?"]
    assert classifier.reference_datetimes == [None]
    assert extractor.questions == []
    assert extractor.reference_datetimes == []


def test_helper_extracts_a_schedule_question():
    departure_time = datetime.fromisoformat("2026-08-24T19:00:00-07:00")
    classifier = StubModule(dspy.Prediction(is_schedule_question=True))
    extractor = StubModule(
        dspy.Prediction(
            departure_station="San Francisco",
            arrival_station="Palo Alto",
            departure_time=departure_time,
        )
    )
    helper = CaltrainScheduleHelper(classifier, extractor, STATIONS)

    reference_datetime = "2026-08-24T15:00:00-07:00"
    result = helper(
        question="San Francisco to Palo Alto after 7pm",
        reference_datetime=reference_datetime,
    )

    assert result == ScheduleQuestion(
        departure_station="san francisco",
        arrival_station="palo alto",
        departure_time=departure_time,
    )
    assert classifier.questions == ["San Francisco to Palo Alto after 7pm"]
    assert classifier.reference_datetimes == [None]
    assert extractor.questions == ["San Francisco to Palo Alto after 7pm"]
    assert extractor.reference_datetimes == [reference_datetime]


def test_helper_uses_reference_datetime_when_departure_time_is_missing():
    classifier = StubModule(dspy.Prediction(is_schedule_question=True))
    extractor = StubModule(
        dspy.Prediction(
            departure_station="san francisco",
            arrival_station="palo alto",
            departure_time="TBD",
        )
    )
    helper = CaltrainScheduleHelper(classifier, extractor, STATIONS)

    reference_datetime = "2026-08-24T15:00:00-07:00"
    result = helper(
        question="San Francisco to Palo Alto?",
        reference_datetime=reference_datetime,
    )

    assert result == ScheduleQuestion(
        departure_station="san francisco",
        arrival_station="palo alto",
        departure_time=datetime.fromisoformat(reference_datetime),
    )


def test_helper_rejects_an_unknown_station_without_raising():
    classifier = StubModule(dspy.Prediction(is_schedule_question=True))
    extractor = StubModule(
        dspy.Prediction(
            departure_station="san francisco",
            arrival_station="unknown",
            departure_time="2026-08-24T19:00:00-07:00",
        )
    )
    helper = CaltrainScheduleHelper(classifier, extractor, STATIONS)

    result = helper(
        question="What trains leave San Francisco at 7pm?",
        reference_datetime="2026-08-24T15:00:00-07:00",
    )

    assert result == UnsupportedQuestion()
