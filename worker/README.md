# FOM Dashboard Worker

A tiny Cloudflare Worker that serves the dashboard from an R2 bucket.

The Python bot writes `index.html` into the bucket via R2's S3-compatible API;
this Worker reads the same key and serves it at a permanent `*.workers.dev`
URL (or your own domain). The `backups/` prefix in the bucket is **not**
exposed — only the live dashboard.

## One-time setup

```bash
cd worker
npm install                                  # installs wrangler + types locally
npx wrangler login                           # opens browser, authenticates
npx wrangler r2 bucket create fom-dashboard  # creates the bucket
npx wrangler deploy                          # publishes the Worker
```

The last command prints the Worker URL, for example:
```
https://fom-dashboard.<your-subdomain>.workers.dev
```

That URL is permanent. Put it in two places:

1. The project's `.env` as `CLOUDFLARE_DOMAIN=<URL>` (used by the
   group "Open in Browser" button).
2. **@BotFather → /myapps → your app → Edit Web App URL**, paste the same URL
   (used by the fullscreen Mini App button). After this, you should never
   need to touch BotFather again.

## R2 credentials for the Python bot

The Worker reads R2 through its binding (no credentials needed), but the
Python bot writes through R2's S3-compatible API and needs an API token.

1. Open the [Cloudflare dashboard](https://dash.cloudflare.com/) → **R2** →
   **Manage R2 API Tokens** → **Create API Token**.
2. Permissions: **Object Read & Write**.
3. Bucket: pick the bucket (`fom-dashboard`).
4. Copy the **Access Key ID**, **Secret Access Key**, and your **Account ID**
   (top-right corner of the R2 page) into the project `.env`:
   ```
   R2_ACCOUNT_ID=...
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET_NAME=fom-dashboard
   ```

## Day-to-day

```bash
cd worker
npx wrangler deploy   # re-deploy after editing src/index.ts
npx wrangler tail     # stream live request logs
npx wrangler dev      # run locally on http://localhost:8787 (uses real R2)
```

## What this Worker does (and doesn't)

- `GET /` or `GET /index.html` → returns `index.html` from R2 with
  `Cache-Control: no-store` (so each request sees the latest upload).
- `HEAD` is supported, returns the same headers without a body.
- Any other path → `404 Not Found`.
- The bucket's `backups/` prefix is never reachable through the Worker.
- The Worker is read-only; the bot is the only writer.
