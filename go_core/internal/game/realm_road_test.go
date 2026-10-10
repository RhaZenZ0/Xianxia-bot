package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A road for every realm (v1.16.0): the crossing into a realm hands over the
// stage the content wrote for it, through grantOrdinaryQuestTx, so a
// cultivator who stands at Core Formation today holds the road from there
// rather than nothing. No dice are lent: the fixture character carries will
// 100, so a breakthrough at realm 0 stage 9 cannot fail and only the gate
// decides, which the banked insight opens.

func realmRoadDB(t *testing.T) string {
	t.Helper()
	path := setupCultivationDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// The quest tables as production declares them, which the cultivation
	// fixture never needed: a breakthrough handed nothing over until now.
	if err := conn.ExecScript(`
CREATE TABLE IF NOT EXISTS character_quests(user_id INTEGER NOT NULL,quest_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',progress_json TEXT NOT NULL DEFAULT '{}',accepted_game_minute INTEGER NOT NULL DEFAULT 0,completed_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL,commission INTEGER NOT NULL DEFAULT 0,deadline_game_minute INTEGER,variant_index INTEGER NOT NULL DEFAULT 0,resolved_game_minute INTEGER,terms_json TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,quest_key));
CREATE TABLE IF NOT EXISTS quest_definitions(quest_key TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',source_type TEXT NOT NULL DEFAULT 'forge',source_key TEXT NOT NULL DEFAULT '',objectives_json TEXT NOT NULL DEFAULT '[]',rewards_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'draft',created_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0,giver_npc TEXT NOT NULL DEFAULT '',realm_band TEXT NOT NULL DEFAULT '',tier INTEGER NOT NULL DEFAULT 1,deadline_game_minutes INTEGER NOT NULL DEFAULT 0,variants_json TEXT NOT NULL DEFAULT '[]',seed_json TEXT NOT NULL DEFAULT '{}');
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

// defineRoadStage seeds the shipped stage for a realm as the bot would, and
// returns its key.
func defineRoadStage(t *testing.T, path string, realm int64) string {
	t.Helper()
	key := realmRoadStageFor(crossingCatalog(t), realm)
	if key == "" {
		t.Fatalf("the content authors no realm road stage for realm %d", realm)
	}
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,source_key,created_at,updated_at) VALUES(?,?,'approved','realm_road',0,0)`, key, key)
	return key
}

func heldRoad(t *testing.T, path, key string) int64 {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND quest_key=?`, key))
}

func TestTheCrossingIntoARealmHandsOverItsRoad(t *testing.T) {
	path := realmRoadDB(t)
	world := batch4WorldPath(t)
	first := defineRoadStage(t, path, 1)
	second := defineRoadStage(t, path, 2)
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=9,cultivation=100000,insight_xp=7 WHERE user_id=42`)
	batch4Result(t, batch4Apply(t, path, world, "cultivation.insight", 1, map[string]any{}))
	crossed := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 2, map[string]any{"confirm": true}))
	if crossed["success"] != true || crossed["realm_gate"] != true {
		t.Fatalf("the crossing did not happen: %v", crossed)
	}
	if crossed["realm_road_quest"] != first {
		t.Fatalf("the crossing into %s handed nothing over (realm_road_quest=%v); the road for realm 1 is %q", crossed["to_realm"], crossed["realm_road_quest"], first)
	}
	if n := heldRoad(t, path, first); n != 1 {
		t.Fatalf("character_quests rows for %s = %d, want 1", first, n)
	}
	if n := heldRoad(t, path, second); n != 0 {
		t.Fatalf("the crossing into realm 1 handed over realm 2's road as well (%d rows)", n)
	}
	// A stage within the realm is not a crossing: nothing more is handed over.
	batch4Exec(t, path, `UPDATE characters SET cultivation=100000 WHERE user_id=42`)
	staged := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 3, map[string]any{"confirm": true}))
	if staged["success"] != true || staged["realm_gate"] == true {
		t.Fatalf("expected a stage breakthrough inside realm 1: %v", staged)
	}
	if _, handed := staged["realm_road_quest"]; handed {
		t.Fatalf("a stage breakthrough handed a road over: %v", staged["realm_road_quest"])
	}
	if n := heldRoad(t, path, second); n != 0 {
		t.Fatalf("a stage breakthrough handed over realm 2's road (%d rows)", n)
	}
}

// The road's labels name /breakthrough, the qi ladder: a body crossing is a
// different command and hands nothing over.
func TestABodyCrossingHandsOverNoRoad(t *testing.T) {
	path := realmRoadDB(t)
	world := batch4WorldPath(t)
	first := defineRoadStage(t, path, 1)
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=1,body_realm_index=0,body_phase=9,body_cultivation=100000 WHERE user_id=42`)
	crossed := batch4Result(t, batch4Apply(t, path, world, "cultivation.body_breakthrough", 1, map[string]any{"confirm": true}))
	if crossed["success"] != true || crossed["realm_gate"] != true {
		t.Fatalf("the body crossing did not happen: %v", crossed)
	}
	if _, handed := crossed["realm_road_quest"]; handed {
		t.Fatalf("a body crossing handed a road over: %v", crossed["realm_road_quest"])
	}
	if n := heldRoad(t, path, first); n != 0 {
		t.Fatalf("a body crossing handed over the qi ladder's road (%d rows)", n)
	}
}

// A realm the content writes no stage for, or a stage a GM retired, is "no"
// and never an error: a breakthrough must not fail over a quest.
func TestARealmWithNoRoadStillCrosses(t *testing.T) {
	path := realmRoadDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=9,cultivation=100000,insight_xp=7 WHERE user_id=42`)
	batch4Result(t, batch4Apply(t, path, world, "cultivation.insight", 1, map[string]any{}))
	crossed := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 2, map[string]any{"confirm": true}))
	if crossed["success"] != true {
		t.Fatalf("the crossing did not happen: %v", crossed)
	}
	if _, handed := crossed["realm_road_quest"]; handed {
		t.Fatalf("an unseeded road was handed over: %v", crossed["realm_road_quest"])
	}
}

// The same rule through the engine's own door: a travel report of the
// capital's gate advances a quest that names the capital, and the stage's
// objective is counted off the pinned terms.
func TestWalkingToACitysGateAdvancesAQuestNamingTheCity(t *testing.T) {
	path := realmRoadDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	city := "Azure Crown Imperial City"
	gate := ""
	for _, part := range cityPartsOf(catalog, city) {
		if strings.Contains(part, "Gate") {
			gate = part
			break
		}
	}
	if gate == "" {
		t.Fatalf("%s has no gate; the content reader is broken, not the tree", city)
	}
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,objectives_json,created_at,updated_at)
		VALUES('walk_to_the_capital','Walk','approved','[{"id":"walk","type":"travel","target":"Azure Crown Imperial City","count":1}]',0,0)`)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	granted, err := grantOrdinaryQuestTx(conn, 42, "walk_to_the_capital", 100)
	if err != nil || !granted {
		t.Fatalf("grant: %v %v", granted, err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	batch4SetCanonicalGameMinute(t, path, 130)
	raw, _ := json.Marshal(map[string]any{"quest_key": "walk_to_the_capital", "objective_type": "travel", "target": gate, "amount": 1})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "road-walk-1", Operation: "quest.progress", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	result, _ := out.Result.(map[string]any)
	if result["complete"] != true {
		t.Fatalf("arriving at %q did not complete a quest naming %q: %v", gate, city, result)
	}
}

// A city's gate is that city, for a quest too. A road arrives at the gate
// facing where you came from, so "walk to the capital" is reported as its
// gate; the report is read as the city when a travel objective names the city
// and none names the gate.
func TestAQuestNamingACityIsMetAtItsGate(t *testing.T) {
	catalog := crossingCatalog(t)
	city := "Azure Crown Imperial City"
	parts := cityPartsOf(catalog, city)
	gate := ""
	for _, part := range parts {
		if strings.Contains(part, "Gate") {
			gate = part
			break
		}
	}
	if gate == "" {
		t.Fatalf("%s has no gate among its parts %v; the content reader is broken, not the tree", city, parts)
	}
	namesCity := []map[string]any{{"id": "walk", "type": "travel", "target": city}}
	if got := questTargetCity(catalog, gate, "travel", namesCity); got != city {
		t.Fatalf("a travel report of %q against an objective naming %q was read as %q, want the city", gate, city, got)
	}
	namesGate := []map[string]any{{"id": "walk", "type": "travel", "target": gate}, {"id": "look", "type": "explore", "target": city}}
	if got := questTargetCity(catalog, gate, "travel", namesGate); got != "" {
		t.Fatalf("a quest naming the gate itself had its report rewritten to %q", got)
	}
	// A quest naming the place and its city in one type is met at the place
	// as it stands (v1.33.0): the GM's report echoes an objective's own target,
	// and rewriting the gate to the city would meet the wrong objective. No
	// shipped quest does this, so the shipped walk cannot see the guard.
	namesBoth := []map[string]any{{"id": "gate", "type": "travel", "target": gate}, {"id": "city", "type": "travel", "target": city}}
	if got := questTargetCity(catalog, gate, "travel", namesBoth); got != "" {
		t.Fatalf("a quest naming both %q and its city had the gate's report rewritten to %q", gate, got)
	}
	otherType := []map[string]any{{"id": "speak", "type": "talk", "target": city}}
	if got := questTargetCity(catalog, gate, "travel", otherType); got != "" {
		t.Fatalf("an objective of another type made the report read as %q", got)
	}
	if got := questTargetCity(catalog, city, "travel", namesCity); got != "" {
		t.Fatalf("a report of the city itself needs no rewriting, got %q", got)
	}
}
