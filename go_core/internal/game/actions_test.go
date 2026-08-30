package game

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
)

func setupActionDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "actions.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE npc_relationships(user_id INTEGER,npc_name TEXT,trust INTEGER,respect INTEGER,fear INTEGER,affection INTEGER,debt INTEGER,grudge INTEGER,encounter_count INTEGER,last_summary TEXT,updated_at REAL,PRIMARY KEY(user_id,npc_name));
CREATE TABLE player_scene_state(user_id INTEGER PRIMARY KEY,physical_location TEXT,scene_type TEXT,scene_key TEXT,scene_label TEXT,channel_id INTEGER,metadata_json TEXT,updated_at REAL);
CREATE TABLE character_quests(user_id INTEGER,quest_key TEXT,status TEXT,progress_json TEXT,completed_game_minute INTEGER,updated_at REAL,PRIMARY KEY(user_id,quest_key));
CREATE TABLE characters(user_id INTEGER PRIMARY KEY,vitality INTEGER,vitality_max INTEGER,cultivation INTEGER,spirit_stones INTEGER,insight_xp INTEGER,updated_at REAL);
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY,user_id INTEGER,player_hp INTEGER,player_hp_max INTEGER,status TEXT,version INTEGER,updated_at REAL);
CREATE TABLE currency_wallets(user_id INTEGER,currency_id TEXT,balance INTEGER,PRIMARY KEY(user_id,currency_id));
CREATE TABLE inventory(user_id INTEGER,item_id TEXT,quantity INTEGER,PRIMARY KEY(user_id,item_id));
CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,event_type TEXT,payload_json TEXT,created_at REAL);
CREATE TABLE world_state(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at REAL NOT NULL DEFAULT 0);
INSERT INTO characters VALUES(42,20,20,5,0,0,0);
INSERT INTO battles VALUES(7,42,20,20,'active',0,0);
INSERT INTO character_quests VALUES(42,'first_steps','active','{"talk":0}',NULL,0);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func applyAction(t *testing.T, path, op string, actor int64, payload map[string]any) map[string]any {
	t.Helper()
	raw, _ := json.Marshal(payload)
	out, err := Apply(path, ActionRequest{Operation: op, ActorID: actor, Payload: raw})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("%s result type %T", op, out.Result)
	}
	return result
}

func actionScalar(t *testing.T, path, sql string, params ...any) any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, params)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return nil
	}
	return res.Rows[0][0]
}

func TestRelationshipUpdatePersistsAndClamps(t *testing.T) {
	path := setupActionDB(t)
	first := applyAction(t, path, "relationship.update", 42, map[string]any{"npc_name": "Elder Pine", "trust": 15, "respect": 20, "summary": "Greeting"})
	if storage.ParseInt(first["trust"]) != 15 || storage.ParseInt(first["encounter_count"]) != 1 {
		t.Fatalf("first=%v", first)
	}
	second := applyAction(t, path, "relationship.update", 42, map[string]any{"npc_name": "Elder Pine", "trust": 500, "grudge": -500, "summary": "Service"})
	if storage.ParseInt(second["trust"]) != 100 || storage.ParseInt(second["grudge"]) != -100 || storage.ParseInt(second["encounter_count"]) != 2 {
		t.Fatalf("second=%v", second)
	}
}

func TestSceneTransitionPersistsPhysicalAndActiveScene(t *testing.T) {
	path := setupActionDB(t)
	applyAction(t, path, "scene.transition", 42, map[string]any{"physical_location": "Greenriver Town", "scene_type": "expedition", "scene_key": "expedition:42", "scene_label": "Greenriver Expedition", "channel_id": 123, "metadata": map[string]any{"guild_id": 9}})
	if got := actionScalar(t, path, "SELECT physical_location FROM player_scene_state WHERE user_id=42"); got != "Greenriver Town" {
		t.Fatalf("physical=%v", got)
	}
	if got := actionScalar(t, path, "SELECT scene_type FROM player_scene_state WHERE user_id=42"); got != "expedition" {
		t.Fatalf("scene=%v", got)
	}
}

func TestQuestProgressUpdatesPersistentQuestState(t *testing.T) {
	path := setupActionDB(t)
	batch4SetCanonicalGameMinute(t, path, 130)
	result := applyAction(t, path, "quest.progress", 42, map[string]any{"quest_key": "first_steps", "objectives": []map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}}, "objective_type": "talk", "target": "elder pine", "amount": 1})
	if complete, _ := result["complete"].(bool); !complete {
		t.Fatalf("result=%v", result)
	}
	if got := actionScalar(t, path, "SELECT status FROM character_quests WHERE user_id=42 AND quest_key='first_steps'"); got != "completed" {
		t.Fatalf("status=%v", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT completed_game_minute FROM character_quests WHERE user_id=42 AND quest_key='first_steps'")); got != 130 {
		t.Fatalf("completed=%d", got)
	}
}

func TestCombatDamageKeepsBattleAndCharacterVitalityInSync(t *testing.T) {
	path := setupActionDB(t)
	result := applyAction(t, path, "combat.apply_damage", 42, map[string]any{"battle_id": 7, "damage": 6})
	if storage.ParseInt(result["vitality"]) != 14 {
		t.Fatalf("result=%v", result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT player_hp FROM battles WHERE battle_id=7")); got != 14 {
		t.Fatalf("battle hp=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT vitality FROM characters WHERE user_id=42")); got != 14 {
		t.Fatalf("character hp=%d", got)
	}
}

func TestCultivationRewardCapsProgressAndCommitsWalletItemsAndEvent(t *testing.T) {
	path := setupActionDB(t)
	result := applyAction(t, path, "cultivation.reward", 42, map[string]any{"cultivation": 10, "cultivation_cap": 12, "spirit_stones": 3, "insight_xp": 2, "items": map[string]int64{"spirit_herb": 2}, "event_type": "cultivate"})
	if storage.ParseInt(result["cultivation_awarded"]) != 7 {
		t.Fatalf("result=%v", result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT cultivation FROM characters WHERE user_id=42")); got != 12 {
		t.Fatalf("cultivation=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got != 3 {
		t.Fatalf("wallet=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")); got != 2 {
		t.Fatalf("items=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM event_log WHERE user_id=42 AND event_type='cultivate'")); got != 1 {
		t.Fatalf("events=%d", got)
	}
}
