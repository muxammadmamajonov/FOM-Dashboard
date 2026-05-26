# FOM Dashboard Worker

This Worker **is** the bot. It does four things:

1. **`POST /webhook`** — receives Telegram updates (verified by
   `X-Telegram-Bot-Api-Secret-Token`), enforces admin-only uploads, downloads
   the file from Telegram, validates it, injects the Telegram Mini App
   fullscreen bootstrap, and writes it to Workers KV.
2. **`GET /` / `GET /index.html`** — reads the one KV cell and streams it
   back with `Cache-Control: no-store`.
3. **Notifies the team group** after a successful upload, with a fullscreen
   Mini App button (and a browser fallback).
4. **Returns 404** for everything else. There are no other keys, no history.

Source layout:

```
src/
├── index.ts        # router
├── webhook.ts      # secret-verified webhook entry
├── upload.ts       # admin guard → download → validate → inject → KV.put → notify
├── telegram.ts     # tiny Bot API client (getMe, getFile, sendMessage)
├── validate.ts     # HTML validation
├── inject.ts       # idempotent fullscreen-script injection
├── dashboard.ts    # GET / handler
└── env.ts          # Env type + helpers
```

## Setup

See the top-level [README](../README.md). In short, from this directory:

```bash
npm install
npx wrangler login
npx wrangler kv namespace create DASHBOARD_KV   # paste id into wrangler.toml
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put WEBHOOK_SECRET
npx wrangler secret put ADMIN_USER_ID
npx wrangler secret put TELEGRAM_GROUP_CHAT_ID
npx wrangler deploy
```

Then run `scripts/setup-webhook.sh` (in the project root) once.

## Useful commands

```bash
npx wrangler deploy           # publish latest src/ to Cloudflare
npx wrangler tail             # stream live request logs from the Worker
npx wrangler dev              # local dev (talks to real KV by default)
npx tsc --noEmit              # type-check without emitting JS
```

## Why a single Worker (and not Worker + R2, or Python + tunnel)

- **Stateless serving + permanent URL** — the dashboard URL is the Worker's
  workers.dev URL, which never changes.
- **No laptop running 24/7** — nothing executes locally.
- **No bucket / no backup history** — KV stores one cell; each upload
  overwrites it. Nothing accumulates.
- **One thing to deploy** — `wrangler deploy`. CI/CD is built in.
