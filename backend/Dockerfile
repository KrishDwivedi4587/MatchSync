# syntax=docker/dockerfile:1
# Multi-stage build for the MatchSync backend.
# - "base"    installs runtime deps into a venv.
# - "runtime" is a slim, non-root production image.
# The same image runs the API, the Celery worker, and Beat — the command is
# supplied by docker-compose, so one build serves all three processes.

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: build tools for any wheels that need compiling, plus libpq for
# psycopg. Removed from the final image via multi-stage copy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[dev,test]"

# ---------------------------------------------------------------------------
FROM base AS runtime

# Create an unprivileged user; never run the app as root (Stage 1 security).
RUN useradd --create-home --uid 1000 appuser

COPY --chown=appuser:appuser . /app

USER appuser

EXPOSE 8000

# Default command runs the API. docker-compose overrides this for worker/beat.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
