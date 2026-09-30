package game

// A software update the GM asks for from the dashboard (v1.4.0).
//
// Nothing inside the stack can update it: code is baked into the images and
// no container holds the Docker socket or the host checkout. What can is
// `update.sh` on the NAS, and a watcher beside it (`update_watch.sh`) that
// polls for a request and runs it. So an update is two world_state rows and
// three operations:
//
//   - admin.server.request_update  - the GM's ask, audited, refused while one
//     is already open. Writes `update_request`.
//   - admin.server.update_status   - the watcher's reports (acked, fetching,
//     done, failed), audited under actor 0 so the audit table shows who wrote
//     them. Each must carry the open request's nonce, and a closed request
//     takes no more reports: a watcher that restarted, or a second one, cannot
//     re-report against a request already settled. A terminal report also
//     writes `update_result`, a separate row, so the last outcome outlives the
//     next request. A `heartbeat` report carries no nonce and no audit row: it
//     says the watcher is alive and nothing about any request.
//   - admin.server.update_request  - the read both the watcher and the
//     dashboard use. No audit row. It also answers the request's nonce, status
//     and channel, and whether the world is closed, as flat top-level values
//     (v1.12.3), so the watcher - a shell script - never has to cut a JSON
//     object out of the answer by hand.
//   - admin.server.cancel_update   - the GM's way to close a request that will
//     never finish (v1.12.3), audited. Without it an open request refused every
//     later one for ever, and only an edit of the database could clear it.
//
// The watcher reaches these the way `update.sh` already reaches
// `/v1/db/backups`: `docker compose exec` into the engine container, the token
// read from the container's own environment. No new port, no new privilege.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

const (
	updateRequestKey   = "update_request"
	updateResultKey    = "update_result"
	updateHeartbeatKey = "update_watcher_heartbeat"
	// A watcher whose heartbeat is older than this is "not running" - the
	// dashboard's card says so at the same age (UPDATE_WATCHER_STALE_SECONDS in
	// app/dashboard/server.py, held equal by a test), and it is the only point
	// past which a request the watcher has already picked up may be cancelled.
	updateWatcherStaleSeconds = 15 * 60
	// The watcher's reports are bounded here rather than trusted: a detail is
	// the tail of a log, and the log stays on the host.
	updateDetailLimit = 300
	// The watcher writes its reports as this actor, so an audit row it wrote
	// is never mistaken for a GM's.
	updateWatcherActorID int64 = 0
)

// updateStatuses are the reports a watcher may make, in the order a run makes
// them. `done` and `failed` are terminal.
var updateStatuses = map[string]bool{
	"acked": true, "fetching": true, "installing": true, "done": true, "failed": true,
}

var updateChannels = map[string]bool{"stable": true, "beta": true}

type updateRequestState struct {
	Status      string  `json:"status"`
	Nonce       string  `json:"nonce"`
	Channel     string  `json:"channel"`
	Reason      string  `json:"reason"`
	RequestedAt float64 `json:"requested_at"`
	RequestedBy int64   `json:"requested_by"`
	UpdatedAt   float64 `json:"updated_at"`
	Detail      string  `json:"detail"`
}

// open is "not terminal": done, failed and cancelled close a request.
func (s updateRequestState) open() bool {
	return s.Nonce != "" && s.Status != "" && s.Status != "done" && s.Status != "failed" && s.Status != "cancelled"
}

// updateWatcherRunning is the card's own test: a heartbeat younger than the
// stale window. An absent or unreadable heartbeat is a watcher nobody has
// heard from - not running - never a zero age.
func updateWatcherRunning(heartbeat map[string]any, now float64) bool {
	at, ok := heartbeat["at"].(float64)
	if !ok {
		return false
	}
	return now-at < updateWatcherStaleSeconds
}

// readUpdateStateTx reads one of the three rows. An absent or unreadable blob
// is no request, the way the maintenance flag reads: a watcher that cannot
// read the row must not invent an update to run.
func readUpdateStateTx(conn *storage.Conn, key string) (map[string]any, error) {
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{key})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, nil
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(fmt.Sprint(row["value_json"])), &out); err != nil {
		return nil, nil
	}
	return out, nil
}

func readUpdateRequestTx(conn *storage.Conn) (updateRequestState, error) {
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{updateRequestKey})
	if err != nil {
		return updateRequestState{}, err
	}
	row := firstRowMap(res)
	if row == nil {
		return updateRequestState{}, nil
	}
	var state updateRequestState
	if err := json.Unmarshal([]byte(fmt.Sprint(row["value_json"])), &state); err != nil {
		return updateRequestState{}, nil
	}
	return state, nil
}

func writeWorldStateTx(conn *storage.Conn, key string, value any, now float64) error {
	encoded, _ := json.Marshal(value)
	_, err := conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)
         ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`,
		[]any{key, string(encoded), now},
	)
	return err
}

// adminServerRequestUpdate is the GM's ask: `admin.server.request_update`.
func adminServerRequestUpdate(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	channel := strings.ToLower(strings.TrimSpace(stringField(p, "channel")))
	if channel == "" {
		channel = "stable"
	}
	if !updateChannels[channel] {
		return nil, errors.New("channel must be stable or beta")
	}
	reason := strings.TrimSpace(stringField(p, "reason"))
	if len(reason) > updateDetailLimit {
		reason = reason[:updateDetailLimit]
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	before, err := readUpdateRequestTx(conn)
	if err != nil {
		return nil, err
	}
	if before.open() {
		return nil, fmt.Errorf("an update is already %s; wait for the watcher to finish it", before.Status)
	}
	nonce, err := secureChoiceID()
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	next := updateRequestState{
		Status: "requested", Nonce: nonce, Channel: channel, Reason: reason,
		RequestedAt: now, RequestedBy: adminUserID, UpdatedAt: now,
	}
	if err := writeWorldStateTx(conn, updateRequestKey, next, now); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.server.request_update", updateRequestKey,
		map[string]any{"status": before.Status},
		map[string]any{"status": next.Status, "channel": channel}, reason); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"status": next.Status, "channel": channel, "nonce": nonce, "requested_at": now}, nil
}

// adminServerUpdateStatus is the watcher's report: `admin.server.update_status`.
func adminServerUpdateStatus(conn *storage.Conn, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	status := strings.ToLower(strings.TrimSpace(stringField(p, "status")))
	now := float64(time.Now().UnixNano()) / 1e9
	if status == "heartbeat" {
		if err := begin(conn); err != nil {
			return nil, err
		}
		defer func() {
			if conn.InTransaction() {
				rollback(conn)
			}
		}()
		if err := writeWorldStateTx(conn, updateHeartbeatKey, map[string]any{"at": now}, now); err != nil {
			return nil, err
		}
		if err := conn.Commit(); err != nil {
			return nil, err
		}
		return map[string]any{"status": "heartbeat", "at": now}, nil
	}
	if !updateStatuses[status] {
		return nil, errors.New("status must be one of acked, fetching, installing, done, failed, heartbeat")
	}
	nonce := strings.TrimSpace(stringField(p, "nonce"))
	detail := strings.TrimSpace(stringField(p, "detail"))
	if len(detail) > updateDetailLimit {
		detail = detail[:updateDetailLimit]
	}
	installed := strings.TrimSpace(stringField(p, "installed_version"))
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	before, err := readUpdateRequestTx(conn)
	if err != nil {
		return nil, err
	}
	if !before.open() {
		return nil, errors.New("no update is in progress")
	}
	if nonce == "" || nonce != before.Nonce {
		return nil, errors.New("the report names a different update than the one in progress")
	}
	next := before
	next.Status = status
	next.Detail = detail
	next.UpdatedAt = now
	if err := writeWorldStateTx(conn, updateRequestKey, next, now); err != nil {
		return nil, err
	}
	terminal := status == "done" || status == "failed"
	if terminal {
		result := map[string]any{
			"status": status, "detail": detail, "installed_version": installed,
			"finished_at": now, "requested_by": before.RequestedBy, "requested_at": before.RequestedAt,
			"channel": before.Channel,
		}
		if err := writeWorldStateTx(conn, updateResultKey, result, now); err != nil {
			return nil, err
		}
	}
	if err := auditAdmin(conn, updateWatcherActorID, "admin.server.update_status", updateRequestKey,
		map[string]any{"status": before.Status},
		map[string]any{"status": status, "installed_version": installed}, detail); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"status": status, "terminal": terminal, "nonce": nonce}, nil
}

// adminServerCancelUpdate is the GM's way out: `admin.server.cancel_update`.
// A request is open until the watcher reports done or failed, and a watcher
// that died, was stopped or never saw it leaves it open for ever - while
// adminServerRequestUpdate refuses a second request beside an open one. So an
// open request can be cancelled, but only when nothing is acting on it:
//
//   - still `requested`: the watcher has not picked it up, so there is nothing
//     to stop; or
//   - in any later state, once the watcher has not been heard from for
//     updateWatcherStaleSeconds - the same "not running" the card shows.
//
// An install in progress (a live watcher, a later state) is refused: the
// world is closed, update.sh is replacing the code, and a request cancelled
// under it would leave the watcher's closing report with nothing to land on.
func adminServerCancelUpdate(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	reason := strings.TrimSpace(stringField(p, "reason"))
	if len(reason) > updateDetailLimit {
		reason = reason[:updateDetailLimit]
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	before, err := readUpdateRequestTx(conn)
	if err != nil {
		return nil, err
	}
	if !before.open() {
		return nil, errors.New("there is no open update request to cancel")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if before.Status != "requested" {
		heartbeat, err := readUpdateStateTx(conn, updateHeartbeatKey)
		if err != nil {
			return nil, err
		}
		if updateWatcherRunning(heartbeat, now) {
			return nil, fmt.Errorf("the update is already %s and the watcher is running; an install in progress cannot be cancelled", before.Status)
		}
	}
	next := before
	next.Status = "cancelled"
	next.Detail = reason
	if next.Detail == "" {
		next.Detail = "cancelled by a GM"
	}
	next.UpdatedAt = now
	if err := writeWorldStateTx(conn, updateRequestKey, next, now); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.server.cancel_update", updateRequestKey,
		map[string]any{"status": before.Status, "nonce": before.Nonce},
		map[string]any{"status": next.Status}, reason); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"status": next.Status, "previous_status": before.Status, "nonce": before.Nonce}, nil
}

// adminServerUpdateRequest is the read: `admin.server.update_request`. It
// answers the request, the last result and the watcher's heartbeat as stored,
// and writes nothing.
func adminServerUpdateRequest(conn *storage.Conn) (any, error) {
	request, err := readUpdateStateTx(conn, updateRequestKey)
	if err != nil {
		return nil, err
	}
	result, err := readUpdateStateTx(conn, updateResultKey)
	if err != nil {
		return nil, err
	}
	heartbeat, err := readUpdateStateTx(conn, updateHeartbeatKey)
	if err != nil {
		return nil, err
	}
	// A missing row is JSON null, not an empty object: a typed nil map would
	// read as "a request with no fields" to a caller asking `!= nil`.
	present := func(m map[string]any) any {
		if m == nil {
			return nil
		}
		return m
	}
	// Flat values for the watcher, which is a shell script: an empty string is
	// "no request", never a missing key, so the same sed answers both. They sit
	// beside the object rather than inside it because cutting an object out of
	// the answer by hand broke on any `}` in the GM's reason.
	world, err := readMaintenanceStateTx(conn)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"request": present(request), "result": present(result), "heartbeat": present(heartbeat),
		"request_nonce":       stringField(request, "nonce"),
		"request_status":      stringField(request, "status"),
		"request_channel":     stringField(request, "channel"),
		"maintenance_enabled": world.Enabled,
	}, nil
}
