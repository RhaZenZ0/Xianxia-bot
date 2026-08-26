package game

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
)

func setupAdminDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "admin.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	schema := `
CREATE TABLE characters(user_id INTEGER PRIMARY KEY,name TEXT,life_status TEXT,location TEXT,karma_score INTEGER,vitality INTEGER,vitality_max INTEGER,qi INTEGER,qi_max INTEGER,spirit_stones INTEGER,death_game_minute INTEGER,reincarnation_ready_game_minute INTEGER,updated_at REAL);
CREATE TABLE currency_wallets(user_id INTEGER,currency_id TEXT,balance INTEGER,PRIMARY KEY(user_id,currency_id));
CREATE TABLE catalog_locations(name TEXT PRIMARY KEY,data_json TEXT,updated_at REAL);
CREATE TABLE player_scene_state(user_id INTEGER PRIMARY KEY,physical_location TEXT,scene_type TEXT,scene_key TEXT,scene_label TEXT,channel_id INTEGER,metadata_json TEXT,updated_at REAL);
CREATE TABLE reincarnation_state(user_id INTEGER PRIMARY KEY,active INTEGER);
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,updated_at REAL);
CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,event_type TEXT,payload_json TEXT,created_at REAL);
CREATE TABLE admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_user_id INTEGER NOT NULL,action TEXT,target TEXT,before_json TEXT,after_json TEXT,reason TEXT,created_at REAL);
CREATE TABLE world_state(key TEXT PRIMARY KEY,value_json TEXT,updated_at REAL);
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
INSERT INTO characters VALUES(42,'Lin Test','dead','Old Place',5,0,20,0,30,10,100,200,0);
INSERT INTO catalog_locations VALUES('Greenriver Town','{}',0);
INSERT INTO reincarnation_state VALUES(42,1);
INSERT INTO battles VALUES(1,42,'active',0);
INSERT INTO world_simulation_state VALUES('npc_life',0,10080,0,0);
`
	if err := conn.ExecScript(schema); err != nil {
		t.Fatal(err)
	}
	return path
}

func applyAdmin(t *testing.T, path, op string, payload map[string]any) any {
	t.Helper()
	raw, _ := json.Marshal(payload)
	out, err := Apply(path, ActionRequest{Operation: op, ActorID: 0, Payload: raw})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	return out.Result
}

func scalar(t *testing.T, path, sql string, params ...any) any {
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

func TestAdminActionsMutateAndAudit(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.teleport", map[string]any{"user_id": 42, "location": "Greenriver Town", "reason": "test"})
	if got := scalar(t, path, "SELECT location FROM characters WHERE user_id=42"); got != "Greenriver Town" {
		t.Fatalf("location=%v", got)
	}

	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_stone", "amount": 90, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got != 90 {
		t.Fatalf("balance=%d", got)
	}

	applyAdmin(t, path, "admin.player.karma", map[string]any{"user_id": 42, "delta": 25, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42")); got != 30 {
		t.Fatalf("karma=%d", got)
	}

	applyAdmin(t, path, "admin.player.revive", map[string]any{"user_id": 42, "reason": "test"})
	if got := scalar(t, path, "SELECT life_status FROM characters WHERE user_id=42"); got != "alive" {
		t.Fatalf("life=%v", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT vitality FROM characters WHERE user_id=42")); got != 20 {
		t.Fatalf("vitality=%d", got)
	}

	applyAdmin(t, path, "admin.player.clear_battle", map[string]any{"user_id": 42, "reason": "test"})
	if got := scalar(t, path, "SELECT status FROM battles WHERE battle_id=1"); got != "abandoned" {
		t.Fatalf("battle=%v", got)
	}

	applyAdmin(t, path, "admin.automation.set", map[string]any{"system": "npc_life", "enabled": false, "reason": "test"})
	applyAdmin(t, path, "admin.simulation.interval", map[string]any{"system": "npc_life", "days": 3, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT interval_game_minutes FROM world_simulation_state WHERE system='npc_life'")); got != 4320 {
		t.Fatalf("interval=%d", got)
	}

	applyAdmin(t, path, "admin.world.advance_time", map[string]any{"minutes": 1440, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM admin_audit_log")); got < 8 {
		t.Fatalf("audit count=%d", got)
	}
}
