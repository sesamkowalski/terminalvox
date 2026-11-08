#!/bin/bash
# One command: a dedicated Claude Code session in tmux (if there isn't one yet) + the voice server.
# The server listens on 127.0.0.1 only; from other devices it is reachable via `tailscale serve` (HTTPS in your tailnet).
set -e
cd "$(dirname "$0")"
[ -f .env ] || { echo "no .env: copy .env.example to .env and set ELEVENLABS_API_KEY"; exit 1; }
set -a; . ./.env; set +a

SESSION=${CLAUDE_TMUX_SESSION:-voice}
PORT=${PORT:-7860}
./start-session.sh "$SESSION"
export TMUX_TARGET=${TMUX_TARGET:-=$SESSION:0.0}  # "=": exact session name, no prefix matching

if [ "${TAILSCALE_SERVE:-1}" = 0 ]; then
  echo "tailscale serve skipped (TAILSCALE_SERVE=0)"
elif command -v tailscale >/dev/null && {
  # Right after boot tailscaled may not be connected yet: wait up to a minute.
  for _ in $(seq 60); do tailscale status >/dev/null 2>&1 && break; sleep 1; done
  tailscale status >/dev/null 2>&1
}; then
  # If Serve/HTTPS isn't enabled in the tailnet yet, the command prints a link and waits for it.
  timeout 120 tailscale serve --bg --https=443 "http://127.0.0.1:$PORT" \
    && echo "Open: https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')/" \
    || echo "tailscale serve is not set up: see README"
else
  echo "Tailscale is not connected: access from this machine only (http://localhost:$PORT/)"
fi

exec uv run bot.py -t webrtc --host 127.0.0.1 --port "$PORT"
