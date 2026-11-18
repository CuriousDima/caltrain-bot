"""Transport-independent question answering shared by every frontend."""

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from loguru import logger

from caltrain_bot.config import Settings
from caltrain_bot.question_analysis import (
    CALTRAIN_TIMEZONE,
    CaltrainScheduleHelper,
    ScheduleQuestion,
    UnsupportedQuestion,
    build_caltrain_schedule_helper,
)
from caltrain_bot.schedule import (
    ScheduleManager,
    Train,
    check_feed_expiry,
    is_after_feed_end,
)

ANALYSIS_FAILED_MESSAGE = (
    "Sorry, I could not process your question right now. Please try again in a moment."
)
UNSUPPORTED_MESSAGE = (
    "I can only help with Caltrain train schedules, routes, and stations."
)
FEED_EXPIRED_MESSAGE = (
    "We sincerely apologize! Our schedule data does not cover that date yet. "
    "We will update the schedule soon, so please check back later."
)

AnswerStatus = Literal["trains", "unsupported", "feed_expired", "error"]


@dataclass(frozen=True)
class Answer:
    """Outcome of one question; each frontend renders it in its own format."""

    status: AnswerStatus
    # Set for "trains" and "feed_expired": what the question was understood as.
    question: ScheduleQuestion | None = None
    trains: tuple[Train, ...] = ()
    # User-facing explanation for every status except "trains".
    message: str | None = None


@dataclass(frozen=True)
class ScheduleService:
    """Everything needed to answer a question, built once at startup."""

    schedule_manager: ScheduleManager
    schedule_helper: CaltrainScheduleHelper
    feed_end_date: date


def build_schedule_service(settings: Settings) -> ScheduleService:
    feed_end_date = check_feed_expiry(settings.gtfs_file_path)
    schedule_manager = ScheduleManager(
        schedules_file=settings.gtfs_file_path,
        preprocess_sql=settings.preprocessing_sql_path,
        use_in_memory_db=(os.getenv("DEBUG") != "1"),
    )
    schedule_helper = build_caltrain_schedule_helper(
        settings.llm, schedule_manager.stations
    )
    return ScheduleService(
        schedule_manager=schedule_manager,
        schedule_helper=schedule_helper,
        feed_end_date=feed_end_date,
    )


async def answer_question(question: str, service: ScheduleService) -> Answer:
    """Answer one plain-English question. Never raises: failures become an Answer."""
    reference_datetime = datetime.now(CALTRAIN_TIMEZONE).isoformat()
    try:
        result = await service.schedule_helper.acall(
            question=question, reference_datetime=reference_datetime
        )
    except Exception:
        logger.exception(f"Question analysis failed for: {question!r}")
        return Answer(status="error", message=ANALYSIS_FAILED_MESSAGE)
    if isinstance(result, UnsupportedQuestion):
        return Answer(status="unsupported", message=UNSUPPORTED_MESSAGE)
    if is_after_feed_end(result.departure_time, service.feed_end_date):
        return Answer(
            status="feed_expired", question=result, message=FEED_EXPIRED_MESSAGE
        )
    if result.departure_time_stated:
        trains = service.schedule_manager.get_trains(
            departure_station_query_name=result.departure_station,
            arrival_station_query_name=result.arrival_station,
            departure_time=result.departure_time,
        )
    else:
        # "Next train": a window around now would often hold one train or none.
        trains = service.schedule_manager.get_next_trains(
            departure_station_query_name=result.departure_station,
            arrival_station_query_name=result.arrival_station,
            after=result.departure_time,
        )
    return Answer(status="trains", question=result, trains=tuple(trains))
