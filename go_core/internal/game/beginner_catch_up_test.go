package game

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Grandfathering the tutorial at any quest report (v1.2.0).
//
// rc.34 hands a stage that was added after a player finished the one before
// it over at the lesson's door - and only there. A cultivator who finished the
// lesson before v1.2.0 added two stages after it holds `road_to_a_sect` and
// nothing hands them "Iron from the Seam", because the door they would need
// is behind them. Any report of theirs that touches an active quest is a door
// now, through `catchUpBeginnerPathTx` at the end of `questProgress`.

func TestAGraduateReportingProgressIsHandedTheStagesAddedAfterThem(t *testing.T) {
	path := setupCommissionDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	if len(catalog.BeginnerPath) < 3 {
		t.Fatal("the beginner path has fewer than three stages; nothing to catch up")
	}
	conn := beginnerConn(t, path)
	// Every stage is seeded, the way the bot seeds them, and the sect road
	// beside them.
	for _, stage := range catalog.BeginnerPath {
		defineQuest(t, conn, stage.QuestKey, "", stage.FollowOn)
	}
	if _, err := conn.Execute(`INSERT INTO quest_definitions(
        quest_key,title,description,objectives_json,rewards_json,status,giver_npc,seed_json,created_at,updated_at)
        VALUES('road_to_a_sect','A Road Toward a Sect','','[{"id":"find","type":"sect_discovery","count":1,"label":"Find"},{"id":"trial","type":"sect_trial","count":1,"label":"Trial"}]','{"insight_xp":5}','approved','','{}',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	// They finished the whole path as it stood before the new stages: the
	// lesson is completed, the sect road is held, and the two stages after the
	// lesson were never given.
	lessonIndex := -1
	for i, stage := range catalog.BeginnerPath {
		if stage.QuestKey == "beginner_lesson" {
			lessonIndex = i
		}
	}
	if lessonIndex < 0 || lessonIndex == len(catalog.BeginnerPath)-1 {
		t.Fatalf("the lesson is stage %d of %d; the content no longer has stages after it", lessonIndex, len(catalog.BeginnerPath))
	}
	for _, stage := range catalog.BeginnerPath[:lessonIndex+1] {
		if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at) VALUES(42,?,'completed',0,0)`, []any{stage.QuestKey}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,created_at,updated_at,terms_json) VALUES(42,'road_to_a_sect','active',0,0,?)`,
		[]any{`{"objectives":[{"id":"find","type":"sect_discovery","count":1},{"id":"trial","type":"sect_trial","count":1}],"rewards":{}}`}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	next := catalog.BeginnerPath[lessonIndex+1].QuestKey

	raw, _ := json.Marshal(map[string]any{"quest_key": "road_to_a_sect", "objective_type": "sect_discovery", "amount": 1})
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion, ActionID: "catch-up-1", Operation: "quest.progress", ActorID: 42, Payload: raw,
	})
	if err != nil {
		t.Fatalf("quest.progress: %v", err)
	}
	result, _ := out.Result.(map[string]any)
	caught, _ := result["caught_up"].([]string)
	if len(caught) != 1 || caught[0] != next {
		t.Fatalf("a graduate reporting progress was not handed %s: caught_up=%v (result %v)", next, caught, result)
	}
	held := heldQuests(t, beginnerConn(t, path), 42)
	found := false
	for _, key := range held {
		if key == next {
			found = true
		}
	}
	if !found {
		t.Fatalf("the stage was reported handed over and is not held: %v", held)
	}
	// Idempotent: the row is the memory, so a second report hands over nothing.
	raw2, _ := json.Marshal(map[string]any{"quest_key": "road_to_a_sect", "objective_type": "sect_discovery", "amount": 1})
	out2, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion, ActionID: "catch-up-2", Operation: "quest.progress", ActorID: 42, Payload: raw2,
	})
	if err != nil {
		t.Fatalf("second quest.progress: %v", err)
	}
	result2, _ := out2.Result.(map[string]any)
	if again, ok := result2["caught_up"]; ok {
		t.Fatalf("a second report handed over %v again", again)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND quest_key=?`, next)); got != 1 {
		t.Fatalf("%s is held %d times", next, got)
	}
	_ = fmt.Sprint
}
