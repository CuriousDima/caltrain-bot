from aiohttp.web import AppRunner, run_app
from dotenv import load_dotenv
from loguru import logger

from caltrain_bot.answers import build_schedule_service
from caltrain_bot.config import load_settings
from caltrain_bot.stats_web import build_stats_app, start_stats_server
from caltrain_bot.telegram_bot import build_app
from caltrain_bot.usage import open_usage_log
from caltrain_bot.web import build_web_app, start_web_server


def main() -> None:
    _ = load_dotenv()
    settings = load_settings()
    # The schedule takes ~1.6 GB of memory, so both frontends share one copy.
    service = build_schedule_service(settings)

    web_app = None
    stats_app = None
    web_port = settings.web_port
    stats_port = settings.stats_port
    if web_port is not None:
        # One usage log: the web page writes it, the statistics page reads it.
        usage_log = open_usage_log(settings.usage_db_path)
        web_app = build_web_app(service, usage_log)
        if stats_port is not None and usage_log is not None:
            stats_app = build_stats_app(usage_log)
        elif stats_port is not None:
            logger.warning(
                "The statistics page is not served: the usage log is unavailable."
            )

    runners: list[AppRunner] = []

    async def start_stats(_: object) -> None:
        if stats_app is None or stats_port is None:
            return
        try:
            runners.append(await start_stats_server(stats_app, stats_port))
        except OSError as error:
            # A busy port must not take the public page down with it.
            logger.warning(f"The statistics page is not served: {error}")

    async def stop_servers(_: object) -> None:
        for runner in runners:
            await runner.cleanup()

    if settings.telegram_bot_token is None:
        # load_settings() guarantees at least one frontend, so this is the web.
        if web_app is not None:
            if stats_app is not None:
                # run_app() owns the event loop; the statistics server rides along.
                web_app.on_startup.append(start_stats)
                web_app.on_cleanup.append(stop_servers)
            run_app(web_app, port=web_port)
        return

    telegram_app = build_app(settings.telegram_bot_token, service)
    if web_app is not None and web_port is not None:
        # Telegram polling owns the event loop; the web servers ride along.
        async def start_web(_: object) -> None:
            runners.append(await start_web_server(web_app, web_port))
            await start_stats(None)

        telegram_app.post_init = start_web
        telegram_app.post_shutdown = stop_servers
    telegram_app.run_polling()


if __name__ == "__main__":
    main()
