/**
 * FOM Dashboard Cloudflare Worker.
 *
 * Serves the single `index.html` object from an R2 bucket at the root of the
 * Worker's URL, with `no-store` cache headers so a freshly uploaded dashboard
 * is reflected on the very next page load.
 *
 * Security: only the dashboard itself is exposed. Every other path (including
 * the `backups/` prefix) returns 404 so upload history is never browsable.
 *
 * The Python bot writes to the same bucket via R2's S3-compatible API.
 */

export interface Env {
  /** R2 bucket binding declared in wrangler.toml as DASHBOARD_BUCKET. */
  DASHBOARD_BUCKET: R2Bucket;
}

const INDEX_KEY = "index.html";

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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { allow: "GET, HEAD" },
      });
    }

    const url = new URL(request.url);
    // Only the dashboard is exposed; `backups/...` and anything else 404s.
    if (url.pathname !== "/" && url.pathname !== "/index.html") {
      return new Response("Not found", { status: 404 });
    }

    const object = await env.DASHBOARD_BUCKET.get(INDEX_KEY);
    if (object === null) {
      return new Response(PLACEHOLDER_HTML, {
        status: 200,
        headers: HTML_HEADERS,
      });
    }

    const headers = new Headers(HTML_HEADERS);
    headers.set("etag", object.httpEtag);
    if (object.uploaded) {
      headers.set("last-modified", object.uploaded.toUTCString());
    }

    return new Response(request.method === "HEAD" ? null : object.body, {
      headers,
    });
  },
};
