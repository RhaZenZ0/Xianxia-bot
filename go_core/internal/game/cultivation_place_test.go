package game

import (
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func sprint(v any) string { return strings.TrimSpace(fmt.Sprint(v)) }

// The place matters, Insight XP does something, and every roll says its
// odds (v1.0.0-rc.4).

func TestTheGroundShapesASessionAndTheSheetSaysSo(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	shrine := ""
	for name, loc := range catalog.Locations {
		if loc.RoadSite == "shrine" {
			shrine = name
			break
		}
	}
	if shrine == "" {
		t.Fatal("the catalogue has no shrine")
	}
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0,location=? WHERE user_id=42`, shrine)
	status := cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["place_name"] != "the shrine" || status["place_quality"] != "good" {
		t.Fatalf("shrine status: place=%v quality=%v", status["place_name"], status["place_quality"])
	}
	trained := batch4Result(t, batch4Apply(t, path, world, "cultivation.train", 1, map[string]any{"cooldown_seconds": 0, "game_minute": 600}))
	if m, _ := trained["place_mult"].(float64); m != placeShrineMult || trained["place_name"] != "the shrine" {
		t.Fatalf("shrine training: %v %v", trained["place_name"], trained["place_mult"])
	}
	// A sect gate is a place too; open country is nothing in particular.
	gate := ""
	for _, sect := range catalog.Sects {
		if sect.Recruitment.Location != "" {
			gate = sect.Recruitment.Location
			break
		}
	}
	if gate == "" {
		t.Fatal("no sect names its gate")
	}
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, gate)
	status = cultivationQuery(t, path, world, "cultivation.status", 42)
	if !strings.HasPrefix(strings.TrimSpace(sprint(status["place_name"])), "the gate of the ") {
		t.Fatalf("gate status: %v", status["place_name"])
	}
	if odds, _ := status["odds"].(map[string]any); odds == nil {
		t.Fatalf("status keeps its odds: %v", status)
	}
}

func TestAFailedBreakthroughCanBeSeizedOnceForInsightXP(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	// Will of -40: the dice cannot reach the target, so every roll fails.
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=100000,insight_xp=2,attributes_json='{"body":10,"agility":10,"spirit":10,"insight":10,"will":-40,"presence":10}' WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "cultivation.breakthrough", 42, 1, map[string]any{"reroll": true}); err == nil || !strings.Contains(err.Error(), "no moment to seize") {
		t.Fatalf("a reroll before any failure must refuse, got %v", err)
	}
	failed := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 2, map[string]any{}))
	if failed["success"] != false || failed["reroll_available"] != true || storage.ParseInt(failed["reroll_cost"]) != 5 {
		t.Fatalf("failure: %v", failed)
	}
	roll, _ := failed["roll"].(map[string]any)
	if storage.ParseInt(roll["probability"]) != 0 {
		t.Fatalf("the roll carries its odds: %v", roll)
	}
	status := cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["reroll_available"] != true {
		t.Fatalf("status after failure: %v", status["reroll_available"])
	}
	if _, err := batch4ApplyErr(path, world, "cultivation.breakthrough", 42, 3, map[string]any{"reroll": true}); err == nil || !strings.Contains(err.Error(), "costs 5 Insight XP") {
		t.Fatalf("short of XP must refuse, got %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET insight_xp=9 WHERE user_id=42`)
	// The essence fell on the failure; the seized moment does not ask for it back.
	batch4Exec(t, path, `UPDATE characters SET cultivation=0 WHERE user_id=42`)
	seized := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 4, map[string]any{"reroll": true}))
	if seized["reroll"] != true || seized["success"] != false || storage.ParseInt(seized["insight_xp"]) != 4 || seized["reroll_available"] != false {
		t.Fatalf("seized: %v", seized)
	}
	if _, err := batch4ApplyErr(path, world, "cultivation.breakthrough", 42, 5, map[string]any{"reroll": true}); err == nil || !strings.Contains(err.Error(), "seized once already") {
		t.Fatalf("a second seize at the stage must refuse, got %v", err)
	}
	// A success clears the memory of the failure and the seize.
	batch4Exec(t, path, `UPDATE characters SET cultivation=100000,attributes_json='{"body":10,"agility":10,"spirit":10,"insight":10,"will":100,"presence":10}' WHERE user_id=42`)
	won := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 6, map[string]any{}))
	if won["success"] != true || storage.ParseInt(won["to_stage"]) != 4 {
		t.Fatalf("won: %v", won)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_state WHERE key IN (?,?)`, cultivationLastFailKey(42), cultivationRerollKey(42))); got != 0 {
		t.Fatalf("keys after success=%d", got)
	}
}

func TestInsightXPCanBePutIntoALaw(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=7,insight_xp=3 WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "law.comprehend", 42, 1, map[string]any{"law": "fire", "spend_insight": true}); err == nil || !strings.Contains(err.Error(), "costs 4 Insight XP") {
		t.Fatalf("short of XP must refuse, got %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET insight_xp=10 WHERE user_id=42`)
	result := batch4Result(t, batch4Apply(t, path, world, "law.comprehend", 2, map[string]any{"law": "fire", "spend_insight": true, "game_minute": 200}))
	if storage.ParseInt(result["insight_spent"]) != 4 || storage.ParseInt(result["insight_bonus"]) != 3 {
		t.Fatalf("law with insight: %v", result)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT insight_xp FROM characters WHERE user_id=42`)); got != 6 {
		t.Fatalf("insight_xp after=%d", got)
	}
	roll, _ := result["roll"].(map[string]any)
	if _, ok := roll["probability"]; !ok {
		t.Fatalf("every roll carries its odds: %v", roll)
	}
}
