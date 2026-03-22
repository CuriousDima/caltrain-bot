from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from caltrain_bot.config import load_settings
from caltrain_bot.schedule import ScheduleManager
from caltrain_bot.text2sql import Text2SqlConvertor

load_dotenv()


def welcome_message(name: str) -> str:
    return f"Hello {name}! Welcome to the Caltrain Bot."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    name = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(welcome_message(name))


def build_app():
    settings = load_settings()
    schedule_manager = ScheduleManager(
        schedules_file=settings.gtfs_file_path,
        preprocess_sql=settings.preprocessing_sql_path,
        use_in_memory_db=False,
    )
    text2sql_convertor = Text2SqlConvertor(settings.llm, schedule_manager.stations)
    stations_prediction = text2sql_convertor.convert(
        "Trains from SF to Menlo Park departing after in 30 minutes"
    )
    print(stations_prediction)

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    return app
