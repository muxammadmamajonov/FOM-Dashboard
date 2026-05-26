"""Configuration management for the FOM Dashboard Telegram bot.

Loads all settings from environment variables (a ``.env`` file is supported via
``python-dotenv``) following the 12-factor app methodology. Configuration is
validated eagerly at construction time so the application fails fast with a
descriptive error instead of crashing later with a cryptic stack trace.

Storage backend is **Cloudflare R2** (S3-compatible). The Worker under
``worker/`` reads the same bucket and serves the dashboard at a permanent
``*.workers.dev`` URL.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
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
        cloudflare_domain: Public dashboard URL (the Worker's ``*.workers.dev``
            address or a custom domain). Used by the "Open in Browser" button.
        max_file_size: Maximum accepted upload size, in bytes.
        log_level: Root logging level name (e.g. ``"INFO"``).
        webapp_fullscreen_enabled: Inject the Telegram Mini App fullscreen
            bootstrap into each saved dashboard.
        webapp_short_name: BotFather Mini App short name. When set, the group
            notification button opens the dashboard as a fullscreen Mini App
            (``https://t.me/<bot>/<short_name>``); when empty it opens the
            plain browser URL.
        r2_account_id: Cloudflare account id (used to build the R2 endpoint).
        r2_access_key_id: R2 API token Access Key Id.
        r2_secret_access_key: R2 API token Secret Access Key.
        r2_bucket_name: R2 bucket that holds ``index.html`` and ``backups/``.
        backup_enabled: Whether to keep timestamped backups in R2 on overwrite.
    """

    telegram_bot_token: str
    admin_user_id: int
    telegram_group_chat_id: int
    cloudflare_domain: str
    max_file_size: int
    log_level: str
    webapp_fullscreen_enabled: bool
    webapp_short_name: str
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str
    backup_enabled: bool

    # ----------------------------------------------------------------- helpers
    @property
    def r2_endpoint_url(self) -> str:
        """Account-scoped R2 S3 endpoint."""
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    def summary(self) -> str:
        """Return a human-readable, secret-free summary for startup logging.

        The bot token and R2 secret are intentionally redacted so they never
        reach the logs.
        """
        return (
            f"admin_user_id={self.admin_user_id}, "
            f"group_chat_id={self.telegram_group_chat_id}, "
            f"dashboard_url={self.cloudflare_domain}, "
            f"r2_bucket={self.r2_bucket_name}, "
            f"r2_account={self.r2_account_id}, "
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

        # --- Telegram --------------------------------------------------------
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
                "(your Worker's *.workers.dev address or custom domain)."
            )

        # --- App behaviour --------------------------------------------------
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

        backup_enabled = _parse_bool(os.getenv("BACKUP_ENABLED", "true"))

        # --- R2 storage (required) ------------------------------------------
        r2_account_id = _read_required_str("R2_ACCOUNT_ID", errors)
        r2_access_key_id = _read_required_str("R2_ACCESS_KEY_ID", errors)
        r2_secret_access_key = _read_required_str("R2_SECRET_ACCESS_KEY", errors)
        r2_bucket_name = _read_required_str("R2_BUCKET_NAME", errors)
        if r2_bucket_name and not _is_valid_bucket_name(r2_bucket_name):
            errors.append(
                "R2_BUCKET_NAME must be 3-63 chars, lowercase letters, digits, "
                "or hyphens."
            )

        if errors:
            joined = "\n  - ".join(errors)
            raise ConfigError(f"Invalid configuration:\n  - {joined}")

        return cls(
            telegram_bot_token=token,  # type: ignore[arg-type]
            admin_user_id=admin_user_id,  # type: ignore[arg-type]
            telegram_group_chat_id=group_chat_id,  # type: ignore[arg-type]
            cloudflare_domain=domain,  # type: ignore[arg-type]
            max_file_size=max_file_size,  # type: ignore[arg-type]
            log_level=log_level,
            webapp_fullscreen_enabled=webapp_fullscreen,
            webapp_short_name=webapp_short_name,
            r2_account_id=r2_account_id,  # type: ignore[arg-type]
            r2_access_key_id=r2_access_key_id,  # type: ignore[arg-type]
            r2_secret_access_key=r2_secret_access_key,  # type: ignore[arg-type]
            r2_bucket_name=r2_bucket_name,  # type: ignore[arg-type]
            backup_enabled=backup_enabled,
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


def _is_valid_bucket_name(name: str) -> bool:
    """Return ``True`` if ``name`` is a syntactically valid R2 bucket name."""
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", name))
