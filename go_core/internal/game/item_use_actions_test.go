package game

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func setupItemUseDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "item_use.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY, qi INTEGER, qi_max INTEGER, vitality INTEGER, vitality_max INTEGER,
	life_extension_years INTEGER NOT NULL DEFAULT 0, updated_at REAL
);
CREATE TABLE battles(
	battle_id INTEGER PRIMARY KEY, user_id INTEGER, player_hp INTEGER, player_hp_max INTEGER, status TEXT,
	version INTEGER DEFAULT 0, updated_at REAL
);
CREATE TABLE inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id));
CREATE TABLE active_effects(
	id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, effect_key TEXT, name TEXT, source_type TEXT, source_id TEXT,
	effect_json TEXT, stacks INTEGER, starts_game_minute INTEGER, ends_game_minute INTEGER, created_at REAL,
	UNIQUE(user_id,effect_key,source_type,source_id)
);
CREATE TABLE alchemy_state(
	user_id INTEGER PRIMARY KEY, pill_toxicity INTEGER NOT NULL DEFAULT 0, last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0,
	total_refinements INTEGER NOT NULL DEFAULT 0, successful_refinements INTEGER NOT NULL DEFAULT 0,
	flawless_refinements INTEGER NOT NULL DEFAULT 0, best_margin INTEGER NOT NULL DEFAULT -99, last_quality TEXT NOT NULL DEFAULT '',
	updated_at REAL
);
INSERT INTO characters(user_id,qi,qi_max,vitality,vitality_max,life_extension_years,updated_at) VALUES(101,5,20,3,20,0,0);
INSERT INTO inventory(user_id,item_id,quantity) VALUES(101,'qi_pill',2),(101,'longevity_pill',1),(101,'swift_wind_talisman',1),(101,'plain_stone',1);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func itemUseCatalog() worlddata.Catalog {
	return worlddata.Catalog{Items: map[string]worlddata.Item{
		"qi_pill": {
			Name: "Qi Pill",
			Use: worlddata.ItemUse{
				Instant:             worlddata.ItemInstantUse{QiRestore: 10},
				EffectKey:           "qi_nourishment",
				Name:                "Qi Nourishment",
				DurationGameMinutes: 240,
				Effect: map[string]any{
					"description": "Medicinal qi.",
					"modifiers":   []any{map[string]any{"stat": "cultivation_gain", "operation": "mul", "value": 1.2}, map[string]any{"operation": "add", "value": 1}},
					"tags":        []any{"pill", "cultivation"},
				},
			},
		},
		"longevity_pill": {
			Name: "Longevity Pill",
			Use:  worlddata.ItemUse{LifespanYears: 5},
		},
		"swift_wind_talisman": {
			Name: "Swift Wind Talisman",
			Use: worlddata.ItemUse{
				Effect: map[string]any{"modifiers": []any{map[string]any{"stat": "agility", "operation": "add", "value": 2}}},
			},
		},
		"plain_stone": {Name: "Plain Stone"},
	}}
}

func runItemUse(t *testing.T, path, itemID string, gameMinute int64) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"item_id": itemID, "game_minute": gameMinute})
	mut, err := itemUseActionGo(conn, itemUseCatalog(), 101, raw)
	if err != nil {
		return nil, err
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any), nil
}

func TestItemUseDoesAllFiveStepsInOneAction(t *testing.T) {
	path := setupItemUseDB(t)
	out, err := runItemUse(t, path, "qi_pill", 100)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// 1. consumed one
	if got := actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=101 AND item_id='qi_pill'"); storage.ParseInt(got) != 1 {
		t.Fatalf("quantity=%v, want 1", got)
	}
	// 2. restored, clamped to qi_max
	if storage.ParseInt(out["qi"]) != 15 || storage.ParseInt(out["qi_max"]) != 20 {
		t.Fatalf("qi=%v/%v, want 15/20", out["qi"], out["qi_max"])
	}
	// 4. effect keyed by the content's effect_key, sourced to the item, expiring at 100+240
	row := actionScalar(t, path, "SELECT ends_game_minute FROM active_effects WHERE user_id=101 AND effect_key='qi_nourishment' AND source_type='item' AND source_id='qi_pill'")
	if storage.ParseInt(row) != 340 {
		t.Fatalf("effect ends_game_minute=%v, want 340", row)
	}
	effectJSON := actionScalar(t, path, "SELECT effect_json FROM active_effects WHERE user_id=101 AND effect_key='qi_nourishment'")
	var payload map[string]any
	if err := json.Unmarshal([]byte(effectJSON.(string)), &payload); err != nil {
		t.Fatal(err)
	}
	mods := payload["modifiers"].([]any)
	if len(mods) != 1 { // the modifier without a stat is dropped, as normalize_effect_payload drops it
		t.Fatalf("modifiers=%v, want the one with a stat", mods)
	}
	if payload["name"] != "Qi Nourishment" || payload["category"] != "General" || payload["stacking"] != "replace" {
		t.Fatalf("normalised payload=%v", payload)
	}
	// 5. toxicity: a cultivation-tagged pill adds 12; below 40 there is no penalty effect
	if storage.ParseInt(out["toxicity_gain"]) != 12 || storage.ParseInt(out["pill_toxicity"]) != 12 || out["toxicity_band"] != "Clear Meridians" {
		t.Fatalf("toxicity=%v/%v/%v", out["toxicity_gain"], out["pill_toxicity"], out["toxicity_band"])
	}
	if got := actionScalar(t, path, "SELECT COUNT(*) FROM active_effects WHERE effect_key='pill_toxicity'"); storage.ParseInt(got) != 0 {
		t.Fatalf("pill_toxicity effect rows=%v, want 0 below the 40 threshold", got)
	}
	if _, ok := out["life_extension_total"]; ok {
		t.Fatalf("a qi pill must not report a life extension: %v", out)
	}
}

func TestItemUseToxicityCrossesIntoThePenaltyBandAndTheRowGoesAtZero(t *testing.T) {
	path := setupItemUseDB(t)
	// Two qi pills: 12 + 12 = 24, still clear; then seed to 30 and take the last... simpler: seed state.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO alchemy_state(user_id,pill_toxicity,last_toxicity_game_minute,updated_at) VALUES(101,35,100,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		_ = conn.Commit()
	}
	conn.Close()
	out, err := runItemUse(t, path, "qi_pill", 100)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if storage.ParseInt(out["pill_toxicity"]) != 47 || out["toxicity_band"] != "Pill Saturation" {
		t.Fatalf("toxicity=%v band=%v, want 47 / Pill Saturation", out["pill_toxicity"], out["toxicity_band"])
	}
	// The engine's own curve wrote the penalty effect - Python no longer has to.
	if got := actionScalar(t, path, "SELECT COUNT(*) FROM active_effects WHERE effect_key='pill_toxicity' AND source_type='alchemy'"); storage.ParseInt(got) != 1 {
		t.Fatalf("pill_toxicity effect rows=%v, want 1 at 47", got)
	}
	// second pill: 47+12=59, the row for the last pill is deleted rather than left at 0
	if _, err := runItemUse(t, path, "qi_pill", 100); err != nil {
		t.Fatal(err)
	}
	if got := actionScalar(t, path, "SELECT COUNT(*) FROM inventory WHERE user_id=101 AND item_id='qi_pill'"); storage.ParseInt(got) != 0 {
		t.Fatalf("inventory rows for qi_pill=%v, want 0 (no zero-quantity row)", got)
	}
	if _, err := runItemUse(t, path, "qi_pill", 100); err == nil || !strings.Contains(err.Error(), "no longer in your carried inventory") {
		t.Fatalf("third use err=%v, want the inventory refusal", err)
	}
}

func TestItemUseToxicityDecaysBeforeItAdds(t *testing.T) {
	path := setupItemUseDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	// 60 toxicity, last settled at minute 0; by minute 100+ the decay steps apply first.
	if _, err := conn.Execute(`INSERT INTO alchemy_state(user_id,pill_toxicity,last_toxicity_game_minute,updated_at) VALUES(101,60,1,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		_ = conn.Commit()
	}
	conn.Close()
	later := int64(1 + pillToxicityDecayMinutes*3)
	out, err := runItemUse(t, path, "qi_pill", later)
	if err != nil {
		t.Fatal(err)
	}
	want := maxI64(0, 60-3*pillToxicityDecayAmount) + 12
	if storage.ParseInt(out["pill_toxicity"]) != want {
		t.Fatalf("pill_toxicity=%v, want decayed-then-added %d", out["pill_toxicity"], want)
	}
}

func TestItemUseLifespanPillIsPermanentAndHeavilyToxic(t *testing.T) {
	path := setupItemUseDB(t)
	out, err := runItemUse(t, path, "longevity_pill", 50)
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(out["life_extension_years"]) != 5 || storage.ParseInt(out["life_extension_total"]) != 5 {
		t.Fatalf("life extension=%v/%v", out["life_extension_years"], out["life_extension_total"])
	}
	if got := actionScalar(t, path, "SELECT life_extension_years FROM characters WHERE user_id=101"); storage.ParseInt(got) != 5 {
		t.Fatalf("characters.life_extension_years=%v", got)
	}
	// name contains "pill" and it extends life: 24 residue, as pill_toxicity_value says
	if storage.ParseInt(out["toxicity_gain"]) != 24 {
		t.Fatalf("toxicity_gain=%v, want 24 for a lifespan pill", out["toxicity_gain"])
	}
	if _, ok := out["qi"]; ok {
		t.Fatalf("no instant restore on a longevity pill: %v", out)
	}
}

func TestItemUseTalismanEffectNeverExpiresAndIsNotAPill(t *testing.T) {
	path := setupItemUseDB(t)
	out, err := runItemUse(t, path, "swift_wind_talisman", 10)
	if err != nil {
		t.Fatal(err)
	}
	if out["effect_key"] != "swift_wind_talisman" || out["effect_name"] != "Swift Wind Talisman" {
		t.Fatalf("effect defaults=%v/%v", out["effect_key"], out["effect_name"])
	}
	if _, ok := out["effect_ends_game_minute"]; ok {
		t.Fatalf("duration 0 must mean no expiry: %v", out)
	}
	if got := actionScalar(t, path, "SELECT ends_game_minute IS NULL FROM active_effects WHERE effect_key='swift_wind_talisman'"); storage.ParseInt(got) != 1 {
		t.Fatalf("ends_game_minute should be NULL, got IS NULL=%v", got)
	}
	if _, ok := out["toxicity_gain"]; ok {
		t.Fatalf("a talisman is not a pill: %v", out)
	}
	if got := actionScalar(t, path, "SELECT COUNT(*) FROM alchemy_state"); storage.ParseInt(got) != 0 {
		t.Fatalf("alchemy_state rows=%v, want none touched", got)
	}
}

func TestItemUseRefusesItemsWithoutAnActiveUseBeforeConsuming(t *testing.T) {
	path := setupItemUseDB(t)
	_, err := runItemUse(t, path, "plain_stone", 10)
	if err == nil || !strings.Contains(err.Error(), "no implemented active use") {
		t.Fatalf("err=%v", err)
	}
	if got := actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=101 AND item_id='plain_stone'"); storage.ParseInt(got) != 1 {
		t.Fatalf("a refused use must not consume: quantity=%v", got)
	}
	if _, err := runItemUse(t, path, "not_in_catalog", 10); err == nil || !strings.Contains(err.Error(), "unknown item") {
		t.Fatalf("err=%v", err)
	}
}

func TestItemUseMidBattleKeepsTheHPBarInLockstep(t *testing.T) {
	path := setupItemUseDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO battles(battle_id,user_id,player_hp,player_hp_max,status,version,updated_at) VALUES(9,101,3,20,'active',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		_ = conn.Commit()
	}
	conn.Close()
	catalog := itemUseCatalog()
	healing := catalog.Items["qi_pill"]
	healing.Use.Instant = worlddata.ItemInstantUse{VitalityRestore: 12}
	catalog.Items["qi_pill"] = healing
	conn, err = storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"item_id": "qi_pill", "game_minute": 100})
	if _, err := itemUseActionGo(conn, catalog, 101, raw); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		_ = conn.Commit()
	}
	if got := actionScalar(t, path, "SELECT player_hp FROM battles WHERE battle_id=9"); storage.ParseInt(got) != 15 {
		t.Fatalf("battles.player_hp=%v, want 15", got)
	}
	if got := actionScalar(t, path, "SELECT version FROM battles WHERE battle_id=9"); storage.ParseInt(got) != 1 {
		t.Fatalf("battles.version=%v, want 1", got)
	}
}

func TestItemUseIsRegisteredAsAnAuthoritativeOperation(t *testing.T) {
	if !authoritativeMutations["item.use"] {
		t.Fatal("item.use is not in the authoritative operation set")
	}
}
