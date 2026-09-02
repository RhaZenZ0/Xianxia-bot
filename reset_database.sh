#!/bin/sh
set -eu

# Xianxia RP database reset (v0.19+).
#
# Wipes the running world back to a brand-new game: every character, NPC,
# family, sect, event, world-history entry - everything in the SQLite
# database - is deleted. If the stack is running, this also deletes every
# Discord thread the bot was tracking (expedition journals, household
# threads, sect/cave abodes, event scenes, battle threads - they'd otherwise
# be orphaned pointers to characters that no longer exist) and posts a
# world-reset announcement in the configured announcement channel. Discord
# channels/roles themselves are left alone; re-run Server Setup -> Repair
# afterward if you want the bot to reconcile stale bindings.
#
# Usage:
#   ./reset_database.sh                    # interactive: confirm, clean Discord, backup, reset, restart
#   ./reset_database.sh --yes              # skip the typed confirmation (still backs up, still cleans Discord)
#   ./reset_database.sh --no-backup        # skip the safety backup (not recommended)
#   ./reset_database.sh --no-discord-cleanup  # leave Discord threads/announcement alone
#   ./reset_database.sh --no-restart       # reset but leave the stack stopped
#   ./reset_database.sh -h | --help
#
# A safety backup is always taken first unless --no-backup is given: through
# the authoritative Go engine's SQLite backup API when the stack is running
# (the same transaction-safe method the dashboard's "Create backup" button
# and update.sh use - see README's "Backups and maintenance"), or a plain
# file copy when the stack is already stopped (nothing has the file open in
# that case, so a direct copy is safe). Backups land in data/backups/, which
# this script never deletes, and restoring is: stop the stack, copy the
# backup over data/xianxia.sqlite3 (removing any -wal/-shm alongside it),
# start the stack again.
#
# Discord cleanup (thread deletion + announcement) requires the bot to be
# live, since only discord.py can touch Discord - it's skipped automatically
# when the stack is already stopped, or always with --no-discord-cleanup.
# The same cleanup is also available as the "Reset World" button on the GM
# dashboard's Discord Setup tab, for triggering it without a database wipe.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=${XIANXIA_PROJECT_DIR:-$SCRIPT_DIR}
DB_PATH=${XIANXIA_DB_PATH:-$PROJECT_DIR/data/xianxia.sqlite3}
BACKUP_DIR="$PROJECT_DIR/data/backups"

SKIP_CONFIRM=0
SKIP_BACKUP=0
SKIP_DISCORD=0
SKIP_RESTART=0

for arg in "$@"; do
    case "$arg" in
        --yes|-y) SKIP_CONFIRM=1 ;;
        --no-backup) SKIP_BACKUP=1 ;;
        --no-discord-cleanup) SKIP_DISCORD=1 ;;
        --no-restart) SKIP_RESTART=1 ;;
        -h|--help) sed -n '3,38p' "$0"; exit 0 ;;
        *) echo "ERROR: Unknown option: $arg" >&2; exit 2 ;;
    esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }

env_value() {
    key="$1"
    [ -f "$PROJECT_DIR/.env" ] || return 0
    sed -n "s/^[[:space:]]*${key}=//p" "$PROJECT_DIR/.env" | tail -n 1 | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//'
}

command -v docker >/dev/null 2>&1 || fail "Docker is not available."
docker info >/dev/null 2>&1 || fail "Docker is not accessible. Run: sudo ./reset_database.sh"
docker compose version >/dev/null 2>&1 || fail "Docker Compose V2 is required (docker compose)."

if [ ! -f "$DB_PATH" ]; then
    echo "No database found at $DB_PATH - nothing to reset."
    echo "It will be created fresh the next time you run ./startup.sh."
    exit 0
fi

stack_running() {
    [ "$(docker inspect -f '{{.State.Running}}' xianxia-game-engine 2>/dev/null || true)" = "true" ]
}
bot_running() {
    [ "$(docker inspect -f '{{.State.Running}}' xianxia-roleplay-bot 2>/dev/null || true)" = "true" ]
}

echo "This will PERMANENTLY DELETE the entire Xianxia RP world database:"
echo "  $DB_PATH"
echo "Every character, NPC, family, sect, war, event and world-history entry"
echo "will be gone."
if [ "$SKIP_DISCORD" -ne 1 ] && bot_running; then
    echo "Every Discord thread the bot is tracking will also be deleted, and a"
    echo "world-reset announcement will be posted in the announcement channel."
else
    echo "Discord channels/threads are not touched - the bot will treat them as"
    echo "unbound until you run Server Setup -> Repair."
fi
echo

if [ "$SKIP_CONFIRM" -ne 1 ]; then
    if [ ! -t 0 ]; then
        fail "Not running interactively; pass --yes to confirm a non-interactive reset."
    fi
    printf 'Type RESET to continue: '
    read -r answer
    [ "$answer" = "RESET" ] || { echo "Aborted. Nothing was changed."; exit 1; }
fi

if [ "$SKIP_DISCORD" -ne 1 ]; then
    if bot_running; then
        echo "Cleaning up Discord threads through the running bot..."
        CONTROL_TOKEN=$(env_value BOT_CONTROL_TOKEN)
        [ -n "$CONTROL_TOKEN" ] || CONTROL_TOKEN=$(env_value DASHBOARD_TOKEN)
        if [ -z "$CONTROL_TOKEN" ]; then
            echo "WARNING: No BOT_CONTROL_TOKEN/DASHBOARD_TOKEN found in .env; skipping Discord cleanup." >&2
        else
            response=$(cd "$PROJECT_DIR" && docker compose exec -T xianxia-bot \
                wget -q -O - --header="X-Xianxia-Control: $CONTROL_TOKEN" --header='Content-Type: application/json' \
                --post-data='{"action":"reset_world","payload":{"confirm":"RESET","reason":"reset_database.sh"}}' \
                http://127.0.0.1:8080/control/discord 2>/dev/null || true)
            deleted=$(printf '%s' "$response" | sed -n 's/.*"threads_deleted"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')
            found=$(printf '%s' "$response" | sed -n 's/.*"threads_found"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')
            if [ -n "$deleted" ]; then
                echo "Discord cleanup: deleted $deleted of $found tracked thread(s); reset announcement posted."
            else
                echo "WARNING: Discord cleanup did not complete (bot control call failed or was rejected)." >&2
                echo "         Continuing with the database reset anyway; clean up threads manually if needed." >&2
            fi
        fi
    else
        echo "Stack is not running; skipping Discord cleanup (the bot must be live to reach Discord)."
    fi
fi

BACKUP_PATH=""
if [ "$SKIP_BACKUP" -ne 1 ]; then
    mkdir -p "$BACKUP_DIR"
    if stack_running; then
        echo "Creating transaction-safe SQLite backup through the Go engine..."
        response=$(cd "$PROJECT_DIR" && docker compose exec -T xianxia-engine \
            wget -q -O - --header='Content-Type: application/json' --post-data='{}' \
            http://127.0.0.1:8081/v1/db/backups 2>/dev/null || true)
        name=$(printf '%s' "$response" | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        [ -n "$name" ] || fail "Could not create a safe SQLite backup through the running engine. Fix that first, or pass --no-backup to proceed without one."
        BACKUP_PATH="$BACKUP_DIR/$name"
        [ -s "$BACKUP_PATH" ] || fail "Engine reported backup $name but the file is missing/empty."
    else
        echo "Stack is not running; copying the database file directly (safe: nothing has it open)..."
        stamp=$(date -u '+%Y%m%d-%H%M%S' 2>/dev/null || date '+%s')
        BACKUP_PATH="$BACKUP_DIR/xianxia-$stamp.sqlite3"
        cp -p "$DB_PATH" "$BACKUP_PATH"
        for suffix in -wal -shm; do
            [ -f "$DB_PATH$suffix" ] && cp -p "$DB_PATH$suffix" "$BACKUP_PATH$suffix" || true
        done
    fi
    echo "Backup: $BACKUP_PATH"
else
    echo "Skipping safety backup (--no-backup)."
fi

if stack_running; then
    if [ -x "$PROJECT_DIR/stop.sh" ]; then
        echo "Stopping Xianxia RP..."
        "$PROJECT_DIR/stop.sh"
    else
        (cd "$PROJECT_DIR" && docker compose --profile dashboard down --remove-orphans)
    fi
fi

echo "Deleting database..."
rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm" "$DB_PATH-journal"

if [ "$SKIP_RESTART" -eq 1 ]; then
    echo
    echo "Database reset complete. Stack left stopped (--no-restart)."
    echo "Start it with ./startup.sh when ready; xianxia-db-init will create a fresh schema."
else
    [ -x "$PROJECT_DIR/startup.sh" ] || fail "startup.sh not found or not executable."
    echo "Starting Xianxia RP with a fresh database..."
    "$PROJECT_DIR/startup.sh"
    echo
    echo "Database reset complete. Xianxia RP is running with a brand-new world."
fi
[ -n "$BACKUP_PATH" ] && echo "Pre-reset backup kept at: $BACKUP_PATH"
exit 0
