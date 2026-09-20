package game

import (
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

// The peach that nothing grew (v1.0.0-rc.50).
//
// `hundred_year_peach` was the one item in a catalogue of 287 that nothing
// could produce: no shop, no recipe, no realm room, no event, and no line of
// Go or Python. Fifty years of lifespan, 12,000 base price, and
// `door_event_chance: 65` - which meant a second authored system was dark too,
// because `advanced_maintenance.go` only writes an `auction_door_risks` row
// when a legendary lot is struck, and you cannot auction a fruit that does not
// exist.
//
// It grows in the Salt King's Throne now, the last room of a realm with no key
// that opens only when the marsh floods. **The chance is the point**: a realm
// is walked again on every run - `secret_realm_runs` keeps one row per user
// and resets `room_index` to 0 on entry - so a guaranteed drop there would be
// a fifty-year fruit on tap. This test enters twice for exactly that reason.

// `base` is the action id each request carries, and it must differ between
// runs: duplicate requests are idempotent (v0.22.2), so reusing ids replays
// the first run's receipts and grants nothing the second time - which is
// exactly how the first version of this test failed, with "2 then 2".
func saltKingRun(t *testing.T, path, world string, base int) map[string]any {
	t.Helper()
	clearCooldowns(t, path, 42)
	now := float64(time.Now().UnixNano()) / 1e9
	batch4Exec(t, path, "DELETE FROM world_events WHERE dedupe_key='secret_realm:salt_kings_barrow'")
	batch4Exec(t, path, "INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at,thread_id) VALUES(?,?,?,?,?,?,1,?,?,?)",
		"peach-open", "secret_realm:salt_kings_barrow", "secret_realm", "Salt King's Barrow",
		"Salt King's Ruin", `{"realm_id":"salt_kings_barrow"}`, now-10, now+3600, 424242)
	if entered := batch4Result(t, batch4Apply(t, path, world, "secret_realm.enter", base, map[string]any{
		"realm_id": "salt_kings_barrow", "game_minute": 700})); entered["entered"] != true {
		t.Fatalf("enter=%v", entered)
	}
	var last map[string]any
	for i := 0; i < 4; i++ {
		clearCooldowns(t, path, 42)
		last = batch4Result(t, batch4Apply(t, path, world, "secret_realm.explore", base+1+i, map[string]any{
			"game_minute": 701 + i}))
		if success, _ := last["success"].(bool); !success {
			t.Fatalf("room %d should succeed with the fixture's stats: %v", i, last)
		}
	}
	return last
}

func peachesHeld(t *testing.T, path string) int64 {
	t.Helper()
	return storage.ParseInt(actionScalar(t,
		path, "SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='hundred_year_peach'"))
}

func TestTheSaltKingsThroneYieldsThePeachOnlyOnTheRareRoll(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	// The barrow's floor is realm 2, and fifty years is a prize that only
	// means anything down here - every deeper keyless realm is the wrong
	// audience for it.
	batch4Exec(t, path, "UPDATE characters SET location='Salt King''s Ruin',realm_index=3 WHERE user_id=42")

	// A miss first, so the run that finds one cannot be a leftover.
	restore := lendSecretRealmRareDice(t, 99)
	if throne := saltKingRun(t, path, world, 4000); throne["rare_items"] != nil {
		t.Fatalf("a roll of 99 against a chance of 6 found something: %v", throne["rare_items"])
	}
	if got := peachesHeld(t, path); got != 0 {
		t.Fatalf("peaches after a missed roll=%d", got)
	}
	restore()

	// And the same barrow, walked again - which is the shape that made the
	// chance necessary in the first place.
	restore = lendSecretRealmRareDice(t, 0)
	throne := saltKingRun(t, path, world, 4100)
	rare, ok := throne["rare_items"].(map[string]int64)
	if !ok || rare["hundred_year_peach"] != 1 {
		t.Fatalf("a roll of 0 against a chance of 6 found nothing: %v", throne["rare_items"])
	}
	restore()
	if got := peachesHeld(t, path); got != 1 {
		t.Fatalf("peaches after a hit=%d; the find never reached the inventory", got)
	}
}

// lendSecretRealmRareDice answers every rare roll with one value and hands back
// the restore, which the caller must run. gamerng is crypto/rand with no seed,
// so a test that asserted "walk it enough times and surely one dropped" would
// fail for no reason at a rate nobody can drive to zero (CLAUDE.md).
func lendSecretRealmRareDice(t *testing.T, value int) func() {
	t.Helper()
	original := secretRealmRareIntn
	secretRealmRareIntn = func(int) (int, error) { return value, nil }
	return func() { secretRealmRareIntn = original }
}

// The ordinary rooms are untouched by any of this: a room that holds two
// spirit iron still holds them whether the throne's roll lands or not.
func TestTheOrdinaryRoomsPayTheSameWhicheverWayTheRareRollGoes(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "UPDATE characters SET location='Salt King''s Ruin',realm_index=3 WHERE user_id=42")

	restore := lendSecretRealmRareDice(t, 99)
	saltKingRun(t, path, world, 4200)
	restore()
	missed := storage.ParseInt(actionScalar(t, path,
		"SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='spirit_iron'"))

	restore = lendSecretRealmRareDice(t, 0)
	saltKingRun(t, path, world, 4300)
	restore()
	hit := storage.ParseInt(actionScalar(t, path,
		"SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='spirit_iron'"))

	if missed <= 0 || hit != missed*2 {
		t.Fatalf("the Salt Stair's two spirit iron changed with the rare roll: %d then %d", missed, hit)
	}
}
