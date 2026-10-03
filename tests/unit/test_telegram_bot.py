import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


def _run_get_trains_info(departure_time: datetime, feed_end_date: date):
    message = SimpleNamespace(text="Palo Alto to SF", reply_text=AsyncMock())
    update = SimpleNamespace(message=message)
    schedule_manager = MagicMock()
    schedule_manager.get_trains.return_value = []
    schedule_helper = MagicMock(
        return_value=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="san francisco",
            departure_time=departure_time,
        )
    )
    asyncio.run(
        get_trains_info(
            update,  # type: ignore[arg-type]
            MagicMock(),
            schedule_manager=schedule_manager,
            schedule_helper=schedule_helper,
            feed_end_date=feed_end_date,
        )
    )
    replies = [call.args[0] for call in message.reply_text.call_args_list]
    return replies, schedule_manager


def test_get_trains_info_apologizes_for_dates_after_feed_end():
    replies, schedule_manager = _run_get_trains_info(
        datetime(2026, 9, 1, 8, 0), feed_end_date=date(2026, 8, 31)
    )

    assert "sincerely apologize" in replies[-1]
    schedule_manager.get_trains.assert_not_called()


def test_get_trains_info_queries_schedule_within_feed_dates():
    replies, schedule_manager = _run_get_trains_info(
        datetime(2026, 8, 31, 8, 0), feed_end_date=date(2026, 8, 31)
    )

    assert replies[-1] == "No matching trains found."
    schedule_manager.get_trains.assert_called_once()
