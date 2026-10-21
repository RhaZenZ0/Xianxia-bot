package game

import (
	"encoding/json"
	"testing"

	"xianxia/core/internal/storage"
)

// A talk is worth one trust a day (v1.37.0), and the rule is the engine's.

func talkWith(t *testing.T, path string, userID int64, npc, summary, actionID string) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"npc_name": npc, "summary": summary})
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{Operation: "npc.talk", ActorID: userID, ActionID: actionID, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func TestATalkIsWorthOneTrustADay(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	// Production's table, migration 82's column included.
	createProductionTable(t, path, "npc_relationships")
	batch4SetCanonicalGameMinute(t, path, 10_000)

	first, err := talkWith(t, path, 42, "Elder Pine", "a greeting", "t:1")
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(first["trust"]) != 1 || first["trust_gained"] != true {
		t.Fatalf("the first talk of the day: %v, want trust 1 and trust_gained true", first)
	}
	second, err := talkWith(t, path, 42, "Elder Pine", "more talk", "t:2")
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(second["trust"]) != 1 || second["trust_gained"] != false {
		t.Fatalf("the second talk the same day: %v - a talk paid its point every time, so sixty talks in an afternoon made an old friend", second)
	}
	if storage.ParseInt(second["encounter_count"]) != 2 || second["last_summary"] != "more talk" {
		t.Fatalf("the second talk is not counted or kept: %v", second)
	}
	// Another NPC the same day pays its own point.
	other, err := talkWith(t, path, 42, "Steward Qiao", "hello", "t:3")
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(other["trust"]) != 1 || other["trust_gained"] != true {
		t.Fatalf("the day's gate is per NPC: %v", other)
	}
	// A world day later it pays again.
	batch4SetCanonicalGameMinute(t, path, 10_000+talkTrustGameMinutes)
	third, err := talkWith(t, path, 42, "Elder Pine", "the next day", "t:4")
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(third["trust"]) != 2 || third["trust_gained"] != true {
		t.Fatalf("a day later: %v, want trust 2 and trust_gained true", third)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT last_talk_game_minute FROM npc_relationships WHERE user_id=42 AND npc_name='Elder Pine'")); got != 10_000+talkTrustGameMinutes {
		t.Fatalf("last_talk_game_minute=%d, want the minute the point was paid", got)
	}
}

// Before migration 82 the column is not there, and every talk pays its point
// as every talk did: a refused talk would be the worse failure in the window
// between the engine coming up and db-init migrating (v1.1.0).
func TestATalkOnAWorldWithoutTheColumnStillPays(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE npc_relationships(user_id INTEGER NOT NULL,npc_name TEXT NOT NULL,trust INTEGER NOT NULL DEFAULT 0,respect INTEGER NOT NULL DEFAULT 0,fear INTEGER NOT NULL DEFAULT 0,affection INTEGER NOT NULL DEFAULT 0,debt INTEGER NOT NULL DEFAULT 0,grudge INTEGER NOT NULL DEFAULT 0,encounter_count INTEGER NOT NULL DEFAULT 0,last_summary TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL,PRIMARY KEY(user_id,npc_name))`)
	batch4SetCanonicalGameMinute(t, path, 10_000)
	for i, want := range []int64{1, 2} {
		out, err := talkWith(t, path, 42, "Elder Pine", "talk", "old:"+string(rune('a'+i)))
		if err != nil {
			t.Fatal(err)
		}
		if storage.ParseInt(out["trust"]) != want {
			t.Fatalf("talk %d on the old schema: %v, want trust %d", i+1, out, want)
		}
	}
}

func TestATalkNamesSomebody(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	createProductionTable(t, path, "npc_relationships")
	batch4SetCanonicalGameMinute(t, path, 10_000)
	if _, err := talkWith(t, path, 42, "  ", "talk", "t:0"); err == nil {
		t.Fatal("a talk with nobody was taken")
	}
}
