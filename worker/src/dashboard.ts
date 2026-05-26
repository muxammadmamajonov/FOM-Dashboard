/**
 * GET /  →  serve the current dashboard from KV.
 *
 * KV holds a single key, ``index.html``. Each request reads it fresh and
 * returns it with `Cache-Control: no-store` so the latest upload is visible
 * immediately. When the key is empty (first ever run, before any upload) a
 * small placeholder page is served instead.
 *
 * Everything else is 404 — there are no other keys, no backups prefix, no
 * directory listing.
 */

import type { Env } from "./env";

export const INDEX_KEY = "index.html";

const PLACEHOLDER_HTML = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>FOM Dashboard</title></head>
<body style="font-family:sans-serif;text-align:center;margin-top:15vh">
  <h1>Dashboard not published yet</h1>
  <p>Waiting for the administrator to upload the first HTML file.</p>
</body></html>`;

const HTML_HEADERS: Record<string, string> = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "no-store, must-revalidate",
};

export async function handleDashboard(request: Request, env: Env): Promise<Response> {
  // For HEAD, just check existence; for GET, stream the value back.
  if (request.method === "HEAD") {
    const exists = await env.DASHBOARD_KV.get(INDEX_KEY, "text");
    return new Response(null, {
      status: exists === null ? 200 : 200, // placeholder also returns 200
      headers: HTML_HEADERS,
    });
  }

  const stream = await env.DASHBOARD_KV.get(INDEX_KEY, "stream");
  if (stream === null) {
    return new Response(PLACEHOLDER_HTML, { status: 200, headers: HTML_HEADERS });
  }
  return new Response(stream, { headers: HTML_HEADERS });
}
