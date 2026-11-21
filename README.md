# Caltrain Bot

Caltrain Bot answers Caltrain schedule questions in plain English, as a Telegram bot and as a mobile web page. It loads static GTFS data into SQLite and uses DSPy plus an LLM provider to turn questions like "When is the next train from Palo Alto to San Francisco around 8am?" into schedule lookups.

Try the live bot on Telegram: [@CalTrain](https://t.me/CalTrain)

Read the build story:
[Part 1](https://dimatimofeev.com/learning-by-building-part-1-caltrain-bot-and-the-theory-practice-gap/),
[Part 2](https://dimatimofeev.com/learning-by-building-part-2-caltrain-bot-sql-with-llms-and-dspy/),
[Part 3](https://dimatimofeev.com/learning-by-building-part-3-caltrain-bot-local-llms-and-reality/)

## What It Does

- Answers Caltrain schedule questions from natural language.
- Uses bundled Caltrain GTFS data, so schedule lookups come from a static transit feed rather than live scraping.
- Uses hosted language models through OpenRouter.
- Runs as a Telegram bot, as a mobile-first web page, or both from one process. The schedule and question-analysis logic is shared, so other chat frontends can reuse it.

A question with a time ("around 8am", "after 6pm") gets the trains leaving from
20 minutes before to 30 minutes after it. A question without one ("next train")
gets the next three departures.

Example questions:

- "When is the next train from Mountain View to San Francisco?"
- "Are there any trains from Palo Alto to San Jose after 6pm?"
- "What trains leave Millbrae around 8 in the morning?"

## Quick Start

### Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A Telegram bot token, unless you only run the web page
- An OpenRouter API key

### Install dependencies

```shell
uv sync
```

### Configure environment variables

The bot reads configuration from your shell and from a `.env` file if present.

Required variables:

- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- At least one frontend:
  - `TELEGRAM_BOT_TOKEN`: runs the Telegram bot.
  - `WEB_PORT`: serves the web page on this port, for example `8080`.

Optional variables:

- `OPENROUTER_FALLBACK_MODEL`: model OpenRouter switches to when
  `OPENROUTER_MODEL` fails or is no longer served.
- `OPENROUTER_REASONING_EFFORT`: OpenRouter reasoning effort (`none`, `low`,
  ...). Leave it unset for the model's default, which is the most accurate.
- `USAGE_DB_PATH`: where the web page's usage statistics are stored. Defaults
  to `data/usage.db`.

Example `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=anthropic/claude-haiku-5.5
OPENROUTER_FALLBACK_MODEL=openai/gpt-6-luna
WEB_PORT=8080
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

With `WEB_PORT=8080` set, open <http://localhost:8080>.

## Web Interface

The web page is built for a rider in a hurry on a phone: one text box, one
button, and the matching trains as large cards with a live "In 7 min"
countdown. It remembers the last few questions on the device so a regular trip
is one tap.

- `GET /` returns the whole page (HTML, CSS, and JavaScript) in a single
  response, so it loads in one round trip on a weak signal.
- `POST /api/ask` takes `{"question": "..."}` and returns JSON with the
  understood route and the trains.
- Each question is one paid LLM call, so the endpoint limits how many it
  answers; see [Security](#security).
- The server speaks plain HTTP. Put it behind a reverse proxy or tunnel that
  terminates HTTPS before exposing it to the internet.

### Security

The web server assumes that only a trusted reverse proxy can connect to its
port, for example a Cloudflare Tunnel: run `cloudflared` on a private Docker
network with the bot and do not publish the port. The proxy must terminate
HTTPS and pass the visitor's `Host` header through unchanged, which a
Cloudflare Tunnel does unless `httpHostHeader` is set. Anyone who can connect
to the port directly can forge the headers that identify a visitor, and is
then held back only by the total limits.

- **Question limits.** 12 questions per minute per client, 120 per minute and
  3000 per day in total, and 8 questions in progress at once. The day is the
  Caltrain calendar day; the count lives in memory, so a restart resets it.
  Over a limit the endpoint answers `429` (or `503` when all 8 slots are busy)
  without calling the LLM. An answer that takes more than 30 seconds is given
  up. Questions are capped at 300 characters and request bodies at 8 KB.
- **Who a client is.** The address in `CF-Connecting-IP`, which Cloudflare sets
  itself; without it, the last `X-Forwarded-For` entry; without that, the
  socket peer. IPv6 addresses are counted per /64 network, because one home or
  phone has a whole /64 to pick addresses from.
- **Requests from other websites.** A `POST` under `/api/` must have
  `Content-Type: application/json`, which a browser cannot send across sites
  without a CORS preflight that the server never approves (`415` otherwise).
  It is refused with `403` when `Sec-Fetch-Site` is anything but `same-origin`
  or `none`, or when the host in `Origin` differs from the `Host` header.
  Clients that send neither header, such as `curl`, are allowed.
- **Response headers.** Every response, error pages included, carries
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin`,
  `Cross-Origin-Resource-Policy: same-origin`, a `Permissions-Policy` that
  switches off every device feature, and
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`. The page
  has a hash-based `Content-Security-Policy`; everything else gets
  `default-src 'none'; frame-ancestors 'none'` and `Cache-Control: no-store`.
  The `Server` header is removed.
- **Errors.** An unexpected failure is logged and answered with a generic
  `500` JSON error; no exception text reaches the client.

These protections cover the web page only. The Telegram bot has no rate limit
of its own.

### Usage statistics

The web server records one row per answered question in a small SQLite file:
the time, an anonymous visitor ID, the outcome, and how long the answer took.
It stores no question text and no IP address. The visitor ID is a random value
the page keeps in the browser's local storage and sends only with a question;
the page sets no cookies. Telegram usage is not counted.

Print the report:

```shell
uv run caltrain-bot-usage --days 14
```

```text
Day         Users  New  Questions  Trains  No trains  Off-topic  Failed  Avg ms
2026-11-18     12    4         31      26          2          3       0    1180

All time: 57 users, 240 questions
Active users: 23 in the last 7 days, 41 in the last 30 days
```

"Users" are people who asked at least one question that day; opening the page
without asking is not counted. The file is `data/usage.db` unless
`USAGE_DB_PATH` points elsewhere.

The page is styled with [Tailwind CSS](https://tailwindcss.com). The generated
stylesheet `src/caltrain_bot/static/app.css` is checked in, so running the bot
needs no Node tooling. After changing classes in
`src/caltrain_bot/static/index.html`, rebuild it (a unit test fails if you
forget):

```shell
npm install
npm run build:css
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

- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `TELEGRAM_BOT_TOKEN`, `WEB_PORT`, or both

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

To serve the web page as well, add `WEB_PORT=8080` to the env file and publish
the port with `-p 8080:8080`. The Telegram bot and the web page then share the
one in-memory schedule. To put the page on the public internet, do not publish
the port; let a tunnel or reverse proxy on the same Docker network reach it
instead (see [Security](#security)).

The image keeps usage statistics in `/app/usage`. Mount a volume there with
`-v caltrain-bot-usage:/app/usage`, or they are lost when the container is
replaced. Read them with:

```shell
docker exec caltrain-bot caltrain-bot-usage
```

## Project Layout

- `src/caltrain_bot/__init__.py`: CLI entrypoint that starts the Telegram bot, the web server, or both.
- `src/caltrain_bot/answers.py`: Frontend-independent flow that turns a question into trains or a user-facing message.
- `src/caltrain_bot/telegram_bot.py`: Telegram bot handlers and user-facing message formatting.
- `src/caltrain_bot/web.py`: Web server: the page, the `/api/ask` JSON endpoint, rate limiting, and the security middlewares.
- `src/caltrain_bot/usage.py`: Usage statistics for the web page and the `caltrain-bot-usage` report command.
- `src/caltrain_bot/static/`: The web page (`index.html`), its Tailwind source (`tailwind.css`), and the generated `app.css`.
- `src/caltrain_bot/question_analysis.py`: DSPy program that classifies a question and extracts stations and time in a single LLM call.
- `src/caltrain_bot/schedule.py`: GTFS loading, SQL preprocessing, station lookup, and train queries.
- `src/caltrain_bot/config.py`: Environment validation and repo-relative asset paths.
- `sql/train_stop_timeline.sql`: SQL used to preprocess imported GTFS data into query-friendly tables.
- `data/caltrain-ca-us.zip`: Bundled Caltrain GTFS feed used by the app.
- `data/schedule_question_program.json`: GEPA-optimized prompt loaded at startup.
- `evals/Evals & Optimizer.ipynb`: evaluation and GEPA optimization of the question analysis.
- `tests/unit/`: Unit tests.

## Contributing

Contributions are welcome. The project ships Telegram and web frontends, and the schedule and question-analysis logic is separated enough to support additional chat frontends.

People are especially welcome to contribute WhatsApp and WeChat support. If you want to add another messaging integration, try to keep transport-specific code at the edge and reuse `answers.py`, as `telegram_bot.py` and `web.py` do.
