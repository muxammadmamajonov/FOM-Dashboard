/**
 * HTML upload validation, ported from the original Python bot.
 *
 * The authoritative checks are extension + content (UTF-8 decodable, presence
 * of ``<!DOCTYPE html>``, no null bytes); the MIME type from Telegram is
 * advisory only because clients report inconsistent values for `.html` files
 * (commonly ``application/octet-stream``). Only obviously-binary MIME types
 * are pre-rejected.
 */

const MIN_HTML_SIZE = 1024; // 1 KiB
const MAX_HTML_SIZE = 50 * 1024 * 1024; // 50 MiB
const DOCTYPE_MARKER = "<!doctype html";

const REJECTED_MIME_PREFIXES = ["image/", "video/", "audio/"];
const REJECTED_MIME_EXACT = new Set<string>([
  "application/pdf",
  "application/zip",
  "application/gzip",
  "application/x-tar",
  "application/x-rar-compressed",
  "application/x-7z-compressed",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

export type ValidationResult =
  | { ok: true; text: string }
  | { ok: false; message: string };

/**
 * Validate a downloaded upload.
 *
 * @returns `{ ok: true, text }` with the decoded HTML on success, or
 *          `{ ok: false, message }` with a user-friendly reason on failure.
 */
export function validateHtml(
  bytes: Uint8Array,
  filename: string,
  mimeType: string | undefined,
): ValidationResult {
  if (bytes.length === 0) return fail("The file is empty.");
  if (bytes.length < MIN_HTML_SIZE) {
    return fail("The file is smaller than the 1 KB minimum — it looks truncated or empty.");
  }
  if (bytes.length > MAX_HTML_SIZE) {
    return fail("The file exceeds the maximum allowed size.");
  }

  if (!filename.toLowerCase().endsWith(".html")) {
    return fail("Only files with a .html extension are accepted.");
  }

  const mime = (mimeType ?? "").toLowerCase().trim();
  if (REJECTED_MIME_PREFIXES.some((p) => mime.startsWith(p)) || REJECTED_MIME_EXACT.has(mime)) {
    return fail("The file is not an HTML document (unexpected MIME type).");
  }

  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
  } catch {
    return fail("The file is not valid UTF-8 text.");
  }

  if (text.includes("\0")) {
    return fail("The file contains binary data and is not valid HTML.");
  }

  // DOCTYPE typically sits at the very top of the document; scanning a window
  // is enough and avoids being fooled by the literal string deep in content.
  if (!text.slice(0, 2048).toLowerCase().includes(DOCTYPE_MARKER)) {
    return fail("The '<!DOCTYPE html>' declaration was not found.");
  }

  return { ok: true, text };
}

function fail(message: string): ValidationResult {
  return { ok: false, message };
}
