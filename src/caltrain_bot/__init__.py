from aiohttp.web import AppRunner, run_app
from dotenv import load_dotenv
from telegram.ext import Application

from caltrain_bot.answers import build_schedule_service
from caltrain_bot.config import load_settings
from caltrain_bot.telegram_bot import build_app
from caltrain_bot.usage import open_usage_log
from caltrain_bot.web import build_web_app, start_web_server


def main() -> None:
    _ = load_dotenv()
    settings = load_settings()
    # The schedule takes ~1.6 GB of memory, so both frontends share one copy.
    service = build_schedule_service(settings)

    web_app = None
    web_port = settings.web_port
    if web_port is not None:
        web_app = build_web_app(service, open_usage_log(settings.usage_db_path))

    if settings.telegram_bot_token is None:
        # load_settings() guarantees at least one frontend, so this is the web.
        if web_app is not None:
            run_app(web_app, port=web_port)
        return

    telegram_app = build_app(settings.telegram_bot_token, service)
    if web_app is not None and web_port is not None:
        # Telegram polling owns the event loop; the web server rides along.
        runners: list[AppRunner] = []

        async def start_web(_: Application) -> None:
            runners.append(await start_web_server(web_app, web_port))

        async def stop_web(_: Application) -> None:
            for runner in runners:
                await runner.cleanup()

        telegram_app.post_init = start_web
        telegram_app.post_shutdown = stop_web
    telegram_app.run_polling()


if __name__ == "__main__":
    main()
