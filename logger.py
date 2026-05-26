"""Logging infrastructure for the FOM Dashboard Telegram bot.

Provides a single :func:`setup_logging` entry point that configures the root
logger with two handlers:

* a console handler (stdout) for operator-facing INFO+ messages, and
* a daily-rotating file handler (``logs/app.log``) that captures DEBUG+ detail
  and keeps a week of history.

Modules obtain their own named logger via :func:`get_logger`. The structured
format ``YYYY-MM-DD HH:MM:SS | LEVEL | module.function | message`` makes the log
greppable and lets you reconstruct a full upload flow from the file alone.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s.%(funcName)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Guard so repeated calls (e.g. in tests) don't stack duplicate handlers.
_configured = False


def setup_logging(level: str = "INFO", log_dir: str = "./logs") -> None:
    """Configure root logging handlers. Safe to call once at startup.

    Args:
        level: Console log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            The file handler always captures DEBUG and above.
        log_dir: Directory for the rotating log file; created if absent.

    Notes:
        This function is idempotent — calling it more than once is a no-op so
        that handlers are never duplicated.
    """
    global _configured
    if _configured:
        return

    log_path = Path(log_dir).expanduser().resolve()
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    # The root captures everything; individual handlers filter by level.
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(_coerce_level(level))
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    file_handler = TimedRotatingFileHandler(
        filename=str(log_path / "app.log"),
        when="midnight",
        backupCount=7,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # aiogram/aiohttp are chatty at DEBUG; keep them at INFO unless we are
    # explicitly debugging so our own DEBUG lines stay readable.
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name`` (typically ``__name__``).

    Args:
        name: The logger name, conventionally the module's ``__name__``.

    Returns:
        A :class:`logging.Logger` that inherits the root configuration set up
        by :func:`setup_logging`.
    """
    return logging.getLogger(name)


def _coerce_level(level: str) -> int:
    """Translate a level name into its numeric value, defaulting to INFO."""
    resolved = logging.getLevelName(level.upper())
    return resolved if isinstance(resolved, int) else logging.INFO
