from datetime import datetime

import dspy

from caltrain_bot.question_analysis import (
    CaltrainScheduleHelper,
    ScheduleQuestion,
    UnsupportedQuestion,
)


class StubModule(dspy.Module):
    def __init__(self, prediction: dspy.Prediction) -> None:
        super().__init__()
        self.prediction = prediction
        self.questions: list[str] = []

    def forward(self, question: str) -> dspy.Prediction:
        self.questions.append(question)
        return self.prediction


def test_helper_does_not_extract_an_unsupported_question():
    classifier = StubModule(dspy.Prediction(is_schedule_question=False))
    extractor = StubModule(dspy.Prediction())
    helper = CaltrainScheduleHelper(classifier, extractor)

    result = helper(question="How much is a ticket?")

    assert result == UnsupportedQuestion()
    assert classifier.questions == ["How much is a ticket?"]
    assert extractor.questions == []


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
    helper = CaltrainScheduleHelper(classifier, extractor)

    result = helper(question="San Francisco to Palo Alto after 7pm")

    assert result == ScheduleQuestion(
        departure_station="San Francisco",
        arrival_station="Palo Alto",
        departure_time=departure_time,
    )
    assert classifier.questions == ["San Francisco to Palo Alto after 7pm"]
    assert extractor.questions == ["San Francisco to Palo Alto after 7pm"]
