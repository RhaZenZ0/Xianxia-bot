package game

import (
	"encoding/json"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A Law control effect reaches its target (v1.3.3).
//
// `special_effects.spatial_lockdown` and `.spatial_strangulation` have carried
// modifiers describing a *target* since they were written, and `combat.technique`
// resolved both while writing them nowhere - a battle opponent is a name on
// `battles`, not a row `active_effects` can address. Schema 63 gives the battle
// row the one place they can go, and these hold the two readers: the opponent's
// counter-attack loses the `agility`/`body` the effect took, and the player's
// flee gains what the opponent's `escape_bonus` lost.
//
// The dice are not pinned: the fixture's attributes of 500 make the technique
// certain, and the readers are asserted on the *modifier* each roll reports,
// which is arithmetic the dice do not touch.

func lawControlCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		LawSystem: worlddata.LawSystem{
			Stages: []worlddata.LawStage{{Index: 0, Name: "Unawakened", Min: 0}, {Index: 1, Name: "Glimpsed", Min: 10}},
			Techniques: map[string]worlddata.LawTechnique{
				"spatial_lockdown": {Name: "Spatial Lockdown", Law: "space", RequiresStage: 1, MinRealmIndex: 0, Effect: "spatial_lockdown"},
			},
		},
		SpecialEffects: map[string]map[string]any{
			// The shipped numbers, so the assertion below is about them.
			"spatial_lockdown": {
				"name": "Spatial Lockdown", "category": "Law Control", "description": "movement, flight and escape are suppressed",
				"modifiers": []any{
					map[string]any{"stat": "agility", "operation": "add", "value": -3.0},
					map[string]any{"stat": "escape_bonus", "operation": "add", "value": -5.0},
				},
			},
		},
	}
}

func setupLawControlDB(t *testing.T, withColumn bool) string {
	t.Helper()
	path := setupBugslayerCombatTurnDB(t, 901, false)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	script := `CREATE TABLE law_progress(user_id INTEGER NOT NULL,law_id TEXT NOT NULL,comprehension INTEGER NOT NULL DEFAULT 0,insights INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(user_id,law_id));
INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(901,'space',50,0,0);`
	if withColumn {
		script += "\nALTER TABLE battles ADD COLUMN opponent_modifiers_json TEXT NOT NULL DEFAULT '{}';"
	}
	if err := conn.ExecScript(script); err != nil {
		t.Fatal(err)
	}
	return path
}

func lawControlTechnique(t *testing.T, path string) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 1, "technique": "spatial_lockdown", "game_minute": 100})
	mut, err := combatTechniqueAction(conn, lawControlCatalog(), 901, raw)
	if err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any)
}

func lawControlTurn(t *testing.T, path, style string) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 1, "style": style, "game_minute": 100})
	mut, err := combatTurnAction(conn, lawControlCatalog(), 901, raw)
	if err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any)
}

func rollModifier(t *testing.T, out map[string]any, key string) int64 {
	t.Helper()
	roll, ok := out[key].(map[string]any)
	if !ok {
		t.Fatalf("%s is not a roll: %v", key, out[key])
	}
	return i64(roll["modifier"])
}

func TestALandedControlEffectIsWrittenOntoTheOpponent(t *testing.T) {
	path := setupLawControlDB(t, true)
	out := lawControlTechnique(t, path)
	if !bval(out["roll"].(map[string]any), "success") {
		t.Fatal("the fixture's attributes of 500 must make the technique certain")
	}
	raw := actionScalar(t, path, "SELECT opponent_modifiers_json FROM battles WHERE battle_id=1")
	var mods map[string]float64
	if err := json.Unmarshal([]byte(raw.(string)), &mods); err != nil {
		t.Fatal(err)
	}
	if mods["agility"] != -3 || mods["escape_bonus"] != -5 {
		t.Fatalf("the effect's modifiers did not reach the opponent: %v", mods)
	}
}

// The counter-attack is the reader that matters most: a locked-down opponent
// strikes back with what is left of them. The suppression the technique also
// applies has to run out first, so the turns are walked until a counter rolls.
func TestTheOpponentsCounterAttackLosesWhatTheEffectTook(t *testing.T) {
	plain := setupLawControlDB(t, true)
	debuffed := setupLawControlDB(t, true)
	lawControlTechnique(t, debuffed)
	var baseline, weakened int64
	for i := 0; i < 6; i++ {
		out := lawControlTurn(t, plain, "attack")
		if _, ok := out["counter_roll"]; ok {
			baseline = rollModifier(t, out, "counter_roll")
			break
		}
	}
	found := false
	for i := 0; i < 6; i++ {
		out := lawControlTurn(t, debuffed, "attack")
		if _, ok := out["counter_roll"]; ok {
			weakened = rollModifier(t, out, "counter_roll")
			found = true
			break
		}
	}
	if !found {
		t.Fatal("the suppression never ran out, so no counter was ever rolled")
	}
	if weakened != baseline-3 {
		t.Fatalf("a locked-down opponent countered at modifier %d against %d unhindered; the effect's agility -3 did not reach the counter-attack", weakened, baseline)
	}
}

func TestTheFleeRollGainsWhatTheOpponentCannotFollow(t *testing.T) {
	plain := setupLawControlDB(t, true)
	debuffed := setupLawControlDB(t, true)
	lawControlTechnique(t, debuffed)
	baseline := rollModifier(t, lawControlTurn(t, plain, "flee"), "player_roll")
	helped := rollModifier(t, lawControlTurn(t, debuffed, "flee"), "player_roll")
	if helped != baseline+5 {
		t.Fatalf("fleeing a locked-down opponent rolled at modifier %d against %d; the opponent's escape_bonus -5 did not reach the flee roll", helped, baseline)
	}
}

// In the compose stack the engine is healthy before db-init migrates, so a
// battle can be fought on a world without schema 63's column. It is fought
// without the debuff, never refused.
func TestAWorldWithoutTheColumnFightsWithoutTheDebuff(t *testing.T) {
	path := setupLawControlDB(t, false)
	out := lawControlTechnique(t, path)
	if !bval(out["roll"].(map[string]any), "success") {
		t.Fatal("the technique must still land")
	}
	if _, err := storage.Open(path); err != nil {
		t.Fatal(err)
	}
	turn := lawControlTurn(t, path, "flee")
	if _, ok := turn["player_roll"]; !ok {
		t.Fatalf("a turn on a world without the column did not resolve: %v", turn)
	}
}
