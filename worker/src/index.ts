/**
 * FOM Dashboard — single-Worker entry point.
 *
 * Routing:
 *   GET  /            -> serve the dashboard HTML from KV
 *   GET  /index.html  -> same
 *   POST /webhook     -> Telegram webhook (validated by secret header)
 *   *                 -> 404
 *
 * The whole bot lives in this Worker: receiving uploads, validating them,
 * injecting the fullscreen bootstrap, storing the result in KV, and serving
 * it. There is no Python process, no R2 bucket, no backup history.
 */

import { handleDashboard } from "./dashboard";
import type { Env } from "./env";
import { handleWebhook } from "./webhook";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/webhook") {
      return handleWebhook(request, env, ctx, url.origin);
    }

    if (
      (request.method === "GET" || request.method === "HEAD") &&
      (url.pathname === "/" || url.pathname === "/index.html")
    ) {
      return handleDashboard(request, env);
    }

    return new Response("Not found", { status: 404 });
  },
};
