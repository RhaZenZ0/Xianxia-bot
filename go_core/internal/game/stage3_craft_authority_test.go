package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupCraftAuthorityTables(t *testing.T, path string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	if err := conn.ExecScript(`
ALTER TABLE cave_abodes ADD COLUMN alchemy_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN forge_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cave_abodes ADD COLUMN formation_level INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS world_state(
	key TEXT PRIMARY KEY,
	value_json TEXT NOT NULL,
	updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alchemy_state(
	user_id INTEGER PRIMARY KEY,
	pill_toxicity INTEGER NOT NULL DEFAULT 0,
	last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0,
	total_refinements INTEGER NOT NULL DEFAULT 0,
	successful_refinements INTEGER NOT NULL DEFAULT 0,
	flawless_refinements INTEGER NOT NULL DEFAULT 0,
	best_margin INTEGER NOT NULL DEFAULT -99,
	last_quality TEXT NOT NULL DEFAULT '',
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alchemy_batches(
	batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
	user_id INTEGER NOT NULL,
	recipe_name TEXT NOT NULL,
	quality TEXT NOT NULL,
	margin INTEGER NOT NULL DEFAULT 0,
	success INTEGER NOT NULL DEFAULT 0,
	output_json TEXT NOT NULL DEFAULT '{}',
	location TEXT NOT NULL DEFAULT '',
	game_minute INTEGER NOT NULL DEFAULT 0,
	created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sect_membership(
	user_id INTEGER PRIMARY KEY,
	sect_name TEXT NOT NULL,
	rank_name TEXT NOT NULL DEFAULT 'Disciple',
	rank_level INTEGER NOT NULL DEFAULT 0,
	joined_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sect_manors(
	sect_name TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	base_location TEXT NOT NULL,
	qi_array_level INTEGER NOT NULL DEFAULT 0,
	alchemy_hall_level INTEGER NOT NULL DEFAULT 0,
	forge_pavilion_level INTEGER NOT NULL DEFAULT 0,
	defense_array_level INTEGER NOT NULL DEFAULT 0,
	founded_by_user_id INTEGER,
	created_game_minute INTEGER NOT NULL DEFAULT 0,
	created_at REAL NOT NULL DEFAULT 0,
	updated_at REAL NOT NULL DEFAULT 0
);
`); err != nil {
		t.Fatal(err)
	}
}

func applyCraftExpectError(t *testing.T, path, world string, seq int, payload map[string]any) error {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	_, err = ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   fmt.Sprintf("stage3-craft-error-%d", seq),
		Operation:  "craft.resolve",
		ActorID:    42,
		Payload:    raw,
	})
	return err
}

func TestCraftResolveRejectsForgedCanonicalInputs(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)

	for index, field := range []string{
		"context_bonus",
		"location",
		"game_minute",
		"effect_bonus",
		"facility_bonus",
		"manor_facility_bonus",
		"family_bonus",
		"modifier",
		"tn",
		"profession",
		"cost",
		"output",
		"quality",
		"roll",
	} {
		payload := map[string]any{"recipe": "Spirit-Iron Sword", field: 999999}
		err := applyCraftExpectError(t, path, world, index+1, payload)
		if err == nil || !strings.Contains(err.Error(), "client-supplied "+field+" is forbidden") {
			t.Fatalf("%s err=%v", field, err)
		}
	}
}

func TestCraftResolveDerivesCanonicalAlchemyContextAndTime(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldWithForageAptitudeBonus(t)

	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at)
		VALUES('world_clock','{"anchor_game_minute":1540,"anchor_real_ts":1,"scale":0}',0)`)
	batch4Exec(t, path, `UPDATE characters SET location='guest_cave' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO cave_abodes(
		user_id,location_key,base_location,herb_garden_level,alchemy_level,forge_level,formation_level
	) VALUES(43,'guest_cave','Greenriver Town',0,4,0,0)`)
	batch4Exec(t, path, `INSERT INTO cave_abode_access(owner_user_id,guest_user_id) VALUES(43,42)`)
	batch4Exec(t, path, `INSERT INTO birth_families(archetype) VALUES('alchemy_family')`)
	batch4Exec(t, path, `INSERT INTO character_birth_family(user_id,family_id) VALUES(42,1)`)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name) VALUES(42,'Azure Test Sect')`)
	batch4Exec(t, path, `INSERT INTO sect_manors(
		sect_name,name,base_location,alchemy_hall_level
	) VALUES('Azure Test Sect','Azure Test Manor','guest_cave',3)`)
	batch4Exec(t, path, `INSERT INTO profession_progress(
		user_id,profession,level,xp,successes,failures,quality_points,updated_at
	) VALUES(42,'Alchemy',2,0,0,0,0,0)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES
		(42,'spirit_herb',10),(42,'beast_core',10)`)
	batch4Exec(t, path, `INSERT INTO active_effects(
		user_id,effect_key,name,source_type,source_id,effect_json,stacks,
		starts_game_minute,ends_game_minute,created_at
	) VALUES(42,'stage3_buff','Stage 3 Buff','test','buff',
		'{"modifiers":[{"stat":"alchemy_bonus","operation":"add","value":3}]}',1,0,NULL,0)`)
	batch4Exec(t, path, `INSERT INTO deployed_location_arrays(
		location,item_id,name,owner_user_id,sect_name,effect_json,
		starts_game_minute,ends_game_minute,created_at,updated_at
	) VALUES('guest_cave','stage3_array','Stage 3 Array',42,'',
		'{"modifiers":[{"stat":"alchemy_bonus","operation":"add","value":2}]}',
		0,2000,0,0)`)
	batch4Exec(t, path, `UPDATE character_spiritual_roots SET mutation='stage2_forager' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO alchemy_state(
		user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
		successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
	) VALUES(42,50,100,0,0,0,-99,'',0)`)

	result := batch4Result(t, batch4Apply(
		t,
		path,
		world,
		"craft.resolve",
		30,
		map[string]any{"recipe": "Recovery Pill"},
	))

	expectInt := map[string]int64{
		"game_minute":          1540,
		"effect_bonus":         6,
		"facility_bonus":       8,
		"manor_facility_bonus": 6,
		"family_bonus":         2,
		"context_bonus":        22,
		"profession_bonus":     2,
		"modifier":             224,
	}
	for field, want := range expectInt {
		if got := storage.ParseInt(result[field]); got != want {
			t.Fatalf("%s=%d want %d", field, got, want)
		}
	}
	if got := fmt.Sprint(result["location"]); got != "guest_cave" {
		t.Fatalf("location=%q want guest_cave", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT pill_toxicity FROM alchemy_state WHERE user_id=42")); got != 48 {
		t.Fatalf("pill_toxicity=%d want 48", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT last_toxicity_game_minute FROM alchemy_state WHERE user_id=42")); got != 1540 {
		t.Fatalf("last_toxicity_game_minute=%d want 1540", got)
	}
	if got := fmt.Sprint(actionScalar(t, path, "SELECT location FROM alchemy_batches WHERE user_id=42 ORDER BY batch_id DESC LIMIT 1")); got != "guest_cave" {
		t.Fatalf("alchemy batch location=%q", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT game_minute FROM alchemy_batches WHERE user_id=42 ORDER BY batch_id DESC LIMIT 1")); got != 1540 {
		t.Fatalf("alchemy batch game_minute=%d", got)
	}
}

func TestCraftResolveDerivesForgingAndFormationFacilities(t *testing.T) {
	tests := []struct {
		name            string
		recipe          string
		profession      string
		location        string
		effectStat      string
		abodeColumn     string
		abodeLevel      int64
		manorColumn     string
		manorLevel      int64
		professionLevel int64
		inventorySQL    string
		wantEffect      int64
		wantFacility    int64
		wantManor       int64
		wantContext     int64
		wantModifier    int64
	}{
		{
			name:            "forging",
			recipe:          "Spirit-Iron Sword",
			profession:      "Forging",
			location:        "forge_cave",
			effectStat:      "forging_bonus",
			abodeColumn:     "forge_level",
			abodeLevel:      2,
			manorColumn:     "forge_pavilion_level",
			manorLevel:      3,
			professionLevel: 1,
			inventorySQL:    `(42,'spirit_iron',10),(42,'beast_core',10)`,
			wantEffect:      6,
			wantFacility:    4,
			wantManor:       6,
			wantContext:     16,
			wantModifier:    217,
		},
		{
			name:            "formation",
			recipe:          "Swift-Wind Talisman",
			profession:      "Formation",
			location:        "formation_cave",
			effectStat:      "formation_bonus",
			abodeColumn:     "formation_level",
			abodeLevel:      1,
			manorColumn:     "defense_array_level",
			manorLevel:      5,
			professionLevel: 0,
			inventorySQL:    `(42,'talisman_paper',10),(42,'spirit_ink',10)`,
			wantEffect:      4,
			wantFacility:    2,
			wantManor:       10,
			wantContext:     16,
			wantModifier:    216,
		},
	}

	for index, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			path := setupBatch4AuthorityDB(t)
			setupCraftAuthorityTables(t, path)
			world := batch4WorldPath(t)

			batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at)
				VALUES('world_clock','{"anchor_game_minute":2000,"anchor_real_ts":1,"scale":0}',0)`)
			batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, tc.location)
			batch4Exec(
				t,
				path,
				fmt.Sprintf(
					`INSERT INTO cave_abodes(user_id,location_key,base_location,herb_garden_level,%s)
					 VALUES(42,?,?,0,?)`,
					tc.abodeColumn,
				),
				tc.location,
				"Greenriver Town",
				tc.abodeLevel,
			)
			batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name) VALUES(42,'Stage 3 Sect')`)
			batch4Exec(
				t,
				path,
				fmt.Sprintf(
					`INSERT INTO sect_manors(sect_name,name,base_location,%s)
					 VALUES('Stage 3 Sect','Stage 3 Manor',?,?)`,
					tc.manorColumn,
				),
				tc.location,
				tc.manorLevel,
			)
			batch4Exec(
				t,
				path,
				`INSERT INTO profession_progress(
					user_id,profession,level,xp,successes,failures,quality_points,updated_at
				) VALUES(42,?,?,0,0,0,0,0)`,
				tc.profession,
				tc.professionLevel,
			)
			batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES `+tc.inventorySQL)
			batch4Exec(
				t,
				path,
				`INSERT INTO active_effects(
					user_id,effect_key,name,source_type,source_id,effect_json,stacks,
					starts_game_minute,ends_game_minute,created_at
				) VALUES(42,'stage3_profession_buff','Stage 3 Profession Buff','test','buff',?,1,0,NULL,0)`,
				fmt.Sprintf(`{"modifiers":[{"stat":%q,"operation":"add","value":4}]}`, tc.effectStat),
			)
			batch4Exec(
				t,
				path,
				`INSERT INTO deployed_location_arrays(
					location,item_id,name,owner_user_id,sect_name,effect_json,
					starts_game_minute,ends_game_minute,created_at,updated_at
				) VALUES(?,'stage3_profession_array','Stage 3 Profession Array',42,'',?,0,3000,0,0)`,
				tc.location,
				fmt.Sprintf(`{"modifiers":[{"stat":%q,"operation":"add","value":%d}]}`, tc.effectStat, tc.wantEffect-4),
			)

			result := batch4Result(t, batch4Apply(
				t,
				path,
				world,
				"craft.resolve",
				200+index,
				map[string]any{"recipe": tc.recipe},
			))

			expectInt := map[string]int64{
				"game_minute":          2000,
				"effect_bonus":         tc.wantEffect,
				"facility_bonus":       tc.wantFacility,
				"manor_facility_bonus": tc.wantManor,
				"family_bonus":         0,
				"context_bonus":        tc.wantContext,
				"profession_bonus":     tc.professionLevel,
				"modifier":             tc.wantModifier,
			}
			for field, want := range expectInt {
				if got := storage.ParseInt(result[field]); got != want {
					t.Fatalf("%s=%d want %d", field, got, want)
				}
			}
			if got := fmt.Sprint(result["location"]); got != tc.location {
				t.Fatalf("location=%q want %q", got, tc.location)
			}
		})
	}
}

func TestCraftResolveRequiresLivingCanonicalCharacter(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupCraftAuthorityTables(t, path)
	world := batch4WorldPath(t)

	batch4Exec(t, path, `UPDATE characters SET life_status='deceased' WHERE user_id=42`)
	err := applyCraftExpectError(
		t,
		path,
		world,
		100,
		map[string]any{"recipe": "Spirit-Iron Sword"},
	)
	if err == nil || !strings.Contains(err.Error(), "only a living character can craft") {
		t.Fatalf("err=%v", err)
	}
}
