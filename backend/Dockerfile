FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.10 /uv /usr/local/bin/uv
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY tests ./tests

ENV PYTHONPATH=/app/src
RUN uv sync --group dev

EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && uv run ran-ai-api"]
