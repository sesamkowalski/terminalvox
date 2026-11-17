#!/bin/bash
# Starts a dedicated Claude Code session for voice in tmux or herdr, unless it is already running.
# Usage: ./start-session.sh [session-name] [working-directory]
# Prints the bot's target for it (MUX_TARGET) on stdout; messages go to stderr.
#   CLAUDE_MUX: auto (default: tmux if installed, else herdr), tmux, herdr, or none (no dedicated session).
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
NAME=${1:-${CLAUDE_SESSION:-${CLAUDE_TMUX_SESSION:-voice}}}
CWD=${2:-${CLAUDE_CWD:-$HOME}}
MUX=${CLAUDE_MUX:-auto}
say() { echo "$@" >&2; }

if [ "$MUX" = auto ]; then
  if command -v tmux >/dev/null; then MUX=tmux
  elif command -v herdr >/dev/null; then MUX=herdr
  else say "neither tmux nor herdr is installed: install one of them (see README)"; exit 1
  fi
fi

# In a folder it hasn't seen, claude first asks whether to trust it, and the session isn't usable
# (nor listed) until someone answers. The answer is the user's, so only point at it.
check_trust() {  # $1: screen text, $2: how to get to the session
  sleep 4
  if eval "$1" | grep -q "trust this folder"; then
    say "claude asks whether to trust $CWD: $2 and choose \"Yes, I trust this folder\" once."
  fi
}

case "$MUX" in
none)
  say "no dedicated session (CLAUDE_MUX=none): pick a session on the page" ;;
tmux)
  if tmux has-session -t "=$NAME" 2>/dev/null; then
    say "tmux session $NAME already exists"
  else
    # Voice mode rules live in ~/.claude/CLAUDE.md (see voice-rules.md); the bridge turns voice mode on itself.
    tmux new-session -d -s "$NAME" -c "$CWD" -x 200 -y 50 claude -n "$NAME"
    say "started claude in tmux session $NAME (directory $CWD)"
    check_trust "tmux capture-pane -p -t =$NAME:0.0" "run \`tmux attach -t $NAME\`"
  fi
  echo "=$NAME:0.0" ;;  # "=": exact session name, no prefix matching
herdr)
  command -v herdr >/dev/null || { say "CLAUDE_MUX=herdr, but herdr is not installed"; exit 1; }
  json() { python3 -c "import json,sys; d=json.load(sys.stdin)['result']; print($1)"; }
  # The server runs with the herdr app; without it, start a headless one (it outlives this script and,
  # like the app, restores the saved workspaces). Not with HERDR_SOCKET_PATH: that moves only the
  # socket, the data stays the default session's, so a second server would resume every saved claude
  # session again and overwrite session.json. A separate session is `herdr --session NAME`.
  if ! herdr workspace list >/dev/null 2>&1; then
    if [ -n "${HERDR_SOCKET_PATH:-}" ]; then
      say "no herdr server at HERDR_SOCKET_PATH=$HERDR_SOCKET_PATH: start it yourself"; exit 1
    fi
    nohup herdr server >/dev/null 2>&1 &
    for _ in $(seq 50); do herdr workspace list >/dev/null 2>&1 && break; sleep 0.2; done
    say "started a headless herdr server"
  fi
  WS=$(herdr workspace list | json "next((w['workspace_id'] for w in d['workspaces'] if w['label'] == '$NAME'), '')")
  if [ -n "$WS" ]; then
    PANE=$(herdr pane list --workspace "$WS" | json "d['panes'][0]['pane_id']")
    AGENT=$(herdr pane get "$PANE" | json "d['pane'].get('agent', '')")
    say "herdr workspace $NAME already exists ($PANE)"
  else
    PANE=$(herdr workspace create --cwd "$CWD" --label "$NAME" --no-focus | json "d['root_pane']['pane_id']")
    AGENT=
  fi
  if [ "$AGENT" != claude ]; then
    herdr pane run "$PANE" "claude -n $NAME" >/dev/null
    say "started claude in herdr workspace $NAME, pane $PANE (directory $CWD)"
    check_trust "herdr pane read $PANE --source visible" "open herdr (\`herdr\`), go to the workspace $NAME"
  fi
  echo "$PANE" ;;
*)
  say "CLAUDE_MUX=$MUX: expected auto, tmux, herdr or none"; exit 1 ;;
esac
