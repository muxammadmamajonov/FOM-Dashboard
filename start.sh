#!/usr/bin/env bash
#
# start.sh — launch the Telegram bot in the background.
#
# In the hybrid Worker setup the public dashboard URL is served by the
# Cloudflare Worker under `worker/` (deployed once with `wrangler deploy`),
# so this script no longer starts the local web server or cloudflared.
# It just runs the Python bot, which writes uploads to R2.
#
# Usage:   ./start.sh
# Stop:    ./stop.sh
# Logs:    tail -f logs/bot.log    (or logs/app.log for structured logs)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV_PY="$PROJECT_DIR/.venv/bin/python"
ENV_FILE="$PROJECT_DIR/.env"
RUN_DIR="$PROJECT_DIR/.run"
LOG_DIR="$PROJECT_DIR/logs"

if [ ! -x "$VENV_PY" ]; then
  echo "❌ .venv not found. Create it first:"
  echo "   python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "❌ .env not found. Create it:  cp .env.example .env  (then fill it in)"
  exit 1
fi

mkdir -p "$RUN_DIR" "$LOG_DIR"

# Stop anything from a previous run so we start clean.
if [ -x "$PROJECT_DIR/stop.sh" ]; then
  bash "$PROJECT_DIR/stop.sh" >/dev/null 2>&1 || true
fi

echo "▶ Starting Telegram bot ..."
nohup "$VENV_PY" bot.py > "$LOG_DIR/bot.log" 2>&1 &
echo $! > "$RUN_DIR/bot.pid"

# Give the bot a moment, then confirm it didn't crash on startup.
sleep 3
BOT_PID="$(cat "$RUN_DIR/bot.pid")"
if ! kill -0 "$BOT_PID" 2>/dev/null; then
  echo "❌ Bot failed to start. Last lines of logs/bot.log:"
  echo "---------------------------------------------------"
  tail -n 30 "$LOG_DIR/bot.log" || true
  echo "---------------------------------------------------"
  rm -f "$RUN_DIR/bot.pid"
  exit 1
fi

cat <<BANNER

==================================================================
 ✅ Bot is running (pid $BOT_PID).

 Public dashboard URL (deployed by the Cloudflare Worker):
   $(grep -E '^CLOUDFLARE_DOMAIN=' "$ENV_FILE" | head -n1 | cut -d= -f2-)

 Logs:  logs/bot.log   logs/app.log
 Stop:  ./stop.sh
==================================================================

BANNER
