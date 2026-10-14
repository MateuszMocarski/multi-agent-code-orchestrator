#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -n "${ORCHESTRATOR_ENV_FILE+x}" ]]; then
  env_file="$ORCHESTRATOR_ENV_FILE"
  if [[ ! -f "$env_file" ]]; then
    echo "Environment file not found: $env_file" >&2
    exit 1
  fi
else
  env_file="$PWD/.env"
  if [[ -f "$env_file" ]]; then
    set -a
    . "$env_file"
    set +a
  fi
fi

if [[ -n "${ORCHESTRATOR_ENV_FILE+x}" ]]; then
  set -a
  . "$env_file"
  set +a
fi

exec python3 -m orchestrator.image "$@"
