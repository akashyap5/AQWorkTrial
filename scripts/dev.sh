#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo 'Create .venv with Python 3.12+ and install requirements.txt first.' >&2
  exit 1
fi
export OPENROUTER_API_BASE="${OPENROUTER_API_BASE:-https://api.aqinference.com/v1}"
export PATH="$PWD/.venv/bin:$PATH"
TASKLAB_BIND_PORT="${TASKLAB_PORT:-8000}"
.venv/bin/python -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(("127.0.0.1",int(sys.argv[1]))); s.close()' "$TASKLAB_BIND_PORT"
TASKLAB_CHILDREN=()
cleanup() {
  if [ "${#TASKLAB_CHILDREN[@]}" -gt 0 ]; then
    kill -TERM "${TASKLAB_CHILDREN[@]}" 2>/dev/null || true
    wait "${TASKLAB_CHILDREN[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 0' INT TERM
.venv/bin/python -m runner.service &
WORKER_PID=$!
TASKLAB_CHILDREN+=("$WORKER_PID")
.venv/bin/python -m phase2.controller --worker &
PHASE2_PID=$!
TASKLAB_CHILDREN+=("$PHASE2_PID")
.venv/bin/python -m phase2.search --worker &
SEARCH_PID=$!
TASKLAB_CHILDREN+=("$SEARCH_PID")
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port "$TASKLAB_BIND_PORT" &
API_PID=$!
TASKLAB_CHILDREN+=("$API_PID")
echo "Task Lab: http://127.0.0.1:$TASKLAB_BIND_PORT"
echo 'Use Stop search or Stop batch before shutting down; interrupted paid work is not automatically retried.'
# Bash 3.2 (the macOS default) has no wait -n. Stop all services if one exits.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WORKER_PID" 2>/dev/null && kill -0 "$PHASE2_PID" 2>/dev/null && kill -0 "$SEARCH_PID" 2>/dev/null; do sleep 1; done
