"""Logging: one readable line per record on stdout, where `docker logs` finds it."""

from __future__ import annotations

import logging.config


def configure_logging(level: str = "INFO") -> None:
    """Route the `app.*` loggers to stdout. Uvicorn configures its own loggers."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s"},
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "app": {"handlers": ["stdout"], "level": level.upper(), "propagate": False},
            },
        }
    )
