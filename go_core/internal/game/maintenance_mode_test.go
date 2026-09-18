package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func storedMaintenance(t *testing.T, path string) map[string]any {
	t.Helper()
	raw := scalar(t, path, `SELECT value_json FROM world_state WHERE key='maintenance_mode'`)
	if raw == nil {
		return nil
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(stringOf(raw))), &out); err != nil {
		t.Fatalf("unreadable maintenance blob %v: %v", raw, err)
	}
	return out
}

func stringOf(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func TestClosingTheWorldRefusesPlayersAndAudits(t *testing.T) {
	path := setupAdminDB(t)

	result := applyAdmin(t, path, "admin.server.maintenance_mode", map[string]any{
		"enabled": true, "reason": "upgrading to rc.41",
	})
	data, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if data["enabled"] != true || data["changed"] != true {
		t.Fatalf("result=%#v", data)
	}
	if state := storedMaintenance(t, path); state["enabled"] != true || state["reason"] != "upgrading to rc.41" {
		t.Fatalf("stored state=%#v", state)
	}
	if got := storage.ParseInt(scalar(t, path,
		`SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.server.maintenance_mode'`)); got != 1 {
		t.Fatalf("audit rows=%d, want 1", got)
	}

	// A player action through the authoritative path is refused, in the
	// operator's own words.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	err = checkMaintenanceTx(conn)
	if err == nil {
		t.Fatal("a closed world must refuse a player action")
	}
	if !strings.Contains(err.Error(), "upgrading to rc.41") {
		t.Fatalf("the refusal must carry the operator's reason, got %q", err)
	}
}

func TestOpeningTheWorldAgainClearsTheReasonAndLetsPlayersBack(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.server.maintenance_mode", map[string]any{"enabled": true, "reason": "a nap"})
	applyAdmin(t, path, "admin.server.maintenance_mode", map[string]any{"enabled": false})

	state := storedMaintenance(t, path)
	if state["enabled"] != false || state["reason"] != "" {
		t.Fatalf("reopening must clear the reason: %#v", state)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := checkMaintenanceTx(conn); err != nil {
		t.Fatalf("an open world must refuse nobody: %v", err)
	}
}

func TestAClosedWorldIsStillTheGMsToOpen(t *testing.T) {
	// The reason the gate lives in applyAuthoritative and not in the HTTP
	// middleware: every admin.* lever reaches the switch in ApplyWithWorld
	// without passing the player gate, so closing the world can never lock
	// the operator out of reopening it.
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.server.maintenance_mode", map[string]any{"enabled": true, "reason": "locked"})

	// A GM lever of a different kind still answers while the world is shut.
	applyAdmin(t, path, "admin.world.advance_time", map[string]any{"minutes": 60, "reason": "during maintenance"})

	// And the lever that reopens it answers too.
	applyAdmin(t, path, "admin.server.maintenance_mode", map[string]any{"enabled": false})
	if state := storedMaintenance(t, path); state["enabled"] != false {
		t.Fatalf("the world stayed shut: %#v", state)
	}
}

func TestAnAbsentOrBrokenFlagLeavesTheWorldOpen(t *testing.T) {
	// Fail-open, deliberately: a flag that gates all play must fail towards
	// letting play continue. A world nobody can enter is also a world nobody
	// can reach to unlock.
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := checkMaintenanceTx(conn); err != nil {
		t.Fatalf("no row must mean open, got %v", err)
	}

	if _, err := conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES('maintenance_mode','not json at all',0)`, nil,
	); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if err := checkMaintenanceTx(conn); err != nil {
		t.Fatalf("an unreadable blob must mean open, got %v", err)
	}
}

func TestTheModeRefusesAPayloadThatDoesNotSayWhich(t *testing.T) {
	path := setupAdminDB(t)
	raw, _ := json.Marshal(map[string]any{"reason": "no enabled field"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.server.maintenance_mode", Payload: raw}); err == nil {
		t.Fatal("expected a refusal when enabled is missing")
	} else if !strings.Contains(err.Error(), "enabled must be true") {
		t.Fatalf("unexpected error: %v", err)
	}
}
