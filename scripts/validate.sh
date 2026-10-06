#!/usr/bin/env bash
# Run the structural validator on a generated task.
# Usage: bash scripts/validate.sh output/my-task

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [ $# -lt 1 ]; then
    echo "Usage: bash scripts/validate.sh <task_dir>"
    echo "Example: bash scripts/validate.sh output/fix-a-memory-bug-in-a-c-ring-buffer"
    exit 1
fi

# Resolve the task dir relative to the repo root so callers can pass either an
# absolute path or one relative to the repo (e.g. examples/csv-normalizer).
TASK_DIR="$1"
if [ ! -d "$TASK_DIR" ] && [ -d "$ROOT_DIR/$TASK_DIR" ]; then
    TASK_DIR="$ROOT_DIR/$TASK_DIR"
fi

cd "$ROOT_DIR/validator"
PYTHON="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then PYTHON=python3; fi
"$PYTHON" validate.py "$TASK_DIR"
