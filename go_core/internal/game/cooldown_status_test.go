package game

// `cooldown.status` answers the question a player had no way to ask before:
// what am I waiting on, and what can I do right now. The parts worth pinning
// are the ones that are easy to get quietly wrong - a composite key split into
// the wrong family, a wait on the world clock rendered as though it were on
// the wall clock, a "ready" line offered to a cultivator who cannot use it,
// and a read that turns out to write.

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

func cooldownStatus(t *testing.T, path string, actor int64) map[string]any {
	t.Helper()
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: authoritativeAPIVersion,
		Operation:  "cooldown.status",
		ActorID:    actor,
		Payload:    json.RawMessage(`{}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("cooldown.status result type %T", out.Result)
	}
	return result
}

func cooldownRows(t *testing.T, result map[string]any, bucket string) []map[string]any {
	t.Helper()
	raw, ok := result[bucket].([]map[string]any)
	if !ok {
		t.Fatalf("%s is %T, want a list of rows", bucket, result[bucket])
	}
	return raw
}

func cooldownFamiliesIn(rows []map[string]any) map[string]map[string]any {
	out := map[string]map[string]any{}
	for _, row := range rows {
		out[fmt.Sprint(row["family"])] = row
	}
	return out
}

// setWorldClock writes an anchor the conversion can actually use. The shared
// fixture pins scale 0 (a stopped world), which is the frozen case rather than
// the ordinary one.
func setRunningWorldClock(t *testing.T, path string, gameMinute int64, scale int64) {
	t.Helper()
	now := float64(time.Now().UnixNano()) / 1e9
	state := fmt.Sprintf(`{"anchor_game_minute":%d,"anchor_real_ts":%f,"scale":%d}`, gameMinute, now, scale)
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0)
		ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json`, state)
}

func TestCooldownStatusNamesEveryRowsFamilyAndSubject(t *testing.T) {
	path := setupSupportVoteDB(t)
	future := float64(time.Now().Unix()) + 3600
	for _, key := range []string{"explore", "manual:azure_reed_sword", "beast_feed:17", "aptitude_temper:root"} {
		batch4Exec(t, path, `INSERT INTO cooldowns(user_id,action,available_at) VALUES(42,?,?)`, key, future)
	}

	result := cooldownStatus(t, path, 42)
	waits := cooldownFamiliesIn(cooldownRows(t, result, "waits"))

	for _, tc := range []struct{ family, subject string }{
		{"explore", ""},
		{"manual", "azure_reed_sword"},
		{"beast_feed", "17"},
		{"aptitude_temper", "root"},
	} {
		row, found := waits[tc.family]
		if !found {
			t.Fatalf("family %q is not in the waits", tc.family)
		}
		if got := fmt.Sprint(row["subject"]); got != tc.subject {
			t.Fatalf("%s subject = %q, want %q", tc.family, got, tc.subject)
		}
		if ready, _ := row["ready"].(bool); ready {
			t.Fatalf("%s is waiting an hour out but reports ready", tc.family)
		}
		if remaining := i64(row["remaining_seconds"]); remaining <= 0 || remaining > 3600 {
			t.Fatalf("%s remaining = %d, want 0 < n <= 3600", tc.family, remaining)
		}
		if i64(row["available_at_unix"]) != int64(future) {
			t.Fatalf("%s available_at_unix = %d, want %d", tc.family, i64(row["available_at_unix"]), int64(future))
		}
	}
}

func TestASpentRowIsReadyRatherThanWaiting(t *testing.T) {
	path := setupSupportVoteDB(t)
	batch4Exec(t, path, `INSERT INTO cooldowns(user_id,action,available_at) VALUES(42,'explore',1)`)

	result := cooldownStatus(t, path, 42)
	if _, waiting := cooldownFamiliesIn(cooldownRows(t, result, "waits"))["explore"]; waiting {
		t.Fatal("a cooldown that expired in 1970 is still being counted as a wait")
	}
	if _, ready := cooldownFamiliesIn(cooldownRows(t, result, "ready"))["explore"]; !ready {
		t.Fatal("an expired cooldown did not come back as ready")
	}
}

func TestEveryUngatedFlatFamilyIsOfferedAsReadyAndNoCompositeOneIs(t *testing.T) {
	path := setupSupportVoteDB(t)
	result := cooldownStatus(t, path, 42)
	ready := cooldownFamiliesIn(cooldownRows(t, result, "ready"))

	for _, family := range cooldownFamilies {
		_, offered := ready[family.Family]
		switch {
		case family.Prefix != "":
			// The world holds more manuals than a card can show.
			if offered {
				t.Fatalf("composite family %q was offered as ready", family.Family)
			}
		case family.Gate != gateNone:
			// Gated families are the next test's business.
		default:
			if !offered {
				t.Fatalf("flat family %q is not offered to a cultivator with no cooldowns at all", family.Family)
			}
		}
	}
}

func TestAWaitAndAReadyLineAreNeverTheSameFamilyTwice(t *testing.T) {
	path := setupSupportVoteDB(t)
	future := float64(time.Now().Unix()) + 600
	batch4Exec(t, path, `INSERT INTO cooldowns(user_id,action,available_at) VALUES(42,'hunt',?)`, future)

	result := cooldownStatus(t, path, 42)
	if _, waiting := cooldownFamiliesIn(cooldownRows(t, result, "waits"))["hunt"]; !waiting {
		t.Fatal("hunt is on cooldown but not in the waits")
	}
	if _, ready := cooldownFamiliesIn(cooldownRows(t, result, "ready"))["hunt"]; ready {
		t.Fatal("hunt is both waiting and ready")
	}
}

func TestGatedFamiliesAreNotOfferedToACultivatorWhoCannotUseThem(t *testing.T) {
	path := setupSupportVoteDB(t)

	// The fixture walks the Sword Cultivator's road, so the ghost road's two
	// actions are not theirs to take.
	ready := cooldownFamiliesIn(cooldownRows(t, cooldownStatus(t, path, 42), "ready"))
	for _, family := range []string{"ghost_harvest", "ghost_appease", "perfect_quest", "body_perfect_quest"} {
		if _, offered := ready[family]; offered {
			t.Fatalf("%q was offered to a cultivator with no claim to it", family)
		}
	}

	// An active realm perfection opens its two.
	batch4Exec(t, path, `INSERT INTO realm_perfection(user_id,realm_index,active,updated_at) VALUES(42,0,1,0)`)
	ready = cooldownFamiliesIn(cooldownRows(t, cooldownStatus(t, path, 42), "ready"))
	for _, family := range []string{"perfect_quest", "perfect_trial"} {
		if _, offered := ready[family]; !offered {
			t.Fatalf("%q is not offered during an active perfection", family)
		}
	}
	if _, offered := ready["ghost_harvest"]; offered {
		t.Fatal("the ghost road opened for a sword cultivator")
	}
}

func TestAJourneyOnTheWorldClockArrivesOnTheSameAxisAsTheRest(t *testing.T) {
	path := setupSupportVoteDB(t)
	setRunningWorldClock(t, path, 5000, 4)
	state := `{"origin":"Greenriver Town","destination":"Riverguard City","departure_game_minute":4900,"arrival_game_minute":5600}`
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,0)`, "road_transit:42", state)

	result := cooldownStatus(t, path, 42)
	row, found := cooldownFamiliesIn(cooldownRows(t, result, "waits"))["road_transit"]
	if !found {
		t.Fatal("a journey in progress is not on the card")
	}
	if got := fmt.Sprint(row["source"]); got != "game_clock" {
		t.Fatalf("source = %q, want game_clock", got)
	}
	if got := fmt.Sprint(row["subject"]); got != "Riverguard City" {
		t.Fatalf("subject = %q, want the destination", got)
	}
	if scheduled, _ := row["scheduled"].(bool); !scheduled {
		t.Fatal("a running clock produced an unscheduled arrival")
	}
	// 600 game minutes at scale 4 is 150 real minutes; the card must express
	// that as a real timestamp, not as a game minute.
	if remaining := i64(row["remaining_seconds"]); remaining < 8000 || remaining > 9200 {
		t.Fatalf("remaining_seconds = %d, want about 9000 (600 game minutes at scale 4)", remaining)
	}
	if i64(row["available_at_unix"]) <= time.Now().Unix() {
		t.Fatalf("available_at_unix = %d, want a moment in the future", i64(row["available_at_unix"]))
	}
}

func TestAStoppedWorldClockSaysSoRatherThanPrintingNineteenSeventy(t *testing.T) {
	path := setupSupportVoteDB(t)
	// The fixture's clock is already stopped (scale 0), which is the GM having
	// frozen the world: an arrival ahead of the anchor then happens at no real
	// time at all, and a zero timestamp would render as 1970 on the card.
	state := `{"origin":"Greenriver Town","destination":"Riverguard City","departure_game_minute":4900,"arrival_game_minute":5600}`
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,0)`, "road_transit:42", state)

	row, found := cooldownFamiliesIn(cooldownRows(t, cooldownStatus(t, path, 42), "waits"))["road_transit"]
	if !found {
		t.Fatal("a journey under a stopped clock vanished from the card")
	}
	if scheduled, _ := row["scheduled"].(bool); scheduled {
		t.Fatal("a stopped clock reported a scheduled arrival")
	}
	if i64(row["available_at_unix"]) != 0 {
		t.Fatalf("available_at_unix = %d under a stopped clock, want 0 with scheduled=false", i64(row["available_at_unix"]))
	}
	if i64(row["remaining_game_minutes"]) != 600 {
		t.Fatalf("remaining_game_minutes = %d, want 600 - the world-clock truth is still reported", i64(row["remaining_game_minutes"]))
	}
}

// Five of the eight external sources are tables the shared fixture does not
// carry. A cooldown card is a convenience: a database mid-migration must get a
// shorter card, never an error.
func TestCooldownStatusSurvivesADatabaseMissingTheOptionalTables(t *testing.T) {
	path := setupSupportVoteDB(t)
	for _, table := range []string{"seclusion_sessions", "sect_recruitment_attempts", "secret_realm_runs", "reincarnation_state"} {
		if exists := cooldownScalar(t, path, `SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?`, table); exists != 0 {
			t.Fatalf("fixture unexpectedly carries %s; this test no longer proves anything", table)
		}
	}
	result := cooldownStatus(t, path, 42)
	if _, ok := result["waits"]; !ok {
		t.Fatal("cooldown.status returned nothing against a partial database")
	}
}

func TestCooldownStatusWritesNothing(t *testing.T) {
	path := setupSupportVoteDB(t)
	future := float64(time.Now().Unix()) + 3600
	batch4Exec(t, path, `INSERT INTO cooldowns(user_id,action,available_at) VALUES(42,'explore',?)`, future)
	before := cooldownScalar(t, path, `SELECT COUNT(*) FROM cooldowns WHERE user_id=42`)

	cooldownStatus(t, path, 42)
	cooldownStatus(t, path, 42)

	if after := cooldownScalar(t, path, `SELECT COUNT(*) FROM cooldowns WHERE user_id=42`); after != before {
		t.Fatalf("cooldown rows went from %d to %d across two reads", before, after)
	}
	if events := cooldownScalar(t, path, `SELECT COUNT(*) FROM domain_events WHERE actor_id=42`); events != 0 {
		t.Fatalf("a query wrote %d domain events", events)
	}
}

func cooldownScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}
