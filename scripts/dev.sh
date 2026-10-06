#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo 'Create .venv with Python 3.12+ and install requirements.txt first.' >&2
  exit 1
fi
export OPENROUTER_API_BASE="${OPENROUTER_API_BASE:-https://api.aqinference.com/v1}"
export PATH="$PWD/.venv/bin:$PATH"
PORT="${TASKLAB_PORT:-8000}"
.venv/bin/python -c 'import socket,sys; s=socket.socket(); s.bind(("127.0.0.1",int(sys.argv[1]))); s.close()' "$PORT"
.venv/bin/python -m runner.service &
WORKER_PID=$!
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port "$PORT" &
API_PID=$!
cleanup() {
  kill -TERM "$API_PID" "$WORKER_PID" 2>/dev/null || true
  wait "$API_PID" "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM
echo "Task Lab: http://127.0.0.1:$PORT"
echo 'Stopping the UI leaves active Harbor trials running; restarting reconnects to their results.'
wait -n 2>/dev/null || while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WORKER_PID" 2>/dev/null; do sleep 1; done
