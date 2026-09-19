package game

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A realm's treasure is kept (v1.0.0-rc.54).
//
// `ItemUse.DurationGameMinutes` has meant "0 does not expire" since v0.21.0 and
// **no item in the catalogue has ever set it**: every effect the game shipped
// ran for 120 to 360 minutes. The five upper-world realms added here each hold
// one find that grants +1 to the attribute its own last trial tests, for good -
// so the field stops being a promise the content never took up.
//
// Two halves have to hold or the treasure is decoration. The writer must store
// NULL rather than a minute, and every reader is
// `ends_game_minute IS NULL OR ends_game_minute > ?` - so a 0 written as 0
// would read as expired the instant it was drunk, which is precisely the shape
// this drives out.
func permanentTreasureCatalog() worlddata.Catalog {
	return worlddata.Catalog{Items: map[string]worlddata.Item{
		"keepsake": {
			Name: "A Realm's Keepsake",
			Use: worlddata.ItemUse{
				EffectKey:           "keepsake",
				Name:                "The Realm's Mark",
				DurationGameMinutes: 0,
				Effect: map[string]any{
					"modifiers": []any{map[string]any{"stat": "will", "operation": "add", "value": 1}},
				},
			},
		},
	}}
}

func TestARealmsTreasureNeverExpires(t *testing.T) {
	path := filepath.Join(t.TempDir(), "treasure.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, qi INTEGER, qi_max INTEGER, vitality INTEGER, vitality_max INTEGER,
	life_extension_years INTEGER NOT NULL DEFAULT 0, updated_at REAL);
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY, user_id INTEGER, player_hp INTEGER, player_hp_max INTEGER, status TEXT,
	version INTEGER DEFAULT 0, updated_at REAL);
CREATE TABLE inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id));
CREATE TABLE active_effects(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, effect_key TEXT, name TEXT,
	source_type TEXT, source_id TEXT, effect_json TEXT, stacks INTEGER, starts_game_minute INTEGER,
	ends_game_minute INTEGER, created_at REAL, UNIQUE(user_id,effect_key,source_type,source_id));
CREATE TABLE alchemy_state(user_id INTEGER PRIMARY KEY, pill_toxicity INTEGER NOT NULL DEFAULT 0,
	last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0, total_refinements INTEGER NOT NULL DEFAULT 0,
	successful_refinements INTEGER NOT NULL DEFAULT 0, flawless_refinements INTEGER NOT NULL DEFAULT 0,
	best_margin INTEGER NOT NULL DEFAULT -99, last_quality TEXT NOT NULL DEFAULT '', updated_at REAL);
INSERT INTO characters(user_id,qi,qi_max,vitality,vitality_max,life_extension_years,updated_at) VALUES(101,5,20,3,20,0,0);
INSERT INTO inventory(user_id,item_id,quantity) VALUES(101,'keepsake',1);
`); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"item_id": "keepsake", "game_minute": 1000})
	if _, err := itemUseActionGo(conn, permanentTreasureCatalog(), 101, raw); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	res, err := conn.Execute(`SELECT ends_game_minute FROM active_effects WHERE user_id=101 AND effect_key='keepsake'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) != 1 {
		t.Fatalf("the treasure wrote %d effect rows, wanted 1", len(res.Rows))
	}
	if res.Rows[0][0] != nil {
		t.Fatalf("ends_game_minute is %v, not NULL - a treasure that expires is a pill", res.Rows[0][0])
	}
	conn.Close()

	// A year of world time later it is still in the set every roll reads.
	fresh, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer fresh.Close()
	mods, err := loadEffectModifiers(fresh, 101, 1000+525600, AptitudeBundle{}, mechanicsCharacter{}, worlddata.Catalog{})
	if err != nil {
		t.Fatal(err)
	}
	if got := mods.Add["will"]; got != 1 {
		t.Fatalf("will carries %v a year on, wanted +1 - the treasure stopped applying", got)
	}
}
