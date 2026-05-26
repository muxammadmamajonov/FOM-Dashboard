/**
 * Thin wrappers over the Telegram Bot API used by the Worker.
 *
 * Only the methods this project actually needs are exposed: `tg` for generic
 * JSON calls, `sendMessage`, `downloadFile`, and `getMe` (with a tiny in-memory
 * cache for the bot's username so we don't re-fetch it on every notification).
 */

import type { Env } from "./env";

const BASE = "https://api.telegram.org";
const FILE_BASE = "https://api.telegram.org/file";

// Telegram limits bot downloads to 20 MB regardless of the file size the
// client uploaded. We surface this as a friendly error.
const MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024;

export interface TgUser {
  id: number;
  is_bot?: boolean;
  username?: string;
}

export interface TgDocument {
  file_id: string;
  file_unique_id: string;
  file_name?: string;
  mime_type?: string;
  file_size?: number;
}

export interface TgChat {
  id: number;
  type: "private" | "group" | "supergroup" | "channel";
}

export interface TgMessage {
  message_id: number;
  from?: TgUser;
  chat: TgChat;
  date: number;
  text?: string;
  document?: TgDocument;
}

export interface TgUpdate {
  update_id: number;
  message?: TgMessage;
}

interface TgFile {
  file_id: string;
  file_path?: string;
  file_size?: number;
}

interface TgEnvelope<T> {
  ok: boolean;
  result?: T;
  description?: string;
  error_code?: number;
}

export class TelegramError extends Error {
  constructor(public method: string, public code: number | undefined, message: string) {
    super(`Telegram ${method} failed (${code ?? "?"}): ${message}`);
  }
}

/** Generic Bot API call, returns ``result`` or throws ``TelegramError``. */
export async function tg<T>(
  env: Env,
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  const res = await fetch(`${BASE}/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  const envelope = (await res.json()) as TgEnvelope<T>;
  if (!envelope.ok || envelope.result === undefined) {
    throw new TelegramError(method, envelope.error_code, envelope.description ?? "unknown");
  }
  return envelope.result;
}

export async function sendMessage(
  env: Env,
  chatId: number | string,
  text: string,
  replyMarkup?: unknown,
): Promise<void> {
  await tg<TgMessage>(env, "sendMessage", {
    chat_id: chatId,
    text,
    reply_markup: replyMarkup,
  });
}

/**
 * Resolve a Telegram file id to its bytes. Returns a Uint8Array.
 * Throws on size-limit or HTTP errors.
 */
export async function downloadFile(env: Env, fileId: string): Promise<Uint8Array> {
  const file = await tg<TgFile>(env, "getFile", { file_id: fileId });
  if (file.file_size !== undefined && file.file_size > MAX_DOWNLOAD_BYTES) {
    throw new Error(
      "File exceeds Telegram's 20 MB bot-download limit. Please use a smaller file.",
    );
  }
  if (!file.file_path) {
    throw new Error("Telegram did not return a file_path.");
  }
  const res = await fetch(`${FILE_BASE}/bot${env.TELEGRAM_BOT_TOKEN}/${file.file_path}`);
  if (!res.ok) {
    throw new Error(`Download failed: HTTP ${res.status}`);
  }
  const buf = await res.arrayBuffer();
  return new Uint8Array(buf);
}

// Module-scoped cache. Workers reuse isolates for many requests, so this
// avoids re-hitting getMe on every notification. If the isolate is recycled
// we just re-fetch — that's fine.
let cachedMe: TgUser | null = null;

export async function getMeCached(env: Env): Promise<TgUser> {
  if (cachedMe) return cachedMe;
  cachedMe = await tg<TgUser>(env, "getMe");
  return cachedMe;
}
