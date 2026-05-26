# FOM Dashboard

A single **Cloudflare Worker** that publishes the FOM CEO Dashboard.

The administrator sends an `.html` file to the bot in a private chat. The
Worker — which is the bot — validates it, injects a Telegram Mini App
fullscreen bootstrap, replaces the live dashboard in a single
Workers KV cell, and posts a "dashboard refreshed" notification (with a
fullscreen Mini App button) to the team group. The dashboard is served by
the same Worker at a permanent `*.workers.dev` URL.

There is **no Python process, no R2 bucket, no backup history**. Each upload
overwrites the one KV cell that holds the current dashboard; nothing else
is stored.

```
Telegram ──webhook POST──►  Cloudflare Worker  ◄──GET /──►  CEO's browser / Telegram Mini App
                                  │
                                  └── KV: "index.html"  (one cell, overwritten on every upload)
```

## Features

- **One thing to deploy.** A single Worker handles webhook intake, validation,
  storage, and serving. `wrangler deploy` is the whole CI/CD.
- **Admin-only uploads.** Every incoming document is checked against the
  configured admin user id.
- **Validates uploads** (size, .html extension, UTF-8 encoding, presence of
  `<!DOCTYPE html>`, deny-list of obviously-binary MIME types).
- **Auto-fullscreen.** Each saved dashboard is injected with the Telegram Web
  App SDK plus a `requestFullscreen()` bootstrap (Bot API 8.0+, mobile and
  desktop). Idempotent — re-uploads never stack duplicate scripts.
- **No-store serving.** Responses carry `Cache-Control: no-store, must-revalidate`
  so a freshly uploaded dashboard is visible on the very next page load.
- **No history.** Single KV key; each upload replaces it. There is no
  `backups/` prefix, no accumulating uploads folder.
- **Verified webhooks.** Telegram sends a secret token in a header; the Worker
  rejects anything missing or mismatched.
- **`ctx.waitUntil` work pattern.** The Worker responds to Telegram in
  milliseconds and does the heavy lifting (download, validate, KV write,
  notifications) in the background.

## Project layout

```
FOM-Dashboard/
├── worker/                      # the entire bot lives here
│   ├── src/
│   │   ├── index.ts             # router: GET / and POST /webhook
│   │   ├── webhook.ts           # secret-verified Telegram webhook
│   │   ├── upload.ts            # admin guard + download + validate + inject + KV.put + notify
│   │   ├── telegram.ts          # tiny Bot API client (getMe, getFile, sendMessage)
│   │   ├── validate.ts          # HTML upload validation
│   │   ├── inject.ts            # idempotent fullscreen-script injection
│   │   ├── dashboard.ts         # GET /  →  serves KV["index.html"]
│   │   └── env.ts               # Env type + small helpers
│   ├── wrangler.toml
│   ├── package.json
│   ├── tsconfig.json
│   └── README.md
├── scripts/
│   └── setup-webhook.sh         # one-time: point Telegram at the Worker
├── README.md
└── .gitignore
```

## First-time setup

You'll need a Cloudflare account and Node ≥18. The Worker free tier covers
this project comfortably.

### 1. Telegram

* @BotFather → `/newbot` → save the **token**.
* (Optional but recommended) @BotFather → `/newapp` → pick this bot. Set the
  **Web App URL** to your future `https://fom-dashboard.<sub>.workers.dev`
  (you'll get the exact URL from `wrangler deploy` in step 3). Pick a short
  name like `dashboard` — that will become the value of `WEBAPP_SHORT_NAME`
  in `wrangler.toml`.
* @userinfobot → save your numeric **admin user id**.
* Save the numeric **group chat id** (negative). Easiest path: add
  @RawDataBot to the group temporarily and read `message.chat.id`.

### 2. Wrangler + secrets + KV

```bash
cd worker
npm install
npx wrangler login                              # opens browser
npx wrangler kv namespace create DASHBOARD_KV   # prints an id
```

Paste the printed id into `worker/wrangler.toml` under `[[kv_namespaces]]`
(`id = "..."`).

Now register the secrets (each command prompts for a value):

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN       # the @BotFather token
npx wrangler secret put WEBHOOK_SECRET           # any random string, e.g. `openssl rand -hex 32`
npx wrangler secret put ADMIN_USER_ID            # your numeric Telegram id
npx wrangler secret put TELEGRAM_GROUP_CHAT_ID   # the negative group chat id
```

### 3. Deploy

```bash
npx wrangler deploy
```

The output ends with your permanent URL, e.g.
`https://fom-dashboard.<your-subdomain>.workers.dev`. That URL never changes.

### 4. Point Telegram at the Worker

From the project root (one level up from `worker/`):

```bash
TELEGRAM_BOT_TOKEN="<token>" \
WEBHOOK_SECRET="<same value you set with wrangler secret put>" \
  ./scripts/setup-webhook.sh "https://fom-dashboard.<sub>.workers.dev"
```

You should see `"ok":true` from Telegram and a green confirmation.

### 5. Done

Send an `.html` file to your bot from the admin account in a private chat.
You should get an "✅ Dashboard updated successfully!" reply, the group
should get a notification with a 📊 **View Dashboard** button (fullscreen
Mini App), and visiting the workers.dev URL in any browser should serve the
new file.

## Day-to-day

```bash
cd worker
npx wrangler deploy   # re-deploy after editing the code
npx wrangler tail     # stream live request logs
npx wrangler dev      # local dev server (uses real KV)
```

Changing a secret:

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN   # overwrites the previous value
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Telegram getFile failed (401): Unauthorized` | `TELEGRAM_BOT_TOKEN` is wrong. Re-set the secret. |
| Webhook receives nothing | `setup-webhook.sh` not run, or `WEBHOOK_SECRET` mismatch between Telegram and the Worker. |
| `"<!DOCTYPE html>" declaration was not found` | Add a `<!DOCTYPE html>` at the top of your file. |
| Upload says "exceeds 20 MB" | Telegram caps bot downloads at 20 MB. |
| Worker returns the placeholder page | KV still empty — first upload hasn't happened yet. |
| Mini App button shows an error | `WEBAPP_SHORT_NAME` doesn't match a real BotFather app, or the BotFather Mini App's Web App URL doesn't match the Worker URL. |
| Old dashboard still showing after upload | Hard-refresh the browser. KV is eventually consistent; in rare cases a different edge POP may serve a slightly stale copy for up to ~60s. |

## Security notes

- The bot token, webhook secret, admin id, and group id are all set with
  `wrangler secret put` and are **not** in `wrangler.toml`. They never enter
  git history.
- Every Telegram webhook request is verified by the
  `X-Telegram-Bot-Api-Secret-Token` header; mismatches get 403.
- Only `ADMIN_USER_ID` can upload; group messages are informational only.
- The Worker exposes only `/` and `/index.html`. There is no other key, no
  history, and nothing else to enumerate.
- HTML dashboards legitimately contain JavaScript, so the validator does
  **not** strip `<script>`; only upload dashboards you trust, since the
  Worker serves them verbatim.
