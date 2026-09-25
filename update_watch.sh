#!/bin/sh
set -eu

# Xianxia RP update watcher (v1.4.0): the host half of "update from the dashboard".
#
# Usage:
#   ./update_watch.sh             # poll forever (nohup sh ./update_watch.sh >/dev/null 2>&1 &)
#   ./update_watch.sh --oneshot   # one poll; act if a request is waiting; exit (a scheduled task)
#
# Nothing inside the stack can update it - the images carry the code and no
# container holds the Docker socket - so the GM's "Request update" on the
# dashboard only writes an audited request into the engine. This script, on
# the NAS beside update.sh, reads that request the way update.sh already
# reaches the engine (docker compose exec into the container, the token read
# from the container's own environment), closes the world, runs
# `./update.sh --upgrade`, reopens the world, and reports what happened under
# the request's own nonce. It adds no port and no privilege.
#
# It is deliberately NOT a mode of update.sh: that script replaces itself
# mid-run from a detached copy, and a loop living inside the file being
# replaced is exactly the shape that dance exists to avoid. This file only
# ever runs whatever update.sh is on disk and waits for it to exit.
#
# Environment (all optional): XIANXIA_PROJECT_DIR, UPDATE_WATCH_INTERVAL_SECONDS
# (.env or environment, default 30), XIANXIA_ENGINE_CALL (a command that stands
# in for the docker exec - tests), XIANXIA_UPDATE_SH (the updater to run - tests).

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=${XIANXIA_PROJECT_DIR:-$SCRIPT_DIR}
PROJECT_DIR=$(CDPATH= cd -- "$PROJECT_DIR" && pwd)
PARENT_DIR=$(dirname "$PROJECT_DIR")
LOG_FILE=${XIANXIA_WATCH_LOG:-$PROJECT_DIR/update_watch.log}
STATE_FILE=${XIANXIA_WATCH_STATE:-$PARENT_DIR/.xianxia-watcher-state}
LOCK_DIR=${XIANXIA_WATCH_LOCK:-$PARENT_DIR/.xianxia-watcher.lock}
UPDATE_SH=${XIANXIA_UPDATE_SH:-$PROJECT_DIR/update.sh}
HEARTBEAT_SECONDS=300
REOPEN_TRIES=${XIANXIA_WATCH_REOPEN_TRIES:-20}
RETRY_SECONDS=${XIANXIA_WATCH_RETRY_SECONDS:-5}
ONESHOT=0
LAST_HEARTBEAT=0

case "${1:-}" in
    "") ;;
    --oneshot) ONESHOT=1 ;;
    *) echo "ERROR: Unknown option: $1 (use --oneshot or nothing)" >&2; exit 2 ;;
esac

env_value() {
    # $1 = key; from $PROJECT_DIR/.env, ignoring comments, quotes stripped.
    [ -f "$PROJECT_DIR/.env" ] || return 0
    sed -n "s/^[[:space:]]*$1=//p" "$PROJECT_DIR/.env" | tail -n 1 | tr -d '"'"'" | tr -d '[:space:]'
}
INTERVAL=${UPDATE_WATCH_INTERVAL_SECONDS:-$(env_value UPDATE_WATCH_INTERVAL_SECONDS)}
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=30 ;; esac
[ "$INTERVAL" -ge 5 ] || INTERVAL=5

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"; }

# engine_call OPERATION JSON_PAYLOAD -> the engine's JSON answer on stdout,
# nonzero when it could not be reached. The body rides an environment
# variable into the container so no quoting inside `sh -c` can break it.
engine_call() {
    body=$(printf '{"operation":"%s","actor_id":0,"payload":%s}' "$1" "$2")
    if [ -n "${XIANXIA_ENGINE_CALL:-}" ]; then
        XW_BODY="$body" "$XIANXIA_ENGINE_CALL" "$1"
        return $?
    fi
    (cd "$PROJECT_DIR" && docker compose exec -T -e XW_BODY="$body" xianxia-engine sh -c \
        'wget -q -O - --header="Content-Type: application/json" --header="X-Xianxia-Engine-Token: $ENGINE_AUTH_TOKEN" --post-data="$XW_BODY" http://127.0.0.1:8081/v1/game/action') 2>/dev/null
}

# json_str TEXT KEY -> the string value of "KEY" in a flat JSON object.
json_str() { printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1; }
# The request object out of the read's answer: flat, so it ends at its first '}'.
request_object() { printf '%s' "$1" | sed -n 's/.*"request"[[:space:]]*:[[:space:]]*\({[^}]*}\).*/\1/p' | head -n 1; }

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\n\r'; }

report() {
    # $1 nonce, $2 status, $3 detail, $4 installed version
    payload=$(printf '{"nonce":"%s","status":"%s","detail":"%s","installed_version":"%s"}' \
        "$1" "$2" "$(json_escape "$3")" "$(json_escape "${4:-}")")
    if engine_call admin.server.update_status "$payload" >/dev/null; then
        log "reported $2${3:+: $3}"
    else
        log "could not report $2 (engine unreachable)"
        return 1
    fi
}

set_world() {
    # $1 true|false, $2 reason. Reopening is retried by the caller while the
    # engine is still coming back.
    engine_call admin.server.maintenance_mode \
        "$(printf '{"enabled":%s,"reason":"%s"}' "$1" "$(json_escape "$2")")" >/dev/null
}

heartbeat() {
    now=$(date +%s)
    [ $((now - LAST_HEARTBEAT)) -ge "$HEARTBEAT_SECONDS" ] || return 0
    if engine_call admin.server.update_status '{"status":"heartbeat"}' >/dev/null; then
        LAST_HEARTBEAT=$now
    fi
}

handled_before() { [ -f "$STATE_FILE" ] && grep -qx "$1" "$STATE_FILE"; }
remember() { printf '%s\n' "$1" >> "$STATE_FILE"; }

run_update() {
    # $1 nonce, $2 channel. update.sh's own exit code and log say what happened:
    # 0 is installed; nonzero before it ever said "Stopping" means the .env
    # preflight refused and nothing was changed; nonzero after means its own
    # rollback already ran.
    report "$1" acked "" || return 0
    set_world true "Server update in progress; back shortly" || log "could not close the world (continuing)"
    report "$1" fetching "" || true
    run_log=$(mktemp "$PARENT_DIR/.xianxia-watcher-run.XXXXXX")
    log "running update.sh --upgrade --channel $2"
    status=0; installed=""
    sh "$UPDATE_SH" --upgrade --channel "$2" >"$run_log" 2>&1 || status=$?
    cat "$run_log" >> "$LOG_FILE"
    if [ "$status" -eq 0 ]; then
        installed=$(tr -d '[:space:]' < "$PROJECT_DIR/VERSION" 2>/dev/null || true)
        outcome=done; detail="installed ${installed:-the new release}"
    elif ! grep -q 'Stopping Xianxia RP' "$run_log"; then
        outcome=failed
        detail="nothing was changed: $(grep 'ERROR:' "$run_log" | tail -n 1 | cut -c1-200)"
        if grep -q 'does not satisfy' "$run_log"; then
            detail="needs ./migrate_env.sh on the NAS: the release added a .env key (nothing was changed)"
        fi
    else
        outcome=failed
        detail="rolled back: $(grep 'ERROR:\|WARNING:' "$run_log" | tail -n 1 | cut -c1-200)"
    fi
    rm -f "$run_log"
    # Reopen whatever happened: a failed update that leaves the world shut is
    # worse than one that reopens it. The stack may still be starting, so try
    # a few times before giving the report and leaving the rest to later ticks.
    tries=0
    until set_world false ""; do
        tries=$((tries + 1))
        if [ "$tries" -ge "$REOPEN_TRIES" ]; then
            NEED_REOPEN=1; log "could not reopen the world after $tries tries; will keep trying"; break
        fi
        sleep "$RETRY_SECONDS"
    done
    tries=0
    until report "$1" "$outcome" "$detail" "$installed"; do
        tries=$((tries + 1))
        [ "$tries" -lt "$REOPEN_TRIES" ] || { log "could not deliver the $outcome report; the engine never came back"; return 0; }
        sleep "$RETRY_SECONDS"
    done
}

NEED_REOPEN=0
poll_once() {
    if [ "$NEED_REOPEN" -eq 1 ] && set_world false ""; then
        NEED_REOPEN=0; log "world reopened"
    fi
    answer=$(engine_call admin.server.update_request '{}') || return 0
    request=$(request_object "$answer")
    [ -n "$request" ] || { heartbeat; return 0; }
    status=$(json_str "$request" status)
    nonce=$(json_str "$request" nonce)
    channel=$(json_str "$request" channel)
    case "$channel" in stable|beta) ;; *) channel=stable ;; esac
    if [ "$status" = "requested" ] && [ -n "$nonce" ] && ! handled_before "$nonce"; then
        remember "$nonce"
        log "update requested (nonce $nonce, channel $channel)"
        run_update "$nonce" "$channel"
    fi
    heartbeat
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "Another update watcher is running (pid $pid): $LOCK_DIR" >&2
        exit 0
    fi
    rm -rf "$LOCK_DIR"; mkdir "$LOCK_DIR"
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM HUP

log "watcher started (interval ${INTERVAL}s, oneshot=$ONESHOT)"
if [ "$ONESHOT" -eq 1 ]; then
    poll_once
    exit 0
fi
while :; do
    poll_once
    sleep "$INTERVAL"
done
