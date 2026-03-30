#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${GAIA_GEMINI_ENV_FILE:-$ROOT_DIR/.env.gemini.local}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Gemini env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

export GAIA_LLM_PROVIDER="${GAIA_LLM_PROVIDER:-gemini}"
export VISION_PROVIDER="${VISION_PROVIDER:-gemini}"
export GAIA_LLM_MODEL="${GAIA_LLM_MODEL:-gemini-2.5-pro}"
export VISION_MODEL="${VISION_MODEL:-$GAIA_LLM_MODEL}"

if [[ $# -eq 0 ]]; then
  echo "Usage: scripts/with_gemini_env.sh <command> [args...]" >&2
  exit 2
fi

exec "$@"
