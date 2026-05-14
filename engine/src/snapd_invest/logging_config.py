"""structlog configuration.

Console rendering for local development, JSON for production. Bound context
variables (e.g. `correlation_id`) propagate via `structlog.contextvars`.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from snapd_invest.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib logger to emit consistent output."""
    log_level = getattr(logging, settings.log_level)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format == "json":
        # JSONRenderer needs format_exc_info to serialize exceptions; the
        # ConsoleRenderer does pretty-exception rendering itself and rejects
        # this processor (emits UserWarning).
        shared_processors.append(structlog.processors.format_exc_info)
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a bound structlog logger."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
