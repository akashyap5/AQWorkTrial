#!/usr/bin/env bash
# Generate a single task from a topic string.
# Usage: bash scripts/generate.sh "fix a memory bug in a C ring buffer"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [ $# -lt 1 ]; then
    echo "Usage: bash scripts/generate.sh <topic>"
    echo "Example: bash scripts/generate.sh \"fix a memory bug in a C ring buffer\""
    exit 1
fi

cd "$ROOT_DIR/generator"
PYTHON="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then PYTHON=python3; fi
"$PYTHON" generate.py "$@"
