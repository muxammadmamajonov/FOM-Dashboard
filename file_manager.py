"""R2-backed file storage for the FOM Dashboard bot.

The bot writes the validated, fullscreen-injected HTML to a Cloudflare R2
bucket (S3-compatible). The Worker under ``worker/`` reads the same bucket and
serves it at a permanent ``*.workers.dev`` URL — so the bot can run anywhere,
the dashboard URL never changes, and there is no local web server or tunnel
to keep alive.

Storage layout in the bucket:

    index.html                                  current dashboard
    backups/index_YYYYMMDD_HHMMSS_HASH8.html    rotated previous versions

Atomicity is provided by R2 itself: each ``PutObject`` is a single atomic
write. Backups are created before the new upload, so a failed upload cannot
destroy the previous version.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Tuple

import aioboto3
import aiofiles
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

import utils
from config import Config
from logger import get_logger
from utils import MIN_HTML_SIZE

logger = get_logger(__name__)

# Object key layout inside the R2 bucket.
INDEX_KEY = "index.html"
BACKUP_PREFIX = "backups/"
MAX_BACKUPS = 10

# Custom metadata header stored on each object so we can dedupe and name
# backups without re-downloading the full body.
_HASH_META_KEY = "content-sha256"

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
_CONTENT_TYPE = "text/html; charset=utf-8"
_CACHE_CONTROL = "no-store, must-revalidate"


class FileManager:
    """Owns all reads/writes against the configured R2 bucket.

    Args:
        config: The validated application configuration. The manager derives
            the endpoint, credentials, bucket, size limit, and feature flags
            from it. R2 clients are short-lived: one is opened per save.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._bucket: str = config.r2_bucket_name
        self._max_file_size: int = config.max_file_size
        self._backup_enabled: bool = config.backup_enabled
        self._webapp_fullscreen: bool = config.webapp_fullscreen_enabled
        self._session = aioboto3.Session()
        # Modest connect/read timeouts so transient R2 hiccups surface quickly
        # instead of hanging the upload handler indefinitely.
        self._boto_config = BotoConfig(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
            signature_version="s3v4",
        )

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
        from pathlib import Path

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
        """Inject the Mini App bootstrap and upload ``file_path`` to R2.

        Flow: read the validated temp file -> inject (when enabled) -> compute
        SHA-256 -> back up the existing ``index.html`` to ``backups/...`` if it
        differs -> PutObject the new ``index.html`` -> rotate old backups.

        Args:
            file_path: Path to the validated temporary file.
            admin_id: Telegram id of the uploading admin (for logging context).

        Returns:
            ``{'success': bool, 'message': str, 'file_size': int,
            'timestamp': str}``.
        """
        timestamp = utils.get_iso_timestamp()
        try:
            data = await self._prepare_payload(file_path)
            content_hash = hashlib.sha256(data).hexdigest()

            async with self._client() as s3:
                if self._backup_enabled:
                    backed_up = await self._backup_current(s3, content_hash)
                    if not backed_up:
                        return self._result(
                            False, "Backup of the previous version failed.",
                            0, timestamp,
                        )

                await s3.put_object(
                    Bucket=self._bucket,
                    Key=INDEX_KEY,
                    Body=data,
                    ContentType=_CONTENT_TYPE,
                    CacheControl=_CACHE_CONTROL,
                    Metadata={_HASH_META_KEY: content_hash},
                )
                logger.info(
                    "Uploaded dashboard to R2 r2://%s/%s (%s) for admin_id=%s",
                    self._bucket, INDEX_KEY,
                    utils.human_readable_size(len(data)), admin_id,
                )

                await self._cleanup_backups(s3)

            return self._result(True, "Dashboard updated successfully.",
                                len(data), timestamp)

        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "?")
            logger.error("R2 ClientError %s: %s", code, exc, exc_info=True)
            return self._result(False, f"Cloud storage error ({code}).",
                                0, timestamp)
        except BotoCoreError as exc:
            logger.error("R2 transport error: %s", exc, exc_info=True)
            return self._result(False, f"Cloud storage error: {exc}",
                                0, timestamp)
        except OSError as exc:
            logger.error("Local I/O error before upload: %s", exc, exc_info=True)
            return self._result(False, f"I/O error: {exc}", 0, timestamp)

    # ----------------------------------------------------------------- backup
    async def _backup_current(self, s3, new_content_hash: str) -> bool:
        """Server-side copy ``index.html`` to a timestamped backup key.

        Skipped (returned ``True``) when there is no existing dashboard or when
        the existing dashboard's content hash matches the new upload (i.e. the
        admin re-uploaded the same file).

        Args:
            s3: An open aioboto3 S3 client.
            new_content_hash: SHA-256 hex of the about-to-be-uploaded payload.

        Returns:
            ``True`` on success (or no-op), ``False`` if the copy failed.
        """
        try:
            head = await s3.head_object(Bucket=self._bucket, Key=INDEX_KEY)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("404", "NoSuchKey", "NotFound") or status == 404:
                logger.debug("No prior dashboard in R2 to back up")
                return True
            logger.error("HEAD on current index failed: %s", exc)
            return False

        existing_hash = head.get("Metadata", {}).get(_HASH_META_KEY, "")
        if existing_hash == new_content_hash:
            logger.info(
                "Skipping backup: content hash %s matches current dashboard",
                new_content_hash[:8],
            )
            return True

        hash8 = (existing_hash or "unknown ")[:8]
        backup_key = (
            f"{BACKUP_PREFIX}index_{utils.get_backup_timestamp()}_{hash8}.html"
        )
        try:
            await s3.copy_object(
                Bucket=self._bucket,
                Key=backup_key,
                CopySource={"Bucket": self._bucket, "Key": INDEX_KEY},
                MetadataDirective="COPY",
            )
            logger.info("Backup created: %s", backup_key)
            return True
        except ClientError as exc:
            logger.error("Failed to copy backup %s: %s", backup_key, exc)
            return False

    async def _cleanup_backups(self, s3) -> None:
        """Delete all but the newest :data:`MAX_BACKUPS` backups."""
        try:
            paginator = s3.get_paginator("list_objects_v2")
            items: list[dict] = []
            async for page in paginator.paginate(
                Bucket=self._bucket, Prefix=BACKUP_PREFIX
            ):
                items.extend(page.get("Contents", []) or [])
        except ClientError as exc:
            logger.error("Could not list backups for rotation: %s", exc)
            return

        items.sort(key=lambda obj: obj["LastModified"], reverse=True)
        for old in items[MAX_BACKUPS:]:
            try:
                await s3.delete_object(Bucket=self._bucket, Key=old["Key"])
                logger.debug("Removed old backup: %s", old["Key"])
            except ClientError as exc:
                logger.warning(
                    "Could not delete old backup %s: %s", old["Key"], exc
                )

    # ----------------------------------------------------------------- helpers
    async def _prepare_payload(self, file_path: str) -> bytes:
        """Read the temp file and (optionally) inject the fullscreen bootstrap."""
        async with aiofiles.open(file_path, "rb") as src:
            raw = await src.read()

        if not self._webapp_fullscreen:
            return raw

        try:
            injected = utils.inject_telegram_webapp_fullscreen(
                raw.decode("utf-8")
            )
            data = injected.encode("utf-8")
            if len(data) != len(raw):
                logger.info("Injected Telegram Mini App fullscreen script")
            else:
                logger.debug("Fullscreen script already present; left as-is")
            return data
        except UnicodeDecodeError:
            # Validation already proved it is UTF-8, but never corrupt the
            # save over an injection problem -- fall back to the raw bytes.
            logger.warning("Could not decode for injection; uploading as-is")
            return raw

    def _client(self):
        """Open an async R2 client bound to the configured account/credentials.

        Returns:
            An async context manager yielding an aiobotocore S3 client.
        """
        return self._session.client(
            "s3",
            endpoint_url=self._config.r2_endpoint_url,
            aws_access_key_id=self._config.r2_access_key_id,
            aws_secret_access_key=self._config.r2_secret_access_key,
            # R2 ignores the region but the SDK requires one. "auto" is the
            # value Cloudflare's documentation recommends.
            region_name="auto",
            config=self._boto_config,
        )

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

    @staticmethod
    def _result(success: bool, message: str, size: int, ts: str) -> Dict[str, Any]:
        """Shape the public result dict consumed by the handler layer."""
        return {
            "success": success,
            "message": message,
            "file_size": size,
            "timestamp": ts,
        }
