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
  # DASHBOARD_TOKEN belongs in this list. .env.example ships DASHBOARD_ENABLED=true
  # with DASHBOARD_TOKEN empty, so a first run that fills in exactly the three
  # names above gets all the way to the dashboard check below and dies on a
  # requirement it was never told about.
  echo "The GM dashboard is on by default and also needs DASHBOARD_TOKEN (20+ characters):" >&2
  echo "  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
  echo "Or set DASHBOARD_ENABLED=false in .env to start without it." >&2
  exit 2
fi

DISCORD_TOKEN_VALUE="$(env_value DISCORD_TOKEN)"
GUILD_ID_VALUE="$(env_value GUILD_ID)"
NARRATOR_PROVIDER_VALUE="$(env_value NARRATOR_PROVIDER)"
OPENROUTER_KEY_VALUE="$(env_value OPENROUTER_API_KEY)"
DASHBOARD_ENABLED_VALUE="$(env_value DASHBOARD_ENABLED)"
DASHBOARD_TOKEN_VALUE="$(env_value DASHBOARD_TOKEN)"

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

# Normalise once and reuse. The acceptance test below and the log-command hint at
# the end used to disagree: acceptance took 1/true/yes/on, the hint compared
# against the literal "true", so DASHBOARD_ENABLED=yes started the dashboard and
# then never told you how to read its logs.
DASHBOARD_ON=0
case "$(printf '%s' "$DASHBOARD_ENABLED_VALUE" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) DASHBOARD_ON=1 ;;
esac

case "$DASHBOARD_ON" in
  1)
    [ ${#DASHBOARD_TOKEN_VALUE} -ge 20 ] || fail "DASHBOARD_TOKEN must be at least 20 characters when the dashboard is enabled. Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"  (or set DASHBOARD_ENABLED=false)"
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
