# Caltrain Bot

Caltrain Bot is a Python chatbot that answers Caltrain schedule questions from natural language. It currently ships as a Telegram bot, loads Caltrain GTFS data into SQLite, and uses DSPy plus an LLM provider to understand questions such as "When is the next train from Palo Alto to San Francisco around 8am?"

## What It Does

- Accepts free-form questions about Caltrain trips, stations, and departure times.
- Classifies whether a message is actually about the Caltrain schedule before querying data.
- Extracts departure station, arrival station, and approximate departure time from the user message.
- Queries a preprocessed GTFS-backed schedule database and returns matching trains with departure time, arrival time, train number, and service pattern.
- Runs with an in-memory database by default for normal use, with an optional on-disk cache for local debugging.

## Project Layout

- `src/caltrain_bot/telegram_bot.py`: Telegram bot handlers and message formatting.
- `src/caltrain_bot/question_analysis.py`: DSPy-based question classification and entity extraction.
- `src/caltrain_bot/schedule.py`: GTFS loading, preprocessing, and schedule queries.
- `src/caltrain_bot/config.py`: Environment-based configuration for Telegram and the LLM provider.
- `sql/train_stop_timeline.sql`: SQL used to preprocess imported GTFS data into query-friendly tables.
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
