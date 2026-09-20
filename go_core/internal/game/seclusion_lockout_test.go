package game

// The doors stay shut, and they open again on their own (v1.0.0-rc.56).
//
// The lockout is the half of this release that can brick an account if it is
// got wrong: a gate refusing every action, on state that only an action can
// clear, is a deadlock. So the two tests that matter most here are not "is a
// secluded player refused" but "can a secluded player ever get out": on the
// deadline, and before it.

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// Built on the cultivation fixture rather than the seclusion one, because
// what these tests have to show is an ordinary action going through - so the
// fixture has to be one an ordinary action can run against.
func lockedIn(t *testing.T, realDeadlineSecondsFromNow float64) (string, string) {
	t.Helper()
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS seclusion_sessions(user_id INTEGER PRIMARY KEY,mode TEXT NOT NULL,started_game_minute INTEGER NOT NULL,ends_game_minute INTEGER NOT NULL,last_settled_game_minute INTEGER NOT NULL,start_location TEXT NOT NULL DEFAULT '',environment_mult REAL NOT NULL DEFAULT 1.0,accumulated_gain INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'active',ended_reason TEXT NOT NULL DEFAULT '',ends_real_ts REAL,created_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0)`)
	clearCooldowns(t, path, 42)
	batch4SetCanonicalGameMinute(t, path, 1000)
	batch4Exec(t, path, `INSERT INTO seclusion_sessions(user_id,mode,started_game_minute,ends_game_minute,ends_real_ts,last_settled_game_minute,start_location,environment_mult,status)
		VALUES(42,'qi',1000,1480,?,1000,'abode:42',1,'active')`, nowSeconds()+realDeadlineSecondsFromNow)
	return path, world
}

func lockedAct(t *testing.T, path, world, op string, seq int) error {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{})
	_, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "lockout-" + op + "-" + string(rune('a'+seq)),
		Operation:  op,
		ActorID:    42,
		Payload:    raw})
	return err
}

func TestASecludedCultivatorIsRefusedOnTheAuthoritativePath(t *testing.T) {
	path, world := lockedIn(t, 3600)
	for seq, op := range []string{"cultivation.train", "exploration.explore", "shop.buy", "trade.offer", "item.use"} {
		err := lockedAct(t, path, world, op, seq)
		if err == nil || !strings.Contains(err.Error(), "closed-door") {
			t.Errorf("%s behind a closed door: %v", op, err)
		}
	}
}

// The way out. `seclusion.settle` is the one exempt operation, and it has to
// be: a retreat this gate cannot end is a character nobody can play again.
func TestTheWayOutIsNeverLocked(t *testing.T) {
	path, world := lockedIn(t, 3600)
	raw, _ := json.Marshal(map[string]any{"force_end": true, "end_reason": "emerged early"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "lockout-way-out",
		Operation:  "seclusion.settle",
		ActorID:    42,
		Payload:    raw}); err != nil {
		t.Fatalf("a secluded cultivator could not emerge: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42 AND status='active'`)); got != 0 {
		t.Fatal("the retreat is still active after an explicit end")
	}
	// And the doors are open again.
	if err := lockedAct(t, path, world, "cultivation.train", 9); err != nil && strings.Contains(err.Error(), "closed-door") {
		t.Fatalf("still locked out after emerging: %v", err)
	}
}

// The other end, and the load-bearing one: nobody has to come back. A retreat
// whose deadline has passed is settled and completed by the gate itself, so
// the next thing the player does simply works - and it does not matter
// whether a GM has switched the background sweep off.
func TestAnExpiredRetreatOpensItsOwnDoors(t *testing.T) {
	path, world := lockedIn(t, -1)
	if err := lockedAct(t, path, world, "cultivation.train", 1); err != nil {
		t.Fatalf("an expired retreat still refused: %v", err)
	}
	// Not merely let through: settled, paid and closed. A gate that opened
	// the doors without paying would lose the whole retreat silently.
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM seclusion_sessions WHERE user_id=42 AND status='completed'`)); got != 1 {
		t.Fatal("the gate let the action through without settling and ending the retreat")
	}
}

// A retreat started before schema 57 has no real deadline and keeps the
// game-minute end it was given. It must still open on its own when the world
// clock reaches that end - and the way out must still work before it, because
// a frozen world clock may never reach it at all.
func TestAGrandfatheredRetreatIsNotADeadlock(t *testing.T) {
	path, world := lockedIn(t, 3600)
	batch4Exec(t, path, `DELETE FROM seclusion_sessions WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO seclusion_sessions(user_id,mode,started_game_minute,ends_game_minute,last_settled_game_minute,start_location,environment_mult,status)
		VALUES(42,'qi',1000,500000,1000,'abode:42',1,'active')`)
	if err := lockedAct(t, path, world, "cultivation.train", 2); err == nil || !strings.Contains(err.Error(), "world-minutes remain") {
		t.Fatalf("a grandfathered retreat must refuse in world time: %v", err)
	}
	raw, _ := json.Marshal(map[string]any{"force_end": true, "end_reason": "emerged early"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "lockout-grandfathered-out",
		Operation:  "seclusion.settle",
		ActorID:    42,
		Payload:    raw}); err != nil {
		t.Fatalf("a five-hundred-thousand-minute retreat could not be ended: %v", err)
	}
}

// A GM is immune by construction: every admin.* lever falls through to the
// switch in ApplyWithWorld rather than coming through applyAuthoritative,
// which is the same asymmetry maintenance mode relies on. Stated as a test
// because it is a property of where the gate sits, and a later refactor that
// routed admin operations through the player path would take it away silently.
func TestAClosedDoorIsStillTheGMsToOpen(t *testing.T) {
	path, world := lockedIn(t, 3600)
	raw, _ := json.Marshal(map[string]any{"user_id": 42, "location": "Greenriver Town", "reason": "test"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "lockout-gm-relocate",
		Operation:  "admin.player.relocate",
		ActorID:    1,
		Payload:    raw}); err != nil && strings.Contains(err.Error(), "closed-door") {
		t.Fatalf("a GM was locked out of a player's retreat: %v", err)
	}
}
