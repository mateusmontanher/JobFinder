"""Logging configuration for command-line execution."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_file: Path = Path("logs/app.log")) -> None:
    """Configure UTF-8 file logging once per process."""
    formatter = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    except OSError:
        handlers = [logging.NullHandler()]
    logging.basicConfig(level=logging.INFO, format=formatter, handlers=handlers, force=True)
