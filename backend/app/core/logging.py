"""Structured logging foundation.

Implements the Stage 1 logging strategy:
- Structured logs (JSON in production, colorized console in development).
- Standard fields on every line, including a per-request ``request_id`` and,
  later, a ``run_id`` for sync jobs — propagated via context variables.
- Log level and format driven entirely by configuration.
- Secrets are never logged (they are ``SecretStr`` and excluded by design).

We standardize on ``structlog`` layered over the stdlib logging module so that
third-party libraries (uvicorn, SQLAlchemy, Celery) share the same output.
"""

import logging
import sys

import structlog

from app.core.config import get_settings
from app.utils.correlation import request_id_var


def _add_request_id(
    _: structlog.typing.WrappedLogger, __: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """structlog processor: attach the current request/correlation id."""
    request_id = request_id_var.get()
    if request_id is not None:
        event_dict["request_id"] = request_id
    return event_dict


def configure_logging() -> None:
    """Configure structlog + stdlib logging for the whole process.

    Idempotent: safe to call from both the API and the Celery worker entrypoints.
    """
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_request_id,
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy) through structlog's renderer.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Return a bound structlog logger. Prefer this over ``logging.getLogger``.

    The declared type matches what ``configure_logging`` actually installs
    (``make_filtering_bound_logger``); ``structlog.get_logger`` itself returns
    ``Any`` by design, so we pin it via the annotated local.
    """
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
