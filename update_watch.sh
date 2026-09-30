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
# v1.12.3 - a request can no longer be left open for ever by this script:
#   * the engine's read answers the nonce, status and channel as flat values,
#     so nothing here cuts a JSON object by hand (a `}` in a reason broke that);
#   * a nonce is remembered only after the `acked` report was taken, so a
#     report that failed is retried on the next tick rather than skipped;
#   * the closing report is written to disk before it is sent and sent again
#     at the start of every poll until the engine takes it;
#   * the world is reopened only if this script closed it - a world a GM had
#     already closed stays closed, with the GM's own reason;
#   * a signal ends the process (after an update in progress, which it will
#     not abandon half-way) and releases the lock.
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
# Beside the handled-nonce list, so they live and die with it: a closing report
# not yet taken by the engine, and the marker that this script closed the world.
PENDING_FILE="$STATE_FILE.pending"
CLOSED_FILE="$STATE_FILE.closed"
LOCK_DIR=${XIANXIA_WATCH_LOCK:-$PARENT_DIR/.xianxia-watcher.lock}
UPDATE_SH=${XIANXIA_UPDATE_SH:-$PROJECT_DIR/update.sh}
HEARTBEAT_SECONDS=300
REOPEN_TRIES=${XIANXIA_WATCH_REOPEN_TRIES:-20}
RETRY_SECONDS=${XIANXIA_WATCH_RETRY_SECONDS:-5}
ONESHOT=0
LAST_HEARTBEAT=0
IN_UPDATE=0
STOP=0
SLEEP_PID=

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

# json_str TEXT KEY -> the string value of "KEY" in TEXT. The engine's read
# answers the three request values it needs as flat top-level strings
# (request_nonce, request_status, request_channel), so this runs on the whole
# answer; a value quoted inside a GM's reason is escaped (\"), which the
# pattern - a quote, the key, a quote - cannot match.
json_str() { printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1; }
# json_word TEXT KEY -> true, false or null for a flat boolean.
json_word() { printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\([a-z]*\).*/\1/p" | head -n 1; }

# A JSON string body: a tab becomes a space and every other control character
# is dropped (a tab or an ESC in a quoted log line made the body invalid JSON,
# and the engine answered 400 to every retry), then backslash and quote are
# escaped.
json_escape() { printf '%s' "$1" | tr '\t' ' ' | tr -d '\000-\037\177' | sed 's/\\/\\\\/g; s/"/\\"/g'; }

report() {
    # $1 nonce, $2 status, $3 detail, $4 installed version
    payload=$(printf '{"nonce":"%s","status":"%s","detail":"%s","installed_version":"%s"}' \
        "$1" "$2" "$(json_escape "$3")" "$(json_escape "${4:-}")")
    if engine_call admin.server.update_status "$payload" >/dev/null; then
        log "reported $2${3:+: $3}"
    else
        log "could not report $2 (engine unreachable or it refused)"
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

# -- the closing report, kept until the engine takes it ------------------------

write_pending() {
    # $1 nonce, $2 status, $3 detail, $4 installed version - one per line.
    pending_tmp="$PENDING_FILE.tmp"
    {
        printf '%s\n' "$1" "$2"
        printf '%s\n' "$3" | tr -d '\r'
        printf '%s\n' "$4"
    } > "$pending_tmp"
    mv -f "$pending_tmp" "$PENDING_FILE"
}

read_pending() {
    p_nonce=""; p_status=""; p_detail=""; p_version=""
    {
        IFS= read -r p_nonce || true
        IFS= read -r p_status || true
        IFS= read -r p_detail || true
        IFS= read -r p_version || true
    } < "$PENDING_FILE"
}

deliver_pending() {
    read_pending
    if report "$p_nonce" "$p_status" "$p_detail" "$p_version"; then
        rm -f "$PENDING_FILE"
        return 0
    fi
    return 1
}

# pending_holds_the_poll NONCE STATUS -> 0 when a closing report was waiting
# (delivered now, or still waiting) and this poll should do nothing more; 1
# when there is none, or it belonged to a request that is no longer open and
# was dropped - a report the engine can only refuse must not be retried for ever.
pending_holds_the_poll() {
    [ -f "$PENDING_FILE" ] || return 1
    read_pending
    case "$2" in
        ""|done|failed|cancelled) open=0 ;;
        *) open=1 ;;
    esac
    if [ "$open" -eq 0 ] || [ "$1" != "$p_nonce" ]; then
        log "dropping an unsent $p_status report: its request is no longer open"
        rm -f "$PENDING_FILE"
        return 1
    fi
    deliver_pending || true
    return 0
}

# -- the world ----------------------------------------------------------------

close_world_if_open() {
    # Read the flag BEFORE writing it: closing a world a GM had already closed
    # would overwrite their reason, and reopening it afterwards would undo their
    # closure. Only a world this script closed is this script's to reopen.
    world=$(engine_call admin.server.update_request '{}') || { log "could not read the world's state; leaving it as it is"; return 0; }
    case "$(json_word "$world" maintenance_enabled)" in
        false)
            : > "$CLOSED_FILE"
            set_world true "Server update in progress; back shortly" || log "could not close the world (continuing)"
            ;;
        true) log "the world was already closed by a GM; leaving it closed" ;;
        *) log "could not tell whether the world is closed; leaving it as it is" ;;
    esac
}

reopen_world() {
    # $1 = how many tries. Only when this script closed it (the marker file).
    [ -f "$CLOSED_FILE" ] || return 0
    reopen_tries=0
    until set_world false ""; do
        reopen_tries=$((reopen_tries + 1))
        if [ "$reopen_tries" -ge "$1" ]; then
            log "could not reopen the world after $reopen_tries tries; will keep trying"
            return 0
        fi
        sleep "$RETRY_SECONDS"
    done
    rm -f "$CLOSED_FILE"
    log "world reopened"
}

finish() {
    # $1 nonce, $2 outcome, $3 detail, $4 installed version. The report goes to
    # disk first, so nothing after this point can lose it.
    write_pending "$1" "$2" "$3" "$4"
    reopen_world "$REOPEN_TRIES"
    finish_tries=0
    until deliver_pending; do
        finish_tries=$((finish_tries + 1))
        if [ "$finish_tries" -ge "$REOPEN_TRIES" ]; then
            log "could not deliver the $2 report yet; it is kept and retried on every poll"
            return 0
        fi
        sleep "$RETRY_SECONDS"
    done
}

run_update() {
    # $1 nonce, $2 channel. update.sh's own exit code and log say what happened:
    # 0 is installed; nonzero before it ever said "Stopping" means the .env
    # preflight refused and nothing was changed; nonzero after means its own
    # rollback already ran.
    report "$1" acked "" || return 0
    # Only now is the request ours: a failed ack leaves it `requested`, and the
    # next tick tries again.
    remember "$1"
    # Everything that can fail before the world is touched, is done before it.
    if ! run_log=$(mktemp "$PARENT_DIR/.xianxia-watcher-run.XXXXXX"); then
        finish "$1" failed "nothing was changed: the watcher could not create a scratch file in the folder above the project" ""
        return 0
    fi
    close_world_if_open
    report "$1" fetching "" || true
    log "running update.sh --upgrade --channel $2"
    run_status=0; installed=""
    sh "$UPDATE_SH" --upgrade --channel "$2" >"$run_log" 2>&1 || run_status=$?
    cat "$run_log" >> "$LOG_FILE"
    if [ "$run_status" -eq 0 ]; then
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
    # Reopen whatever happened (if it was this script that closed the world): a
    # failed update that leaves the world shut is worse than one that reopens it.
    finish "$1" "$outcome" "$detail" "$installed"
}

poll_once() {
    # A world this script closed and could not reopen is reopened first - one
    # try per poll; the engine may still be coming back.
    reopen_world 1
    answer=$(engine_call admin.server.update_request '{}') || return 0
    req_status=$(json_str "$answer" request_status)
    req_nonce=$(json_str "$answer" request_nonce)
    req_channel=$(json_str "$answer" request_channel)
    case "$req_channel" in stable|beta) ;; *) req_channel=stable ;; esac
    if pending_holds_the_poll "$req_nonce" "$req_status"; then
        heartbeat; return 0
    fi
    [ -n "$req_nonce" ] || { heartbeat; return 0; }
    case "$req_status" in
        requested)
            # Remembered and still `requested`: the engine was put back from a
            # backup by a rollback. The GM may cancel it; it is not run twice.
            if ! handled_before "$req_nonce"; then
                log "update requested (nonce $req_nonce, channel $req_channel)"
                IN_UPDATE=1; run_update "$req_nonce" "$req_channel"; IN_UPDATE=0
            fi
            ;;
        acked)
            if handled_before "$req_nonce"; then
                interrupted "$req_nonce" "$req_status"
            else
                # The ack reached the engine and its answer did not reach us.
                log "update $req_nonce was acked but never run; running it"
                IN_UPDATE=1; run_update "$req_nonce" "$req_channel"; IN_UPDATE=0
            fi
            ;;
        fetching|installing) interrupted "$req_nonce" "$req_status" ;;
    esac
    heartbeat
}

interrupted() {
    # An open request past `requested` with nothing running and no report
    # waiting: the process that was running it died. Close it, so the Request
    # button returns, and say so - the install may or may not have finished.
    log "update $1 was left $2 by a watcher that stopped; reporting it failed"
    report "$1" failed "the watcher stopped before it finished this update; check the server, then ask again" "" || true
}

cleanup() {
    [ -z "${SLEEP_PID:-}" ] || kill "$SLEEP_PID" 2>/dev/null || true
    rm -rf "$LOCK_DIR"
}

on_signal() {
    # An update in progress is not abandoned half-way - the world is closed and
    # update.sh is replacing the code - so it finishes, reports, and then the
    # loop ends. Anywhere else the signal ends the process now.
    if [ "$IN_UPDATE" -eq 1 ]; then
        STOP=1
        log "stop requested; finishing the update in progress first"
        return 0
    fi
    log "watcher stopped by a signal"
    exit 143
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
trap cleanup EXIT
trap on_signal INT TERM HUP

log "watcher started (interval ${INTERVAL}s, oneshot=$ONESHOT)"
if [ "$ONESHOT" -eq 1 ]; then
    poll_once
    exit 0
fi
while :; do
    poll_once
    if [ "$STOP" -eq 1 ]; then log "watcher stopped by a signal"; exit 143; fi
    # The sleep runs in the background and is waited on, so a signal ends the
    # wait at once instead of after the interval.
    sleep "$INTERVAL" &
    SLEEP_PID=$!
    wait "$SLEEP_PID" || true
    SLEEP_PID=
done
