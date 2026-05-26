"""Entry point for the FOM Dashboard Telegram bot.

Startup order is deliberate and fail-fast:

1. Load and validate configuration (refuse to start on any problem).
2. Configure logging at the requested level.
3. Connect to Telegram and verify the token via ``get_me``.
4. Register handlers and poll until a shutdown signal arrives.

SIGINT/SIGTERM trigger a graceful shutdown that stops polling and closes the
bot's HTTP session before the process exits.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from config import Config, ConfigError
from file_manager import FileManager
from logger import get_logger, setup_logging
from telegram_handlers import register_handlers

logger = get_logger(__name__)


async def main() -> None:
    """Initialize dependencies and run the bot until shutdown."""
    # --- 1. Configuration (fail fast, before anything else is set up) -------
    try:
        config = Config.from_env()
    except ConfigError as exc:
        # Logging is not configured yet, so write directly to stderr.
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- 2. Logging ---------------------------------------------------------
    setup_logging(level=config.log_level)
    logger.info("Bot initialization started")
    logger.info("Configuration loaded successfully: %s", config.summary())

    # --- 3. Telegram client -------------------------------------------------
    bot = Bot(token=config.telegram_bot_token)
    try:
        me = await bot.get_me()
        logger.info("Telegram bot connected as @%s (id=%s)", me.username, me.id)
    except TelegramUnauthorizedError:
        logger.critical("TELEGRAM_BOT_TOKEN is invalid — aborting startup.")
        await bot.session.close()
        sys.exit(1)
    except TelegramNetworkError as exc:
        logger.critical("Cannot reach Telegram during startup: %s", exc)
        await bot.session.close()
        sys.exit(1)

    # --- 4. Dispatcher, dependency injection, and handlers ------------------
    dispatcher = Dispatcher()
    dispatcher["config"] = config
    dispatcher["file_manager"] = FileManager(config)
    register_handlers(dispatcher)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    # Clear any stale webhook so long-polling works; keep pending updates so a
    # file sent during a brief restart is still processed.
    await bot.delete_webhook(drop_pending_updates=False)

    logger.info("Bot started and polling...")
    polling = asyncio.create_task(
        dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    )

    await stop_event.wait()
    logger.info("Shutdown requested; stopping polling...")

    await dispatcher.stop_polling()
    try:
        await polling
    except asyncio.CancelledError:
        pass
    finally:
        await bot.session.close()
        logger.info("Bot shut down cleanly")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Wire SIGINT/SIGTERM to set ``stop_event`` for graceful shutdown.

    Falls back silently on platforms (e.g. Windows) where
    ``add_signal_handler`` is unavailable; ``KeyboardInterrupt`` still works
    there via the ``__main__`` guard.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            logger.debug("Signal %s not installable on this platform", sig)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as exc:  # noqa: BLE001 - top-level safety net
        logger.critical("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)
