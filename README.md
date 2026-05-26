# FOM Dashboard Telegram Bot

A small, production-oriented Telegram bot that turns a private chat into a
lightweight deployment pipeline for a static HTML dashboard.

The administrator sends an HTML file to the bot. The bot validates it, backs up
the previous version, atomically installs it as the live `uploads/index.html`,
acknowledges the admin, and posts a "dashboard refreshed" message — with a
button linking to the live URL — to a team group.

```
Admin ──HTML file──▶ Bot ──validate──▶ backup ──atomic save──▶ ✅ to admin
                                                    │
                                                    └──▶ ✅ notification to group  [📊 View Dashboard]
```

## Features

- **Admin-only uploads** — every upload is checked against `ADMIN_USER_ID`.
- **Thorough validation** — extension, MIME, size bounds, UTF-8 encoding, and a
  `<!DOCTYPE html>` declaration.
- **Atomic saves** — files are streamed to a temp file then `os.replace`-d, so a
  crash mid-write never corrupts the live dashboard.
- **Rotating backups** — timestamped, content-hashed backups; the latest 10 are
  kept and duplicate uploads are detected.
- **Resilient notifications** — exponential backoff and rate-limit handling for
  the group message; the admin is alerted if delivery ultimately fails.
- **Structured logging** — console + daily-rotating file logs, correlated per
  upload with a short request id.
- **Graceful shutdown** — SIGINT/SIGTERM stop polling and close the session
  cleanly.

## Project layout

```
FOM-Dashboard/
├── bot.py                 # Entry point: startup, DI, graceful shutdown
├── telegram_handlers.py   # Handlers: upload, group notification, commands
├── file_manager.py        # Validation, atomic save, backup rotation
├── config.py              # Env-based configuration + validation
├── utils.py               # Stateless helpers (hashing, timestamps, paths)
├── logger.py              # Logging setup
├── requirements.txt
├── .env.example
├── .gitignore
├── uploads/               # Created at runtime (index.html + backups/)
└── logs/                  # Created at runtime (app.log)
```

## Requirements

- Python 3.9+
- A Telegram bot token

## Setup

```bash
# 1. (recommended) create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. create your config
cp .env.example .env
chmod 600 .env                     # keep secrets readable only by you
$EDITOR .env                       # fill in the values below
```

## Configuration

All settings come from environment variables (loaded from `.env`). The bot
validates every value at startup and refuses to run if anything is missing or
malformed.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | — | From @BotFather, format `<id>:<hash>`. |
| `ADMIN_USER_ID` | yes | — | Positive integer Telegram user id. |
| `TELEGRAM_GROUP_CHAT_ID` | yes | — | Negative chat id of the group. |
| `CLOUDFLARE_DOMAIN` | yes | — | Public dashboard URL (`http`/`https`). |
| `UPLOAD_FOLDER_PATH` | no | `./uploads` | Where files/backups are stored. |
| `BACKUP_ENABLED` | no | `false` | `true` to keep previous versions. |
| `MAX_FILE_SIZE` | no | `52428800` | Max upload size in bytes (50 MB). |
| `WEBAPP_FULLSCREEN_ENABLED` | no | `true` | Inject the fullscreen Mini App script into saved dashboards. |
| `WEBAPP_SHORT_NAME` | no | _(empty)_ | BotFather Mini App short name → group button opens fullscreen. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. |

### How to get each value

**Bot token** — message [@BotFather](https://t.me/BotFather), send `/newbot`,
follow the prompts, and copy the token it returns.

**Admin user id** — message [@userinfobot](https://t.me/userinfobot); it replies
with your numeric `Id`.

**Group chat id** — add your bot to the group, then either:
- temporarily add [@RawDataBot](https://t.me/RawDataBot) and read
  `message.chat.id` (a negative number, supergroups start with `-100`), or
- post a message in the group and open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` to find `chat.id`.

> For the bot to *see* normal group messages you may need to disable privacy
> mode via BotFather (`/setprivacy` → Disable). It is **not** required to *send*
> notifications to the group, which is all this bot does there.

**Cloudflare domain** — the public URL where you serve `uploads/index.html`
(e.g. a Cloudflare Tunnel pointing at a local web server hosting that file).

## Fullscreen dashboard (Telegram Mini App)

By default the bot injects the Telegram Web App SDK plus a fullscreen bootstrap
into every dashboard it saves, so when the page is opened **as a Mini App** it
calls `ready()`, `expand()`, and `requestFullscreen()` (Bot API 8.0+, works on
both mobile and Telegram Desktop). In a normal browser the script is inert, so
the dashboard still renders fine.

> Important: the fullscreen API only exists when the page is opened *inside
> Telegram as a Mini App*. A plain `url` button opens a browser, where Telegram
> can't control fullscreen. `web_app`-type buttons aren't allowed in groups, so
> the group button must be a Mini App **direct link** — which requires a
> one-time BotFather registration.

### One-time BotFather setup

1. Open [@BotFather](https://t.me/BotFather) → `/newapp` → choose your bot.
2. Provide a title, description, and a 640×360 photo when prompted.
3. **Web App URL**: set it to your `CLOUDFLARE_DOMAIN` (the public dashboard URL).
4. **Short name**: choose something like `dashboard` (letters/digits/underscore).
5. Put that exact short name in `.env` as `WEBAPP_SHORT_NAME=dashboard`.

The bot auto-detects its own `@username`, so the group button becomes
`https://t.me/<your_bot>/dashboard`. Clicking it opens the dashboard as a
fullscreen Mini App; a secondary **🌐 Open in Browser** button is included as a
fallback.

> Because the Mini App URL lives in BotFather, a **changing** Cloudflare quick-
> tunnel URL means re-running BotFather's `/editapp` each time. This is the main
> reason to use a stable **named tunnel** (see below) once you go past testing.
> If you leave `WEBAPP_SHORT_NAME` empty, the bot falls back to a single plain
> browser button (no fullscreen).

## Running

```bash
python bot.py
```

You should see startup logs ending in `Bot started and polling...`. Send an
`.html` file to the bot from the admin account to trigger an update.

### Run it as a service (systemd)

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

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fom-dashboard-bot
sudo journalctl -u fom-dashboard-bot -f
```

## Monitoring logs

- **Console** — INFO and above (whatever runs the process / `journalctl`).
- **File** — `logs/app.log`, DEBUG and above, rotated daily, 7 days retained.

```bash
tail -f logs/app.log
```

Each upload is tagged with a short request id (e.g. `[a1b2c3d4]`) so you can
follow one upload from "File received" through validation, save, and group
notification.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `FATAL: Invalid configuration` on startup | A required env var is missing/malformed; the message lists every problem. |
| `TELEGRAM_BOT_TOKEN is invalid` | Wrong/expired token; re-copy it from BotFather. |
| Upload says "could not download… 20 MB" | Telegram caps bot downloads at 20 MB regardless of `MAX_FILE_SIZE`. |
| `<!DOCTYPE html> declaration not found` | Add a `<!DOCTYPE html>` at the top of your file. |
| Group notification fails / admin gets a warning | Check `TELEGRAM_GROUP_CHAT_ID` is correct and the bot is a member of the group. |
| Non-admin can't upload | Expected — only `ADMIN_USER_ID` may upload. |
| Backups not appearing | Set `BACKUP_ENABLED=true`. |

## Security notes

- Keep `.env` out of version control (it is in `.gitignore`) and `chmod 600` it.
- The bot token is never logged (the config summary redacts it).
- Only the configured admin can upload; group messages are informational only.
- Uploaded filenames are sanitized to prevent directory traversal.
- HTML files legitimately contain JavaScript, so the validator intentionally
  does **not** strip `<script>`; it only verifies the file is well-formed text.
  Only upload dashboards you trust, since they are served verbatim.
```

## License

Internal project — add a license here if distributing.
