package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Pacing, growth and the cost of forcing (v1.0.0-rc.5).

func trainOnce(t *testing.T, path, world string, seq int) map[string]any {
	t.Helper()
	batch4Exec(t, path, `DELETE FROM cooldowns WHERE user_id=42`)
	return batch4Result(t, batch4Apply(t, path, world, "cultivation.train", seq, map[string]any{"cooldown_seconds": 1, "game_minute": 600}))
}

func TestTheClimbTightensWithEachRealmAndEasesOnAscension(t *testing.T) {
	// Eight sessions a stage at the first realm, rising five quarters a
	// realm: the early game is quick and the ladder gets steeper.
	if sessionsForStage(0) != 8 || sessionsForStage(4) != 13 || sessionsForStage(8) != 18 {
		t.Fatalf("sessions a stage: %d %d %d", sessionsForStage(0), sessionsForStage(4), sessionsForStage(8))
	}
	for realm := int64(1); realm < 32; realm++ {
		if sessionsForStage(realm) < sessionsForStage(realm-1) {
			t.Fatalf("the ladder must never slacken: realm %d", realm)
		}
	}
}

func TestASessionIsAShareOfTheStageSoEveryRealmTakesAboutTheSameWork(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	// A plain cultivator: the fixture's will of 100 would drown the pace.
	batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":3,"agility":3,"spirit":3,"insight":2,"will":3,"presence":2}',location='Greenriver Town' WHERE user_id=42`)
	for _, realm := range []int64{0, 3, 6} {
		batch4Exec(t, path, `UPDATE characters SET realm_index=?,phase=1,cultivation=0 WHERE user_id=42`, realm)
		cost, err := phaseCost(catalog.Realms, realm, 1)
		if err != nil {
			t.Fatal(err)
		}
		result := trainOnce(t, path, world, int(100+realm))
		gain := storage.ParseInt(result["gain"])
		sessions := float64(cost) / float64(gain)
		// The target rises with the realm (v1.0.0-rc.6); a cultivator's own
		// multipliers pull the real number under it, and the floor under a
		// session makes the very first stages quicker still.
		target := float64(sessionsForStage(realm))
		floor := stagePace(cost, realm) == cultivationPaceFloor
		if sessions > target+2 || (!floor && sessions < target/2) {
			t.Fatalf("realm %d: cost %d, gain %d -> %.1f sessions a stage, target %.0f", realm, cost, gain, sessions, target)
		}
		if storage.ParseInt(result["pace"]) != stagePace(cost, realm) {
			t.Fatalf("realm %d pace=%v want %d", realm, result["pace"], stagePace(cost, realm))
		}
	}
}

func TestTheHigherWorldsAreThickWithQi(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	if worldQiMultiplier(catalog, "Mortal World") != 1.0 {
		t.Fatalf("the mortal world is the baseline: %v", worldQiMultiplier(catalog, "Mortal World"))
	}
	if worldQiMultiplier(catalog, "Spiritual World") <= 1.0 || worldQiMultiplier(catalog, "Immortal World") <= worldQiMultiplier(catalog, "Spiritual World") {
		t.Fatalf("the worlds must thicken: %v", catalog.WorldQiDensity)
	}
	if worldQiMultiplier(catalog, "Nowhere") != 1.0 {
		t.Fatal("an unlisted world is the baseline")
	}
	// The first realm of the Spiritual World, trained: the result names the
	// world and the multiplier it applied.
	spiritual := int64(-1)
	for index, realm := range catalog.Realms {
		if realm.World == "Spiritual World" {
			spiritual = int64(index)
			break
		}
	}
	if spiritual < 0 {
		t.Fatal("no spiritual-world realm in the catalogue")
	}
	batch4Exec(t, path, `UPDATE characters SET realm_index=?,phase=1,cultivation=0 WHERE user_id=42`, spiritual)
	result := trainOnce(t, path, world, 200)
	if result["world_name"] != "Spiritual World" {
		t.Fatalf("world_name=%v", result["world_name"])
	}
	if m, _ := result["world_mult"].(float64); m != worldQiMultiplier(catalog, "Spiritual World") {
		t.Fatalf("world_mult=%v", result["world_mult"])
	}
}

func TestCrossingARealmRaisesTheCultivatorsAttributes(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	// A will the dice cannot argue with: this test is about the growth, not
	// about the roll, and a 94% breakthrough fails six times in a hundred.
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=9,cultivation=100000,insight_xp=20,path='Sword Cultivator',attributes_json='{"body":2,"agility":3,"spirit":2,"insight":1,"will":100,"presence":1}' WHERE user_id=42`)
	batch4Result(t, batch4Apply(t, path, world, "cultivation.insight", 1, map[string]any{}))
	// A stage inside the realm changes nothing.
	batch4Exec(t, path, `UPDATE characters SET phase=1 WHERE user_id=42`)
	stage := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 2, map[string]any{"confirm": true}))
	stageGains, _ := stage["attribute_gains"].(map[string]any)
	if stage["success"] != true || len(stageGains) != 0 {
		t.Fatalf("a stage is not a realm: %v", stage["attribute_gains"])
	}
	before := attributesOf(t, path)
	batch4Exec(t, path, `UPDATE characters SET phase=9,cultivation=100000 WHERE user_id=42`)
	crossed := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 3, map[string]any{"confirm": true}))
	if crossed["success"] != true || crossed["realm_gate"] != true {
		t.Fatalf("crossing: %v", crossed)
	}
	gains, _ := crossed["attribute_gains"].(map[string]any)
	if storage.ParseInt(gains["will"]) != 1 || storage.ParseInt(gains["agility"]) != 1 {
		t.Fatalf("a Sword Cultivator gains will and their agility: %v", gains)
	}
	after := attributesOf(t, path)
	if after["will"] != before["will"]+1 || after["agility"] != before["agility"]+1 || after["spirit"] != before["spirit"] {
		t.Fatalf("attributes %v -> %v", before, after)
	}
}

func attributesOf(t *testing.T, path string) map[string]int64 {
	t.Helper()
	var raw map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(sprint(actionScalar(t, path, `SELECT attributes_json FROM characters WHERE user_id=42`)))), &raw); err != nil {
		t.Fatal(err)
	}
	out := map[string]int64{}
	for key, value := range raw {
		out[key] = storage.ParseInt(value)
	}
	return out
}

func TestAnUntreatedDeviationDeepensAndAFullStageBanksNothing(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0,insight_xp=0 WHERE user_id=42`)
	batch4Result(t, batch4Apply(t, path, world, "cultivation.stance", 1, map[string]any{"stance": "force"}))
	seen := map[int64]bool{}
	for i := 0; i < 200 && len(seen) < 2; i++ {
		batch4Exec(t, path, `UPDATE characters SET cultivation=0 WHERE user_id=42`)
		result := trainOnce(t, path, world, 300+i)
		if dev, ok := result["deviation"].(map[string]any); ok && dev != nil {
			seen[storage.ParseInt(dev["severity"])] = true
		}
	}
	if !seen[1] || !seen[2] {
		t.Fatalf("an untreated deviation must deepen; severities seen: %v", seen)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT severity FROM character_conditions WHERE user_id=42 AND condition_key='qi_deviation' AND state='active'`)); got < 2 {
		t.Fatalf("stored severity=%d", got)
	}

	// A full stage: the session gathers nothing, so it banks nothing and
	// risks nothing either.
	batch4Result(t, batch4Apply(t, path, world, "cultivation.stance", 900, map[string]any{"stance": "refine"}))
	batch4Exec(t, path, `UPDATE characters SET cultivation=100000,insight_xp=0 WHERE user_id=42`)
	full := trainOnce(t, path, world, 901)
	if storage.ParseInt(full["gain"]) != 0 || full["stage_full"] != true {
		t.Fatalf("a full stage: %v", full)
	}
	if storage.ParseInt(full["insight_xp_gain"]) != 0 {
		t.Fatalf("a full stage banked Insight XP: %v", full["insight_xp_gain"])
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT insight_xp FROM characters WHERE user_id=42`)); got != 0 {
		t.Fatalf("insight_xp=%d after meditating at a full stage", got)
	}
}

func TestSeclusionIsPacedLikeTheStageItFills(t *testing.T) {
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	character := map[string]any{
		"realm_index": int64(0), "phase": int64(1), "body_realm_index": int64(0), "body_phase": int64(1),
		"attributes_json": `{"will":3,"body":3,"insight":2}`,
	}
	low := seclusionDailyGainGo(catalog, character, "qi", 1.0, 1.0)
	character["realm_index"], character["phase"] = int64(6), int64(9)
	high := seclusionDailyGainGo(catalog, character, "qi", 1.0, 1.0)
	if low < 1 || high <= low*5 {
		t.Fatalf("seclusion must scale with the stage: %d -> %d", low, high)
	}
	// It stays slower than sitting down for the sessions by hand.
	cost, _ := phaseCost(catalog.Realms, 6, 9)
	if float64(high) > float64(stagePace(cost, 6))*2 {
		t.Fatalf("seclusion pays %d a day against a pace of %d", high, stagePace(cost, 6))
	}
}
