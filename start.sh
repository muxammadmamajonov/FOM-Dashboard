#!/usr/bin/env bash
#
# start.sh — launch the whole FOM Dashboard stack with one command.
#
# It starts, in order:
#   1) the local web server (serve.py)      -> serves uploads/index.html
#   2) the cloudflared quick tunnel         -> public https URL
#   3) the Telegram bot (bot.py)            -> handles uploads
#
# It auto-captures the random *.trycloudflare.com URL, writes it into .env as
# CLOUDFLARE_DOMAIN, then prints it so you can paste it into BotFather > MyApps.
#
# Usage:   ./start.sh
# Stop:    ./stop.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PORT=8080
VENV_PY="$PROJECT_DIR/.venv/bin/python"
ENV_FILE="$PROJECT_DIR/.env"
RUN_DIR="$PROJECT_DIR/.run"
LOG_DIR="$PROJECT_DIR/logs"

# --------------------------------------------------------------- preconditions
if [ ! -x "$VENV_PY" ]; then
  echo "❌ .venv not found. Create it first:"
  echo "   python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "❌ .env not found. Create it:  cp .env.example .env  (then fill it in)"
  exit 1
fi
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "❌ cloudflared is not installed.  brew install cloudflared"
  exit 1
fi

mkdir -p "$RUN_DIR" "$LOG_DIR" "$PROJECT_DIR/uploads/backups"

# Stop anything from a previous run so we start clean.
if [ -x "$PROJECT_DIR/stop.sh" ]; then
  bash "$PROJECT_DIR/stop.sh" >/dev/null 2>&1 || true
fi

# Honour a custom UPLOAD_FOLDER_PATH from .env (fallback to ./uploads).
UPLOAD_DIR="$(grep -E '^UPLOAD_FOLDER_PATH=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
UPLOAD_DIR="${UPLOAD_DIR:-./uploads}"

# ------------------------------------------------------------- 1) web server
echo "▶ Starting local web server on http://127.0.0.1:$PORT ..."
nohup "$VENV_PY" "$PROJECT_DIR/serve.py" --port "$PORT" --dir "$UPLOAD_DIR" \
  > "$LOG_DIR/serve.log" 2>&1 &
echo $! > "$RUN_DIR/serve.pid"

# -------------------------------------------------------------- 2) cloudflared
echo "▶ Starting cloudflared tunnel ..."
: > "$LOG_DIR/cloudflared.log"
nohup cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" \
  > "$LOG_DIR/cloudflared.log" 2>&1 &
echo $! > "$RUN_DIR/cloudflared.pid"

# ------------------------------------------------------- 3) wait for the URL
printf "▶ Waiting for the public tunnel URL "
URL=""
for _ in $(seq 1 40); do
  URL="$(grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' \
        "$LOG_DIR/cloudflared.log" | head -n1 || true)"
  [ -n "$URL" ] && break
  printf "."
  sleep 1
done
printf "\n"

if [ -z "$URL" ]; then
  echo "❌ Could not obtain a tunnel URL. Check logs/cloudflared.log"
  echo "   (trycloudflare can be rate-limited — try ./start.sh again in a moment)"
  bash "$PROJECT_DIR/stop.sh" >/dev/null 2>&1 || true
  exit 1
fi
echo "✔ Tunnel URL: $URL"

# --------------------------------------------------- 4) write URL into .env
if grep -q '^CLOUDFLARE_DOMAIN=' "$ENV_FILE"; then
  tmp="$(mktemp)"
  sed "s|^CLOUDFLARE_DOMAIN=.*|CLOUDFLARE_DOMAIN=$URL|" "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
else
  printf '\nCLOUDFLARE_DOMAIN=%s\n' "$URL" >> "$ENV_FILE"
fi
echo "✔ Wrote CLOUDFLARE_DOMAIN into .env"

# --------------------------------------------------------------- 5) the bot
# Started AFTER .env is updated, because the bot reads config once at startup.
echo "▶ Starting Telegram bot ..."
nohup "$VENV_PY" "$PROJECT_DIR/bot.py" > "$LOG_DIR/bot.log" 2>&1 &
echo $! > "$RUN_DIR/bot.pid"

# Give the bot a couple of seconds, then confirm it didn't crash on startup.
sleep 3
BOT_PID="$(cat "$RUN_DIR/bot.pid")"
if ! kill -0 "$BOT_PID" 2>/dev/null; then
  echo "❌ Bot failed to start. Last lines of logs/bot.log:"
  echo "---------------------------------------------------"
  tail -n 20 "$LOG_DIR/bot.log" || true
  echo "---------------------------------------------------"
  bash "$PROJECT_DIR/stop.sh" >/dev/null 2>&1 || true
  exit 1
fi

# ------------------------------------------------------------------- summary
cat <<BANNER

==================================================================
 ✅ Everything is running.

   👉 DASHBOARD URL (paste this into BotFather):

        $URL

 In Telegram → @BotFather → /myapps → select your app →
   "Edit Web App URL" → paste the URL above.
 (This makes the fullscreen 📊 View Dashboard button work.)

 The browser-fallback button is already updated automatically.

 Logs:   logs/serve.log   logs/cloudflared.log   logs/bot.log
 Stop:   ./stop.sh
==================================================================

BANNER
