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

exec python3 main.py
