# FOM Dashboard Telegram Bot

A small, production-oriented Telegram bot that turns a private chat into a
lightweight deployment pipeline for a static HTML dashboard.

The administrator sends an HTML file to the bot. The bot validates it, injects
the Telegram Mini App fullscreen bootstrap, atomically uploads it to
**Cloudflare R2**, rotates a small history of backups, and posts a "dashboard
refreshed" notification — with a Mini App button — to a team group. A tiny
**Cloudflare Worker** in [`worker/`](worker/README.md) reads the same bucket
and serves the dashboard at a permanent `*.workers.dev` URL.

```
                            ┌──── group notification (📊 / 🌐) ─►  Telegram group
Admin ──HTML──► Telegram bot ─┤
                            └─► R2 bucket  ◄── Cloudflare Worker ──►  https://fom-dashboard.<sub>.workers.dev
                                  ▲                                          ▲
                                  └── backups/index_TIMESTAMP_HASH.html      └── CEO opens here, fullscreen
```

Because R2 is the storage and the Worker is the public URL, the bot itself
can run anywhere; the dashboard URL never changes; and there is no local
web server or cloudflared tunnel to keep alive.

## Features

- **Admin-only uploads** — every upload is checked against `ADMIN_USER_ID`.
- **Thorough validation** — extension, MIME, size bounds, UTF-8 encoding, and
  a `<!DOCTYPE html>` declaration.
- **Auto-fullscreen injection** — every saved dashboard gets the Telegram Web
  App SDK plus a `requestFullscreen()` bootstrap (Bot API 8.0+, mobile +
  desktop). The script is inert in a normal browser.
- **R2 storage, atomic writes** — each upload is a single `PutObject`; the
  previous version is first server-side-copied to `backups/` and only deleted
  during rotation.
- **Rotating backups** — newest 10 kept; duplicate uploads are detected via a
  content hash stored as object metadata.
- **Mini App link** — auto-built `t.me/<bot>/<short_name>` button + 🌐 browser
  fallback.
- **Resilient notifications** — exponential backoff and rate-limit handling.
- **Structured logging** — console + daily-rotating file logs, per-upload
  request id for easy log tracing.
- **Graceful shutdown** — SIGINT/SIGTERM stop polling and close the session.

## Project layout

```
FOM-Dashboard/
├── bot.py                 # Entry point: startup, DI, graceful shutdown
├── telegram_handlers.py   # Handlers: upload, group notification, commands
├── file_manager.py        # Validation, fullscreen injection, R2 upload, backups
├── config.py              # Env-based configuration + validation
├── utils.py               # Stateless helpers (hashing, timestamps, injection)
├── logger.py              # Logging setup
├── requirements.txt
├── start.sh / stop.sh     # Run/stop the bot in the background
├── .env.example
└── worker/                # Cloudflare Worker (serves dashboard from R2)
    ├── src/index.ts
    ├── wrangler.toml
    ├── package.json
    └── README.md
```

## First-time setup

You'll set up two things: the **Cloudflare Worker + R2 bucket** (one-time),
and the **Python bot** (also one-time, then run anywhere).

### 1. Worker + R2 bucket

```bash
cd worker
npm install                                  # wrangler + types
npx wrangler login                           # opens browser, authenticates
npx wrangler r2 bucket create fom-dashboard  # creates the bucket
npx wrangler deploy                          # publishes the Worker
```

Note the URL `wrangler deploy` prints (e.g.
`https://fom-dashboard.<your-subdomain>.workers.dev`). It's permanent.

Also create an R2 API token for the Python bot: Cloudflare dashboard → **R2** →
**Manage R2 API Tokens** → **Create API Token** → Object **Read & Write** on
the bucket → copy the **Access Key Id** and **Secret Access Key**. Grab your
**Account Id** from the right side of the R2 page.

### 2. Python bot

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
$EDITOR .env    # see the table below
```

Required `.env` keys:

| Variable | Where it comes from |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` |
| `ADMIN_USER_ID` | @userinfobot — your numeric id |
| `TELEGRAM_GROUP_CHAT_ID` | The negative chat id of the team group |
| `CLOUDFLARE_DOMAIN` | The `*.workers.dev` URL `wrangler deploy` printed |
| `R2_ACCOUNT_ID` | Cloudflare R2 page (top-right) |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | R2 API token you just created |
| `R2_BUCKET_NAME` | `fom-dashboard` (matches the bucket above) |
| `WEBAPP_SHORT_NAME` _(optional)_ | BotFather Mini App short name (see below) |

Optional keys (all sensible defaults): `BACKUP_ENABLED`, `MAX_FILE_SIZE`,
`LOG_LEVEL`, `WEBAPP_FULLSCREEN_ENABLED`.

### 3. Fullscreen Mini App (optional but recommended)

For the **📊 View Dashboard** button to open *fullscreen inside Telegram* it
must launch a Mini App (`web_app`-type buttons aren't allowed in groups; only
a `t.me/<bot>/<short_name>` direct link is). One-time BotFather setup:

1. @BotFather → `/newapp` → choose your bot.
2. Title, description, 640×360 photo.
3. **Web App URL**: your `CLOUDFLARE_DOMAIN` (the workers.dev URL).
4. **Short name**: e.g. `dashboard`. Put it in `.env` as `WEBAPP_SHORT_NAME=dashboard`.

Because the Worker URL never changes, this is set once and forgotten.

If you leave `WEBAPP_SHORT_NAME` empty, the group button is a single plain
browser link (no fullscreen).

## Running

```bash
./start.sh    # runs the bot in the background, writes logs/bot.log
./stop.sh     # stops it
```

`start.sh` checks `.venv` and `.env` exist, then starts `bot.py`. It prints
the dashboard URL it read from `.env`.

### Or run it as a service (systemd)

```ini
# /etc/systemd/system/fom-dashboard-bot.service
[Unit]
Description=FOM Dashboard Telegram Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/FOM-Dashboard
ExecStart=/opt/FOM-Dashboard/.venv/bin/python bot.py
Restart=always
RestartSec=5
User=fombot

[Install]
WantedBy=multi-user.target
```

## How an upload flows

1. Admin sends an `.html` file to the bot privately.
2. Bot downloads it to a tempfile and validates structure/size/encoding.
3. Bot injects the Telegram Mini App fullscreen script (idempotent).
4. Bot computes SHA-256, HEADs the current `index.html` in R2:
   - if hashes match → nothing changed, no backup;
   - otherwise → server-side `CopyObject` to `backups/index_TIMESTAMP_HASH8.html`.
5. Bot `PutObject`s the new `index.html` with `content-sha256` in metadata
   (also `Cache-Control: no-store` so the Worker hands it out fresh).
6. Old backups beyond 10 are deleted.
7. Bot replies "✅ Dashboard updated successfully" to the admin.
8. Bot posts the refresh notification + buttons to the group.
9. Worker continues serving `GET /` → the new file is live immediately.

Each upload gets an 8-char request id (e.g. `[a1b2c3d4]`) so you can follow
one upload from "File received" → R2 PutObject → group notification in
`logs/app.log`.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `FATAL: Invalid configuration` on startup | The message lists every problem; most often missing `R2_*` keys or `CLOUDFLARE_DOMAIN`. |
| `R2 ClientError NoSuchBucket` | Run `npx wrangler r2 bucket create fom-dashboard` (or whatever your `R2_BUCKET_NAME` is). |
| `R2 ClientError InvalidAccessKeyId` / `SignatureDoesNotMatch` | Wrong `R2_*` keys; recreate the R2 API token. |
| Upload says "could not download… 20 MB" | Telegram caps bot downloads at 20 MB regardless of `MAX_FILE_SIZE`. |
| `<!DOCTYPE html> declaration not found` | Add a `<!DOCTYPE html>` at the top of your file. |
| Worker returns the placeholder page | The bucket has no `index.html` yet — upload one. |
| Mini App button opens an error | `WEBAPP_SHORT_NAME` doesn't match a real BotFather app, or the app's Web App URL doesn't match `CLOUDFLARE_DOMAIN`. |
| Backups not appearing | Set `BACKUP_ENABLED=true`. |

## Security notes

- `.env` (with the bot token and R2 secret) is in `.gitignore` and should be
  `chmod 600`.
- The bot token and R2 secret are redacted from the startup log line.
- Only `ADMIN_USER_ID` can upload; group messages are informational only.
- Filenames from Telegram are sanitised; only the basename is used.
- The Worker exposes **only** `index.html` — `backups/...` is never reachable.
- HTML dashboards legitimately contain JavaScript, so the validator does
  **not** strip `<script>`; only upload dashboards you trust, since the
  Worker serves them verbatim.
