import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram.constants import ChatAction

from caltrain_bot.answers import ScheduleService
from caltrain_bot.question_analysis import ScheduleQuestion
from caltrain_bot.telegram_bot import (
    format_info_message,
    format_start_message,
    get_trains_info,
)


def test_format_start_message_includes_examples_and_info_prompt():
    message = format_start_message("Dima & Friends")

    assert "Hello Dima &amp; Friends!" in message
    assert "San Francisco to Palo Alto after 7pm" in message
    assert "Next train from Mountain View to San Jose Diridon" in message
    assert "Need a train from Millbrae to 22nd Street around 8:30" in message
    assert "Send <code>/info</code>" in message


def test_format_info_message_includes_capabilities_and_contributing():
    message = format_info_message()

    assert "scheduled Caltrain trips between stations" in message
    assert "What is the next train from Sunnyvale to Millbrae?" in message
    assert "not live delay or service alert feeds" in message
    assert "Contributing: https://github.com/CuriousDima/caltrain-bot" in message


def _run_get_trains_info(
    departure_time: datetime | None = None,
    feed_end_date: date = date(2026, 8, 31),
    analysis_error: Exception | None = None,
    typing_error: Exception | None = None,
):
    message = SimpleNamespace(
        text="Palo Alto to SF",
        reply_text=AsyncMock(),
        reply_chat_action=AsyncMock(side_effect=typing_error),
    )
    update = SimpleNamespace(message=message)
    schedule_manager = MagicMock()
    schedule_manager.get_trains.return_value = []
    schedule_helper = MagicMock()
    schedule_helper.acall = AsyncMock(
        side_effect=analysis_error,
        return_value=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="san francisco",
            departure_time=departure_time or datetime(2026, 8, 31, 8, 0),
        ),
    )
    asyncio.run(
        get_trains_info(
            update,  # type: ignore[arg-type]
            MagicMock(),
            service=ScheduleService(
                schedule_manager=schedule_manager,
                schedule_helper=schedule_helper,
                feed_end_date=feed_end_date,
            ),
        )
    )
    replies = [call.args[0] for call in message.reply_text.call_args_list]
    return replies, schedule_manager, message


def test_get_trains_info_apologizes_for_dates_after_feed_end():
    replies, schedule_manager, _ = _run_get_trains_info(
        datetime(2026, 9, 1, 8, 0), feed_end_date=date(2026, 8, 31)
    )

    assert "sincerely apologize" in replies[-1]
    schedule_manager.get_trains.assert_not_called()


def test_get_trains_info_queries_schedule_within_feed_dates():
    replies, schedule_manager, _ = _run_get_trains_info(
        datetime(2026, 8, 31, 8, 0), feed_end_date=date(2026, 8, 31)
    )

    assert replies == ["No matching trains found."]
    schedule_manager.get_trains.assert_called_once()


def test_get_trains_info_looks_up_the_next_trains_when_no_time_is_stated():
    asked_at = datetime(2026, 8, 31, 8, 0)
    message = SimpleNamespace(
        text="Next train from Palo Alto to SF",
        reply_text=AsyncMock(),
        reply_chat_action=AsyncMock(),
    )
    schedule_manager = MagicMock()
    schedule_manager.get_next_trains.return_value = []
    schedule_helper = MagicMock()
    schedule_helper.acall = AsyncMock(
        return_value=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="san francisco",
            departure_time=asked_at,
            departure_time_stated=False,
        )
    )

    asyncio.run(
        get_trains_info(
            SimpleNamespace(message=message),  # type: ignore[arg-type]
            MagicMock(),
            service=ScheduleService(
                schedule_manager=schedule_manager,
                schedule_helper=schedule_helper,
                feed_end_date=date(2026, 8, 31),
            ),
        )
    )

    schedule_manager.get_next_trains.assert_called_once_with(
        departure_station_query_name="palo alto",
        arrival_station_query_name="san francisco",
        after=asked_at,
    )
    schedule_manager.get_trains.assert_not_called()
    message.reply_text.assert_awaited_once_with(
        "No matching trains found.", parse_mode="HTML"
    )


def test_get_trains_info_shows_typing_instead_of_a_waiting_message():
    replies, _, message = _run_get_trains_info()

    assert replies == ["No matching trains found."]
    message.reply_chat_action.assert_awaited_once_with(ChatAction.TYPING)


def test_get_trains_info_answers_even_if_the_typing_indicator_fails():
    replies, _, _ = _run_get_trains_info(typing_error=RuntimeError("telegram down"))

    assert replies == ["No matching trains found."]


def test_get_trains_info_replies_when_question_analysis_fails():
    replies, schedule_manager, _ = _run_get_trains_info(
        analysis_error=TimeoutError("LLM timed out")
    )

    assert len(replies) == 1
    assert "could not process your question" in replies[0]
    schedule_manager.get_trains.assert_not_called()
