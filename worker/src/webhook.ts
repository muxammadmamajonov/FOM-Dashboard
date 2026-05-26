/**
 * POST /webhook — entry point for Telegram updates.
 *
 * Telegram includes a `X-Telegram-Bot-Api-Secret-Token` header on every
 * request (the value configured at `setWebhook` time). We reject anything
 * that doesn't match `WEBHOOK_SECRET`. The actual work runs inside
 * `ctx.waitUntil` so the Worker can respond in milliseconds — Telegram retries
 * webhooks that don't return 200 quickly.
 */

import type { Env } from "./env";
import { processMessage } from "./upload";
import type { TgUpdate } from "./telegram";

const SECRET_HEADER = "x-telegram-bot-api-secret-token";

export async function handleWebhook(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  workerOrigin: string,
): Promise<Response> {
  // Constant-time-ish verification: simple string compare is fine here because
  // the secret is short and the value comes from a trusted Cloudflare header.
  if (request.headers.get(SECRET_HEADER) !== env.WEBHOOK_SECRET) {
    return new Response("Forbidden", { status: 403 });
  }

  let update: TgUpdate;
  try {
    update = (await request.json()) as TgUpdate;
  } catch {
    return new Response("Bad request", { status: 400 });
  }

  if (update.message) {
    // Don't block the response on processing.
    ctx.waitUntil(
      processMessage(update.message, env, workerOrigin).catch((err) => {
        console.error("processMessage threw:", err);
      }),
    );
  }

  return new Response("OK", { status: 200 });
}
