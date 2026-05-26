/**
 * Environment bindings for the FOM Dashboard Worker.
 *
 * - `DASHBOARD_KV` is a Workers KV namespace with a single key, ``index.html``.
 *   Each upload overwrites that key. There are no other keys and no history.
 *
 * - Secrets (set with `wrangler secret put <NAME>` once, never committed):
 *     TELEGRAM_BOT_TOKEN         token from @BotFather
 *     WEBHOOK_SECRET             random string; Telegram sends it back in a
 *                                header so the Worker can verify the request
 *     ADMIN_USER_ID              numeric Telegram user id allowed to upload
 *     TELEGRAM_GROUP_CHAT_ID     numeric chat id of the team group (negative)
 *
 * - Public vars (committed in `wrangler.toml`):
 *     WEBAPP_SHORT_NAME          BotFather Mini App short name (optional)
 *     WEBAPP_FULLSCREEN_ENABLED  "true" | "false" (default: enabled)
 */
export interface Env {
  DASHBOARD_KV: KVNamespace;

  // --- secrets ---
  TELEGRAM_BOT_TOKEN: string;
  WEBHOOK_SECRET: string;
  ADMIN_USER_ID: string;
  TELEGRAM_GROUP_CHAT_ID: string;

  // --- public vars (may be undefined if not set) ---
  WEBAPP_SHORT_NAME?: string;
  WEBAPP_FULLSCREEN_ENABLED?: string;
}

export function isFullscreenEnabled(env: Env): boolean {
  // Anything other than the explicit string "false" counts as enabled, so the
  // default behaviour (var unset) is to inject the fullscreen bootstrap.
  return (env.WEBAPP_FULLSCREEN_ENABLED ?? "true").toLowerCase() !== "false";
}
