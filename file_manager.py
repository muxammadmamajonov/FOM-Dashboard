"""File-system operations for the dashboard bot.

Encapsulates validation, atomic saves, and backup rotation behind a single
:class:`FileManager` so the Telegram handlers stay focused on messaging.

The central safety property here is *atomicity*: a new dashboard is streamed to
a temporary file inside the target directory and only then ``os.replace``-d over
``index.html``. Because ``os.replace`` is atomic on a single filesystem, a crash
mid-write can never leave a half-written, corrupt dashboard in place.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import aiofiles

import utils
from config import Config
from logger import get_logger
from utils import MIN_HTML_SIZE

logger = get_logger(__name__)

# Telegram clients report wildly inconsistent MIME types for .html uploads
# (commonly "application/octet-stream"). The client-supplied MIME is untrusted
# metadata, so it is NOT used as the real gate -- the extension plus the content
# checks (UTF-8 + <!DOCTYPE html> + no null bytes) are authoritative. We only
# reject MIME types that clearly indicate a non-HTML binary payload, as an
# early and friendly rejection of obviously-wrong files.
_REJECTED_MIME_PREFIXES = ("image/", "video/", "audio/")
_REJECTED_MIME_EXACT = {
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/x-tar",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_DOCTYPE_MARKER = "<!doctype html"
_CHUNK_SIZE = 65_536


class FileManager:
    """Owns all reads/writes under the configured upload directory.

    Args:
        config: The validated application configuration. The manager derives the
            upload folder, backups folder, index path, size limit, and backup
            toggle from it.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._upload_folder: Path = config.upload_folder_path
        self._backups_folder: Path = config.backups_folder_path
        self._index_path: Path = config.index_file_path
        self._max_file_size: int = config.max_file_size
        self._backup_enabled: bool = config.backup_enabled
        self._webapp_fullscreen: bool = config.webapp_fullscreen_enabled

    # ------------------------------------------------------------- validation
    async def validate_html_file(
        self,
        file_path: str,
        original_filename: str,
        mime_type: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        """Validate a candidate HTML upload.

        Checks, in order: existence, size bounds, ``.html`` extension, MIME
        plausibility, UTF-8 decodability, absence of binary/null bytes, and the
        presence of a ``<!DOCTYPE html>`` declaration.

        Args:
            file_path: Path to the downloaded temporary file.
            original_filename: The filename as supplied by the uploader (used
                for the extension check; the temp path has no extension).
            mime_type: The MIME type Telegram reported, if any.

        Returns:
            ``(True, None)`` when valid, otherwise ``(False, message)`` where
            ``message`` is a user-friendly explanation of the first failure.
        """
        path = Path(file_path)
        if not path.is_file():
            return self._fail("not_found", original_filename)

        size = path.stat().st_size
        if size == 0:
            return self._fail("empty", original_filename)
        if size < MIN_HTML_SIZE:
            return self._fail("too_small", original_filename)
        if size > self._max_file_size:
            return self._fail("too_large", original_filename)

        if utils.file_extension(original_filename) != ".html":
            return self._fail("bad_extension", original_filename)

        if not self._mime_is_acceptable(mime_type):
            return self._fail("bad_mime", original_filename, extra=mime_type)

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return self._fail("bad_encoding", original_filename)
        except OSError:
            return self._fail("not_found", original_filename)

        if "\x00" in text:
            return self._fail("binary_content", original_filename)

        # DOCTYPE typically sits at the very top; scanning a window is enough
        # and avoids being fooled by the literal string deep inside content.
        if _DOCTYPE_MARKER not in text[:2048].lower():
            return self._fail("no_doctype", original_filename)

        logger.info(
            "HTML validation PASSED for '%s' (%s)",
            original_filename,
            utils.human_readable_size(size),
        )
        return True, None

    # ------------------------------------------------------------------- save
    async def save_html_file(self, file_path: str, admin_id: int) -> Dict[str, Any]:
        """Atomically install ``file_path`` as the served ``index.html``.

        Backs up the existing dashboard first (when backups are enabled), then
        streams the new file to a sibling temp file and atomically replaces the
        target. Old backups are rotated afterwards.

        Args:
            file_path: Path to the validated temporary file to install.
            admin_id: Telegram id of the uploading admin (for logging context).

        Returns:
            A result dict: ``{'success': bool, 'message': str,
            'file_size': int, 'timestamp': str}``.
        """
        timestamp = utils.get_iso_timestamp()
        try:
            self._upload_folder.mkdir(parents=True, exist_ok=True)

            if self._backup_enabled and self._index_path.exists():
                created = await self.create_backup(str(self._index_path))
                if not created:
                    # A failed backup must not silently lose the prior version.
                    logger.warning(
                        "Backup step did not produce a file; aborting save to "
                        "avoid overwriting the previous dashboard."
                    )
                    return {
                        "success": False,
                        "message": "Backup of the previous version failed.",
                        "file_size": 0,
                        "timestamp": timestamp,
                    }

            file_size = await self._install(file_path)

            logger.info(
                "Saved dashboard to %s (%s) for admin_id=%s",
                self._index_path,
                utils.human_readable_size(file_size),
                admin_id,
            )

            await self.cleanup_old_backups()

            return {
                "success": True,
                "message": "Dashboard updated successfully.",
                "file_size": file_size,
                "timestamp": timestamp,
            }
        except PermissionError as exc:
            logger.error("Permission denied writing dashboard: %s", exc)
            return {
                "success": False,
                "message": "Permission denied writing to the upload folder.",
                "file_size": 0,
                "timestamp": timestamp,
            }
        except OSError as exc:
            logger.error("File system error during save: %s", exc, exc_info=True)
            return {
                "success": False,
                "message": f"File system error: {exc}",
                "file_size": 0,
                "timestamp": timestamp,
            }

    async def _install(self, source_path: str) -> int:
        """Read the validated upload, optionally inject the Mini App bootstrap,
        and atomically write it as ``index.html``.

        Args:
            source_path: Path to the validated temporary file.

        Returns:
            The number of bytes written to ``index.html``.
        """
        async with aiofiles.open(source_path, "rb") as src:
            raw = await src.read()

        data = raw
        if self._webapp_fullscreen:
            try:
                injected = utils.inject_telegram_webapp_fullscreen(
                    raw.decode("utf-8")
                )
                data = injected.encode("utf-8")
                if len(data) != len(raw):
                    logger.info("Injected Telegram Mini App fullscreen script")
                else:
                    logger.debug("Fullscreen script already present; left as-is")
            except UnicodeDecodeError:
                # Validation already proved it is UTF-8, but never corrupt the
                # save over an injection problem — fall back to the raw bytes.
                logger.warning("Could not decode for injection; saving as-is")
                data = raw

        return await self._atomic_write(data)

    async def _atomic_write(self, data: bytes) -> int:
        """Write ``data`` to ``index.html`` atomically (temp + fsync + replace).

        Returns:
            The number of bytes written.
        """
        # Temp file lives in the destination directory so os.replace stays on
        # the same filesystem and is therefore atomic.
        tmp_path = self._index_path.with_name(
            f".index.{utils.generate_request_id()}.tmp"
        )
        try:
            async with aiofiles.open(tmp_path, "wb") as dst:
                await dst.write(data)
                await dst.flush()
                os.fsync(dst.fileno())

            # Verify the temp file matches what we intended before committing.
            actual = tmp_path.stat().st_size
            if actual != len(data):
                raise OSError(
                    f"Write verification failed: expected {len(data)} "
                    f"bytes, found {actual}."
                )

            os.replace(tmp_path, self._index_path)
            return len(data)
        finally:
            # If replace succeeded the temp file is gone; otherwise clean it up.
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    logger.debug("Could not remove temp file %s", tmp_path)

    # ----------------------------------------------------------------- backup
    async def create_backup(self, source_path: str) -> bool:
        """Copy ``source_path`` into the backups folder with a stamped name.

        The backup filename is ``index_<YYYYMMDD_HHMMSS>_<hash8>.html``. If the
        most recent backup already has the same content hash, the upload is a
        duplicate and no new backup is created (this is reported as success).

        Args:
            source_path: Path to the current dashboard file to back up.

        Returns:
            ``True`` if a backup exists for the current content afterwards
            (whether freshly written or an existing duplicate), ``False`` on
            failure.
        """
        source = Path(source_path)
        if not source.exists():
            logger.debug("No existing dashboard to back up at %s", source)
            # Nothing to back up is not an error; there is simply no prior file.
            return True

        try:
            self._backups_folder.mkdir(parents=True, exist_ok=True)
            content_hash = utils.calculate_file_hash(source_path)[:8]

            if self._is_duplicate_of_latest(content_hash):
                logger.info(
                    "Skipping backup: content hash %s matches latest backup.",
                    content_hash,
                )
                return True

            backup_name = (
                f"index_{utils.get_backup_timestamp()}_{content_hash}.html"
            )
            backup_path = Path(
                utils.get_safe_file_path(str(self._backups_folder), backup_name)
            )

            await self._copy_file(source_path, str(backup_path))
            logger.info("Backup created: %s", backup_path.name)
            return True
        except (OSError, ValueError) as exc:
            logger.error("Failed to create backup: %s", exc, exc_info=True)
            return False

    async def cleanup_old_backups(self, max_keep: int = 10) -> None:
        """Delete the oldest backups, keeping only the most recent ``max_keep``.

        Args:
            max_keep: Number of newest backups to retain.
        """
        try:
            backups = sorted(
                self._backups_folder.glob("index_*.html"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError as exc:
            logger.error("Could not list backups for rotation: %s", exc)
            return

        for stale in backups[max_keep:]:
            try:
                stale.unlink()
                logger.debug("Removed old backup: %s", stale.name)
            except OSError as exc:
                logger.warning("Could not remove old backup %s: %s", stale, exc)

    # ----------------------------------------------------------------- helpers
    def _is_duplicate_of_latest(self, content_hash: str) -> bool:
        """Return ``True`` if the newest backup's name carries ``content_hash``."""
        backups = sorted(
            self._backups_folder.glob("index_*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not backups:
            return False
        # Filename layout: index_<date>_<time>_<hash8>.html
        return backups[0].stem.endswith(f"_{content_hash}")

    @staticmethod
    async def _copy_file(source_path: str, dest_path: str) -> None:
        """Stream-copy ``source_path`` to ``dest_path`` using bounded memory."""
        async with aiofiles.open(source_path, "rb") as src, aiofiles.open(
            dest_path, "wb"
        ) as dst:
            while True:
                chunk = await src.read(_CHUNK_SIZE)
                if not chunk:
                    break
                await dst.write(chunk)

    def _mime_is_acceptable(self, mime_type: Optional[str]) -> bool:
        """Return ``True`` unless the reported MIME is clearly a non-HTML binary.

        The client-provided MIME type is unreliable, so it is treated as
        advisory only: anything that is not an obvious binary format (image,
        video, audio, archive, PDF, Office doc) is accepted here and validated
        authoritatively by the UTF-8/DOCTYPE content checks.
        """
        normalized = (mime_type or "").lower().strip()
        if normalized.startswith(_REJECTED_MIME_PREFIXES):
            return False
        return normalized not in _REJECTED_MIME_EXACT

    @staticmethod
    def _fail(
        error_key: str,
        filename: str,
        extra: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Log a validation failure and return the user-facing message tuple."""
        message = utils.get_html_validation_error_message(error_key)
        detail = f" ({extra})" if extra else ""
        logger.warning(
            "HTML validation FAILED for '%s': %s%s", filename, error_key, detail
        )
        return False, message
