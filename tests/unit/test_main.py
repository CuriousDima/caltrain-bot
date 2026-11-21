import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import caltrain_bot


@pytest.fixture
def wiring(monkeypatch):
    """Replace everything `main()` starts, so no schedule, port, or bot is real."""
    mocks = SimpleNamespace(
        service=object(),
        web_app=SimpleNamespace(on_startup=[], on_cleanup=[]),
        stats_app=object(),
        telegram_app=MagicMock(post_init=None, post_shutdown=None),
        run_app=MagicMock(),
        runner=MagicMock(cleanup=AsyncMock()),
        stats_runner=MagicMock(cleanup=AsyncMock()),
        warning=MagicMock(),
        usage_log=object(),
    )
    mocks.build_web_app = MagicMock(return_value=mocks.web_app)
    mocks.open_usage_log = MagicMock(return_value=mocks.usage_log)
    mocks.build_app = MagicMock(return_value=mocks.telegram_app)
    mocks.start_web_server = AsyncMock(return_value=mocks.runner)
    mocks.build_stats_app = MagicMock(return_value=mocks.stats_app)
    mocks.start_stats_server = AsyncMock(return_value=mocks.stats_runner)
    monkeypatch.setattr(caltrain_bot, "load_dotenv", lambda: False)
    monkeypatch.setattr(caltrain_bot, "build_schedule_service", lambda _: mocks.service)
    monkeypatch.setattr(caltrain_bot, "build_web_app", mocks.build_web_app)
    monkeypatch.setattr(caltrain_bot, "open_usage_log", mocks.open_usage_log)
    monkeypatch.setattr(caltrain_bot, "build_app", mocks.build_app)
    monkeypatch.setattr(caltrain_bot, "run_app", mocks.run_app)
    monkeypatch.setattr(caltrain_bot, "start_web_server", mocks.start_web_server)
    monkeypatch.setattr(caltrain_bot, "build_stats_app", mocks.build_stats_app)
    monkeypatch.setattr(caltrain_bot, "start_stats_server", mocks.start_stats_server)
    monkeypatch.setattr(caltrain_bot.logger, "warning", mocks.warning)

    def use_settings(telegram_bot_token, web_port, stats_port=None):
        settings = SimpleNamespace(
            telegram_bot_token=telegram_bot_token,
            web_port=web_port,
            stats_port=stats_port,
            usage_db_path="usage.db",
        )
        monkeypatch.setattr(caltrain_bot, "load_settings", lambda: settings)

    mocks.use_settings = use_settings
    return mocks


def test_main_serves_only_the_web_page_without_a_telegram_token(wiring):
    wiring.use_settings(telegram_bot_token=None, web_port=8080)

    caltrain_bot.main()

    wiring.run_app.assert_called_once_with(wiring.web_app, port=8080)
    wiring.open_usage_log.assert_called_once_with("usage.db")
    wiring.build_web_app.assert_called_once_with(wiring.service, wiring.usage_log)
    wiring.build_app.assert_not_called()
    wiring.build_stats_app.assert_not_called()
    assert wiring.web_app.on_startup == []


def test_main_runs_only_telegram_without_a_web_port(wiring):
    wiring.use_settings(telegram_bot_token="token", web_port=None)

    caltrain_bot.main()

    wiring.build_app.assert_called_once_with("token", wiring.service)
    wiring.telegram_app.run_polling.assert_called_once_with()
    assert wiring.telegram_app.post_init is None
    wiring.run_app.assert_not_called()
    wiring.open_usage_log.assert_not_called()


def test_main_serves_the_web_page_next_to_telegram(wiring):
    wiring.use_settings(telegram_bot_token="token", web_port=8080)

    caltrain_bot.main()

    wiring.telegram_app.run_polling.assert_called_once_with()
    wiring.run_app.assert_not_called()
    # Telegram runs these hooks around polling, on its own event loop.
    asyncio.run(wiring.telegram_app.post_init(wiring.telegram_app))
    wiring.start_web_server.assert_awaited_once_with(wiring.web_app, 8080)
    wiring.start_stats_server.assert_not_awaited()
    asyncio.run(wiring.telegram_app.post_shutdown(wiring.telegram_app))
    wiring.runner.cleanup.assert_awaited_once_with()


def test_main_serves_the_statistics_page_next_to_the_web_page(wiring):
    wiring.use_settings(telegram_bot_token=None, web_port=8080, stats_port=8081)

    caltrain_bot.main()

    # Both apps share the one usage log; only the web app goes on WEB_PORT.
    wiring.open_usage_log.assert_called_once_with("usage.db")
    wiring.build_web_app.assert_called_once_with(wiring.service, wiring.usage_log)
    wiring.build_stats_app.assert_called_once_with(wiring.usage_log)
    wiring.run_app.assert_called_once_with(wiring.web_app, port=8080)
    # aiohttp runs these hooks around serving, on its own event loop.
    (on_startup,) = wiring.web_app.on_startup
    (on_cleanup,) = wiring.web_app.on_cleanup
    asyncio.run(on_startup(wiring.web_app))
    wiring.start_stats_server.assert_awaited_once_with(wiring.stats_app, 8081)
    wiring.start_web_server.assert_not_awaited()
    asyncio.run(on_cleanup(wiring.web_app))
    wiring.stats_runner.cleanup.assert_awaited_once_with()


def test_main_serves_the_statistics_page_next_to_telegram(wiring):
    wiring.use_settings(telegram_bot_token="token", web_port=8080, stats_port=8081)

    caltrain_bot.main()

    wiring.build_stats_app.assert_called_once_with(wiring.usage_log)
    wiring.telegram_app.run_polling.assert_called_once_with()
    wiring.run_app.assert_not_called()
    asyncio.run(wiring.telegram_app.post_init(wiring.telegram_app))
    wiring.start_web_server.assert_awaited_once_with(wiring.web_app, 8080)
    wiring.start_stats_server.assert_awaited_once_with(wiring.stats_app, 8081)
    asyncio.run(wiring.telegram_app.post_shutdown(wiring.telegram_app))
    wiring.runner.cleanup.assert_awaited_once_with()
    wiring.stats_runner.cleanup.assert_awaited_once_with()


@pytest.mark.parametrize("telegram_bot_token", [None, "token"])
def test_main_skips_the_statistics_page_without_a_usage_log(wiring, telegram_bot_token):
    wiring.open_usage_log.return_value = None
    wiring.use_settings(
        telegram_bot_token=telegram_bot_token, web_port=8080, stats_port=8081
    )

    caltrain_bot.main()

    wiring.build_web_app.assert_called_once_with(wiring.service, None)
    wiring.build_stats_app.assert_not_called()
    wiring.warning.assert_called_once()
    assert "statistics page is not served" in wiring.warning.call_args.args[0]
    assert wiring.web_app.on_startup == []
    if telegram_bot_token is None:
        wiring.run_app.assert_called_once_with(wiring.web_app, port=8080)
    else:
        asyncio.run(wiring.telegram_app.post_init(wiring.telegram_app))
        wiring.start_web_server.assert_awaited_once_with(wiring.web_app, 8080)
    wiring.start_stats_server.assert_not_awaited()


def test_main_keeps_the_web_page_up_when_the_statistics_port_is_busy(wiring):
    wiring.start_stats_server.side_effect = OSError("address already in use")
    wiring.use_settings(telegram_bot_token="token", web_port=8080, stats_port=8081)

    caltrain_bot.main()

    asyncio.run(wiring.telegram_app.post_init(wiring.telegram_app))
    wiring.start_web_server.assert_awaited_once_with(wiring.web_app, 8080)
    wiring.warning.assert_called_once()
    assert "address already in use" in wiring.warning.call_args.args[0]
    asyncio.run(wiring.telegram_app.post_shutdown(wiring.telegram_app))
    wiring.runner.cleanup.assert_awaited_once_with()
