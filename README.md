# Caltrain Bot

Caltrain Bot is a Python chatbot that answers Caltrain schedule questions from natural language. It currently ships as a Telegram bot, loads Caltrain GTFS data into SQLite, and uses DSPy plus an LLM provider to understand questions such as "When is the next train from Palo Alto to San Francisco around 8am?"


## Project Layout

- `src/caltrain_bot/telegram_bot.py`: Telegram bot handlers and message formatting.
- `src/caltrain_bot/question_analysis.py`: DSPy-based question classification and entity extraction.
- `src/caltrain_bot/schedule.py`: GTFS loading, preprocessing, and schedule queries.
- `sql/train_stop_timeline.sql`: SQL used to preprocess imported GTFS data into query-friendly tables.
- `src/caltrain_bot/config.py`: Environment-based configuration for Telegram and the LLM provider.
- `data/caltrain-ca-us.zip`: Bundled Caltrain GTFS feed used by the app.
- `tests/unit/`: Unit tests for schedule loading and configuration validation.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A Telegram bot token
- A supported LLM provider: Ollama or OpenRouter

## Configuration

The bot loads environment variables from your shell and from a `.env` file if present.

Required variables:

- `TELEGRAM_BOT_TOKEN`
- `LLM_PROVIDER` set to `ollama` or `openrouter`

If `LLM_PROVIDER=ollama`, also set:

- `OLLAMA_API_BASE`
- `OLLAMA_MODEL`

If `LLM_PROVIDER=openrouter`, also set:

- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`

Optional variables:

- `MLFLOW_TRACKING_URL` to enable DSPy tracing with MLflow. Leave it unset or blank to keep observability disabled.

## Run Locally

Install dependencies:

```shell
uv sync
```

Start the bot:

```shell
uv run caltrain-bot
```

This uses the in-memory schedule database by default.

Run in debug mode with the on-disk `data/schedule.db` cache:

```shell
DEBUG=1 uv run caltrain-bot
```

## Run Tests

```shell
uv run pytest
```

## Contributing

Contributions are welcome. The current project focuses on Telegram, but the schedule and question-analysis logic are already separated enough to support additional chat frontends.

People are especially welcome to contribute WhatsApp and WeChat support. If you want to add another messaging integration, try to keep transport-specific code at the edge and reuse the existing schedule lookup and question parsing modules.
