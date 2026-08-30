#!/bin/sh
set -eu

# Xianxia RP transactional local updater (v0.17+).
#
# Usage:
#   ./update.sh                 # check ./updates for a newer local ZIP
#   ./update.sh --install       # install newest local ZIP from ./updates
#   ./update.sh --install PATH  # install a specific local ZIP
#   ./update.sh --force PATH    # intentional reinstall/downgrade
#
# No network access is used. .env, data/, updates/ and update_backups/ are
# persistent. A live SQLite backup is created through the authoritative Go
# engine before services are stopped. Failed installs restore code + database.

# Run from a detached copy so a release can safely replace update.sh itself.
if [ "${XIANXIA_UPDATER_REEXEC:-0}" != "1" ]; then
    ORIGINAL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    RUN_PARENT=$(dirname "${XIANXIA_PROJECT_DIR:-$ORIGINAL_DIR}")
    mkdir -p "$RUN_PARENT"
    RUN_COPY=$(mktemp "$RUN_PARENT/.xianxia-updater-running.XXXXXX")
    cp -p "$0" "$RUN_COPY"
    chmod +x "$RUN_COPY"
    XIANXIA_UPDATER_REEXEC=1 XIANXIA_UPDATER_HOME="$ORIGINAL_DIR" XIANXIA_UPDATER_RUNNING_COPY="$RUN_COPY" \
        exec "$RUN_COPY" "$@"
fi

SCRIPT_DIR=${XIANXIA_UPDATER_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}
PROJECT_DIR=${XIANXIA_PROJECT_DIR:-$SCRIPT_DIR}
UPDATES_DIR=${XIANXIA_UPDATES_DIR:-$PROJECT_DIR/updates}
BACKUP_DIR=${XIANXIA_BACKUP_DIR:-$PROJECT_DIR/update_backups}
DB_PATH=${XIANXIA_DB_PATH:-$PROJECT_DIR/data/xianxia.sqlite3}
VERSION_FILE="$PROJECT_DIR/VERSION"
RUNNING_COPY=${XIANXIA_UPDATER_RUNNING_COPY:-}

MODE=check
REQUESTED_ARCHIVE=""
FORCE=0
ROLLBACK_ARMED=0
ROLLBACK_RUNNING=0
STAGING_DIR=""
INSTALL_TREE=""
SNAPSHOT=""
DB_BACKUP_PATH=""
LOCK_DIR=""
NEXT_UPDATER=""

case "${1:-}" in
    ""|--check) MODE=check ;;
    --install)
        MODE=install
        REQUESTED_ARCHIVE=${2:-}
        [ $# -le 2 ] || { echo "ERROR: Too many arguments for --install." >&2; exit 2; }
        ;;
    --force)
        MODE=install; FORCE=1; REQUESTED_ARCHIVE=${2:-}
        [ -n "$REQUESTED_ARCHIVE" ] || { echo "ERROR: --force requires a local ZIP path." >&2; exit 2; }
        [ $# -le 2 ] || { echo "ERROR: Too many arguments for --force." >&2; exit 2; }
        ;;
    -h|--help)
        sed -n '3,13p' "$0"
        exit 0
        ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
esac

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: Required command '$1' was not found." >&2; exit 1; }; }
for cmd in unzip awk find mktemp cp rm mkdir mv sed tr head basename dirname; do need_cmd "$cmd"; done

clean_version() { printf '%s' "$1" | tr -d '[:space:]' | sed 's/^v//'; }
valid_version() {
    awk -v v="$1" 'BEGIN { n=split(v,a,"."); if(n<2 || n>4) exit 1; for(i=1;i<=n;i++) if(a[i] !~ /^[0-9]+$/) exit 1; exit 0 }'
}
version_gt() {
    awk -v a="$1" -v b="$2" 'BEGIN { na=split(a,A,"."); nb=split(b,B,"."); n=(na>nb?na:nb); for(i=1;i<=n;i++){x=(i<=na?A[i]+0:0);y=(i<=nb?B[i]+0:0);if(x>y)exit 0;if(x<y)exit 1} exit 1 }'
}
archive_version() {
    archive=$1
    value=$(unzip -p "$archive" 'VERSION' 2>/dev/null | head -n 1 || true)
    [ -n "$value" ] || value=$(unzip -p "$archive" '*/VERSION' 2>/dev/null | head -n 1 || true)
    clean_version "$value"
}

cleanup() {
    status=$?
    trap - EXIT INT TERM HUP
    if [ "$status" -ne 0 ] && [ "$ROLLBACK_ARMED" -eq 1 ] && [ "$ROLLBACK_RUNNING" -eq 0 ]; then
        rollback_install || true
    fi
    [ -n "$STAGING_DIR" ] && rm -rf "$STAGING_DIR" 2>/dev/null || true
    [ -n "$INSTALL_TREE" ] && rm -rf "$INSTALL_TREE" 2>/dev/null || true
    [ -n "$LOCK_DIR" ] && rm -rf "$LOCK_DIR" 2>/dev/null || true
    [ -n "$RUNNING_COPY" ] && rm -f "$RUNNING_COPY" 2>/dev/null || true
    exit "$status"
}
trap cleanup EXIT INT TERM HUP

[ -f "$VERSION_FILE" ] || { echo "ERROR: VERSION file not found at: $VERSION_FILE" >&2; exit 1; }
CURRENT_VERSION=$(clean_version "$(cat "$VERSION_FILE")")
valid_version "$CURRENT_VERSION" || { echo "ERROR: Invalid installed VERSION: $CURRENT_VERSION" >&2; exit 1; }

# Normalize persistent directories before deletion logic so custom locations are preserved.
mkdir -p "$UPDATES_DIR" "$BACKUP_DIR"
PROJECT_DIR=$(CDPATH= cd -- "$PROJECT_DIR" && pwd)
UPDATES_DIR=$(CDPATH= cd -- "$UPDATES_DIR" && pwd)
BACKUP_DIR=$(CDPATH= cd -- "$BACKUP_DIR" && pwd)
VERSION_FILE="$PROJECT_DIR/VERSION"
DB_PATH=${XIANXIA_DB_PATH:-$PROJECT_DIR/data/xianxia.sqlite3}
PARENT_DIR=$(dirname "$PROJECT_DIR")

find_latest_update() {
    LATEST_ARCHIVE=""; LATEST_VERSION="$CURRENT_VERSION"
    candidates=$(mktemp "$UPDATES_DIR/.xianxia-update-candidates.XXXXXX")
    find "$UPDATES_DIR" -maxdepth 1 -type f -name '*.zip' -print | while IFS= read -r archive; do
        version=$(archive_version "$archive")
        valid_version "$version" || continue
        printf '%s\t%s\n' "$version" "$archive"
    done > "$candidates"
    tab=$(printf '\t')
    while IFS="$tab" read -r version archive; do
        [ -n "$version" ] || continue
        if version_gt "$version" "$LATEST_VERSION"; then LATEST_VERSION=$version; LATEST_ARCHIVE=$archive; fi
    done < "$candidates"
    rm -f "$candidates"
}

if [ "$MODE" = check ]; then
    find_latest_update
    echo "Xianxia RP installed version: $CURRENT_VERSION"
    echo "Local update folder: $UPDATES_DIR"
    if [ -n "$LATEST_ARCHIVE" ]; then
        echo "UPDATE AVAILABLE: $LATEST_VERSION"
        echo "Package: $LATEST_ARCHIVE"
        echo "Install with: ./update.sh --install"
    else
        echo "No newer local Xianxia RP update was found."
        echo "Place a newer Xianxia RP .zip in ./updates/ and run this script again."
    fi
    exit 0
fi

need_cmd docker
docker compose version >/dev/null 2>&1 || { echo "ERROR: Docker Compose V2 is required." >&2; exit 1; }

LOCK_DIR="$PARENT_DIR/.xianxia-update.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "ERROR: Another Xianxia RP update appears to be running: $LOCK_DIR" >&2
    exit 1
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid" 2>/dev/null || true

if [ -n "$REQUESTED_ARCHIVE" ]; then
    case "$REQUESTED_ARCHIVE" in /*) INSTALL_ARCHIVE=$REQUESTED_ARCHIVE ;; *) INSTALL_ARCHIVE="$PROJECT_DIR/$REQUESTED_ARCHIVE" ;; esac
else
    find_latest_update; INSTALL_ARCHIVE=$LATEST_ARCHIVE
fi
[ -n "${INSTALL_ARCHIVE:-}" ] && [ -f "$INSTALL_ARCHIVE" ] || { echo "ERROR: No local update package is available to install." >&2; exit 1; }

TARGET_VERSION=$(archive_version "$INSTALL_ARCHIVE")
valid_version "$TARGET_VERSION" || { echo "ERROR: ZIP VERSION is missing or malformed: ${TARGET_VERSION:-<empty>}" >&2; exit 1; }
if [ "$FORCE" -ne 1 ] && ! version_gt "$TARGET_VERSION" "$CURRENT_VERSION"; then
    echo "Nothing to install: package $TARGET_VERSION is not newer than installed $CURRENT_VERSION."
    exit 0
fi

echo "Preparing local Xianxia RP update: $CURRENT_VERSION -> $TARGET_VERSION"
echo "Package: $INSTALL_ARCHIVE"

# Reject traversal/absolute-path archives before extraction.
entries=$(mktemp "$PARENT_DIR/.xianxia-zip-entries.XXXXXX")
unzip -Z1 "$INSTALL_ARCHIVE" > "$entries" || { rm -f "$entries"; echo "ERROR: Could not list ZIP contents." >&2; exit 1; }
bad_entry=""
while IFS= read -r entry; do
    case "$entry" in
        /*|../*|*/../*|*/..|..|*\\*) bad_entry=$entry; break ;;
    esac
done < "$entries"
rm -f "$entries"
[ -z "$bad_entry" ] || { echo "ERROR: Unsafe ZIP path rejected: $bad_entry" >&2; exit 1; }

STAGING_DIR=$(mktemp -d "$PARENT_DIR/.xianxia-update-stage.XXXXXX")
unzip -q "$INSTALL_ARCHIVE" -d "$STAGING_DIR"
if [ -f "$STAGING_DIR/VERSION" ]; then NEW_ROOT=$STAGING_DIR; else
    NEW_ROOT=""
    for candidate in "$STAGING_DIR"/*; do
        [ -d "$candidate" ] && [ -f "$candidate/VERSION" ] || continue
        [ -z "$NEW_ROOT" ] || { echo "ERROR: Update ZIP contains multiple possible project roots." >&2; exit 1; }
        NEW_ROOT=$candidate
    done
fi
[ -n "$NEW_ROOT" ] && [ -f "$NEW_ROOT/VERSION" ] || { echo "ERROR: Could not locate project root inside ZIP." >&2; exit 1; }
EXTRACTED_VERSION=$(clean_version "$(cat "$NEW_ROOT/VERSION")")
[ "$EXTRACTED_VERSION" = "$TARGET_VERSION" ] || { echo "ERROR: Extracted VERSION mismatch." >&2; exit 1; }
for required in VERSION startup.sh stop.sh docker-compose.yml go_core app content; do [ -e "$NEW_ROOT/$required" ] || { echo "ERROR: Package missing $required" >&2; exit 1; }; done

# Validate the complete staged release tree before touching the installed tree.
INSTALL_TREE=$(mktemp -d "$PARENT_DIR/.xianxia-release-$TARGET_VERSION.XXXXXX")
for item in "$NEW_ROOT"/* "$NEW_ROOT"/.[!.]* "$NEW_ROOT"/..?*; do
    [ -e "$item" ] || continue
    name=$(basename "$item")
    case "$name" in .env|data|updates|update_backups) continue ;; esac
    cp -a "$item" "$INSTALL_TREE/"
done
[ -f "$INSTALL_TREE/update.sh" ] && chmod +x "$INSTALL_TREE/update.sh" || true

# Validate Compose with the installed configuration without ever packaging or replacing .env.
STAGED_ENV=0
if [ -f "$PROJECT_DIR/.env" ]; then
    cp -p "$PROJECT_DIR/.env" "$INSTALL_TREE/.env"
    STAGED_ENV=1
fi
# Release-time content validation uses the image's Python source without requiring host Python.
# docker compose config also catches malformed Compose before the old service is stopped.
(cd "$INSTALL_TREE" && docker compose config >/dev/null) || { echo "ERROR: New docker-compose.yml is invalid." >&2; exit 1; }
[ "$STAGED_ENV" -eq 0 ] || rm -f "$INSTALL_TREE/.env"

STAMP=$(date '+%Y%m%d_%H%M%S' 2>/dev/null || date '+%s')
SNAPSHOT="$BACKUP_DIR/xianxia_rp_${CURRENT_VERSION}_before_${TARGET_VERSION}_$STAMP"
mkdir -p "$SNAPSHOT"

echo "Creating code snapshot: $SNAPSHOT"
for old_item in "$PROJECT_DIR"/* "$PROJECT_DIR"/.[!.]* "$PROJECT_DIR"/..?*; do
    [ -e "$old_item" ] || continue
    name=$(basename "$old_item")
    case "$name" in .env|data|updates|update_backups) continue ;; esac
    # Preserve custom update/backup trees even when they live under an unusual top-level directory.
    case "$UPDATES_DIR/" in "$old_item"/*) continue ;; esac
    case "$BACKUP_DIR/" in "$old_item"/*) continue ;; esac
    cp -a "$old_item" "$SNAPSHOT/"
done

create_database_backup() {
    [ -f "$DB_PATH" ] || return 0
    echo "Creating transaction-safe SQLite backup through the Go engine..."
    response=$(cd "$PROJECT_DIR" && docker compose exec -T xianxia-engine \
        wget -q -O - --header='Content-Type: application/json' --post-data='{}' \
        http://127.0.0.1:8081/v1/db/backups 2>/dev/null || true)
    name=$(printf '%s' "$response" | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    [ -n "$name" ] || {
        echo "ERROR: Could not create a safe SQLite backup. Start the current stack and retry." >&2
        echo "       Refusing to update a live/possibly-WAL database without the engine backup API." >&2
        return 1
    }
    DB_BACKUP_PATH="$PROJECT_DIR/data/backups/$name"
    [ -s "$DB_BACKUP_PATH" ] || { echo "ERROR: Engine reported backup $name but the file is missing/empty." >&2; return 1; }
    echo "Database backup: $DB_BACKUP_PATH"
}
create_database_backup

restore_code() {
    echo "Restoring code snapshot..." >&2
    for old_item in "$PROJECT_DIR"/* "$PROJECT_DIR"/.[!.]* "$PROJECT_DIR"/..?*; do
        [ -e "$old_item" ] || continue
        name=$(basename "$old_item")
        case "$name" in .env|data|updates|update_backups) continue ;; esac
        case "$UPDATES_DIR/" in "$old_item"/*) continue ;; esac
        case "$BACKUP_DIR/" in "$old_item"/*) continue ;; esac
        rm -rf "$old_item"
    done
    for item in "$SNAPSHOT"/* "$SNAPSHOT"/.[!.]* "$SNAPSHOT"/..?*; do [ -e "$item" ] && cp -a "$item" "$PROJECT_DIR/"; done
}
restore_database() {
    [ -n "$DB_BACKUP_PATH" ] || return 0
    echo "Restoring SQLite backup..." >&2
    cp -a "$DB_BACKUP_PATH" "$DB_PATH"
    rm -f "$DB_PATH-wal" "$DB_PATH-shm"
}
rollback_install() {
    ROLLBACK_RUNNING=1
    echo "Update failed; rolling back Xianxia RP $TARGET_VERSION -> $CURRENT_VERSION." >&2
    (cd "$PROJECT_DIR" && docker compose --profile dashboard down --remove-orphans >/dev/null 2>&1) || true
    restore_code
    restore_database
    chmod +x "$PROJECT_DIR/startup.sh" "$PROJECT_DIR/stop.sh" "$PROJECT_DIR/update.sh" 2>/dev/null || true
    if [ -x "$PROJECT_DIR/startup.sh" ]; then
        echo "Restarting restored Xianxia RP $CURRENT_VERSION..." >&2
        "$PROJECT_DIR/startup.sh" >/dev/null 2>&1 || echo "WARNING: rollback restored files but restart failed; inspect Docker logs." >&2
    fi
    ROLLBACK_ARMED=0
    ROLLBACK_RUNNING=0
}

# Validation + both backups are complete. From this point, any error rolls back.
ROLLBACK_ARMED=1
if [ -x "$PROJECT_DIR/stop.sh" ]; then echo "Stopping Xianxia RP..."; "$PROJECT_DIR/stop.sh"; else (cd "$PROJECT_DIR" && docker compose --profile dashboard down --remove-orphans); fi

echo "Installing Xianxia RP $TARGET_VERSION..."
for old_item in "$PROJECT_DIR"/* "$PROJECT_DIR"/.[!.]* "$PROJECT_DIR"/..?*; do
    [ -e "$old_item" ] || continue
    name=$(basename "$old_item")
    case "$name" in .env|data|updates|update_backups|update.sh) continue ;; esac
    case "$UPDATES_DIR/" in "$old_item"/*) continue ;; esac
    case "$BACKUP_DIR/" in "$old_item"/*) continue ;; esac
    rm -rf "$old_item"
done
for item in "$INSTALL_TREE"/* "$INSTALL_TREE"/.[!.]* "$INSTALL_TREE"/..?*; do
    [ -e "$item" ] || continue
    name=$(basename "$item")
    case "$name" in .env|data|updates|update_backups|update.sh) continue ;; esac
    cp -a "$item" "$PROJECT_DIR/"
done
if [ -f "$INSTALL_TREE/update.sh" ]; then NEXT_UPDATER="$PROJECT_DIR/.update.sh.next"; cp -a "$INSTALL_TREE/update.sh" "$NEXT_UPDATER"; chmod +x "$NEXT_UPDATER"; fi
chmod +x "$PROJECT_DIR/startup.sh" "$PROJECT_DIR/stop.sh" 2>/dev/null || true

INSTALLED_VERSION=$(clean_version "$(cat "$PROJECT_DIR/VERSION")")
[ "$INSTALLED_VERSION" = "$TARGET_VERSION" ] || { echo "ERROR: Post-install VERSION check failed." >&2; exit 1; }

echo "Starting Xianxia RP $TARGET_VERSION..."
"$PROJECT_DIR/startup.sh"

container_ready() {
    name=$1
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || true)
    [ "$state" = healthy ] || [ "$state" = running ]
}
wait_for_release_health() {
    attempts=${XIANXIA_HEALTH_ATTEMPTS:-60}; delay=${XIANXIA_HEALTH_INTERVAL_SECONDS:-2}; i=0
    while [ "$i" -lt "$attempts" ]; do
        if container_ready xianxia-game-engine && container_ready xianxia-roleplay-bot; then
            dashboard_enabled=$(sed -n 's/^[[:space:]]*DASHBOARD_ENABLED=//p' "$PROJECT_DIR/.env" 2>/dev/null | tail -n1 | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
            case "$dashboard_enabled" in 1|true|yes|on) container_ready xianxia-rp-dashboard || { i=$((i+1)); sleep "$delay"; continue; } ;; esac
            return 0
        fi
        i=$((i+1)); sleep "$delay"
    done
    return 1
}
wait_for_release_health || { echo "ERROR: New release did not become healthy." >&2; exit 1; }

# Commit point. The running script is the detached copy, so replacing update.sh is safe.
ROLLBACK_ARMED=0
if [ -n "$NEXT_UPDATER" ] && [ -f "$NEXT_UPDATER" ]; then mv -f "$NEXT_UPDATER" "$PROJECT_DIR/update.sh"; chmod +x "$PROJECT_DIR/update.sh"; fi

echo "Xianxia RP update complete: $CURRENT_VERSION -> $TARGET_VERSION"
echo "Code rollback snapshot: $SNAPSHOT"
[ -n "$DB_BACKUP_PATH" ] && echo "Database rollback snapshot: $DB_BACKUP_PATH"
echo ".env, data/, updates/ and update_backups/ were preserved."
exit 0
