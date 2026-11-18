FROM python:3.11-slim-trixie AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY data/caltrain-ca-us.zip ./data/caltrain-ca-us.zip
COPY data/schedule_question_program.json ./data/schedule_question_program.json
COPY sql ./sql

RUN uv sync --locked --no-dev

FROM python:3.11-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Los_Angeles \
    USAGE_DB_PATH=/app/usage/usage.db \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY data/caltrain-ca-us.zip ./data/caltrain-ca-us.zip
COPY data/schedule_question_program.json ./data/schedule_question_program.json
COPY sql ./sql

# /app/usage holds the web usage statistics; mount a volume there to keep them.
RUN mkdir -p /app/usage && chown -R app:app /app /home/app

USER app

CMD ["caltrain-bot"]
