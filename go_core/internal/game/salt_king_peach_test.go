package game

import (
	"encoding/json"
	"path/filepath"
	"testing"
	"time"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
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

// --- from permanent_treasure_test.go ---

// A realm's treasure is kept (v1.0.0-rc.54).
//
// `ItemUse.DurationGameMinutes` has meant "0 does not expire" since v0.21.0 and
// **no item in the catalogue has ever set it**: every effect the game shipped
// ran for 120 to 360 minutes. The five upper-world realms added here each hold
// one find that grants +1 to the attribute its own last trial tests, for good -
// so the field stops being a promise the content never took up.
//
// Two halves have to hold or the treasure is decoration. The writer must store
// NULL rather than a minute, and every reader is
// `ends_game_minute IS NULL OR ends_game_minute > ?` - so a 0 written as 0
// would read as expired the instant it was drunk, which is precisely the shape
// this drives out.
func permanentTreasureCatalog() worlddata.Catalog {
	return worlddata.Catalog{Items: map[string]worlddata.Item{
		"keepsake": {
			Name: "A Realm's Keepsake",
			Use: worlddata.ItemUse{
				EffectKey:           "keepsake",
				Name:                "The Realm's Mark",
				DurationGameMinutes: 0,
				Effect: map[string]any{
					"modifiers": []any{map[string]any{"stat": "will", "operation": "add", "value": 1}},
				},
			},
		},
	}}
}

func TestARealmsTreasureNeverExpires(t *testing.T) {
	path := filepath.Join(t.TempDir(), "treasure.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, qi INTEGER, qi_max INTEGER, vitality INTEGER, vitality_max INTEGER,
	life_extension_years INTEGER NOT NULL DEFAULT 0, updated_at REAL);
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY, user_id INTEGER, player_hp INTEGER, player_hp_max INTEGER, status TEXT,
	version INTEGER DEFAULT 0, updated_at REAL);
CREATE TABLE inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id));
CREATE TABLE active_effects(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, effect_key TEXT, name TEXT,
	source_type TEXT, source_id TEXT, effect_json TEXT, stacks INTEGER, starts_game_minute INTEGER,
	ends_game_minute INTEGER, created_at REAL, UNIQUE(user_id,effect_key,source_type,source_id));
CREATE TABLE alchemy_state(user_id INTEGER PRIMARY KEY, pill_toxicity INTEGER NOT NULL DEFAULT 0,
	last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0, total_refinements INTEGER NOT NULL DEFAULT 0,
	successful_refinements INTEGER NOT NULL DEFAULT 0, flawless_refinements INTEGER NOT NULL DEFAULT 0,
	best_margin INTEGER NOT NULL DEFAULT -99, last_quality TEXT NOT NULL DEFAULT '', updated_at REAL);
INSERT INTO characters(user_id,qi,qi_max,vitality,vitality_max,life_extension_years,updated_at) VALUES(101,5,20,3,20,0,0);
INSERT INTO inventory(user_id,item_id,quantity) VALUES(101,'keepsake',1);
`); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"item_id": "keepsake", "game_minute": 1000})
	if _, err := itemUseActionGo(conn, permanentTreasureCatalog(), 101, raw); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	res, err := conn.Execute(`SELECT ends_game_minute FROM active_effects WHERE user_id=101 AND effect_key='keepsake'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) != 1 {
		t.Fatalf("the treasure wrote %d effect rows, wanted 1", len(res.Rows))
	}
	if res.Rows[0][0] != nil {
		t.Fatalf("ends_game_minute is %v, not NULL - a treasure that expires is a pill", res.Rows[0][0])
	}
	conn.Close()

	// A year of world time later it is still in the set every roll reads.
	fresh, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer fresh.Close()
	mods, err := loadEffectModifiers(fresh, 101, 1000+525600, AptitudeBundle{}, mechanicsCharacter{}, worlddata.Catalog{})
	if err != nil {
		t.Fatal(err)
	}
	if got := mods.Add["will"]; got != 1 {
		t.Fatalf("will carries %v a year on, wanted +1 - the treasure stopped applying", got)
	}
}
