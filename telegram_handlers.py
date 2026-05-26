"""Telegram message handlers — the bot's "endpoints".

Responsibilities are split into three flows, mirroring the specification:

* **Upload** (:func:`handle_document`): an admin sends an HTML file privately;
  it is downloaded, validated, atomically saved, and acknowledged.
* **Notification** (:func:`notify_group`): on a successful save the configured
  group is told the dashboard refreshed, with a button linking to it.
* **Commands** (:func:`handle_start`, :func:`handle_help`, plus a fallback).

Dependencies (``config``, ``file_manager``) are injected by the dispatcher's
workflow data, and ``bot`` is injected by aiogram, so handlers stay testable.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import utils
from config import Config
from file_manager import FileManager
from logger import get_logger

logger = get_logger(__name__)

router = Router(name="fom-dashboard")

# Network retry tuning for outbound group notifications.
_MAX_SEND_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0

_PRIVATE = F.chat.type == "private"


# --------------------------------------------------------------------- /start
@router.message(CommandStart(), _PRIVATE)
async def handle_start(message: Message, config: Config) -> None:
    """Greet the user and explain what the bot does."""
    is_admin = message.from_user is not None and (
        message.from_user.id == config.admin_user_id
    )
    role = (
        "You are recognised as the dashboard administrator."
        if is_admin
        else "Only the configured administrator can upload dashboards."
    )
    await message.answer(
        "👋 Welcome to the FOM Dashboard bot.\n\n"
        "I publish the FOM CEO Dashboard. When the administrator sends me an "
        "HTML file, I validate it, store it as the live dashboard, and notify "
        "the team.\n\n"
        f"{role}\n\n"
        "Commands:\n"
        "  /start — show this message\n"
        "  /help — detailed usage instructions"
    )


# ---------------------------------------------------------------------- /help
@router.message(Command("help"), _PRIVATE)
async def handle_help(message: Message, config: Config) -> None:
    """Provide detailed usage instructions."""
    await message.answer(
        "ℹ️ How to use this bot\n\n"
        "1. As the administrator, send me an HTML file (a document with a "
        ".html extension) in this private chat.\n"
        "2. I validate it (size, UTF-8 encoding, and a <!DOCTYPE html> "
        "declaration).\n"
        "3. If it passes, I save it as the live dashboard, back up the "
        "previous version, and post an update to the team group.\n\n"
        "Notes:\n"
        "  • Only the configured administrator can upload.\n"
        "  • Files must be between 1 KB and the configured maximum size.\n"
        "  • Group messages are informational only."
    )


# ------------------------------------------------------------------- uploads
@router.message(F.document, _PRIVATE)
async def handle_document(
    message: Message,
    bot: Bot,
    config: Config,
    file_manager: FileManager,
) -> None:
    """Handle an HTML document sent privately by the admin.

    Enforces admin-only access, downloads the file to a temporary location,
    validates it, saves it atomically, acknowledges to the admin, and triggers
    the group notification. Temporary files are always cleaned up.
    """
    request_id = utils.generate_request_id()
    started = time.monotonic()
    user = message.from_user

    if user is None or user.id != config.admin_user_id:
        logger.warning(
            "[%s] Rejected upload from unauthorized user_id=%s",
            request_id,
            getattr(user, "id", "unknown"),
        )
        await message.answer("⛔ You are not authorized to upload dashboards.")
        return

    document = message.document
    assert document is not None  # guaranteed by the F.document filter
    filename = document.file_name or "upload.html"
    logger.info(
        "[%s] File received from admin_id=%s: name=%s, size=%s",
        request_id,
        user.id,
        filename,
        utils.human_readable_size(document.file_size or 0),
    )

    with tempfile.TemporaryDirectory(prefix="fom_") as tmp_dir:
        tmp_path = str(Path(tmp_dir) / "upload.bin")

        if not await _download(bot, document, tmp_path, request_id, message):
            return

        is_valid, error = await file_manager.validate_html_file(
            tmp_path, filename, document.mime_type
        )
        if not is_valid:
            logger.info("[%s] Validation failed: %s", request_id, error)
            await message.answer(_format_admin_error(error or "Unknown error."))
            return

        result = await file_manager.save_html_file(tmp_path, user.id)
        if not result["success"]:
            logger.error("[%s] Save failed: %s", request_id, result["message"])
            await message.answer(_format_admin_error(result["message"]))
            return

    await message.answer(_format_admin_success(result["file_size"]))
    await notify_group(bot, config, request_id)

    duration = time.monotonic() - started
    logger.info(
        "[%s] Operation completed: success=True, duration=%.1fs",
        request_id,
        duration,
    )


# -------------------------------------------------------------- notification
async def notify_group(bot: Bot, config: Config, request_id: str) -> None:
    """Post a refresh notification to the configured group.

    Sends a concise message with an inline button linking to the live
    dashboard, retrying transient network/rate-limit failures with exponential
    backoff. A permanently invalid chat id is reported back to the admin.

    Args:
        bot: The aiogram bot instance.
        config: Application configuration (group id and dashboard URL).
        request_id: Correlation id for log lines.
    """
    text = (
        "✅ Analytics data updated\n\n"
        "FOM CEO Dashboard has been refreshed\n"
        f"Updated: {utils.get_human_timestamp()} UTC"
    )
    keyboard = await _build_dashboard_keyboard(bot, config)

    logger.info("[%s] Sending notification to group", request_id)
    sent = await _send_with_retry(
        bot=bot,
        chat_id=config.telegram_group_chat_id,
        text=text,
        reply_markup=keyboard,
        request_id=request_id,
    )
    if sent:
        logger.info("[%s] Group notification sent successfully", request_id)
    else:
        logger.error("[%s] Group notification could not be delivered", request_id)
        await _alert_admin(
            bot,
            config,
            "⚠️ The dashboard was saved, but I could not notify the group. "
            "Please check TELEGRAM_GROUP_CHAT_ID and my membership in the group.",
        )


# --------------------------------------------------------------- catch-all
@router.message(_PRIVATE)
async def handle_other(message: Message) -> None:
    """Redirect any other private message to the help command."""
    await message.answer(
        "I only accept HTML dashboard files from the administrator. "
        "Send /help for usage instructions."
    )


# ----------------------------------------------------------------- internals
async def _download(
    bot: Bot,
    document: object,
    destination: str,
    request_id: str,
    message: Message,
) -> bool:
    """Download a Telegram document, reporting failures to the admin.

    Returns:
        ``True`` if the file was downloaded, ``False`` otherwise.
    """
    try:
        await bot.download(document, destination=destination)
        return True
    except TelegramBadRequest as exc:
        # The most common cause is the 20 MB bot-download ceiling.
        logger.error("[%s] Download rejected by Telegram: %s", request_id, exc)
        await message.answer(
            "❌ Update failed - I could not download the file.\n\n"
            "Telegram limits bot downloads to 20 MB. Please send a smaller "
            "file or host large dashboards differently."
        )
        return False
    except TelegramNetworkError as exc:
        logger.error("[%s] Network error during download: %s", request_id, exc)
        await message.answer(
            "❌ Update failed - a network error occurred while downloading. "
            "Please try again."
        )
        return False


async def _send_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup],
    request_id: str,
) -> bool:
    """Send a message, retrying transient failures with exponential backoff.

    Retries on network errors and rate limits (honouring Telegram's
    ``retry_after``). A :class:`TelegramBadRequest` (e.g. an invalid chat id)
    is treated as permanent and is not retried.

    Returns:
        ``True`` if the message was sent, ``False`` after exhausting retries.
    """
    for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
        try:
            await bot.send_message(
                chat_id=chat_id, text=text, reply_markup=reply_markup
            )
            return True
        except TelegramRetryAfter as exc:
            delay = float(exc.retry_after)
            logger.warning(
                "[%s] Rate limited (attempt %d/%d); retrying in %.1fs",
                request_id,
                attempt,
                _MAX_SEND_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
        except TelegramNetworkError as exc:
            delay = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "[%s] Network error (attempt %d/%d): %s; retrying in %.1fs",
                request_id,
                attempt,
                _MAX_SEND_ATTEMPTS,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
        except TelegramBadRequest as exc:
            # Permanent: bad chat id, bot not in group, etc. Do not retry.
            logger.error(
                "[%s] Permanent send failure (not retrying): %s",
                request_id,
                exc,
            )
            return False

    return False


async def _build_dashboard_keyboard(
    bot: Bot, config: Config
) -> InlineKeyboardMarkup:
    """Build the dashboard inline keyboard for the group notification.

    When ``WEBAPP_SHORT_NAME`` is configured, the primary button is a Mini App
    direct link (``https://t.me/<bot>/<short_name>``) which opens the dashboard
    *inside* Telegram so the injected fullscreen script can run; a secondary
    button still offers the plain browser URL as a fallback. With no short name
    configured, a single browser button is used (the original behaviour).

    Args:
        bot: The aiogram bot instance (used to resolve the bot's username).
        config: Application configuration.

    Returns:
        The assembled :class:`InlineKeyboardMarkup`.
    """
    rows: list[list[InlineKeyboardButton]] = []

    if config.webapp_short_name:
        username = await _bot_username(bot)
        if username:
            app_link = f"https://t.me/{username}/{config.webapp_short_name}"
            rows.append(
                [InlineKeyboardButton(text="📊 View Dashboard", url=app_link)]
            )

    # Browser fallback (also the sole button when no Mini App is configured).
    rows.append(
        [
            InlineKeyboardButton(
                text="🌐 Open in Browser" if rows else "📊 View Dashboard",
                url=config.cloudflare_domain,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _bot_username(bot: Bot) -> Optional[str]:
    """Resolve the bot's @username (cached by aiogram), or ``None`` on error."""
    try:
        me = await bot.me()
        return me.username
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter) as exc:
        logger.error("Could not resolve bot username for Mini App link: %s", exc)
        return None


async def _alert_admin(bot: Bot, config: Config, text: str) -> None:
    """Best-effort notification to the admin; never raises."""
    try:
        await bot.send_message(chat_id=config.admin_user_id, text=text)
    except (TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter) as exc:
        logger.error("Could not alert admin: %s", exc)


def _format_admin_success(file_size: int) -> str:
    """Build the success acknowledgement sent to the admin."""
    return (
        "✅ Dashboard updated successfully!\n\n"
        "File details:\n"
        f"- Size: {utils.human_readable_size(file_size)}\n"
        f"- Updated: {utils.get_human_timestamp()}\n"
        "- Notification sent to group"
    )


def _format_admin_error(reason: str) -> str:
    """Build the failure message sent to the admin."""
    return (
        "❌ Update failed - Invalid HTML file\n\n"
        "Error details:\n"
        f"{reason}\n\n"
        "Please check your HTML file and try again."
    )


def register_handlers(dispatcher) -> None:
    """Attach this module's router to ``dispatcher``.

    Args:
        dispatcher: The aiogram :class:`~aiogram.Dispatcher` to register with.
    """
    dispatcher.include_router(router)
