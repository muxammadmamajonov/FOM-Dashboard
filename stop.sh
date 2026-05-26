#!/usr/bin/env bash
#
# stop.sh — stop everything start.sh launched (bot, cloudflared, web server).
#
# Usage:  ./stop.sh

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$PROJECT_DIR/.run"

stop_one() {
  local name="$1"
  local pidfile="$RUN_DIR/$name.pid"

  if [ ! -f "$pidfile" ]; then
    return 0
  fi

  local pid
  pid="$(cat "$pidfile" 2>/dev/null || true)"

  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "■ Stopping $name (pid $pid) ..."
    kill "$pid" 2>/dev/null || true
    # Wait up to 5s for a clean exit, then force-kill.
    for _ in $(seq 1 5); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  else
    echo "■ $name not running (clearing stale pid)"
  fi
  rm -f "$pidfile"
}

# Reverse order of startup.
stop_one bot
stop_one cloudflared
stop_one serve

echo "✅ Stopped."
