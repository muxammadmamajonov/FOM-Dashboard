"""Stateless helper functions shared across the bot.

These utilities deliberately have no dependency on configuration or the
Telegram client so they remain trivially testable in isolation.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Minimum plausible size for a real HTML dashboard. Anything smaller is almost
# certainly truncated or empty rather than a genuine document.
MIN_HTML_SIZE = 1024  # 1 KiB

_DOCTYPE_MARKER = "<!doctype html"

_VALIDATION_MESSAGES = {
    "not_found": "The uploaded file could not be found on disk.",
    "empty": "The file is empty.",
    "too_small": (
        "The file is smaller than the 1 KB minimum — it looks truncated or "
        "empty."
    ),
    "too_large": "The file exceeds the maximum allowed size.",
    "bad_extension": "Only files with a .html extension are accepted.",
    "bad_mime": "The file is not an HTML document (unexpected MIME type).",
    "bad_encoding": "The file is not valid UTF-8 text.",
    "no_doctype": "The '<!DOCTYPE html>' declaration was not found.",
    "binary_content": "The file contains binary data and is not valid HTML.",
    "unknown": "The file failed validation for an unspecified reason.",
}


def is_valid_html_file(file_path: str, max_size: int) -> bool:
    """Cheap boolean check that a file looks like a valid HTML document.

    This is a convenience wrapper for callers that only need a yes/no answer;
    use the file manager's validator when you need the specific reason.

    Args:
        file_path: Path to the candidate file.
        max_size: Maximum permitted size in bytes.

    Returns:
        ``True`` if the file exists, is UTF-8, within size bounds, and contains
        a ``<!DOCTYPE html>`` declaration; ``False`` otherwise.
    """
    path = Path(file_path)
    if not path.is_file():
        return False

    size = path.stat().st_size
    if size < MIN_HTML_SIZE or size > max_size:
        return False

    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False

    return _DOCTYPE_MARKER in text[:2048].lower()


def calculate_file_hash(file_path: str) -> str:
    """Compute the SHA-256 hex digest of a file's contents.

    Reads in fixed-size chunks so arbitrarily large files use bounded memory.

    Args:
        file_path: Path to the file to hash.

    Returns:
        The lowercase hex digest string.

    Raises:
        OSError: If the file cannot be read.
    """
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_iso_timestamp() -> str:
    """Return the current UTC time as an ISO-8601 string (``...Z``)."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def get_human_timestamp() -> str:
    """Return the current UTC time as ``YYYY-MM-DD HH:MM:SS`` for messages."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_backup_timestamp() -> str:
    """Return a filename-safe UTC timestamp: ``YYYYMMDD_HHMMSS``."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def ensure_directory_exists(path: str) -> bool:
    """Create ``path`` (and parents) if missing.

    Args:
        path: Directory path to ensure.

    Returns:
        ``True`` on success, ``False`` if the directory could not be created.
    """
    try:
        Path(path).expanduser().mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def get_safe_file_path(directory: str, filename: str) -> str:
    """Join ``filename`` onto ``directory``, blocking directory traversal.

    Strips any path components from ``filename`` and verifies the resolved
    result stays inside ``directory`` so a crafted name like
    ``../../etc/passwd`` cannot escape the upload tree.

    Args:
        directory: The trusted base directory.
        filename: An untrusted filename (its directory parts are discarded).

    Returns:
        The resolved, absolute, safe path as a string.

    Raises:
        ValueError: If the resulting path would fall outside ``directory``.
    """
    base = Path(directory).expanduser().resolve()
    # Discard everything but the final name component.
    leaf = Path(filename).name
    if not leaf:
        raise ValueError("Filename resolves to an empty name.")

    candidate = (base / leaf).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValueError(
            f"Refusing path outside base directory: {filename!r}"
        )
    return str(candidate)


def generate_request_id() -> str:
    """Return a short unique id used to correlate log lines for one upload."""
    return uuid.uuid4().hex[:8]


def human_readable_size(num_bytes: int) -> str:
    """Format a byte count as a compact human string (e.g. ``45.0 KB``)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def get_html_validation_error_message(error_type: str) -> str:
    """Map a validation error key to a user-friendly explanation.

    Args:
        error_type: One of the keys in the internal validation message table.

    Returns:
        A human-readable message; falls back to a generic message for unknown
        keys so callers never surface a raw error code to the admin.
    """
    return _VALIDATION_MESSAGES.get(error_type, _VALIDATION_MESSAGES["unknown"])


def file_extension(filename: str) -> str:
    """Return the lowercase extension of ``filename`` including the dot."""
    return os.path.splitext(filename)[1].lower()


# --------------------------------------------------------- Telegram Mini App
# Marker so injection is idempotent across repeated uploads of the same file.
_INJECTION_MARKER = "fom-dashboard-bot:telegram-fullscreen"
_TELEGRAM_WEBAPP_SDK_URL = "telegram.org/js/telegram-web-app.js"

# Loads the official Telegram Web App SDK, then (only when actually running
# inside Telegram as a Mini App) marks the app ready, expands it, and requests
# fullscreen. Every call is wrapped in try/catch so the page renders normally
# in a plain browser, where window.Telegram is absent.
_TELEGRAM_FULLSCREEN_SNIPPET = """
<!-- {marker} : added automatically so the dashboard opens fullscreen in Telegram -->
<script src="https://{sdk}"></script>
<script>
(function () {{
  function initTelegramFullscreen() {{
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;                       // opened in a normal browser
    try {{ tg.ready(); }} catch (e) {{}}
    try {{ tg.expand(); }} catch (e) {{}}
    try {{
      if (tg.isVersionAtLeast && tg.isVersionAtLeast('8.0') &&
          typeof tg.requestFullscreen === 'function') {{
        tg.requestFullscreen();            // Bot API 8.0+ (mobile & desktop)
      }}
    }} catch (e) {{}}
    try {{ if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); }} catch (e) {{}}
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initTelegramFullscreen);
  }} else {{
    initTelegramFullscreen();
  }}
}})();
</script>
""".format(marker=_INJECTION_MARKER, sdk=_TELEGRAM_WEBAPP_SDK_URL)


def inject_telegram_webapp_fullscreen(html: str) -> str:
    """Insert the Telegram Mini App fullscreen bootstrap into an HTML document.

    The snippet is added just before the closing ``</body>`` tag (falling back
    to ``</html>`` or the end of the document). It is idempotent: if the marker
    or the SDK URL is already present, the HTML is returned unchanged so the
    script is never injected twice.

    Args:
        html: The full HTML document text.

    Returns:
        The HTML with the fullscreen bootstrap injected (or unchanged if it was
        already present).
    """
    if _INJECTION_MARKER in html or _TELEGRAM_WEBAPP_SDK_URL in html:
        return html

    lowered = html.lower()
    insert_at = lowered.rfind("</body>")
    if insert_at == -1:
        insert_at = lowered.rfind("</html>")
    if insert_at == -1:
        return html + _TELEGRAM_FULLSCREEN_SNIPPET
    return html[:insert_at] + _TELEGRAM_FULLSCREEN_SNIPPET + html[insert_at:]
