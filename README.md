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
- Uses hosted language models through OpenRouter.
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
- An OpenRouter API key

### Install dependencies

```shell
uv sync
```

### Configure environment variables

The bot reads configuration from your shell and from a `.env` file if present.

Required variables:

- `TELEGRAM_BOT_TOKEN`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`

Optional variables:

- `OPENROUTER_FALLBACK_MODEL`: model OpenRouter switches to when
  `OPENROUTER_MODEL` fails or is no longer served.
- `OPENROUTER_REASONING_EFFORT`: OpenRouter reasoning effort (`none`, `low`,
  ...). Leave it unset for the model's default, which is the most accurate.

Example `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=anthropic/claude-haiku-5.5
OPENROUTER_FALLBACK_MODEL=openai/gpt-6-luna
```

The prompt is tuned for `anthropic/claude-haiku-5.5`: each question takes one
LLM call of about 1.2 seconds and costs roughly $0.0005. On the 179 labeled
questions that setup answers 178-179 correctly. Setting
`OPENROUTER_REASONING_EFFORT=none` cuts the call to about 0.8 seconds at the
price of a few more misses (175 of 179).

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

### Re-optimize the DSPy program

Question analysis is one DSPy predictor whose instructions were optimized with
GEPA in `evals/Evals & Optimizer.ipynb`, using the labeled questions in
`data/train.jsonl` and `data/validation.jsonl`. The notebook saves the result to
`data/schedule_question_program.json`, which the bot loads at startup; without
that file the bot uses the built-in instructions in `question_analysis.py`.

To redo the optimization (about $1.25 of OpenRouter usage), run:

```shell
uv run --env-file .env jupyter nbconvert --to notebook --execute --inplace \
  "evals/Evals & Optimizer.ipynb" --ExecutePreprocessor.timeout=-1
```

## Docker And Raspberry Pi

CI (`.github/workflows/docker-publish.yml`) builds the ARM64 image after tests
pass on every push to `main` and publishes it to Docker Hub as
`curiousdima/caltrain-bot:latest` plus a `sha-<short-commit>` tag.

To build and publish it by hand instead:

```shell
docker buildx build --platform linux/arm64 \
  -t curiousdima/caltrain-bot:latest \
  --push .
```

Required environment variables:

- `TELEGRAM_BOT_TOKEN`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`

Example `/opt/caltrain-bot/caltrain-bot.env`:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=anthropic/claude-haiku-5.5
OPENROUTER_FALLBACK_MODEL=openai/gpt-6-luna
```

The schedule is expanded into an in-memory SQLite database of about 1.6 GB, so
the container needs roughly 2.5 GB of free memory while it starts.

Deploy on a Raspberry Pi:

```shell
docker run -d \
  --name caltrain-bot \
  --restart unless-stopped \
  --env-file /opt/caltrain-bot/caltrain-bot.env \
  curiousdima/caltrain-bot:latest
```

## Project Layout

- `src/caltrain_bot/__init__.py`: CLI entrypoint that builds and runs the Telegram app.
- `src/caltrain_bot/telegram_bot.py`: Telegram bot handlers and user-facing message formatting.
- `src/caltrain_bot/question_analysis.py`: DSPy program that classifies a question and extracts stations and time in a single LLM call.
- `src/caltrain_bot/schedule.py`: GTFS loading, SQL preprocessing, station lookup, and train queries.
- `src/caltrain_bot/config.py`: Environment validation and repo-relative asset paths.
- `sql/train_stop_timeline.sql`: SQL used to preprocess imported GTFS data into query-friendly tables.
- `data/caltrain-ca-us.zip`: Bundled Caltrain GTFS feed used by the app.
- `data/schedule_question_program.json`: GEPA-optimized prompt loaded at startup.
- `evals/Evals & Optimizer.ipynb`: evaluation and GEPA optimization of the question analysis.
- `tests/unit/`: Unit tests.

## Contributing

Contributions are welcome. The current project focuses on Telegram, but the schedule and question-analysis logic are already separated enough to support additional chat frontends.

People are especially welcome to contribute WhatsApp and WeChat support. If you want to add another messaging integration, try to keep transport-specific code at the edge and reuse the existing schedule lookup and question parsing modules.
