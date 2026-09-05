package game

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

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
CREATE TABLE characters(user_id INTEGER PRIMARY KEY,name TEXT,life_status TEXT,location TEXT,karma_score INTEGER,vitality INTEGER,vitality_max INTEGER,qi INTEGER,qi_max INTEGER,spirit_stones INTEGER,realm_index INTEGER,phase INTEGER,death_game_minute INTEGER,reincarnation_ready_game_minute INTEGER,updated_at REAL,is_muted INTEGER NOT NULL DEFAULT 0,is_frozen INTEGER NOT NULL DEFAULT 0,moderation_reason TEXT NOT NULL DEFAULT '');
CREATE TABLE currency_wallets(user_id INTEGER,currency_id TEXT,balance INTEGER,PRIMARY KEY(user_id,currency_id));
CREATE TABLE catalog_locations(name TEXT PRIMARY KEY,data_json TEXT,updated_at REAL);
CREATE TABLE player_scene_state(user_id INTEGER PRIMARY KEY,physical_location TEXT,scene_type TEXT,scene_key TEXT,scene_label TEXT,channel_id INTEGER,metadata_json TEXT,updated_at REAL);
CREATE TABLE reincarnation_state(user_id INTEGER PRIMARY KEY,active INTEGER,reincarnation_ready_at REAL NOT NULL DEFAULT 0,previous_name TEXT NOT NULL DEFAULT '');
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,updated_at REAL);
CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,event_type TEXT,payload_json TEXT,created_at REAL);
CREATE TABLE admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_user_id INTEGER NOT NULL,action TEXT,target TEXT,before_json TEXT,after_json TEXT,reason TEXT,created_at REAL);
CREATE TABLE world_state(key TEXT PRIMARY KEY,value_json TEXT,updated_at REAL);
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE inventory(user_id INTEGER,item_id TEXT,quantity INTEGER,PRIMARY KEY(user_id,item_id));
CREATE TABLE cooldowns(user_id INTEGER NOT NULL,action TEXT NOT NULL,available_at REAL NOT NULL,PRIMARY KEY(user_id,action));
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,world_name TEXT,updated_at REAL);
CREATE TABLE character_fate(user_id INTEGER PRIMARY KEY,points INTEGER NOT NULL DEFAULT 0,lifetime_earned INTEGER NOT NULL DEFAULT 0,lifetime_spent INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
CREATE TABLE fate_ledger(entry_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,delta INTEGER NOT NULL,balance_after INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
CREATE TABLE world_events(event_key TEXT PRIMARY KEY,title TEXT,location TEXT,active INTEGER,starts_at REAL,ends_at REAL);
CREATE TABLE boss_encounters(encounter_id INTEGER PRIMARY KEY AUTOINCREMENT,party_id INTEGER,status TEXT,updated_at REAL);
CREATE TABLE boss_participants(encounter_id INTEGER,user_id INTEGER,status TEXT,updated_at REAL,PRIMARY KEY(encounter_id,user_id));
CREATE TABLE pvp_matches(match_id INTEGER PRIMARY KEY AUTOINCREMENT,challenge_id INTEGER,player1_user_id INTEGER,player2_user_id INTEGER,status TEXT,updated_at REAL);
CREATE TABLE pvp_challenges(challenge_id INTEGER PRIMARY KEY AUTOINCREMENT,challenger_user_id INTEGER,target_user_id INTEGER,status TEXT,created_at REAL,expires_at REAL);
CREATE TABLE sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL);
CREATE TABLE sects(sect_name TEXT PRIMARY KEY,prestige INTEGER NOT NULL DEFAULT 0,treasury_stones INTEGER NOT NULL DEFAULT 0,policy_json TEXT NOT NULL DEFAULT '{}',updated_at REAL NOT NULL);
CREATE TABLE realm_perfection(user_id INTEGER NOT NULL,realm_index INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,training_progress INTEGER NOT NULL DEFAULT 0,quest_index INTEGER NOT NULL DEFAULT 0,quest_preparation INTEGER NOT NULL DEFAULT 0,completed_quests INTEGER NOT NULL DEFAULT 0,discovered_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL,PRIMARY KEY(user_id,realm_index));
CREATE TABLE body_realm_perfection(user_id INTEGER NOT NULL,realm_index INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,training_progress INTEGER NOT NULL DEFAULT 0,quest_index INTEGER NOT NULL DEFAULT 0,quest_preparation INTEGER NOT NULL DEFAULT 0,completed_quests INTEGER NOT NULL DEFAULT 0,discovered_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL,PRIMARY KEY(user_id,realm_index));
CREATE TABLE character_spiritual_roots(user_id INTEGER PRIMARY KEY,grade TEXT NOT NULL DEFAULT 'Common',purity INTEGER NOT NULL DEFAULT 50,elements_json TEXT NOT NULL DEFAULT '[]',mutation TEXT NOT NULL DEFAULT '',stability INTEGER NOT NULL DEFAULT 100,refinement_progress INTEGER NOT NULL DEFAULT 0,compatibility INTEGER NOT NULL DEFAULT 50,updated_at REAL NOT NULL);
CREATE TABLE character_bloodlines(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,bloodline_id TEXT NOT NULL,name TEXT NOT NULL,affinity TEXT NOT NULL DEFAULT 'None',purity INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'dormant',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,rejection INTEGER NOT NULL DEFAULT 0,mutation TEXT NOT NULL DEFAULT '',primary_lineage INTEGER NOT NULL DEFAULT 0,source_family_id INTEGER,unlocked_techniques_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL,UNIQUE(user_id,bloodline_id));
CREATE TABLE character_physiques(user_id INTEGER PRIMARY KEY,physique_id TEXT NOT NULL DEFAULT 'ordinary_mortal_body',name TEXT NOT NULL DEFAULT 'Ordinary Mortal Body',state TEXT NOT NULL DEFAULT 'ordinary',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,stability INTEGER NOT NULL DEFAULT 100,instability INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
CREATE TABLE tribulation_state(user_id INTEGER NOT NULL,gate_realm_index INTEGER NOT NULL,preparation INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,cleared INTEGER NOT NULL DEFAULT 0,last_result TEXT NOT NULL DEFAULT '',updated_game_minute INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(user_id,gate_realm_index));
CREATE TABLE character_conditions(condition_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,condition_key TEXT NOT NULL,category TEXT NOT NULL,name TEXT NOT NULL,severity INTEGER NOT NULL DEFAULT 1,state TEXT NOT NULL DEFAULT 'active',source_type TEXT NOT NULL DEFAULT 'system',source_id TEXT NOT NULL DEFAULT '',effect_json TEXT NOT NULL DEFAULT '{}',created_game_minute INTEGER NOT NULL DEFAULT 0,updated_game_minute INTEGER NOT NULL DEFAULT 0,resolved_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE alchemy_state(user_id INTEGER PRIMARY KEY,pill_toxicity INTEGER NOT NULL DEFAULT 0,last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0,total_refinements INTEGER NOT NULL DEFAULT 0,successful_refinements INTEGER NOT NULL DEFAULT 0,flawless_refinements INTEGER NOT NULL DEFAULT 0,best_margin INTEGER NOT NULL DEFAULT -99,last_quality TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL);
CREATE TABLE spirit_beasts(beast_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,name TEXT NOT NULL,species TEXT NOT NULL,rank INTEGER NOT NULL DEFAULT 0,element TEXT NOT NULL DEFAULT 'None',intelligence INTEGER NOT NULL DEFAULT 10,temperament TEXT NOT NULL DEFAULT 'wary',bloodline TEXT NOT NULL DEFAULT 'Common',evolution_stage INTEGER NOT NULL DEFAULT 0,loyalty INTEGER NOT NULL DEFAULT 25,contract_type TEXT NOT NULL DEFAULT 'temporary',active INTEGER NOT NULL DEFAULT 0,techniques_json TEXT NOT NULL DEFAULT '[]',created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,slot TEXT NOT NULL,durability INTEGER NOT NULL,max_durability INTEGER NOT NULL,quality INTEGER NOT NULL DEFAULT 100,equipped INTEGER NOT NULL DEFAULT 0,bound_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE cave_abodes(user_id INTEGER PRIMARY KEY,location_key TEXT NOT NULL UNIQUE,name TEXT NOT NULL,base_location TEXT NOT NULL,grade TEXT NOT NULL DEFAULT 'Mortal',cultivation_level INTEGER NOT NULL DEFAULT 1,alchemy_level INTEGER NOT NULL DEFAULT 0,forge_level INTEGER NOT NULL DEFAULT 0,formation_level INTEGER NOT NULL DEFAULT 0,defense_level INTEGER NOT NULL DEFAULT 0,thread_id INTEGER,thread_channel_id INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE cave_abode_access(owner_user_id INTEGER NOT NULL,guest_user_id INTEGER NOT NULL,access_role TEXT NOT NULL DEFAULT 'guest',created_at REAL NOT NULL,PRIMARY KEY(owner_user_id,guest_user_id));
INSERT INTO characters VALUES(42,'Lin Test','dead','Old Place',5,0,20,0,30,10,2,3,100,200,0,0,0,'');
INSERT INTO catalog_locations VALUES('Greenriver Town','{}',0);
INSERT INTO reincarnation_state VALUES(42,1,0,'Lin Previous');
INSERT INTO battles VALUES(1,42,'active',0);
INSERT INTO world_simulation_state VALUES('npc_life',0,10080,0,0);
`
	if err := conn.ExecScript(schema); err != nil {
		t.Fatal(err)
	}
	return path
}

func applyAdminAs(t *testing.T, path, op string, actorID int64, payload map[string]any) any {
	t.Helper()
	raw, _ := json.Marshal(payload)
	out, err := Apply(path, ActionRequest{Operation: op, ActorID: actorID, Payload: raw})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	return out.Result
}

func applyAdmin(t *testing.T, path, op string, payload map[string]any) any {
	t.Helper()
	return applyAdminAs(t, path, op, 0, payload)
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

func TestAdminGrantCurrencyPreservesDiscordRangeAndLegacyMirrorSemantics(t *testing.T) {
	path := setupAdminDB(t)

	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{
		"user_id": 42, "currency_id": "low_spirit_stone", "amount": 90, "reason": "range test",
	})
	if got := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42")); got != 100 {
		t.Fatalf("spirit_stones=%d, want 100", got)
	}

	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{
		"user_id": 42, "currency_id": "low_immortal_stone", "amount": int64(2000000000), "reason": "upper bound",
	})
	if got := storage.ParseInt(scalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_immortal_stone'")); got != 2000000000 {
		t.Fatalf("balance=%d, want 2000000000", got)
	}
}

func TestAdminKarmaAllowsZeroAndPreservesReason(t *testing.T) {
	path := setupAdminDB(t)

	applyAdmin(t, path, "admin.player.karma", map[string]any{
		"user_id": 42, "delta": 0, "reason": "audit reason",
	})
	if got := storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42")); got != 5 {
		t.Fatalf("karma=%d, want 5", got)
	}
	payload := fmt.Sprint(scalar(t, path, "SELECT payload_json FROM event_log WHERE event_type='karma_change' ORDER BY id DESC LIMIT 1"))
	if !strings.Contains(payload, `"reason":"audit reason"`) {
		t.Fatalf("event payload did not preserve reason: %s", payload)
	}
}

func TestAdminAutomationSupportsAllDiscordChoices(t *testing.T) {
	path := setupAdminDB(t)
	for _, system := range []string{
		"event_expiry",
		"auction_settlement",
		"unexpected_events",
		"maintenance_cleanup",
		"npc_civilization",
		"npc_life",
		"sect_politics",
		"dynamic_economy",
		"clan_dynamics",
		"background_seclusion",
		"black_markets",
		"autonomous_world_events",
	} {
		applyAdmin(t, path, "admin.automation.set", map[string]any{
			"system": system, "enabled": false, "reason": "choice coverage",
		})
	}
}

func TestAdminAdvanceTimeAppliesRequestedScaleAndClampsFutureAnchor(t *testing.T) {
	path := setupAdminDB(t)
	future := float64(time.Now().Add(time.Hour).UnixNano()) / 1e9
	state, _ := json.Marshal(map[string]any{
		"anchor_game_minute": int64(1000),
		"anchor_real_ts":     future,
		"scale":              int64(9),
	})
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0)`,
		[]any{string(state)},
	); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	result := applyAdmin(t, path, "admin.world.advance_time", map[string]any{
		"minutes": 60, "scale": 2, "reason": "clock test",
	})
	data, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := storage.ParseInt(data["game_minute"]); got != 1060 {
		t.Fatalf("game_minute=%d, want 1060", got)
	}
	raw := fmt.Sprint(scalar(t, path, "SELECT value_json FROM world_state WHERE key='world_clock'"))
	if !strings.Contains(raw, `"scale":2`) {
		t.Fatalf("world clock did not apply requested scale: %s", raw)
	}
}

func TestAdminAdvanceTimeScaleZeroFreezesAndNegativeScaleRejected(t *testing.T) {
	path := setupAdminDB(t)
	state, _ := json.Marshal(map[string]any{
		"anchor_game_minute": int64(500),
		"anchor_real_ts":     float64(time.Now().UnixNano()) / 1e9,
		"scale":              int64(4),
	})
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(
		`INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0)`,
		[]any{string(state)},
	); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	// scale=0 must be accepted and freeze the clock's ongoing drift rate - this
	// is the dashboard's "New time scale" field wired through in v0.19.29.
	applyAdmin(t, path, "admin.world.advance_time", map[string]any{"minutes": 60, "scale": 0, "reason": "freeze test"})
	raw := fmt.Sprint(scalar(t, path, "SELECT value_json FROM world_state WHERE key='world_clock'"))
	if !strings.Contains(raw, `"scale":0`) {
		t.Fatalf("world clock did not apply scale=0: %s", raw)
	}

	// A negative scale is nonsensical (game time can't run backwards on its
	// own) and must be rejected rather than silently stored.
	raw2, _ := json.Marshal(map[string]any{"minutes": 60, "scale": -1, "reason": "should fail"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.world.advance_time", Payload: raw2}); err == nil {
		t.Fatal("expected error for negative scale")
	} else if !strings.Contains(err.Error(), "scale cannot be negative") {
		t.Fatalf("unexpected error for negative scale: %v", err)
	}
}

func TestAdminForceReincarnationReadyClearsWallClockGateAndAudits(t *testing.T) {
	path := setupAdminDB(t)
	future := float64(time.Now().Add(48 * time.Hour).UnixNano()) / 1e9
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`UPDATE reincarnation_state SET reincarnation_ready_at=? WHERE user_id=42`, []any{future}); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	result := applyAdmin(t, path, "admin.player.force_reincarnation_ready", map[string]any{"user_id": 42, "reason": "unstick soul"})
	data, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := storage.ParseInt(data["reincarnation_ready_at"]); got != 0 {
		t.Fatalf("result reincarnation_ready_at=%d, want 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT reincarnation_ready_at FROM reincarnation_state WHERE user_id=42")); got != 0 {
		t.Fatalf("stored reincarnation_ready_at=%d, want 0", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != "admin.player.force_reincarnation_ready" {
		t.Fatalf("action=%q, want admin.player.force_reincarnation_ready", got)
	}
	beforeJSON := fmt.Sprint(scalar(t, path, "SELECT before_json FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1"))
	if !strings.Contains(beforeJSON, fmt.Sprintf("%v", future)) && !strings.Contains(beforeJSON, "reincarnation_ready_at") {
		t.Fatalf("before_json did not capture prior state: %s", beforeJSON)
	}
}

func TestAdminForceReincarnationReadyRejectsNoActiveCycle(t *testing.T) {
	path := setupAdminDB(t)
	// user 999 has no reincarnation_state row at all.
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.force_reincarnation_ready", Payload: mustJSON(map[string]any{"user_id": 999, "reason": "test"})}); err == nil {
		t.Fatal("expected error for user with no active Samsara cycle")
	} else if !strings.Contains(err.Error(), "no active Samsara cycle is waiting for this soul") {
		t.Fatalf("unexpected error: %v", err)
	}

	// Also verify an *inactive* row (active=0) is treated the same as absent -
	// the AND active=1 guard must not be dropped.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO reincarnation_state(user_id,active,reincarnation_ready_at,previous_name) VALUES(555,0,0,'Inactive Soul')`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.force_reincarnation_ready", Payload: mustJSON(map[string]any{"user_id": 555, "reason": "test"})}); err == nil {
		t.Fatal("expected error for user with an inactive (not active=1) reincarnation_state row")
	} else if !strings.Contains(err.Error(), "no active Samsara cycle is waiting for this soul") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func mustJSON(v map[string]any) json.RawMessage {
	raw, _ := json.Marshal(v)
	return raw
}

func TestAdminSetPillToxicityUpsertsClampsAndAudits(t *testing.T) {
	path := setupAdminDB(t)

	// No alchemy_state row exists yet for user 42 - this must upsert, not error.
	result := applyAdmin(t, path, "admin.player.set_pill_toxicity", map[string]any{"user_id": 42, "pill_toxicity": 250, "reason": "test"})
	data, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := storage.ParseInt(data["pill_toxicity"]); got != 250 {
		t.Fatalf("pill_toxicity=%d, want 250", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT pill_toxicity FROM alchemy_state WHERE user_id=42")); got != 250 {
		t.Fatalf("stored pill_toxicity=%d, want 250", got)
	}

	// Out-of-range values must clamp to the 0-1000 sanity ceiling, not store verbatim.
	applyAdmin(t, path, "admin.player.set_pill_toxicity", map[string]any{"user_id": 42, "pill_toxicity": 999999, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT pill_toxicity FROM alchemy_state WHERE user_id=42")); got != 1000 {
		t.Fatalf("pill_toxicity=%d, want clamped to 1000", got)
	}

	if got := fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != "admin.player.set_pill_toxicity" {
		t.Fatalf("action=%q, want admin.player.set_pill_toxicity", got)
	}
}

func TestAdminSetPillToxicityRejectsUnknownCharacter(t *testing.T) {
	path := setupAdminDB(t)
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_pill_toxicity", Payload: mustJSON(map[string]any{"user_id": 999, "pill_toxicity": 10, "reason": "test"})}); err == nil {
		t.Fatal("expected error for unknown character")
	} else if !strings.Contains(err.Error(), "character not found") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestAdminSetBeastStatsUpdatesOnlySuppliedFieldsAndAudits(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO spirit_beasts(beast_id,user_id,name,species,loyalty,evolution_stage,active,created_at,updated_at) VALUES(1,42,'Cloud Fox','Fox',40,1,1,0,0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	// Supplying only loyalty must leave evolution_stage untouched.
	applyAdmin(t, path, "admin.player.set_beast_stats", map[string]any{"user_id": 42, "beast_id": 1, "loyalty": 90, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT loyalty FROM spirit_beasts WHERE beast_id=1")); got != 90 {
		t.Fatalf("loyalty=%d, want 90", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT evolution_stage FROM spirit_beasts WHERE beast_id=1")); got != 1 {
		t.Fatalf("evolution_stage=%d, want unchanged 1", got)
	}

	// loyalty is clamped to 0-100.
	applyAdmin(t, path, "admin.player.set_beast_stats", map[string]any{"user_id": 42, "beast_id": 1, "loyalty": 500, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT loyalty FROM spirit_beasts WHERE beast_id=1")); got != 100 {
		t.Fatalf("loyalty=%d, want clamped to 100", got)
	}

	if got := fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != "admin.player.set_beast_stats" {
		t.Fatalf("action=%q, want admin.player.set_beast_stats", got)
	}
}

func TestAdminSetBeastStatsRejectsMissingBeastAndEmptyPayload(t *testing.T) {
	path := setupAdminDB(t)
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_beast_stats", Payload: mustJSON(map[string]any{"user_id": 42, "beast_id": 1, "loyalty": 50, "reason": "test"})}); err == nil {
		t.Fatal("expected error for nonexistent beast")
	} else if !strings.Contains(err.Error(), "spirit beast not found for this character") {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_beast_stats", Payload: mustJSON(map[string]any{"user_id": 42, "beast_id": 1, "reason": "test"})}); err == nil {
		t.Fatal("expected error when neither loyalty nor evolution_stage is supplied")
	} else if !strings.Contains(err.Error(), "loyalty or evolution_stage is required") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestAdminRemoveEquipmentDeletesAndAudits(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO equipment_instances(equipment_id,user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at) VALUES(1,42,'iron_sword','weapon',50,100,80,1,0,0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.remove_equipment", map[string]any{"user_id": 42, "equipment_id": 1, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM equipment_instances WHERE equipment_id=1")); got != 0 {
		t.Fatalf("equipment row still exists after remove, count=%d", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != "admin.player.remove_equipment" {
		t.Fatalf("action=%q, want admin.player.remove_equipment", got)
	}
	beforeJSON := fmt.Sprint(scalar(t, path, "SELECT before_json FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1"))
	if !strings.Contains(beforeJSON, "iron_sword") {
		t.Fatalf("before_json did not capture the removed item: %s", beforeJSON)
	}
}

func TestAdminRemoveEquipmentRejectsWrongOwnerOrMissing(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO characters VALUES(43,'Second Test','alive','Old Place',0,10,10,10,10,0,0,1,NULL,NULL,0,0,0,'')`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO equipment_instances(equipment_id,user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at) VALUES(1,42,'iron_sword','weapon',50,100,80,1,0,0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	// equipment 1 belongs to user 42, not 43 - must be rejected, not deleted.
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.remove_equipment", Payload: mustJSON(map[string]any{"user_id": 43, "equipment_id": 1, "reason": "test"})}); err == nil {
		t.Fatal("expected error removing another character's equipment")
	} else if !strings.Contains(err.Error(), "equipment instance not found for this character") {
		t.Fatalf("unexpected error: %v", err)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM equipment_instances WHERE equipment_id=1")); got != 1 {
		t.Fatalf("equipment row was deleted despite ownership mismatch, count=%d", got)
	}
}

func TestAdminSetAbodeAccessGrantsAndRevokesAndAudits(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO characters VALUES(43,'Second Test','alive','Old Place',0,10,10,10,10,0,0,1,NULL,NULL,0,0,0,'')`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO cave_abodes(user_id,location_key,name,base_location,created_at,updated_at) VALUES(42,'lin-abode','Lin''s Retreat','Old Place',0,0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_abode_access", map[string]any{"owner_user_id": 42, "guest_user_id": 43, "access_role": "friend", "reason": "test"})
	if got := fmt.Sprint(scalar(t, path, "SELECT access_role FROM cave_abode_access WHERE owner_user_id=42 AND guest_user_id=43")); got != "friend" {
		t.Fatalf("access_role=%q, want friend", got)
	}

	applyAdmin(t, path, "admin.player.set_abode_access", map[string]any{"owner_user_id": 42, "guest_user_id": 43, "revoke": true, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM cave_abode_access WHERE owner_user_id=42 AND guest_user_id=43")); got != 0 {
		t.Fatalf("access row still exists after revoke, count=%d", got)
	}

	if got := fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != "admin.player.set_abode_access" {
		t.Fatalf("action=%q, want admin.player.set_abode_access", got)
	}
}

func TestAdminSetAbodeAccessRejectsOwnerWithNoAbode(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO characters VALUES(43,'Second Test','alive','Old Place',0,10,10,10,10,0,0,1,NULL,NULL,0,0,0,'')`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_abode_access", Payload: mustJSON(map[string]any{"owner_user_id": 42, "guest_user_id": 43, "reason": "test"})}); err == nil {
		t.Fatal("expected error for owner with no cave abode")
	} else if !strings.Contains(err.Error(), "owner does not have a cave abode") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestAdminSetModerationUpdatesOnlySuppliedFieldsAndAudits(t *testing.T) {
	path := setupAdminDB(t)

	// Only muted supplied - frozen and moderation_reason must stay at defaults.
	result := applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": true, "reason": "test"})
	data, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := storage.ParseInt(data["is_muted"]); got != 1 {
		t.Fatalf("is_muted=%d, want 1", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_muted FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("stored is_muted=%d, want 1", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_frozen FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("is_frozen=%d, want unchanged 0", got)
	}

	// Now supply frozen + a reason without touching muted - muted must stay 1.
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "frozen": true, "moderation_reason": "spamming OOC", "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT is_muted FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("is_muted=%d, want unchanged 1", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_frozen FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("is_frozen=%d, want 1", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT moderation_reason FROM characters WHERE user_id=42")); got != "spamming OOC" {
		t.Fatalf("moderation_reason=%q, want %q", got, "spamming OOC")
	}

	// Explicitly clearing both flags.
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": false, "frozen": false, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT is_muted FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("is_muted=%d, want cleared to 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_frozen FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("is_frozen=%d, want cleared to 0", got)
	}

	if got := fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != "admin.player.set_moderation" {
		t.Fatalf("action=%q, want admin.player.set_moderation", got)
	}
}

func TestAdminSetModerationRejectsEmptyPayloadAndUnknownCharacter(t *testing.T) {
	path := setupAdminDB(t)
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_moderation", Payload: mustJSON(map[string]any{"user_id": 42, "reason": "test"})}); err == nil {
		t.Fatal("expected error when no moderation field is supplied")
	} else if !strings.Contains(err.Error(), "muted, frozen, or moderation_reason is required") {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_moderation", Payload: mustJSON(map[string]any{"user_id": 999, "muted": true, "reason": "test"})}); err == nil {
		t.Fatal("expected error for unknown character")
	} else if !strings.Contains(err.Error(), "character not found") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestAdminAuditUsesActionActorID(t *testing.T) {
	path := setupAdminDB(t)
	applyAdminAs(t, path, "admin.player.teleport", 777, map[string]any{
		"user_id": 42, "location": "Greenriver Town", "reason": "operator identity",
	})
	if got := storage.ParseInt(scalar(t, path, "SELECT admin_user_id FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != 777 {
		t.Fatalf("admin_user_id=%d, want 777", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1")); got != "admin.player.teleport" {
		t.Fatalf("action=%q, want admin.player.teleport", got)
	}
}

func auditCount(t *testing.T, path string) int64 {
	t.Helper()
	return storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM admin_audit_log"))
}

func TestAdminSetRealmUpdatesAndAuditsAndValidatesBounds(t *testing.T) {
	path := setupAdminDB(t)
	before := auditCount(t, path)
	applyAdmin(t, path, "admin.player.set_realm", map[string]any{"user_id": 42, "realm_index": 7, "phase": 5, "reason": "story correction"})
	if got := storage.ParseInt(scalar(t, path, "SELECT realm_index FROM characters WHERE user_id=42")); got != 7 {
		t.Fatalf("realm_index=%d, want 7", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT phase FROM characters WHERE user_id=42")); got != 5 {
		t.Fatalf("phase=%d, want 5", got)
	}
	if got := auditCount(t, path); got != before+1 {
		t.Fatalf("audit count=%d, want %d", got, before+1)
	}

	raw, _ := json.Marshal(map[string]any{"user_id": 42, "realm_index": 32, "phase": 1})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_realm", Payload: raw}); err == nil {
		t.Fatalf("expected an error for realm_index out of the 0-31 bound")
	}
	raw, _ = json.Marshal(map[string]any{"user_id": 42, "realm_index": 0, "phase": 10})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_realm", Payload: raw}); err == nil {
		t.Fatalf("expected an error for phase out of the 1-9 bound")
	}
}

func TestAdminSetResourceCapsUpdatesOnlySuppliedFieldAndClampsCurrent(t *testing.T) {
	path := setupAdminDB(t)
	// vitality starts at 0/20 - raising the cap must not also refill it.
	applyAdmin(t, path, "admin.player.set_resource_caps", map[string]any{"user_id": 42, "vitality_max": 50, "reason": "GM buff"})
	if got := storage.ParseInt(scalar(t, path, "SELECT vitality_max FROM characters WHERE user_id=42")); got != 50 {
		t.Fatalf("vitality_max=%d, want 50", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT vitality FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("vitality=%d, want 0 (raising a cap must not refill)", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT qi_max FROM characters WHERE user_id=42")); got != 30 {
		t.Fatalf("qi_max=%d, want unchanged 30 (only vitality_max was supplied)", got)
	}

	// qi is currently 0/30; lowering qi_max below current qi is a no-op here
	// (0 <= any cap), so bump qi up first via a currency-adjacent path is
	// unnecessary - instead lower vitality_max below the now-raised 50 and
	// confirm vitality gets clamped down with it.
	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_stone", "amount": 1, "reason": "unrelated"})
	if _, err := storage.Open(path); err != nil {
		t.Fatal(err)
	}
	conn, _ := storage.Open(path)
	if _, err := conn.Execute(`UPDATE characters SET vitality=45 WHERE user_id=42`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	applyAdmin(t, path, "admin.player.set_resource_caps", map[string]any{"user_id": 42, "vitality_max": 20, "reason": "lower cap"})
	if got := storage.ParseInt(scalar(t, path, "SELECT vitality FROM characters WHERE user_id=42")); got != 20 {
		t.Fatalf("vitality=%d, want clamped down to 20", got)
	}
}

func TestAdminAdjustItemGrantsAndFloorsAtZeroOnRemoval(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": 5, "reason": "GM grant"})
	if got := storage.ParseInt(scalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")); got != 5 {
		t.Fatalf("quantity=%d, want 5", got)
	}
	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": -9, "reason": "GM removal"})
	if got := scalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'"); got != nil {
		t.Fatalf("expected the row deleted once quantity floors at 0, got %v", got)
	}
}

func TestAdminNpcRelocateUpdatesLocationAndAudits(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO npc_civilization_state VALUES('Elder Wen','Greenriver Town','Azure Crown Imperial City','Mortal Realm',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	before := auditCount(t, path)
	applyAdmin(t, path, "admin.npc.relocate", map[string]any{"npc_name": "Elder Wen", "location": "Greenriver Town", "reason": "story move"})
	if got := scalar(t, path, "SELECT current_location FROM npc_civilization_state WHERE npc_name='Elder Wen'"); got != "Greenriver Town" {
		t.Fatalf("current_location=%v, want Greenriver Town", got)
	}
	if got := auditCount(t, path); got != before+1 {
		t.Fatalf("audit count=%d, want %d", got, before+1)
	}

	raw, _ := json.Marshal(map[string]any{"npc_name": "Nobody Here", "location": "Greenriver Town"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.npc.relocate", Payload: raw}); err == nil {
		t.Fatalf("expected an error relocating an unknown npc")
	}
}

func TestAdminEndWorldEventDeactivates(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO world_events VALUES('ev1','Bandit Raid','Greenriver Town',1,0,999999999)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	before := auditCount(t, path)
	applyAdmin(t, path, "admin.world_event.end", map[string]any{"event_key": "ev1", "reason": "GM ended early"})
	if got := storage.ParseInt(scalar(t, path, "SELECT active FROM world_events WHERE event_key='ev1'")); got != 0 {
		t.Fatalf("active=%d, want 0", got)
	}
	if got := auditCount(t, path); got != before+1 {
		t.Fatalf("audit count=%d, want %d", got, before+1)
	}

	raw, _ := json.Marshal(map[string]any{"event_key": "no-such-event"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.world_event.end", Payload: raw}); err == nil {
		t.Fatalf("expected an error ending an unknown world event")
	}
}

func TestAdminResetCooldownsClearsAllOrOneNamedAction(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO cooldowns VALUES(42,'explore',999999999)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO cooldowns VALUES(42,'hunt',999999999)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	applyAdmin(t, path, "admin.player.reset_cooldowns", map[string]any{"user_id": 42, "action": "explore", "reason": "unstuck"})
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM cooldowns WHERE user_id=42")); got != 1 {
		t.Fatalf("remaining cooldowns=%d, want 1 (only 'explore' cleared)", got)
	}
	applyAdmin(t, path, "admin.player.reset_cooldowns", map[string]any{"user_id": 42, "reason": "clear all"})
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM cooldowns WHERE user_id=42")); got != 0 {
		t.Fatalf("remaining cooldowns=%d, want 0", got)
	}
}

func TestAdminForceEndSceneClearsSceneWithoutMovingLocation(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO player_scene_state VALUES(42,'Old Place','battle','fight1','A stuck fight',123,'{}',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	applyAdmin(t, path, "admin.player.force_end_scene", map[string]any{"user_id": 42, "reason": "stuck scene"})
	if got := scalar(t, path, "SELECT scene_type FROM player_scene_state WHERE user_id=42"); got != "world" {
		t.Fatalf("scene_type=%v, want world", got)
	}
	if got := scalar(t, path, "SELECT physical_location FROM player_scene_state WHERE user_id=42"); got != "Old Place" {
		t.Fatalf("physical_location=%v, want unchanged Old Place (force_end_scene must not teleport)", got)
	}
	if got := scalar(t, path, "SELECT scene_key FROM player_scene_state WHERE user_id=42"); got != "" {
		t.Fatalf("scene_key=%v, want cleared", got)
	}
	if got := scalar(t, path, "SELECT scene_label FROM player_scene_state WHERE user_id=42"); got != "" {
		t.Fatalf("scene_label=%v, want cleared", got)
	}
	if got := scalar(t, path, "SELECT channel_id FROM player_scene_state WHERE user_id=42"); got != nil {
		t.Fatalf("channel_id=%v, want NULL", got)
	}
	if got := scalar(t, path, "SELECT metadata_json FROM player_scene_state WHERE user_id=42"); got != "{}" {
		t.Fatalf("metadata_json=%v, want reset to {}", got)
	}
}

func TestAdminClearBattleAlsoAbandonsGroupCombatAndPvp(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO boss_encounters VALUES(1,10,'active',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO boss_participants VALUES(1,42,'active',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO pvp_matches VALUES(1,1,42,99,'active',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO pvp_challenges VALUES(2,42,55,'pending',0,999999999)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.clear_battle", map[string]any{"user_id": 42, "reason": "fully stuck"})
	if got := scalar(t, path, "SELECT status FROM battles WHERE battle_id=1"); got != "abandoned" {
		t.Fatalf("battle status=%v, want abandoned", got)
	}
	if got := scalar(t, path, "SELECT status FROM boss_encounters WHERE encounter_id=1"); got != "abandoned" {
		t.Fatalf("boss encounter status=%v, want abandoned", got)
	}
	if got := scalar(t, path, "SELECT status FROM pvp_matches WHERE match_id=1"); got != "abandoned" {
		t.Fatalf("pvp match status=%v, want abandoned", got)
	}
	if got := scalar(t, path, "SELECT status FROM pvp_challenges WHERE challenge_id=2"); got != "cancelled" {
		t.Fatalf("pvp challenge status=%v, want cancelled", got)
	}
}

func TestAdminBulkGrantCurrencyAppliesToEveryCharacterInOneAuditEntry(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO characters VALUES(43,'Second Test','alive','Old Place',0,10,10,10,10,0,0,1,NULL,NULL,0,0,0,'')`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	before := auditCount(t, path)
	result := applyAdmin(t, path, "admin.bulk.grant_currency", map[string]any{"currency_id": "low_spirit_stone", "amount": 25, "reason": "server event reward"})
	data, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := storage.ParseInt(data["characters"]); got != 2 {
		t.Fatalf("characters=%d, want 2", got)
	}
	// Grants are additive (matching admin.player.grant_currency's semantics), so
	// each character's final balance is their seeded starting balance plus the
	// grant: user 42 seeds spirit_stones=10 -> 35, user 43 seeds 0 -> 25.
	wantBalances := map[int64]int64{42: 35, 43: 25}
	for uid, want := range wantBalances {
		if got := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=?", uid)); got != want {
			t.Fatalf("user %d spirit_stones=%d, want %d", uid, got, want)
		}
	}
	if got := auditCount(t, path); got != before+1 {
		t.Fatalf("audit count=%d, want %d (one entry for the whole bulk action)", got, before+1)
	}
}

func TestAdminBulkResetCooldownsClearsEveryone(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO cooldowns VALUES(42,'explore',999999999)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO cooldowns VALUES(99,'hunt',999999999)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	applyAdmin(t, path, "admin.bulk.reset_cooldowns", map[string]any{"reason": "season reset"})
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM cooldowns")); got != 0 {
		t.Fatalf("remaining cooldowns=%d, want 0", got)
	}
}

func TestAdminSetSectAssignsChangesRankRemovesAndAudits(t *testing.T) {
	path := setupAdminDB(t)
	before := auditCount(t, path)
	applyAdmin(t, path, "admin.player.set_sect", map[string]any{"user_id": 42, "sect_name": "Azure Cloud Sect", "rank_name": "Outer Disciple", "rank_level": 10, "reason": "story placement"})
	if got := fmt.Sprint(scalar(t, path, "SELECT sect_name FROM sect_membership WHERE user_id=42")); got != "Azure Cloud Sect" {
		t.Fatalf("sect_name=%q, want Azure Cloud Sect", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM sects WHERE sect_name='Azure Cloud Sect'")); got != 1 {
		t.Fatalf("sects row auto-vivify count=%d, want 1", got)
	}
	joinedAt := scalar(t, path, "SELECT joined_at FROM sect_membership WHERE user_id=42")

	applyAdmin(t, path, "admin.player.set_sect", map[string]any{"user_id": 42, "sect_name": "Azure Cloud Sect", "rank_name": "Elder", "rank_level": 55, "reason": "promotion"})
	if got := fmt.Sprint(scalar(t, path, "SELECT rank_name FROM sect_membership WHERE user_id=42")); got != "Elder" {
		t.Fatalf("rank_name=%q, want Elder after promotion", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT rank_level FROM sect_membership WHERE user_id=42")); got != 55 {
		t.Fatalf("rank_level=%d, want 55 after promotion", got)
	}
	if got := scalar(t, path, "SELECT joined_at FROM sect_membership WHERE user_id=42"); got != joinedAt {
		t.Fatalf("joined_at=%v, want unchanged %v (rank change must not restamp it)", got, joinedAt)
	}

	applyAdmin(t, path, "admin.player.set_sect", map[string]any{"user_id": 42, "remove": true, "reason": "left the sect"})
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM sect_membership WHERE user_id=42")); got != 0 {
		t.Fatalf("membership rows=%d, want 0 after removal", got)
	}

	raw, _ := json.Marshal(map[string]any{"user_id": 42, "remove": true})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_sect", Payload: raw}); err == nil {
		t.Fatalf("expected an error removing a membership that no longer exists")
	}
	if got := auditCount(t, path); got != before+3 {
		t.Fatalf("audit count=%d, want %d (assign, promote, remove)", got, before+3)
	}
}

func TestAdminSetRealmPerfectionUpsertsClampsAndValidatesTrack(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 5, "progress": 42, "reason": "story correction"})
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM realm_perfection WHERE user_id=42 AND realm_index=5")); got != 42 {
		t.Fatalf("cultivation progress=%d, want 42", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM body_realm_perfection WHERE user_id=42")); got != 0 {
		t.Fatalf("body_realm_perfection rows=%d, want 0 (track=cultivation must not touch it)", got)
	}

	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 5, "progress": 150, "reason": "over-clamp check"})
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM realm_perfection WHERE user_id=42 AND realm_index=5")); got != 100 {
		t.Fatalf("progress=%d, want clamped to 100", got)
	}

	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "body", "realm_index": 5, "progress": 30, "reason": "body track"})
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM body_realm_perfection WHERE user_id=42 AND realm_index=5")); got != 30 {
		t.Fatalf("body progress=%d, want 30", got)
	}

	raw, _ := json.Marshal(map[string]any{"user_id": 42, "track": "mind", "realm_index": 5, "progress": 1})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_realm_perfection", Payload: raw}); err == nil {
		t.Fatalf("expected an error for an invalid track")
	}
	raw, _ = json.Marshal(map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 32, "progress": 1})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_realm_perfection", Payload: raw}); err == nil {
		t.Fatalf("expected an error for realm_index out of the 0-31 bound")
	}
}

func TestAdminSetSpiritualRootUpsertsValidatesGradeAndLeavesOtherFieldsAlone(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_spiritual_root", map[string]any{"user_id": 42, "grade": "Heaven", "purity": 80, "mutation": "Phoenix Blood", "reason": "story reward"})
	if got := fmt.Sprint(scalar(t, path, "SELECT grade FROM character_spiritual_roots WHERE user_id=42")); got != "Heaven" {
		t.Fatalf("grade=%q, want Heaven", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT stability FROM character_spiritual_roots WHERE user_id=42")); got != 100 {
		t.Fatalf("stability=%d, want table default 100 (upsert must not touch it)", got)
	}

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`UPDATE character_spiritual_roots SET stability=77 WHERE user_id=42`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_spiritual_root", map[string]any{"user_id": 42, "grade": "Immortal", "purity": 150, "mutation": "", "reason": "further story reward"})
	if got := fmt.Sprint(scalar(t, path, "SELECT grade FROM character_spiritual_roots WHERE user_id=42")); got != "Immortal" {
		t.Fatalf("grade=%q, want Immortal", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT purity FROM character_spiritual_roots WHERE user_id=42")); got != 100 {
		t.Fatalf("purity=%d, want clamped to 100", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT stability FROM character_spiritual_roots WHERE user_id=42")); got != 77 {
		t.Fatalf("stability=%d, want preserved 77 (UPDATE must never touch it)", got)
	}

	raw, _ := json.Marshal(map[string]any{"user_id": 42, "grade": "Divine", "purity": 1})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_spiritual_root", Payload: raw}); err == nil {
		t.Fatalf("expected an error for an invalid grade")
	}
}

func TestAdminSetBloodlineRequiresExistingRowClampsAndFloorsEvolution(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO character_bloodlines(user_id,bloodline_id,name,affinity,state,updated_at) VALUES(42,'azure_dragon_blood','Azure Dragon Blood','Water','dormant',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_bloodline", map[string]any{"user_id": 42, "bloodline_id": "azure_dragon_blood", "purity": 150, "evolution_stage": -3, "progress": 60, "reason": "story correction"})
	if got := storage.ParseInt(scalar(t, path, "SELECT purity FROM character_bloodlines WHERE user_id=42 AND bloodline_id='azure_dragon_blood'")); got != 100 {
		t.Fatalf("purity=%d, want clamped to 100", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT evolution_stage FROM character_bloodlines WHERE user_id=42 AND bloodline_id='azure_dragon_blood'")); got != 0 {
		t.Fatalf("evolution_stage=%d, want floored to 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM character_bloodlines WHERE user_id=42 AND bloodline_id='azure_dragon_blood'")); got != 60 {
		t.Fatalf("progress=%d, want 60", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT affinity FROM character_bloodlines WHERE user_id=42 AND bloodline_id='azure_dragon_blood'")); got != "Water" {
		t.Fatalf("affinity=%q, want unchanged Water", got)
	}

	raw, _ := json.Marshal(map[string]any{"user_id": 42, "bloodline_id": "no_such_bloodline", "purity": 1, "evolution_stage": 0, "progress": 1})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_bloodline", Payload: raw}); err == nil {
		t.Fatalf("expected an error for a bloodline_id the character doesn't have")
	}
}

func TestAdminSetPhysiqueRequiresExistingRowAndClamps(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO character_physiques(user_id,physique_id,name,state,updated_at) VALUES(42,'ordinary_mortal_body','Ordinary Mortal Body','ordinary',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_physique", map[string]any{"user_id": 42, "evolution_stage": -3, "progress": 150, "stability": -5, "reason": "story correction"})
	if got := storage.ParseInt(scalar(t, path, "SELECT evolution_stage FROM character_physiques WHERE user_id=42")); got != 0 {
		t.Fatalf("evolution_stage=%d, want floored to 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM character_physiques WHERE user_id=42")); got != 100 {
		t.Fatalf("progress=%d, want clamped to 100", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT stability FROM character_physiques WHERE user_id=42")); got != 0 {
		t.Fatalf("stability=%d, want floored to 0", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT physique_id FROM character_physiques WHERE user_id=42")); got != "ordinary_mortal_body" {
		t.Fatalf("physique_id=%q, want unchanged", got)
	}

	conn, err = storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`DELETE FROM character_physiques WHERE user_id=42`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	raw, _ := json.Marshal(map[string]any{"user_id": 42, "evolution_stage": 0, "progress": 0, "stability": 0})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_physique", Payload: raw}); err == nil {
		t.Fatalf("expected an error when the physique row is missing")
	}
}

func TestAdminSetTribulationClearsAndResets(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_tribulation", map[string]any{"user_id": 42, "gate_realm_index": 7, "mode": "clear", "reason": "unstick the player"})
	if got := storage.ParseInt(scalar(t, path, "SELECT cleared FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != 1 {
		t.Fatalf("cleared=%d, want 1", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT last_result FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != "cleared" {
		t.Fatalf("last_result=%q, want cleared", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT attempts FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != 0 {
		t.Fatalf("attempts=%d, want untouched at 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT updated_game_minute FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != 480 {
		t.Fatalf("updated_game_minute=%d, want 480 (fresh canonical world clock default)", got)
	}

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`UPDATE tribulation_state SET attempts=3 WHERE user_id=42 AND gate_realm_index=7`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_tribulation", map[string]any{"user_id": 42, "gate_realm_index": 7, "mode": "reset", "reason": "let them re-attempt clean"})
	if got := storage.ParseInt(scalar(t, path, "SELECT cleared FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != 0 {
		t.Fatalf("cleared=%d, want 0 after reset", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT attempts FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != 0 {
		t.Fatalf("attempts=%d, want 0 after reset", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT last_result FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != "" {
		t.Fatalf("last_result=%q, want cleared to empty", got)
	}

	raw, _ := json.Marshal(map[string]any{"user_id": 42, "gate_realm_index": 99, "mode": "clear"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_tribulation", Payload: raw}); err == nil {
		t.Fatalf("expected an error for a gate_realm_index outside {7,15,23}")
	}
	raw, _ = json.Marshal(map[string]any{"user_id": 42, "gate_realm_index": 7, "mode": "pause"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.set_tribulation", Payload: raw}); err == nil {
		t.Fatalf("expected an error for an invalid mode")
	}
}

func seedCondition(t *testing.T, path string, conditionID int64, key, name string, severity int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO character_conditions(condition_id,user_id,condition_key,category,name,severity,state,created_at,updated_at) VALUES(?,42,?,'Injury',?,?,'active',0,0)`,
		[]any{conditionID, key, name, severity}); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
}

func TestAdminClearConditionResolvesOneRowAndAudits(t *testing.T) {
	path := setupAdminDB(t)
	seedCondition(t, path, 1, "flesh_wound", "Flesh Wound", 2)
	seedCondition(t, path, 2, "qi_deviation", "Qi Deviation", 4)
	before := auditCount(t, path)

	applyAdmin(t, path, "admin.player.clear_condition", map[string]any{"user_id": 42, "condition_id": 1, "reason": "story resolution"})
	if got := fmt.Sprint(scalar(t, path, "SELECT state FROM character_conditions WHERE condition_id=1")); got != "resolved" {
		t.Fatalf("condition 1 state=%q, want resolved", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT severity FROM character_conditions WHERE condition_id=1")); got != 0 {
		t.Fatalf("condition 1 severity=%d, want 0", got)
	}
	if scalar(t, path, "SELECT resolved_game_minute FROM character_conditions WHERE condition_id=1") == nil {
		t.Fatalf("condition 1 resolved_game_minute is nil, want stamped")
	}
	// The other active condition must be untouched by clearing a specific one.
	if got := fmt.Sprint(scalar(t, path, "SELECT state FROM character_conditions WHERE condition_id=2")); got != "active" {
		t.Fatalf("condition 2 state=%q, want still active", got)
	}
	if got := auditCount(t, path); got != before+1 {
		t.Fatalf("audit rows=%d, want %d", got, before+1)
	}

	// Clearing an already-resolved condition, an unknown condition_id, and a
	// condition_id belonging to a different character must all error.
	raw, _ := json.Marshal(map[string]any{"user_id": 42, "condition_id": 1})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.clear_condition", Payload: raw}); err == nil {
		t.Fatalf("expected an error clearing an already-resolved condition")
	}
	raw, _ = json.Marshal(map[string]any{"user_id": 42, "condition_id": 9999})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.clear_condition", Payload: raw}); err == nil {
		t.Fatalf("expected an error for a nonexistent condition_id")
	}
	raw, _ = json.Marshal(map[string]any{"user_id": 43, "condition_id": 2})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.clear_condition", Payload: raw}); err == nil {
		t.Fatalf("expected an error for a condition_id belonging to a different character")
	}
}

func TestAdminClearConditionClearAllResolvesEveryActiveRowInOneAuditEntry(t *testing.T) {
	path := setupAdminDB(t)
	seedCondition(t, path, 1, "flesh_wound", "Flesh Wound", 2)
	seedCondition(t, path, 2, "qi_deviation", "Qi Deviation", 4)
	seedCondition(t, path, 3, "poisoned", "Poisoned", 3)
	before := auditCount(t, path)

	result := applyAdmin(t, path, "admin.player.clear_condition", map[string]any{"user_id": 42, "clear_all": true, "reason": "clean slate"})
	m, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := storage.ParseInt(m["cleared_count"]); got != 3 {
		t.Fatalf("cleared_count=%d, want 3", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM character_conditions WHERE user_id=42 AND state='active'")); got != 0 {
		t.Fatalf("active conditions remaining=%d, want 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT COUNT(*) FROM character_conditions WHERE user_id=42 AND state='resolved'")); got != 3 {
		t.Fatalf("resolved conditions=%d, want 3", got)
	}
	// One audit entry for the whole clear-all, not one per row.
	if got := auditCount(t, path); got != before+1 {
		t.Fatalf("audit rows=%d, want %d (one entry for the whole clear-all)", got, before+1)
	}

	raw, _ := json.Marshal(map[string]any{"user_id": 42, "clear_all": true})
	if _, err := Apply(path, ActionRequest{Operation: "admin.player.clear_condition", Payload: raw}); err == nil {
		t.Fatalf("expected an error clearing all when nothing is active")
	}
}
