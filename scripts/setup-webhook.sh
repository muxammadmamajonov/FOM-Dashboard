#!/usr/bin/env bash
#
# setup-webhook.sh — point Telegram at the deployed Worker.
#
# Run this once after `wrangler deploy`. It calls Telegram's setWebhook so
# every update goes to https://<worker>/webhook, and configures the secret
# header that the Worker uses to verify each request came from Telegram.
#
# Usage:
#   TELEGRAM_BOT_TOKEN=123:abc \
#   WEBHOOK_SECRET=<same value you `wrangler secret put`-ed> \
#     ./scripts/setup-webhook.sh https://fom-dashboard.<sub>.workers.dev

set -euo pipefail

WORKER_URL="${1:-}"
TOKEN="${TELEGRAM_BOT_TOKEN:-}"
SECRET="${WEBHOOK_SECRET:-}"

if [ -z "$WORKER_URL" ] || [ -z "$TOKEN" ] || [ -z "$SECRET" ]; then
  cat >&2 <<USAGE
Usage:
  TELEGRAM_BOT_TOKEN=<token> WEBHOOK_SECRET=<secret> \\
    $0 https://<your-worker>.workers.dev

Both env vars are required:
  TELEGRAM_BOT_TOKEN  - the same token you gave wrangler
  WEBHOOK_SECRET      - the same value you passed to \`wrangler secret put WEBHOOK_SECRET\`
USAGE
  exit 1
fi

# Trim a trailing slash so we don't end up posting to "/webhook//".
WORKER_URL="${WORKER_URL%/}"

echo "▶ Setting webhook to ${WORKER_URL}/webhook ..."
RESPONSE="$(curl -sS -X POST "https://api.telegram.org/bot${TOKEN}/setWebhook" \
  -d "url=${WORKER_URL}/webhook" \
  -d "secret_token=${SECRET}" \
  -d 'allowed_updates=["message"]' \
  -d "drop_pending_updates=true")"

echo "$RESPONSE"
if echo "$RESPONSE" | grep -q '"ok":true'; then
  echo
  echo "✅ Webhook registered."
  echo "   Test: send an .html file to your bot in a private chat."
else
  echo
  echo "❌ Telegram rejected setWebhook. See the response above."
  exit 1
fi
