#!/bin/sh
set -eu

cd "$(dirname "$0")"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

env_value() {
  key="$1"
  sed -n "s/^[[:space:]]*${key}=//p" .env | tail -n 1 | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//'
}

command -v docker >/dev/null 2>&1 || fail "Docker is not available. Start QNAP Container Station first."
docker info >/dev/null 2>&1 || fail "Docker is not accessible. Run: sudo ./startup.sh"
docker compose version >/dev/null 2>&1 || fail "Docker Compose V2 is required (docker compose)."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example." >&2
  echo "Fill in DISCORD_TOKEN, GUILD_ID and OPENROUTER_API_KEY, then run startup.sh again." >&2
  exit 2
fi

DISCORD_TOKEN_VALUE="$(env_value DISCORD_TOKEN)"
GUILD_ID_VALUE="$(env_value GUILD_ID)"
NARRATOR_PROVIDER_VALUE="$(env_value NARRATOR_PROVIDER)"
OPENROUTER_KEY_VALUE="$(env_value OPENROUTER_API_KEY)"
DASHBOARD_ENABLED_VALUE="$(env_value DASHBOARD_ENABLED)"

[ -n "$DISCORD_TOKEN_VALUE" ] || fail "DISCORD_TOKEN is empty in .env"
[ -n "$GUILD_ID_VALUE" ] || fail "GUILD_ID is empty in .env"

case "$NARRATOR_PROVIDER_VALUE" in
  ""|openrouter)
    [ -n "$OPENROUTER_KEY_VALUE" ] || fail "OPENROUTER_API_KEY is empty while NARRATOR_PROVIDER=openrouter"
    ;;
  procedural|disabled|openai)
    ;;
  *)
    fail "Unsupported NARRATOR_PROVIDER=$NARRATOR_PROVIDER_VALUE. This NAS build has no local LLM provider."
    ;;
esac

mkdir -p data

echo "Starting Xianxia RP on QNAP..."
echo "  Engine:     Go authoritative game engine + SQLite WAL"
echo "  Bot:        Python Discord/RAG frontend"
echo "  Narration:  OpenRouter free cloud fallback chain"

case "$(printf '%s' "$DASHBOARD_ENABLED_VALUE" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    echo "  Dashboard:  enabled"
    docker compose --profile dashboard up -d --build --remove-orphans "$@"
    ;;
  *)
    echo "  Dashboard:  disabled"
    docker compose up -d --build --remove-orphans "$@"
    ;;
esac

echo
docker compose --profile dashboard ps
echo
echo "Xianxia RP startup complete."
echo "Engine logs:    docker compose logs -f --tail=150 xianxia-engine"
echo "Bot logs:       docker compose logs -f --tail=150 xianxia-bot"
echo "Database init:  docker compose logs xianxia-db-init"
if [ "${DASHBOARD_ENABLED_VALUE:-false}" = "true" ]; then
  echo "Dashboard logs: docker compose --profile dashboard logs -f --tail=150 xianxia-dashboard"
fi
