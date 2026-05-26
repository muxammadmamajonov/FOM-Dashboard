/**
 * Telegram message → dashboard update.
 *
 * The webhook handler hands every incoming Telegram update to this module,
 * which routes commands, enforces the admin allow-list, downloads + validates
 * + injects + stores the HTML in KV, then replies to the admin and notifies
 * the team group. There is no backup history: KV holds one key.
 */

import { INDEX_KEY } from "./dashboard";
import { isFullscreenEnabled, type Env } from "./env";
import { injectFullscreen } from "./inject";
import { downloadFile, getMeCached, sendMessage, type TgMessage } from "./telegram";
import { validateHtml } from "./validate";

export async function processMessage(
  message: TgMessage,
  env: Env,
  workerOrigin: string,
): Promise<void> {
  const chat = message.chat;
  const from = message.from;
  if (!from) return;

  // Only operate in 1-to-1 chats with the bot. Group chatter is ignored.
  if (chat.type !== "private") return;

  // Commands first.
  const text = message.text?.trim();
  if (text === "/start") {
    await sendMessage(env, chat.id, welcomeMessage(String(from.id) === env.ADMIN_USER_ID));
    return;
  }
  if (text === "/help") {
    await sendMessage(env, chat.id, helpMessage());
    return;
  }

  // Anything that isn't a document is a redirect to /help.
  if (!message.document) {
    await sendMessage(
      env,
      chat.id,
      "I only accept HTML dashboard files from the administrator. Send /help for usage.",
    );
    return;
  }

  // Admin guard.
  if (String(from.id) !== env.ADMIN_USER_ID) {
    await sendMessage(env, chat.id, "⛔ You are not authorized to upload dashboards.");
    return;
  }

  const document = message.document;
  const filename = document.file_name ?? "upload.html";

  // 1. Download from Telegram.
  let bytes: Uint8Array;
  try {
    bytes = await downloadFile(env, document.file_id);
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    await sendMessage(
      env,
      chat.id,
      `❌ Could not download the file.\n\n${reason}\n\nPlease try again with a smaller file if it exceeds 20 MB.`,
    );
    return;
  }

  // 2. Validate. The result includes the decoded UTF-8 text on success so we
  //    don't decode twice.
  const validation = validateHtml(bytes, filename, document.mime_type);
  if (!validation.ok) {
    await sendMessage(env, chat.id, formatAdminError(validation.message));
    return;
  }

  // 3. Inject the Telegram Mini App fullscreen bootstrap (idempotent).
  const html = isFullscreenEnabled(env) ? injectFullscreen(validation.text) : validation.text;

  // 4. Overwrite the single KV key. KV.put replaces the previous value; there
  //    is no history and no backup.
  await env.DASHBOARD_KV.put(INDEX_KEY, html, {
    metadata: {
      uploaded_at: new Date().toISOString(),
      size: html.length,
      original_filename: filename,
    },
  });

  // 5. Tell the admin, then post the refresh notification to the group.
  await sendMessage(env, chat.id, formatAdminSuccess(html.length));
  await notifyGroup(env, workerOrigin);
}

async function notifyGroup(env: Env, workerOrigin: string): Promise<void> {
  const buttons: Array<Array<{ text: string; url: string }>> = [];

  if (env.WEBAPP_SHORT_NAME) {
    try {
      const me = await getMeCached(env);
      if (me.username) {
        buttons.push([
          {
            text: "📊 View Dashboard",
            url: `https://t.me/${me.username}/${env.WEBAPP_SHORT_NAME}`,
          },
        ]);
      }
    } catch {
      // Fall through — we still send the browser-fallback button below.
    }
  }

  buttons.push([
    {
      text: buttons.length > 0 ? "🌐 Open in Browser" : "📊 View Dashboard",
      url: workerOrigin + "/",
    },
  ]);

  const text =
    "✅ Analytics data updated\n\n" +
    "FOM CEO Dashboard has been refreshed\n" +
    `Updated: ${humanTimestamp()} UTC`;

  await sendMessage(env, env.TELEGRAM_GROUP_CHAT_ID, text, { inline_keyboard: buttons });
}

// ---------------------------------------------------------------- formatting
function welcomeMessage(isAdmin: boolean): string {
  const role = isAdmin
    ? "You are recognised as the dashboard administrator."
    : "Only the configured administrator can upload dashboards.";
  return (
    "👋 Welcome to the FOM Dashboard bot.\n\n" +
    "I publish the FOM CEO Dashboard. When the administrator sends me an " +
    "HTML file, I validate it, store it as the live dashboard, and notify the team.\n\n" +
    `${role}\n\n` +
    "Commands:\n" +
    "  /start — show this message\n" +
    "  /help — detailed usage instructions"
  );
}

function helpMessage(): string {
  return (
    "ℹ️ How to use this bot\n\n" +
    "1. As the administrator, send me an HTML file (a document with a .html " +
    "extension) in this private chat.\n" +
    "2. I validate it (size, UTF-8 encoding, and a <!DOCTYPE html> declaration).\n" +
    "3. If it passes, I save it as the live dashboard and post an update to " +
    "the team group.\n\n" +
    "Notes:\n" +
    "  • Only the configured administrator can upload.\n" +
    "  • Files must be between 1 KB and 20 MB (Telegram's bot-download limit).\n" +
    "  • There is no backup history — each upload replaces the previous file."
  );
}

function formatAdminSuccess(size: number): string {
  return (
    "✅ Dashboard updated successfully!\n\n" +
    "File details:\n" +
    `- Size: ${humanSize(size)}\n` +
    `- Updated: ${humanTimestamp()}\n` +
    "- Notification sent to group"
  );
}

function formatAdminError(reason: string): string {
  return (
    "❌ Update failed - Invalid HTML file\n\n" +
    "Error details:\n" +
    `${reason}\n\n` +
    "Please check your HTML file and try again."
  );
}

function humanTimestamp(): string {
  // ISO 8601 in seconds resolution, swapping the 'T' for a space for legibility.
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}
