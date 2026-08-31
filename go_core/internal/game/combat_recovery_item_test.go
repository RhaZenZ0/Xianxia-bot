package game

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func setupCombatRecoveryItemDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "combat_recovery_item.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(
	user_id INTEGER PRIMARY KEY, qi INTEGER, qi_max INTEGER, vitality INTEGER, vitality_max INTEGER, updated_at REAL
);
CREATE TABLE battles(
	battle_id INTEGER PRIMARY KEY, user_id INTEGER, npc_name TEXT, npc_realm_index INTEGER, npc_stage INTEGER,
	player_hp INTEGER, player_hp_max INTEGER, npc_hp INTEGER, npc_hp_max INTEGER, status TEXT, location TEXT,
	source TEXT, target_key TEXT, npc_suppressed_turns INTEGER DEFAULT 0, version INTEGER DEFAULT 0,
	created_at REAL, updated_at REAL
);
CREATE TABLE inventory(user_id INTEGER, item_id TEXT, quantity INTEGER, PRIMARY KEY(user_id,item_id));
INSERT INTO characters(user_id,qi,qi_max,vitality,vitality_max,updated_at) VALUES(101,5,20,3,20,0);
INSERT INTO battles(battle_id,user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,status,location,source,target_key,npc_suppressed_turns,version,created_at,updated_at)
	VALUES(9,101,'Iron Bandit',1,2,3,20,10,10,'active','Greenriver Town','challenge:npc:iron-bandit','challenge:npc:iron-bandit',0,0,0,0);
INSERT INTO inventory(user_id,item_id,quantity) VALUES(101,'healing_pill',2);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func combatRecoveryItemCatalog() worlddata.Catalog {
	return worlddata.Catalog{
		Items: map[string]worlddata.Item{
			"healing_pill": {
				Name: "Healing Pill",
				Use:  worlddata.ItemUse{Instant: worlddata.ItemInstantUse{VitalityRestore: 12}},
			},
		},
	}
}

func TestCombatRecoveryItemKeepsBattlePlayerHPInLockstepWithCharacterVitality(t *testing.T) {
	path := setupCombatRecoveryItemDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 9, "item_id": "healing_pill", "game_minute": 100})
	mut, err := combatRecoveryItemAction(conn, combatRecoveryItemCatalog(), 101, raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	out := mut.Result.(map[string]any)
	// vitality 3 + 12 restore = 15, clamped to vitality_max 20.
	if storage.ParseInt(out["vitality"]) != 15 {
		t.Fatalf("result vitality=%v", out["vitality"])
	}
	// Before this fix, battles.player_hp never moved off its stale value (3)
	// after a mid-battle heal - only characters.vitality changed.
	if got := actionScalar(t, path, "SELECT player_hp FROM battles WHERE battle_id=9"); storage.ParseInt(got) != 15 {
		t.Fatalf("battles.player_hp=%v, want 15 (in lockstep with characters.vitality)", got)
	}
	if got := actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=101 AND item_id='healing_pill'"); storage.ParseInt(got) != 1 {
		t.Fatalf("inventory quantity=%v, want 1 (one pill consumed)", got)
	}
	if got := actionScalar(t, path, "SELECT version FROM battles WHERE battle_id=9"); storage.ParseInt(got) != 1 {
		t.Fatalf("battles.version=%v, want bumped to 1", got)
	}
}
