package simulation

import (
	"testing"

	"xianxia/core/internal/storage"
)

// v1.28.0: a homestead's Defensive Formation was built and raised for stones
// and read by no rule. Inside their own property a fugitive's capture is
// slowed by a tenth a level, and held off entirely at ten; pressure still
// builds, because the hunter waits at the gate.
func TestAPropertysWardSlowsTheCapture(t *testing.T) {
	path := setupSimulationDB(t, trackingPursuitSchema+`
CREATE TABLE cave_abodes(abode_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, location_key TEXT NOT NULL, defense_level INTEGER NOT NULL DEFAULT 0);
INSERT INTO cave_abodes(user_id,location_key,defense_level) VALUES(45,'abode:45',5),(46,'abode:46',10),(47,'abode:99',10);
`)
	cornered(t, path, 42, "Ash Wolf Hunting Ground") // open country
	cornered(t, path, 45, "abode:45")                // their own property, ward 5
	cornered(t, path, 46, "abode:46")                // their own property, ward 10
	cornered(t, path, 47, "abode:47")                // somebody's property, not theirs
	sweepHunters(t, path)
	capture := func(userID int64) int64 {
		return storage.ParseInt(simScalar(t, path, `SELECT capture_progress FROM bounty_hunter_pursuits WHERE user_id=?`, userID))
	}
	open := capture(42)
	if open <= 0 {
		t.Fatalf("the open-country capture did not advance (%d); the sweep is broken, not the tree", open)
	}
	if got := capture(45); got*2 > open+1 || got*2 < open-1 {
		t.Fatalf("a level-5 ward let the capture reach %d, want half of %d", got, open)
	}
	if got := capture(46); got != 0 {
		t.Fatalf("a level-10 ward let the capture reach %d, want 0", got)
	}
	if got := capture(47); got != open {
		t.Fatalf("a ward that is not the fugitive's slowed the capture to %d of %d", got, open)
	}
	if got := storage.ParseInt(simScalar(t, path, `SELECT pressure FROM bounty_hunter_pursuits WHERE user_id=46`)); got <= 70 {
		t.Fatalf("pressure froze behind the ward (%d): the hunter is at the gate either way", got)
	}
}
