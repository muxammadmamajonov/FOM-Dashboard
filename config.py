"""Configuration management for the FOM Dashboard Telegram bot.

Loads all settings from environment variables (a ``.env`` file is supported via
``python-dotenv``) following the 12-factor app methodology. Configuration is
validated eagerly at construction time so the application fails fast with a
descriptive error instead of crashing later with a cryptic stack trace.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# 50 MiB. Telegram's own bot-download limit is 20 MB, but we keep this
# configurable and validated independently of that transport constraint.
DEFAULT_MAX_FILE_SIZE = 52_428_800


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers keep
    working while still allowing callers to catch this specific failure mode.
    """


@dataclass(frozen=True)
class Config:
    """Validated, immutable application configuration.

    Construct via :meth:`from_env` rather than directly so that environment
    parsing and validation happen in one place.

    Attributes:
        telegram_bot_token: Secret token issued by @BotFather.
        admin_user_id: Telegram user id allowed to upload dashboards.
        telegram_group_chat_id: Chat id of the group to notify (negative).
        cloudflare_domain: Public URL where the dashboard is served.
        upload_folder_path: Absolute path to the upload directory.
        backup_enabled: Whether to keep timestamped backups on overwrite.
        max_file_size: Maximum accepted upload size, in bytes.
        log_level: Root logging level name (e.g. ``"INFO"``).
        webapp_fullscreen_enabled: Inject the Telegram Mini App fullscreen
            bootstrap into each saved dashboard.
        webapp_short_name: BotFather Mini App short name. When set, the group
            notification button opens the dashboard as a fullscreen Mini App
            (``https://t.me/<bot>/<short_name>``); when empty it opens the
            plain browser URL.
    """

    telegram_bot_token: str
    admin_user_id: int
    telegram_group_chat_id: int
    cloudflare_domain: str
    upload_folder_path: Path
    backup_enabled: bool
    max_file_size: int
    log_level: str
    webapp_fullscreen_enabled: bool
    webapp_short_name: str

    # ----------------------------------------------------------------- helpers
    @property
    def backups_folder_path(self) -> Path:
        """Directory holding rotated backups of ``index.html``."""
        return self.upload_folder_path / "backups"

    @property
    def index_file_path(self) -> Path:
        """Canonical path of the currently served dashboard file."""
        return self.upload_folder_path / "index.html"

    def summary(self) -> str:
        """Return a human-readable, secret-free summary for startup logging.

        The bot token is intentionally redacted so it never reaches the logs.
        """
        return (
            f"admin_user_id={self.admin_user_id}, "
            f"group_chat_id={self.telegram_group_chat_id}, "
            f"dashboard_url={self.cloudflare_domain}, "
            f"upload_folder={self.upload_folder_path}, "
            f"backup_enabled={self.backup_enabled}, "
            f"max_file_size={self.max_file_size} bytes, "
            f"log_level={self.log_level}, "
            f"webapp_fullscreen={self.webapp_fullscreen_enabled}, "
            f"webapp_short_name={self.webapp_short_name or '(none)'}"
        )

    # --------------------------------------------------------------- factory
    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "Config":
        """Build and validate a :class:`Config` from environment variables.

        Args:
            env_file: Optional explicit path to a ``.env`` file. When omitted,
                ``python-dotenv`` searches for a ``.env`` in the working tree.

        Returns:
            A fully validated, immutable :class:`Config` instance.

        Raises:
            ConfigError: If any required variable is missing or any value is
                malformed. The message aggregates every problem found so the
                operator can fix them all at once.
        """
        load_dotenv(dotenv_path=env_file, override=False)

        errors: list[str] = []

        token = _read_required_str("TELEGRAM_BOT_TOKEN", errors)
        if token and ":" not in token:
            errors.append(
                "TELEGRAM_BOT_TOKEN looks malformed (expected '<id>:<hash>')."
            )

        admin_user_id = _read_required_int("ADMIN_USER_ID", errors)
        if admin_user_id is not None and admin_user_id <= 0:
            errors.append("ADMIN_USER_ID must be a positive integer.")

        group_chat_id = _read_required_int("TELEGRAM_GROUP_CHAT_ID", errors)
        if group_chat_id is not None and group_chat_id >= 0:
            errors.append(
                "TELEGRAM_GROUP_CHAT_ID must be negative "
                "(group/supergroup chat ids are negative)."
            )

        domain = _read_required_str("CLOUDFLARE_DOMAIN", errors)
        if domain and not _is_valid_http_url(domain):
            errors.append(
                "CLOUDFLARE_DOMAIN must be a valid http(s) URL "
                "(e.g. https://example.cloudflared.app/)."
            )

        upload_folder = os.getenv("UPLOAD_FOLDER_PATH", "./uploads").strip()
        upload_path = Path(upload_folder).expanduser().resolve()

        backup_enabled = _parse_bool(os.getenv("BACKUP_ENABLED", "false"))

        max_file_size = _read_optional_int(
            "MAX_FILE_SIZE", DEFAULT_MAX_FILE_SIZE, errors
        )
        if max_file_size is not None and max_file_size <= 0:
            errors.append("MAX_FILE_SIZE must be a positive integer (bytes).")

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in VALID_LOG_LEVELS:
            errors.append(
                f"LOG_LEVEL '{log_level}' is invalid; "
                f"choose one of {sorted(VALID_LOG_LEVELS)}."
            )

        webapp_fullscreen = _parse_bool(
            os.getenv("WEBAPP_FULLSCREEN_ENABLED", "true")
        )
        webapp_short_name = os.getenv("WEBAPP_SHORT_NAME", "").strip()
        if webapp_short_name and not _is_valid_app_short_name(webapp_short_name):
            errors.append(
                "WEBAPP_SHORT_NAME may only contain letters, digits, and "
                "underscores (3-30 chars) to match a BotFather Mini App name."
            )

        # Verify the upload directory is usable (exists/creatable + writable).
        _validate_writable_dir(upload_path, errors)

        if errors:
            joined = "\n  - ".join(errors)
            raise ConfigError(f"Invalid configuration:\n  - {joined}")

        return cls(
            telegram_bot_token=token,  # type: ignore[arg-type]
            admin_user_id=admin_user_id,  # type: ignore[arg-type]
            telegram_group_chat_id=group_chat_id,  # type: ignore[arg-type]
            cloudflare_domain=domain,  # type: ignore[arg-type]
            upload_folder_path=upload_path,
            backup_enabled=backup_enabled,
            max_file_size=max_file_size,  # type: ignore[arg-type]
            log_level=log_level,
            webapp_fullscreen_enabled=webapp_fullscreen,
            webapp_short_name=webapp_short_name,
        )


# --------------------------------------------------------------------- parsing
def _read_required_str(name: str, errors: list[str]) -> Optional[str]:
    """Read a required string env var, recording an error if absent."""
    value = os.getenv(name)
    if value is None or not value.strip():
        errors.append(f"{name} is required but not set.")
        return None
    return value.strip()


def _read_required_int(name: str, errors: list[str]) -> Optional[int]:
    """Read a required integer env var, recording an error if absent/invalid."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        errors.append(f"{name} is required but not set.")
        return None
    try:
        return int(raw.strip())
    except ValueError:
        errors.append(f"{name} must be an integer (got '{raw}').")
        return None


def _read_optional_int(name: str, default: int, errors: list[str]) -> Optional[int]:
    """Read an optional integer env var, falling back to ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        errors.append(f"{name} must be an integer (got '{raw}').")
        return None


def _parse_bool(raw: str) -> bool:
    """Interpret common truthy strings as ``True``."""
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_valid_http_url(url: str) -> bool:
    """Return ``True`` if ``url`` is a syntactically valid http(s) URL."""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_valid_app_short_name(name: str) -> bool:
    """Return ``True`` if ``name`` is a valid BotFather Mini App short name."""
    return bool(re.fullmatch(r"[A-Za-z0-9_]{3,30}", name))


def _validate_writable_dir(path: Path, errors: list[str]) -> None:
    """Ensure ``path`` exists (creating it if needed) and is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"Upload folder '{path}' cannot be created: {exc}")
        return
    if not os.access(path, os.W_OK):
        errors.append(f"Upload folder '{path}' is not writable.")
