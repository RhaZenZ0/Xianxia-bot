package game

// v0.23.0 regression tests for the out-of-battle half of `/law technique`.
//
// What moved is not the effect but the gate: the requirement checks and the
// write both lived in the Discord command, so "may this player use this
// technique" was answered by the presentation layer and the engine never saw
// the question.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func lawTechniqueCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		LawSystem: worlddata.LawSystem{
			Stages: []worlddata.LawStage{
				{Index: 0, Name: "Unawakened", Min: 0},
				{Index: 1, Name: "Glimpsed", Min: 10},
				{Index: 2, Name: "Grasped", Min: 40},
			},
			Techniques: map[string]worlddata.LawTechnique{
				"spatial_step": {
					Name: "Spatial Step", Law: "space",
					RequiresStage: 2, MinRealmIndex: 3, Effect: "spatial_step_echo",
				},
				"world_collapse": {
					Name: "World Collapse", Law: "space",
					RequiresStage: 2, MinRealmIndex: 3, Effect: "spatial_step_echo",
				},
				"quiet_law": {
					Name: "Quiet Law", Law: "space", RequiresStage: 1, MinRealmIndex: 0,
				},
			},
		},
		SpecialEffects: map[string]map[string]any{
			"spatial_step_echo": {
				"name":      "Spatial Step Echo",
				"category":  "Law",
				"modifiers": []any{map[string]any{"stat": "agility", "operation": "add", "value": 3}},
			},
		},
	}
}

func setupLawTechniqueDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE personal_worlds(user_id INTEGER PRIMARY KEY, stability INTEGER NOT NULL DEFAULT 100);
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL DEFAULT 0);
INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(42,'space',50,3,0);
UPDATE characters SET realm_index=4 WHERE user_id=42;
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func lawTechniqueApply(t *testing.T, path, technique string) (authoritativeMutation, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, err := json.Marshal(map[string]any{"technique": technique, "game_minute": 4000})
	if err != nil {
		t.Fatal(err)
	}
	mut, actionErr := lawTechniqueAction(conn, lawTechniqueCatalog(), 42, raw)
	// storage.Conn opens an implicit transaction on the first write, so a
	// direct call has to commit before another connection can see anything.
	// In production applyAuthoritative owns that commit.
	if conn.InTransaction() {
		if actionErr != nil {
			if err := conn.Rollback(); err != nil {
				t.Fatal(err)
			}
		} else if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut, actionErr
}

func lawEffectRows(t *testing.T, path string) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(
		`SELECT COUNT(*) FROM active_effects WHERE user_id=42 AND source_type='law'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	return storage.ParseInt(res.Rows[0][0])
}

func TestAQualifiedLawTechniqueAppliesItsEffectForTwoHours(t *testing.T) {
	path := setupLawTechniqueDB(t)
	mut, err := lawTechniqueApply(t, path, "spatial_step")
	if err != nil {
		t.Fatal(err)
	}
	result := mut.Result.(map[string]any)
	if fmt.Sprint(result["effect_name"]) != "Spatial Step Echo" {
		t.Fatalf("effect_name=%v", result["effect_name"])
	}
	if got := storage.ParseInt(result["ends_game_minute"]); got != 4000+120 {
		t.Fatalf("ends_game_minute=%d, want 4120", got)
	}
	if got := lawEffectRows(t, path); got != 1 {
		t.Fatalf("law effect rows=%d, want 1", got)
	}
}

func TestATechniqueBelowItsLawStageIsRefused(t *testing.T) {
	path := setupLawTechniqueDB(t)
	// comprehension 50 is stage 2; drop it to stage 1.
	batch4Exec(t, path, `UPDATE law_progress SET comprehension=15 WHERE user_id=42 AND law_id='space'`)
	if _, err := lawTechniqueApply(t, path, "spatial_step"); err == nil {
		t.Fatal("a technique was used below its required law stage")
	}
	if got := lawEffectRows(t, path); got != 0 {
		t.Fatalf("law effect rows=%d after a refusal", got)
	}
}

func TestATechniqueAboveTheCharactersRealmIsRefused(t *testing.T) {
	path := setupLawTechniqueDB(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=1 WHERE user_id=42`)
	if _, err := lawTechniqueApply(t, path, "spatial_step"); err == nil {
		t.Fatal("a technique was used below its required realm")
	}
	if got := lawEffectRows(t, path); got != 0 {
		t.Fatalf("law effect rows=%d after a refusal", got)
	}
}

func TestWorldCollapseNeedsAPersonalWorld(t *testing.T) {
	path := setupLawTechniqueDB(t)
	if _, err := lawTechniqueApply(t, path, "world_collapse"); err == nil {
		t.Fatal("world collapse ran without a personal world")
	}
	batch4Exec(t, path, `INSERT INTO personal_worlds(user_id,stability) VALUES(42,100)`)
	if _, err := lawTechniqueApply(t, path, "world_collapse"); err != nil {
		t.Fatalf("world collapse was refused with a personal world present: %v", err)
	}
}

func TestATechniqueDuringABattleIsSentToTheBattlePanel(t *testing.T) {
	// The command had this branch: with a battle open the technique resolves
	// against the opponent through combat.technique. Reaching the self-buff
	// path mid-duel would be a free buff and a lost turn.
	path := setupLawTechniqueDB(t)
	batch4Exec(t, path, `INSERT INTO battles(user_id,status,updated_at) VALUES(42,'active',0)`)
	_, err := lawTechniqueApply(t, path, "spatial_step")
	if err == nil {
		t.Fatal("a self-buff was applied during an active battle")
	}
	if !strings.Contains(err.Error(), "battle") {
		t.Fatalf("refusal was %q; it should say why", err)
	}
	if got := lawEffectRows(t, path); got != 0 {
		t.Fatalf("law effect rows=%d during a battle", got)
	}
}

func TestATechniqueWithNoEffectStillManifests(t *testing.T) {
	// Not every technique carries a special effect; those are description-only
	// and must not be mistaken for a failure.
	path := setupLawTechniqueDB(t)
	mut, err := lawTechniqueApply(t, path, "quiet_law")
	if err != nil {
		t.Fatal(err)
	}
	result := mut.Result.(map[string]any)
	if _, ok := result["effect_name"]; ok {
		t.Fatalf("an effect-less technique reported one: %v", result)
	}
	if got := lawEffectRows(t, path); got != 0 {
		t.Fatalf("law effect rows=%d, want none", got)
	}
}

func TestAnUnknownTechniqueIsRefused(t *testing.T) {
	path := setupLawTechniqueDB(t)
	if _, err := lawTechniqueApply(t, path, "not_a_technique"); err == nil {
		t.Fatal("an unknown technique was accepted")
	}
}

func TestReusingATechniqueRefreshesRatherThanStacking(t *testing.T) {
	path := setupLawTechniqueDB(t)
	if _, err := lawTechniqueApply(t, path, "spatial_step"); err != nil {
		t.Fatal(err)
	}
	if _, err := lawTechniqueApply(t, path, "spatial_step"); err != nil {
		t.Fatal(err)
	}
	if got := lawEffectRows(t, path); got != 1 {
		t.Fatalf("law effect rows=%d; the effect stacked instead of refreshing", got)
	}
}
