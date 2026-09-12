package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A better cultivation system (v1.0.0-rc.3): the stance, the odds before
// the roll, and the realm gate that a banked insight or a completed
// perfection opens.

func setupCultivationDB(t *testing.T) string {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// The tables the training multipliers read, absent from the batch
	// fixtures because no native test trained before this one.
	if err := conn.ExecScript(`
CREATE TABLE IF NOT EXISTS world_eras(era_id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,modifiers_json TEXT NOT NULL DEFAULT '{}',active INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,contribution_points INTEGER NOT NULL DEFAULT 0,influence INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sect_manors(sect_name TEXT PRIMARY KEY,name TEXT NOT NULL,base_location TEXT NOT NULL,qi_array_level INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sect_lineage(master_user_id INTEGER NOT NULL,disciple_user_id INTEGER NOT NULL,attention INTEGER NOT NULL DEFAULT 0);
ALTER TABLE cave_abodes ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE cave_abodes ADD COLUMN cultivation_level INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS sect_abodes(user_id INTEGER NOT NULL,location_key TEXT NOT NULL,name TEXT NOT NULL DEFAULT '',base_location TEXT NOT NULL DEFAULT '',cultivation_level INTEGER NOT NULL DEFAULT 0);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

// batch4ApplyErr is batch4Apply that hands the refusal back instead of
// failing the test on it - the refusal is what these tests assert.
func batch4ApplyErr(path, world, op string, actor int64, seq int, payload map[string]any) (ActionResponse, error) {
	raw, _ := json.Marshal(payload)
	return ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("stance-%s-%d-%d", op, actor, seq), Operation: op, ActorID: actor, Payload: raw})
}

func cultivationQuery(t *testing.T, path, world, op string, actor int64) map[string]any {
	t.Helper()
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: op, ActorID: actor, Payload: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	return batch4Result(t, out)
}

func TestBreakthroughOddsCountTheHundredPairsOfTwoTens(t *testing.T) {
	cases := []struct{ modifier, tn, want int64 }{
		{0, 2, 100}, {0, 12, 45}, {0, 20, 1}, {0, 21, 0}, {5, 12, 85}, {-3, 12, 21}, {100, 30, 100},
	}
	for _, c := range cases {
		if got := breakthroughOdds(c.modifier, c.tn); got != c.want {
			t.Errorf("odds(%d, %d)=%d want %d", c.modifier, c.tn, got, c.want)
		}
	}
}

func TestTheStanceShapesTrainingAndRefineBanksInsight(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	// Stage 3 with room to gain, so the cap does not hide the multiplier.
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0,insight_xp=0 WHERE user_id=42`)
	status := cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["stance"] != "circulate" || status["realm_gate"] != false || storage.ParseInt(status["stage"]) != 3 {
		t.Fatalf("default status: %v", status)
	}
	if _, err := batch4ApplyErr(path, world, "cultivation.stance", 42, 1, map[string]any{"stance": "meditate"}); err == nil || !strings.Contains(err.Error(), "unknown stance") {
		t.Fatalf("an unknown stance must be refused, got %v", err)
	}
	set := batch4Result(t, batch4Apply(t, path, world, "cultivation.stance", 2, map[string]any{"stance": "refine"}))
	if set["stance"] != "refine" || set["previous"] != "circulate" || set["changed"] != true {
		t.Fatalf("stance set: %v", set)
	}
	trained := batch4Result(t, batch4Apply(t, path, world, "cultivation.train", 3, map[string]any{"cooldown_seconds": 0, "game_minute": 600}))
	if trained["stance"] != "refine" || storage.ParseInt(trained["insight_xp_gain"]) != refineInsightXPPerSession {
		t.Fatalf("refine training: %v", trained)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT insight_xp FROM characters WHERE user_id=42`)); got != refineInsightXPPerSession {
		t.Fatalf("refine banks insight_xp: %d", got)
	}
	if m, _ := trained["stance_mult"].(float64); m != 0.8 {
		t.Fatalf("refine multiplier: %v", trained["stance_mult"])
	}
	// The multiplier is applied: a refine gain is below what the base alone
	// would have given at the fixture's will of 100 (base 108-114 -> x0.8).
	if gain := storage.ParseInt(trained["gain"]); gain <= 0 || gain > 110 {
		t.Fatalf("refine gain=%d", gain)
	}
	status = cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["stance"] != "refine" || storage.ParseInt(status["insight_xp"]) != refineInsightXPPerSession {
		t.Fatalf("status after refine: %v", status)
	}
}

func TestForceStanceRisksAQiDeviationThatIsARealCondition(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0 WHERE user_id=42`)
	batch4Result(t, batch4Apply(t, path, world, "cultivation.stance", 1, map[string]any{"stance": "force"}))
	deviated := false
	for i := 0; i < 80 && !deviated; i++ {
		batch4Exec(t, path, `UPDATE characters SET cultivation=0 WHERE user_id=42`)
		batch4Exec(t, path, `DELETE FROM cooldowns WHERE user_id=42`)
		trained := batch4Result(t, batch4Apply(t, path, world, "cultivation.train", 10+i, map[string]any{"cooldown_seconds": 0, "game_minute": 600}))
		if m, _ := trained["stance_mult"].(float64); m != 1.3 {
			t.Fatalf("force multiplier: %v", trained["stance_mult"])
		}
		if dev, ok := trained["deviation"].(map[string]any); ok && dev != nil {
			if dev["condition_key"] != "qi_deviation" {
				t.Fatalf("deviation: %v", dev)
			}
			deviated = true
		}
	}
	if !deviated {
		t.Fatal("eighty forced sessions and no deviation: the risk is not applied")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM character_conditions WHERE user_id=42 AND condition_key='qi_deviation' AND state='active'`)); got != 1 {
		t.Fatalf("qi deviation rows=%d", got)
	}
}

func TestTheRealmGateNeedsABankedInsightAndSpendsItOnTheCrossing(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	// Realm 0 stage 9, essence to spare, will 100 - the dice cannot fail,
	// so the gate alone decides.
	batch4Exec(t, path, `UPDATE characters SET realm_index=0,phase=9,cultivation=100000,insight_xp=3 WHERE user_id=42`)
	status := cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["realm_gate"] != true || status["gate_open"] != false || storage.ParseInt(status["insight_cost"]) != 5 {
		t.Fatalf("gate status: %v", status)
	}
	odds, _ := status["odds"].(map[string]any)
	if storage.ParseInt(odds["probability"]) != 100 || odds["stage_nine"] != true {
		t.Fatalf("odds: %v", odds)
	}
	if _, err := batch4ApplyErr(path, world, "cultivation.breakthrough", 42, 1, map[string]any{"confirm": true}); err == nil || !strings.Contains(err.Error(), "realm gate") {
		t.Fatalf("the closed gate must refuse, got %v", err)
	}
	if _, err := batch4ApplyErr(path, world, "cultivation.insight", 42, 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), "costs 5 Insight XP") {
		t.Fatalf("short of XP must refuse, got %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET insight_xp=7 WHERE user_id=42`)
	banked := batch4Result(t, batch4Apply(t, path, world, "cultivation.insight", 3, map[string]any{}))
	if banked["banked"] != true || storage.ParseInt(banked["cost"]) != 5 || storage.ParseInt(banked["insight_xp"]) != 2 {
		t.Fatalf("bank: %v", banked)
	}
	if _, err := batch4ApplyErr(path, world, "cultivation.insight", 42, 4, map[string]any{}); err == nil || !strings.Contains(err.Error(), "already banked") {
		t.Fatalf("a second insight must refuse, got %v", err)
	}
	status = cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["gate_open"] != true || status["insight_banked"] != true {
		t.Fatalf("gate after banking: %v", status)
	}
	crossed := batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 5, map[string]any{"confirm": true}))
	if crossed["success"] != true || crossed["gate_via_insight"] != true || crossed["insight_spent"] != true || storage.ParseInt(crossed["probability"]) != 100 {
		t.Fatalf("crossing: %v", crossed)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_state WHERE key=?`, cultivationInsightKey(42))); got != 0 {
		t.Fatalf("the insight must be spent, rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT realm_index FROM characters WHERE user_id=42`)); got != 1 {
		t.Fatalf("realm after crossing=%d", got)
	}
	// A completed perfection opens the gate without an insight.
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=9,cultivation=100000 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO realm_perfection(user_id,realm_index,active,completed,updated_at) VALUES(42,1,0,1,0)`)
	status = cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["gate_open"] != true || status["insight_banked"] != false || status["perfection_completed"] != true {
		t.Fatalf("perfection gate: %v", status)
	}
	crossed = batch4Result(t, batch4Apply(t, path, world, "cultivation.breakthrough", 6, map[string]any{"confirm": true}))
	if crossed["success"] != true || crossed["gate_via_insight"] != false || storage.ParseInt(crossed["perfect_bonus"]) != 2 {
		t.Fatalf("perfection crossing: %v", crossed)
	}
}
