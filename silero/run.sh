#!/bin/bash
# The Silero synthesis service (see server.py). Not `exec uv run`: snap uv would put python outside the unit.
set -e
cd "$(dirname "$0")"
[ -f ../.env ] && { set -a; . ../.env; set +a; }
uv sync --quiet
exec .venv/bin/python server.py
