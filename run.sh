#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Missing .env file."
  echo "Copy .env.example to .env and fill in your local values."
  exit 1
fi

set -a
. ./.env
set +a

if [[ "$ORCHESTRATOR_RUNTIME_DIR" != /* ]]; then
  echo "ORCHESTRATOR_RUNTIME_DIR must be an absolute path."
  exit 1
fi

case "$ORCHESTRATOR_RUNTIME_DIR/" in
  "$PWD/"*)
    echo "ORCHESTRATOR_RUNTIME_DIR must be outside the orchestrator source directory."
    exit 1
    ;;
esac

mkdir -p "$ORCHESTRATOR_RUNTIME_DIR/input" \
  "$ORCHESTRATOR_RUNTIME_DIR/review" \
  "$ORCHESTRATOR_RUNTIME_DIR/output" \
  "$ORCHESTRATOR_RUNTIME_DIR/logs"

exec python3 main.py
