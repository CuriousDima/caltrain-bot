from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import dspy
import pytest

from caltrain_bot.config import OpenRouterSettings
from caltrain_bot.question_analysis import ReActWithDatetime, _validate_settings


class ExampleSignature(dspy.Signature):
    question: str = dspy.InputField()
    answer: str = dspy.OutputField()


def test_react_with_datetime_adds_and_supplies_current_datetime(monkeypatch):
    fixed_datetime = datetime(
        2026,
        8,
        23,
        12,
        30,
        tzinfo=ZoneInfo("America/Los_Angeles"),
    )
    datetime_mock = Mock()
    datetime_mock.now.return_value = fixed_datetime
    monkeypatch.setattr("caltrain_bot.question_analysis.datetime", datetime_mock)

    react = Mock(return_value=dspy.Prediction(answer="test answer"))
    react_factory = Mock(return_value=react)
    monkeypatch.setattr(dspy, "ReAct", react_factory)

    module = ReActWithDatetime(ExampleSignature, tools=[], max_iterations=5)
    prediction = module(question="test question")

    extended_signature = react_factory.call_args.kwargs["signature"]
    assert list(extended_signature.input_fields) == ["question", "current_datetime"]
    assert extended_signature.fields["current_datetime"].annotation is str
    react_factory.assert_called_once_with(
        signature=extended_signature,
        tools=[],
        max_iters=5,
    )
    datetime_mock.now.assert_called_once_with(ZoneInfo("America/Los_Angeles"))
    react.assert_called_once_with(
        question="test question",
        current_datetime=fixed_datetime.isoformat(),
    )
    assert prediction.answer == "test answer"


def test_validate_settings_accepts_openrouter_settings():
    settings = OpenRouterSettings(
        api_key="test-api-key",
        model="openai/gpt-4o-mini",
    )

    _validate_settings(settings)


@pytest.mark.parametrize("api_key", ["", "   ", "\n\t"])
def test_validate_settings_rejects_blank_openrouter_api_key(api_key):
    settings = OpenRouterSettings(
        api_key=api_key,
        model="openai/gpt-4o-mini",
    )

    with pytest.raises(
        ValueError, match="OPENROUTER_API_KEY environment variable is required."
    ):
        _validate_settings(settings)
