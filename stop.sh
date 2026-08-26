#!/bin/sh
set -eu

cd "$(dirname "$0")"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker is not available."
docker info >/dev/null 2>&1 || fail "Docker is not accessible. Run: sudo ./stop.sh"
docker compose version >/dev/null 2>&1 || fail "Docker Compose V2 is required (docker compose)."

echo "Stopping Xianxia RP..."
# Include the optional dashboard profile so the admin console is stopped too.
docker compose --profile dashboard down --remove-orphans "$@"

echo
echo "Xianxia RP has been stopped."
echo "Persistent files in .env and ./data were not deleted."
