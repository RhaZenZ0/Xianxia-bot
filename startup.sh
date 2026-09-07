#!/bin/sh
set -eu

cd "$(dirname "$0")"

# `./startup.sh --check-env` validates .env against THIS release's requirements
# and exits without starting anything (0 = ok, 1 = a requirement is missing).
# update.sh runs the staged release's copy against the installed .env before
# it stops the running stack, so a key a newer release needs is a message,
# not a stop / fail / roll back / restart cycle (v0.20.5).
CHECK_ENV_ONLY=0
ENV_FILE=.env
case "${1:-}" in
  --check-env)
    CHECK_ENV_ONLY=1
    ENV_FILE=${2:-.env}
    ;;
esac

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

env_value() {
  key="$1"
  sed -n "s/^[[:space:]]*${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//'
}

if [ "$CHECK_ENV_ONLY" -eq 0 ]; then
  command -v docker >/dev/null 2>&1 || fail "Docker is not available. Start QNAP Container Station first."
  docker info >/dev/null 2>&1 || fail "Docker is not accessible. Run: sudo ./startup.sh"
  docker compose version >/dev/null 2>&1 || fail "Docker Compose V2 is required (docker compose)."
fi

if [ ! -f "$ENV_FILE" ]; then
  [ "$CHECK_ENV_ONLY" -eq 0 ] || fail "$ENV_FILE does not exist"
  cp .env.example .env
  echo "Created .env from .env.example." >&2
  # Every name this run will hard-fail on, in one message. Naming a subset is
  # how a first run gets all the way to a check it was never told about: the
  # message used to omit DASHBOARD_TOKEN, and then ENGINE_AUTH_TOKEN, both of
  # which are checked below and both of which stop the stack dead.
  echo "Fill in all five required values, then run startup.sh again:" >&2
  echo "  DISCORD_TOKEN, GUILD_ID, OPENROUTER_API_KEY" >&2
  echo "  ENGINE_AUTH_TOKEN  - 20+ characters, the same value for Python and the Go engine" >&2
  echo "  DASHBOARD_TOKEN    - 20+ characters (or set DASHBOARD_ENABLED=false to run without the dashboard)" >&2
  echo "Generate either token with:" >&2
  echo "  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
  echo "They are the first block in .env, above every setting that has a default." >&2
  exit 2
fi

DISCORD_TOKEN_VALUE="$(env_value DISCORD_TOKEN)"
GUILD_ID_VALUE="$(env_value GUILD_ID)"
ENGINE_AUTH_TOKEN_VALUE="$(env_value ENGINE_AUTH_TOKEN)"
NARRATOR_PROVIDER_VALUE="$(env_value NARRATOR_PROVIDER)"
OPENROUTER_KEY_VALUE="$(env_value OPENROUTER_API_KEY)"
DASHBOARD_ENABLED_VALUE="$(env_value DASHBOARD_ENABLED)"
DASHBOARD_TOKEN_VALUE="$(env_value DASHBOARD_TOKEN)"

[ -n "$DISCORD_TOKEN_VALUE" ] || fail "DISCORD_TOKEN is empty in .env"
[ -n "$GUILD_ID_VALUE" ] || fail "GUILD_ID is empty in .env"
[ ${#ENGINE_AUTH_TOKEN_VALUE} -ge 20 ] || fail "ENGINE_AUTH_TOKEN must be at least 20 characters. Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""

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

# The dashboard token is checked below only when the dashboard is on; in
# check-env mode that check must run too, so it is hoisted here.
DASHBOARD_ON=0
case "$(printf '%s' "$DASHBOARD_ENABLED_VALUE" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) DASHBOARD_ON=1 ;;
esac
if [ "$DASHBOARD_ON" -eq 1 ]; then
  [ ${#DASHBOARD_TOKEN_VALUE} -ge 20 ] || fail "DASHBOARD_TOKEN must be at least 20 characters when the dashboard is enabled. Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"  (or set DASHBOARD_ENABLED=false)"
fi

if [ "$CHECK_ENV_ONLY" -eq 1 ]; then
  echo "$ENV_FILE satisfies the requirements of Xianxia RP $(tr -d '[:space:]' < VERSION 2>/dev/null || echo '?')."
  exit 0
fi

mkdir -p data

echo "Starting Xianxia RP on QNAP..."
echo "  Engine:     Go authoritative game engine + SQLite WAL"
echo "  Bot:        Python Discord/RAG frontend"
echo "  Narration:  OpenRouter free cloud fallback chain"

# DASHBOARD_ON was normalised once above (1/true/yes/on) and is reused by the
# log-command hint at the end, so the two can never disagree again.
case "$DASHBOARD_ON" in
  1)
    echo "  Dashboard:  enabled (GM Admin + Discord Server Setup)"
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
if [ "$DASHBOARD_ON" = "1" ]; then
  echo "Dashboard logs: docker compose --profile dashboard logs -f --tail=150 xianxia-dashboard"
fi
