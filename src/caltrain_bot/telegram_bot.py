import functools
import os
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from caltrain_bot.config import load_settings
from caltrain_bot.question_analysis import (
    CaltrainScheduleHelper,
    UnsupportedQuestion,
    build_caltrain_schedule_helper,
)
from caltrain_bot.schedule import ScheduleManager, Train

_ = load_dotenv()


def _format_timestamp(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except ValueError:
        return str(value)


def format_trains_message(trains: list[Train]) -> str:
    if not trains:
        return "No matching trains found."

    parts = [f"Found <b>{len(trains)}</b> matching trains 🚂:"]
    for train in trains:
        origin = escape(train.origin_station_name.removesuffix(" Station"))
        destination = escape(train.destination_station_name.removesuffix(" Station"))
        departure = escape(_format_timestamp(train.origin_departure_timestamp))
        arrival = escape(_format_timestamp(train.destination_arrival_timestamp))
        service_pattern = escape(train.service_pattern)
        parts.append(
            "\n".join(
                (
                    f"<b>{origin}</b> {departure} → <b>{destination}</b> {arrival}",
                    f"{train.travel_minutes} min | Train {train.train_number} | {service_pattern}",
                )
            )
        )
    return "\n\n".join(parts)


def format_start_message(name: str) -> str:
    safe_name = escape(name)
    return "\n".join(
        (
            f"Hello {safe_name}! I can help you find Caltrain trips between stations.",
            "",
            "Ask in plain English. For example:",
            "<code>San Francisco to Palo Alto after 7pm</code>",
            "<code>Next train from Mountain View to San Jose Diridon</code>",
            "<code>Need a train from Millbrae to 22nd Street around 8:30</code>",
            "",
            "Include your departure station, destination, and optionally a time.",
            "Send <code>/info</code> for more about the bot, the maintainer, and contributing.",
        )
    )


def format_info_message() -> str:
    return "\n".join(
        (
            "Caltrain Bot helps with scheduled Caltrain trips between stations.",
            "",
            "What I can do:",
            "- Find matching trains between Caltrain stations",
            "- Understand natural-language questions about routes and times",
            "- Show departure, arrival, duration, train number, and service type",
            "",
            "Example questions:",
            "<code>Belmont to San Francisco tomorrow at 9am</code>",
            "<code>What is the next train from Sunnyvale to Millbrae?</code>",
            "<code>Trains from Palo Alto to San Jose Diridon after 6:15pm</code>",
            "",
            "Good to know:",
            "- This bot works from timetable data, not live delay or service alert feeds",
            "- It does not handle ticketing, fares, or bookings",
            "",
            "Contributing: https://github.com/CuriousDima/caltrain-bot",
        )
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    name = update.effective_user.first_name if update.effective_user else "there"
    _ = await update.message.reply_text(format_start_message(name), parse_mode="HTML")


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    _ = await update.message.reply_text(format_info_message(), parse_mode="HTML")


async def get_trains_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    schedule_manager: ScheduleManager,
    schedule_helper: CaltrainScheduleHelper,
) -> None:
    if not update.message:
        return
    if not update.message.text:
        _ = await update.message.reply_text("Sorry, I can only process text messages.")
        return
    _ = await update.message.reply_text(
        "I am checking your question and looking up the train information now. Please give me a minute!"
    )
    question = update.message.text
    schedule_helper_result = schedule_helper(
        question=question,
        reference_datetime=datetime.now(ZoneInfo("America/Los_Angeles")).isoformat(),
    )
    if isinstance(schedule_helper_result, UnsupportedQuestion):
        _ = await update.message.reply_text(
            "I can only help with Caltrain train schedules, routes, and stations."
        )
        return
    trains = schedule_manager.get_trains(
        departure_station_query_name=schedule_helper_result.departure_station,
        arrival_station_query_name=schedule_helper_result.arrival_station,
        departure_time=schedule_helper_result.departure_time,
    )
    trains_message = format_trains_message(trains)
    _ = await update.message.reply_text(trains_message, parse_mode="HTML")


def build_app():
    settings = load_settings()
    schedule_manager = ScheduleManager(
        schedules_file=settings.gtfs_file_path,
        preprocess_sql=settings.preprocessing_sql_path,
        use_in_memory_db=(os.getenv("DEBUG") != "1"),
    )
    caltrain_schedule_helper = build_caltrain_schedule_helper(
        settings.llm, schedule_manager.stations
    )

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            functools.partial(
                get_trains_info,
                schedule_manager=schedule_manager,
                schedule_helper=caltrain_schedule_helper,
            ),
        )
    )
    return app
