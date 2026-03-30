# Caltrain Bot

Caltrain Bot is a Telegram bot that answers Caltrain schedule questions in plain English. It loads static GTFS data into SQLite and uses DSPy plus an LLM provider to turn questions like "When is the next train from Palo Alto to San Francisco around 8am?" into schedule lookups.

Try the live bot on Telegram: [@CalTrain](https://t.me/CalTrain)

Read the build story:
[Part 1](https://dima.us.kg/learning-by-building-part-1-caltrain-bot-and-the-theory-practice-gap/),
[Part 2](https://dima.us.kg/learning-by-building-part-2-caltrain-bot-sql-with-llms-and-dspy/),
[Part 3](https://dima.us.kg/learning-by-building-part-3-caltrain-bot-local-llms-and-reality/)

## What It Does

- Answers Caltrain schedule questions from natural language.
- Uses bundled Caltrain GTFS data, so schedule lookups come from a static transit feed rather than live scraping.
- Supports both local LLMs through Ollama and hosted models through OpenRouter.
- Runs as a Telegram bot today, with the schedule and question-analysis logic separated enough to support other chat frontends later.

Example questions:

- "When is the next train from Mountain View to San Francisco?"
- "Are there any trains from Palo Alto to San Jose after 6pm?"
- "What trains leave Millbrae around 8 in the morning?"

## Quick Start

### Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A Telegram bot token
- One supported LLM provider: Ollama or OpenRouter

### Install dependencies

```shell
uv sync
```

### Configure environment variables

The bot reads configuration from your shell and from a `.env` file if present.

Required variables:

- `TELEGRAM_BOT_TOKEN`
- `LLM_PROVIDER` set to `ollama` or `openrouter`

If `LLM_PROVIDER=ollama`, also set:

- `OLLAMA_API_BASE`
- `OLLAMA_MODEL`

If `LLM_PROVIDER=openrouter`, also set:

- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`

Example `.env` for OpenRouter:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=openai/gpt-4.1-mini
```

Example `.env` for Ollama:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
LLM_PROVIDER=ollama
OLLAMA_API_BASE=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2
```

### Run the bot

```shell
uv run caltrain-bot
```

## Development

Run the test suite:

```shell
uv run pytest
```

Run linting:

```shell
uv run ruff check .
```

Format the codebase:

```shell
uv run ruff format .
```

Run type checks:

```shell
uv run ty check src
```

## Docker And Raspberry Pi

Build and publish the ARM64 image:

```shell
docker buildx build --platform linux/arm64 \
  -t curiousdima/caltrain-bot:0.1.1 \
  -t curiousdima/caltrain-bot:latest \
  --push .
```

Required environment variables:

- Always: `TELEGRAM_BOT_TOKEN`, `LLM_PROVIDER`
- OpenRouter mode: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
- Ollama mode: `OLLAMA_API_BASE`, `OLLAMA_MODEL`

Example `/opt/caltrain-bot/caltrain-bot.env` for OpenRouter:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=openai/gpt-4.1-mini
```

Deploy on a Raspberry Pi:

```shell
docker run -d \
  --name caltrain-bot \
  --restart unless-stopped \
  --env-file /opt/caltrain-bot/caltrain-bot.env \
  curiousdima/caltrain-bot:latest
```

Example `/opt/caltrain-bot/caltrain-bot.env` for Ollama on the same Raspberry Pi host:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
LLM_PROVIDER=ollama
OLLAMA_API_BASE=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2
```

Deploy with host networking when Ollama runs directly on the Raspberry Pi host:

```shell
docker run -d \
  --name caltrain-bot \
  --restart unless-stopped \
  --network host \
  --env-file /opt/caltrain-bot/caltrain-bot.env \
  curiousdima/caltrain-bot:latest
```

## Project Layout

- `src/caltrain_bot/__init__.py`: CLI entrypoint that builds and runs the Telegram app.
- `src/caltrain_bot/telegram_bot.py`: Telegram bot handlers and user-facing message formatting.
- `src/caltrain_bot/question_analysis.py`: DSPy-based question classification and entity extraction.
- `src/caltrain_bot/schedule.py`: GTFS loading, SQL preprocessing, station lookup, and train queries.
- `src/caltrain_bot/config.py`: Environment validation and repo-relative asset paths.
- `sql/train_stop_timeline.sql`: SQL used to preprocess imported GTFS data into query-friendly tables.
- `data/caltrain-ca-us.zip`: Bundled Caltrain GTFS feed used by the app.
- `tests/unit/`: Unit tests.

## Contributing

Contributions are welcome. The current project focuses on Telegram, but the schedule and question-analysis logic are already separated enough to support additional chat frontends.

People are especially welcome to contribute WhatsApp and WeChat support. If you want to add another messaging integration, try to keep transport-specific code at the edge and reuse the existing schedule lookup and question parsing modules.
