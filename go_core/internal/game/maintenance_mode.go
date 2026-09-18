package game

// Maintenance mode: the doors an operator can bolt while they update (rc.41).
//
// `maintenanceBarrier` in internal/server already exists and is a different
// thing: a sync.RWMutex that makes a restore or a VACUUM wait for in-flight
// writes and makes them wait for it. It never refuses anybody - a request
// held there queues and then runs normally, which is exactly right for a
// thirty-second restore and exactly wrong for an operator who is about to
// swap the binaries out from under a live world. This is the refusal.
//
// Where it sits is the whole design. `applyAuthoritative` handles the ~150
// player-facing operations; every `admin.*` lever falls through to the switch
// in `ApplyWithWorld` instead, so a gate here refuses players and cannot
// refuse a GM - which is what makes the mode switchable off from inside the
// game rather than only from a shell. The same asymmetry the moderation gate
// beside it relies on.
//
// The scope it does NOT have is worth stating as plainly as moderation states
// its own: Python's raw `/v1/db` reads and writes do not pass through here, so
// the engine gate alone would still let a presentation-layer read answer. The
// bot holds the matching gate at its four doors (slash, hub panel, typed line,
// shorthand) and refuses there with the operator's own words. This half is the
// backstop that does not depend on the bot behaving.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

const maintenanceStateKey = "maintenance_mode"

// The reason is shown to players verbatim, so it is bounded here rather than
// trusted from the payload.
const maintenanceReasonLimit = 300

type maintenanceState struct {
	Enabled bool    `json:"enabled"`
	Reason  string  `json:"reason"`
	Since   float64 `json:"since"`
	By      int64   `json:"by"`
}

// readMaintenanceStateTx never inserts. An absent row, an unreadable blob or a
// blob with no `enabled` all mean the world is open: the failure of a flag
// that gates play must be to let play continue, not to lock everybody out of a
// world nobody can reach to unlock. The automation flags beside it fail open
// for the same reason.
func readMaintenanceStateTx(conn *storage.Conn) (maintenanceState, error) {
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{maintenanceStateKey})
	if err != nil {
		return maintenanceState{}, err
	}
	row := firstRowMap(res)
	if row == nil {
		return maintenanceState{}, nil
	}
	var state maintenanceState
	if err := json.Unmarshal([]byte(fmt.Sprint(row["value_json"])), &state); err != nil {
		return maintenanceState{}, nil
	}
	return state, nil
}

// MaintenanceState is the flag as the rest of the engine and the bot read it.
func MaintenanceState(conn *storage.Conn) (enabled bool, reason string, since float64, err error) {
	state, err := readMaintenanceStateTx(conn)
	if err != nil {
		return false, "", 0, err
	}
	return state.Enabled, state.Reason, state.Since, nil
}

func maintenanceRefusal(state maintenanceState) error {
	reason := strings.TrimSpace(state.Reason)
	if reason == "" {
		return errors.New("the world is closed for maintenance; an administrator is updating the server")
	}
	return fmt.Errorf("the world is closed for maintenance: %s", reason)
}

// checkMaintenanceTx is the player gate. It is called from applyAuthoritative
// only, so it never sees a GM lever.
func checkMaintenanceTx(conn *storage.Conn) error {
	state, err := readMaintenanceStateTx(conn)
	if err != nil {
		return err
	}
	if !state.Enabled {
		return nil
	}
	return maintenanceRefusal(state)
}

// adminServerMaintenanceMode is the lever: `admin.server.maintenance_mode`.
// It follows admin.automation.set exactly - one JSON blob under a world_state
// key, written in a transaction with its audit row.
func adminServerMaintenanceMode(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	enabled, ok := p["enabled"].(bool)
	if !ok {
		return nil, errors.New("enabled must be true (close the world) or false (open it)")
	}
	reason := strings.TrimSpace(stringField(p, "reason"))
	if len(reason) > maintenanceReasonLimit {
		reason = reason[:maintenanceReasonLimit]
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	before, err := readMaintenanceStateTx(conn)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	next := maintenanceState{Enabled: enabled, Reason: reason, Since: before.Since, By: adminUserID}
	if enabled && !before.Enabled {
		next.Since = now
	}
	if !enabled {
		next.Since = 0
		next.Reason = ""
	}
	encoded, _ := json.Marshal(next)
	if _, err = conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)
         ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`,
		[]any{maintenanceStateKey, string(encoded), now},
	); err != nil {
		return nil, err
	}
	if err = auditAdmin(conn, adminUserID, "admin.server.maintenance_mode", maintenanceStateKey,
		map[string]any{"enabled": before.Enabled, "reason": before.Reason},
		map[string]any{"enabled": next.Enabled, "reason": next.Reason},
		fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{
		"enabled": next.Enabled, "reason": next.Reason, "since": next.Since, "by": next.By,
		"changed": before.Enabled != next.Enabled,
	}, nil
}
