package game

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The road's pace (v1.2.0). Player feedback: "instant travels ... I don't have
// to wait half hour". The wait a road costs is TRAVEL_TIME_PERCENT of its
// length, default 0, and only the wait puts a traveller in transit.

// withTheOldPace pins the pace the game shipped with, for the tests written
// about the transit row, the arrival minute and the countdown: they are about
// what a wait does, and at the default there is none to do it.
func withTheOldPace(t *testing.T) {
	t.Helper()
	t.Setenv(travelTimePercentKey, "100")
}

func instantTravelDB(t *testing.T) (string, string) {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town',vitality=100,spirit_stones=100 WHERE user_id=42`)
	syncPurse(t, path)
	batch4SetCanonicalGameMinute(t, path, 3000)
	previous := roadEncounterIntn
	roadEncounterIntn = func(n int) (int, error) { return n - 1, nil }
	t.Cleanup(func() { roadEncounterIntn = previous })
	return path, world
}

func TestAtTheDefaultARoadIsWalkedInTheTelling(t *testing.T) {
	t.Setenv(travelTimePercentKey, "")
	path, world := instantTravelDB(t)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{
		"destination": "Azure Crown Imperial City", "mode": "known",
	}))
	if storage.ParseInt(result["travel_minutes"]) <= 0 {
		t.Fatalf("the road's length is no longer reported: %v", result["travel_minutes"])
	}
	if got := storage.ParseInt(result["wait_minutes"]); got != 0 {
		t.Fatalf("wait_minutes=%d at the default, want 0", got)
	}
	if traveling, _ := result["traveling"].(bool); traveling {
		t.Fatal("a road at the default pace put the traveller in transit")
	}
	if got := storage.ParseInt(result["arrival_game_minute"]); got != 3000 {
		t.Fatalf("arrival_game_minute=%d, want the departure minute 3000", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_state WHERE key=?`, roadTransitStateKey(42))); got != 0 {
		t.Fatalf("a transit row was written at the default: %d", got)
	}
	// The next mutation is not refused: the whole point.
	if _, err := batch4ApplyErr(path, world, "check.resolve", 42, 2, map[string]any{"attribute": "body", "tn": 10, "label": "after an instant road"}); err != nil {
		t.Fatalf("the action after an instant road was refused: %v", err)
	}
	if got := actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`); fmtAny(got) != "Azure Crown Imperial City" && !isGateOf(t, world, fmtAny(got), "Azure Crown Imperial City") {
		t.Fatalf("the traveller stands at %v, want the destination or its gate", got)
	}
}

func TestAShareOfTheRoadIsWaited(t *testing.T) {
	t.Setenv(travelTimePercentKey, "50")
	path, world := instantTravelDB(t)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{
		"destination": "Azure Crown Imperial City", "mode": "known",
	}))
	length := storage.ParseInt(result["travel_minutes"])
	if got := storage.ParseInt(result["wait_minutes"]); got != length/2 {
		t.Fatalf("wait_minutes=%d at 50 percent of a %d-minute road, want %d", got, length, length/2)
	}
	if got := storage.ParseInt(result["arrival_game_minute"]); got != 3000+length/2 {
		t.Fatalf("arrival_game_minute=%d, want %d", got, 3000+length/2)
	}
	if traveling, _ := result["traveling"].(bool); !traveling {
		t.Fatal("half a road's wait did not put the traveller in transit")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_state WHERE key=?`, roadTransitStateKey(42))); got != 1 {
		t.Fatalf("transit rows=%d, want 1", got)
	}
}

func TestAnUnreadablePaceIsTheDefault(t *testing.T) {
	for _, raw := range []string{"", "abc", "-5", "101", "1000"} {
		t.Setenv(travelTimePercentKey, raw)
		if got := travelTimePercentFromEnv(); got != travelTimePercentDefault {
			t.Fatalf("%q read as %d, want the default %d", raw, got, travelTimePercentDefault)
		}
	}
	t.Setenv(travelTimePercentKey, "100")
	if got := scaledTravelWait(nil, 90); got != 90 {
		t.Fatalf("at 100 a 90-minute road waits %d", got)
	}
	t.Setenv(travelTimePercentKey, "0")
	if got := scaledTravelWait(nil, 90); got != 0 {
		t.Fatalf("at 0 a 90-minute road waits %d", got)
	}
}

func isGateOf(t *testing.T, world, place, city string) bool {
	t.Helper()
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	return cityOf(catalog, place) == city
}

// The GM sets the pace from the dashboard (v1.7.9): the stored choice beats
// the .env baseline, the roads wait what it says, and the write is audited.
func TestTheGMsTravelPaceBeatsTheEnvAndIsAudited(t *testing.T) {
	t.Setenv(travelTimePercentKey, "")
	path, world := instantTravelDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_user_id INTEGER NOT NULL,action TEXT,target TEXT,before_json TEXT,after_json TEXT,reason TEXT,created_at REAL)`)
	if _, err := applyAdminRaw(t, path, "admin.world.set_travel_pace", 7, map[string]any{"percent": 101, "reason": "t"}); err == nil {
		t.Fatal("a pace above 100 was stored")
	}
	if _, err := applyAdminRaw(t, path, "admin.world.set_travel_pace", 7, map[string]any{"percent": 50, "reason": "t"}); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(scalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.world.set_travel_pace'`)); got != 1 {
		t.Fatalf("audit rows %d, want 1", got)
	}
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.travel", 1, map[string]any{
		"destination": "Azure Crown Imperial City", "mode": "known",
	}))
	travel, wait := storage.ParseInt(result["travel_minutes"]), storage.ParseInt(result["wait_minutes"])
	if travel <= 0 || wait != travel*50/100 {
		t.Fatalf("travel=%d wait=%d, want half the road at the stored pace", travel, wait)
	}
}

// A pace that is not a whole number is refused, not read as 0 (v1.12.3).
// `storage.ParseInt` answers 0 for 50.5, a word and null, and 0 is in range -
// it is instant travel - so a GM typing 50.5 silently switched the waits off.
func TestAPaceThatIsNotAWholeNumberIsRefused(t *testing.T) {
	t.Setenv(travelTimePercentKey, "")
	path, _ := instantTravelDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_user_id INTEGER NOT NULL,action TEXT,target TEXT,before_json TEXT,after_json TEXT,reason TEXT,created_at REAL)`)
	if _, err := applyAdminRaw(t, path, "admin.world.set_travel_pace", 7, map[string]any{"percent": 40, "reason": "t"}); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []any{50.5, "fifty", "50", nil, true, -1, 100.5, 1e300} {
		if _, err := applyAdminRaw(t, path, "admin.world.set_travel_pace", 7, map[string]any{"percent": bad, "reason": "t"}); err == nil {
			t.Fatalf("a pace of %#v was accepted; it would have been stored as 0 (instant travel)", bad)
		}
	}
	stored := fmt.Sprint(scalar(t, path, `SELECT value_json FROM world_state WHERE key='travel_pace'`))
	if !strings.Contains(stored, "40") {
		t.Fatalf("a refused pace changed the stored one: %s", stored)
	}
	// A whole number written as a float is still a whole number.
	if _, err := applyAdminRaw(t, path, "admin.world.set_travel_pace", 7, map[string]any{"percent": 60.0, "reason": "t"}); err != nil {
		t.Fatalf("60.0 is the whole number 60: %v", err)
	}
}
