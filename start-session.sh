#!/bin/bash
# Starts a dedicated Claude Code session for voice in tmux, unless it is already running.
# Usage: ./start-session.sh [tmux-session-name] [working-directory]
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
NAME=${1:-${CLAUDE_TMUX_SESSION:-voice}}
CWD=${2:-${CLAUDE_CWD:-$HOME}}
if tmux has-session -t "=$NAME" 2>/dev/null; then
  echo "tmux session $NAME already exists"
else
  # Voice mode rules live in ~/.claude/CLAUDE.md (see voice-rules.md); the bridge turns voice mode on itself.
  tmux new-session -d -s "$NAME" -c "$CWD" -x 200 -y 50 claude -n "$NAME"
  echo "started claude in tmux session $NAME (directory $CWD)"
fi
