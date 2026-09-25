package game

import (
	"testing"
	"time"

	"xianxia/core/internal/storage"
)

// An inheritance reads its preferred paths (v1.3.4).
//
// `Inheritance.PreferredPaths` was parsed since the inheritances were written
// and read by nothing - `field_readers_test.go` could not tell it from
// `SecretRealmRoom.PreferredPaths`, which is read. The Stygian Keeper Legacy is
// the Ghost Cultivator's now, and its scripture is the path's high manual: a
// Ghost Cultivator who clears the tomb has it studied at once, anybody else is
// handed the sealed copy to study the ordinary way. Driven against the shipped
// catalogue, because the fixture that rewrote this link is the rc.58 lesson.

func stygianTombRun(t *testing.T, path, world string, base int) map[string]any {
	t.Helper()
	clearCooldowns(t, path, 42)
	now := float64(time.Now().UnixNano()) / 1e9
	batch4Exec(t, path, "DELETE FROM world_events WHERE dedupe_key='secret_realm:stygian_lantern_tomb'")
	batch4Exec(t, path, "INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at,thread_id) VALUES(?,?,?,?,?,?,1,?,?,?)",
		"tomb-open", "secret_realm:stygian_lantern_tomb", "secret_realm", "Stygian Lantern Tomb",
		"Greenriver Town", `{"realm_id":"stygian_lantern_tomb"}`, now-10, now+3600, 434343)
	if entered := batch4Result(t, batch4Apply(t, path, world, "secret_realm.enter", base, map[string]any{
		"realm_id": "stygian_lantern_tomb", "game_minute": 700})); entered["entered"] != true {
		t.Fatalf("enter=%v", entered)
	}
	var last map[string]any
	for i := 0; i < 4; i++ {
		clearCooldowns(t, path, 42)
		last = batch4Result(t, batch4Apply(t, path, world, "secret_realm.explore", base+1+i, map[string]any{"game_minute": 701 + i}))
		if success, _ := last["success"].(bool); !success {
			t.Fatalf("room %d should succeed with the fixture's stats: %v", i, last)
		}
	}
	return last
}

func scriptureStudied(t *testing.T, path string) int64 {
	t.Helper()
	return storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM character_manuals WHERE user_id=42 AND manual_id='stygian_ghost_scripture'"))
}

func TestAGhostCultivatorStudiesTheStygianScriptureAtOnce(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "UPDATE characters SET location='Greenriver Town',realm_index=3,path='Ghost Cultivator' WHERE user_id=42")
	last := stygianTombRun(t, path, world, 5000)
	inh, _ := last["inheritance"].(map[string]any)
	if inh == nil || inh["gained"] != true {
		t.Fatalf("the last room granted no inheritance: %v", last)
	}
	if inh["preferred_path"] != true || inh["studied"] != "stygian_ghost_scripture" {
		t.Fatalf("a Ghost Cultivator was not recognised as the path the legacy prefers: %v", inh)
	}
	if got := scriptureStudied(t, path); got != 1 {
		t.Fatalf("the scripture's first-study row is missing (%d rows); the inheritance handed it over unstudied", got)
	}
}

func TestAnotherPathIsHandedTheSealedCopyOnly(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "UPDATE characters SET location='Greenriver Town',realm_index=3,path='Sword Cultivator' WHERE user_id=42")
	last := stygianTombRun(t, path, world, 5100)
	inh, _ := last["inheritance"].(map[string]any)
	if inh == nil || inh["gained"] != true {
		t.Fatalf("the last room granted no inheritance: %v", last)
	}
	if inh["preferred_path"] != false || inh["studied"] != "" {
		t.Fatalf("a Sword Cultivator was treated as the legacy's own: %v", inh)
	}
	if got := scriptureStudied(t, path); got != 0 {
		t.Fatalf("the scripture was studied for a path the legacy does not prefer (%d rows)", got)
	}
	held := storage.ParseInt(actionScalar(t, path, "SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=42 AND item_id='stygian_ghost_scripture'"))
	if held != 1 {
		t.Fatalf("the sealed copy did not reach the bags (%d)", held)
	}
}
