package game

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func batch4WorldPath(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	path := filepath.Clean(filepath.Join(filepath.Dir(file), "../../../content/world.json"))
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("world catalog: %v", err)
	}
	return path
}

func batch4WorldWithForageAptitudeBonus(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	rootSystem, ok := payload["spiritual_root_system"].(map[string]any)
	if !ok {
		t.Fatal("spiritual_root_system missing")
	}
	mutations, ok := rootSystem["mutations"].(map[string]any)
	if !ok {
		t.Fatal("spiritual_root_system.mutations missing")
	}
	mutations["stage2_forager"] = map[string]any{
		"name":          "Stage 2 Forager Root",
		"requires_any":  []string{"Fire"},
		"favored_paths": []string{"Sword Cultivator"},
		"modifiers": []map[string]any{
			{"stat": "alchemy_bonus", "operation": "add", "value": 5},
		},
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "world_stage2.json")
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func setupBatch4AuthorityDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "batch4_authority.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	schema := `
CREATE TABLE characters(
    user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    gender TEXT NOT NULL DEFAULT 'neutral',
    path TEXT NOT NULL,
    spiritual_root TEXT NOT NULL,
    location TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    realm_index INTEGER NOT NULL DEFAULT 0,
    phase INTEGER NOT NULL DEFAULT 1,
    cultivation INTEGER NOT NULL DEFAULT 0,
    body_realm_index INTEGER NOT NULL DEFAULT 0,
    body_phase INTEGER NOT NULL DEFAULT 1,
    body_cultivation INTEGER NOT NULL DEFAULT 0,
    life_status TEXT NOT NULL DEFAULT 'alive',
    karma_score INTEGER NOT NULL DEFAULT 0,
    sense_power_bonus INTEGER NOT NULL DEFAULT 0,
    sense_precision_bonus INTEGER NOT NULL DEFAULT 0,
    sense_range_bonus INTEGER NOT NULL DEFAULT 0,
    concealment_bonus INTEGER NOT NULL DEFAULT 0,
    concealment_active INTEGER NOT NULL DEFAULT 0,
    qi INTEGER NOT NULL DEFAULT 100,
    qi_max INTEGER NOT NULL DEFAULT 100,
    vitality INTEGER NOT NULL DEFAULT 100,
    vitality_max INTEGER NOT NULL DEFAULT 100,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE cooldowns(user_id INTEGER NOT NULL,action TEXT NOT NULL,available_at REAL NOT NULL,PRIMARY KEY(user_id,action));
CREATE TABLE world_state(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE realm_perfection(user_id INTEGER NOT NULL,realm_index INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,training_progress INTEGER NOT NULL DEFAULT 0,quest_index INTEGER NOT NULL DEFAULT 0,quest_preparation INTEGER NOT NULL DEFAULT 0,completed_quests INTEGER NOT NULL DEFAULT 0,discovered_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL,PRIMARY KEY(user_id,realm_index));
CREATE TABLE body_realm_perfection(user_id INTEGER NOT NULL,realm_index INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,training_progress INTEGER NOT NULL DEFAULT 0,quest_index INTEGER NOT NULL DEFAULT 0,quest_preparation INTEGER NOT NULL DEFAULT 0,completed_quests INTEGER NOT NULL DEFAULT 0,discovered_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL,PRIMARY KEY(user_id,realm_index));
CREATE TABLE active_effects(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,effect_key TEXT NOT NULL,name TEXT NOT NULL,source_type TEXT NOT NULL,source_id TEXT NOT NULL,effect_json TEXT NOT NULL,stacks INTEGER NOT NULL DEFAULT 1,starts_game_minute INTEGER NOT NULL,ends_game_minute INTEGER,created_at REAL NOT NULL,UNIQUE(user_id,effect_key,source_type,source_id));
CREATE TABLE deployed_location_arrays(location TEXT PRIMARY KEY,item_id TEXT NOT NULL DEFAULT '',name TEXT NOT NULL DEFAULT '',owner_user_id INTEGER,sect_name TEXT NOT NULL DEFAULT '',effect_json TEXT NOT NULL DEFAULT '{}',starts_game_minute INTEGER NOT NULL,ends_game_minute INTEGER NOT NULL,created_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE character_spiritual_roots(user_id INTEGER PRIMARY KEY,grade TEXT NOT NULL DEFAULT 'Common',purity INTEGER NOT NULL DEFAULT 50,elements_json TEXT NOT NULL DEFAULT '[]',mutation TEXT NOT NULL DEFAULT '',stability INTEGER NOT NULL DEFAULT 100,refinement_progress INTEGER NOT NULL DEFAULT 0,compatibility INTEGER NOT NULL DEFAULT 50,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE character_bloodlines(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,bloodline_id TEXT NOT NULL,name TEXT NOT NULL,affinity TEXT NOT NULL DEFAULT 'None',purity INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'dormant',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,rejection INTEGER NOT NULL DEFAULT 0,mutation TEXT NOT NULL DEFAULT '',primary_lineage INTEGER NOT NULL DEFAULT 0,unlocked_techniques_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE character_physiques(user_id INTEGER PRIMARY KEY,physique_id TEXT NOT NULL DEFAULT 'ordinary_mortal_body',name TEXT NOT NULL DEFAULT 'Ordinary Mortal Body',state TEXT NOT NULL DEFAULT 'ordinary',evolution_stage INTEGER NOT NULL DEFAULT 0,progress INTEGER NOT NULL DEFAULT 0,stability INTEGER NOT NULL DEFAULT 100,instability INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE soul_legacy(user_id INTEGER PRIMARY KEY,incarnation_count INTEGER NOT NULL DEFAULT 1,legacy_points INTEGER NOT NULL DEFAULT 0,memory_seed INTEGER NOT NULL DEFAULT 0,talent_echo INTEGER NOT NULL DEFAULT 0,law_echo INTEGER NOT NULL DEFAULT 0,insight_echo INTEGER NOT NULL DEFAULT 0,karmic_fortune INTEGER NOT NULL DEFAULT 0,special_trait TEXT NOT NULL DEFAULT '',awakened_memory INTEGER NOT NULL DEFAULT 0,past_lives_json TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE law_progress(user_id INTEGER NOT NULL,law_id TEXT NOT NULL,comprehension INTEGER NOT NULL DEFAULT 0,insights INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(user_id,law_id));
CREATE TABLE dao_progress(user_id INTEGER NOT NULL,dao_id TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(user_id,dao_id));
CREATE TABLE inventory(user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,item_id));
CREATE TABLE profession_progress(user_id INTEGER NOT NULL,profession TEXT NOT NULL,level INTEGER NOT NULL DEFAULT 0,xp INTEGER NOT NULL DEFAULT 0,successes INTEGER NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,quality_points INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0,PRIMARY KEY(user_id,profession));
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY,world_name TEXT NOT NULL,spirit_resources INTEGER NOT NULL DEFAULT 50);
CREATE TABLE cave_abodes(user_id INTEGER PRIMARY KEY,location_key TEXT NOT NULL UNIQUE,base_location TEXT NOT NULL,herb_garden_level INTEGER NOT NULL DEFAULT 0);
CREATE TABLE cave_abode_access(owner_user_id INTEGER NOT NULL,guest_user_id INTEGER NOT NULL,PRIMARY KEY(owner_user_id,guest_user_id));
CREATE TABLE birth_families(family_id INTEGER PRIMARY KEY AUTOINCREMENT,archetype TEXT NOT NULL);
CREATE TABLE character_birth_family(user_id INTEGER PRIMARY KEY,family_id INTEGER NOT NULL);
CREATE TABLE character_conditions(condition_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,condition_key TEXT NOT NULL,category TEXT NOT NULL,name TEXT NOT NULL,severity INTEGER NOT NULL DEFAULT 1,state TEXT NOT NULL DEFAULT 'active',source_type TEXT NOT NULL DEFAULT 'system',source_id TEXT NOT NULL DEFAULT '',effect_json TEXT NOT NULL DEFAULT '{}',created_game_minute INTEGER NOT NULL DEFAULT 0,updated_game_minute INTEGER NOT NULL DEFAULT 0,resolved_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE UNIQUE INDEX idx_conditions_active_key ON character_conditions(user_id,condition_key) WHERE state='active';
CREATE TABLE currency_wallets(user_id INTEGER NOT NULL,currency_id TEXT NOT NULL,balance INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,currency_id));
CREATE TABLE tribulation_state(user_id INTEGER NOT NULL,gate_realm_index INTEGER NOT NULL,preparation INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,cleared INTEGER NOT NULL DEFAULT 0,last_result TEXT NOT NULL DEFAULT '',updated_game_minute INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(user_id,gate_realm_index));
CREATE TABLE tribulation_attempts(attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,gate_realm_index INTEGER NOT NULL,preparation_used INTEGER NOT NULL DEFAULT 0,waves_json TEXT NOT NULL DEFAULT '[]',success INTEGER NOT NULL DEFAULT 0,created_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
CREATE TABLE faction_reputation(user_id INTEGER NOT NULL,faction_key TEXT NOT NULL,score INTEGER NOT NULL DEFAULT 0,last_reason TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL,PRIMARY KEY(user_id,faction_key));
CREATE TABLE character_fate(user_id INTEGER PRIMARY KEY,points INTEGER NOT NULL DEFAULT 0,lifetime_earned INTEGER NOT NULL DEFAULT 0,lifetime_spent INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
CREATE TABLE fate_ledger(entry_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,delta INTEGER NOT NULL,balance_after INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
CREATE TABLE authoritative_actor_versions(actor_id INTEGER PRIMARY KEY,state_version INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
CREATE TABLE authoritative_action_receipts(action_id TEXT PRIMARY KEY,actor_id INTEGER NOT NULL,operation TEXT NOT NULL,state_version INTEGER NOT NULL,result_json TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE authoritative_entity_versions(domain TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,state_version INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(domain,entity_type,entity_id));
CREATE TABLE domain_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,event_uid TEXT NOT NULL UNIQUE,domain TEXT NOT NULL,event_type TEXT NOT NULL,actor_id INTEGER,entity_type TEXT NOT NULL DEFAULT '',entity_id TEXT NOT NULL DEFAULT '',subject_type TEXT NOT NULL DEFAULT '',subject_id TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,state_version INTEGER NOT NULL DEFAULT 0,payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);

INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,life_status,karma_score,qi,qi_max,vitality,vitality_max)
VALUES(42,'Lin Test','neutral','Sword Cultivator','Fire Root','Greenriver Town','{"body":100,"agility":100,"spirit":100,"insight":100,"will":100,"presence":100}',0,9,100000,0,9,100000,'alive',50,100,100,100,100);
INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,life_status,karma_score,qi,qi_max,vitality,vitality_max,concealment_active)
VALUES(43,'Target Test','neutral','Rogue Cultivator','Water Root','Greenriver Town','{"body":10,"agility":10,"spirit":10,"insight":10,"will":10,"presence":10}',1,3,0,0,1,0,'alive',0,20,20,20,20,1);
INSERT INTO character_spiritual_roots(user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility) VALUES(42,'Heavenly',90,'["Fire"]','',100,0,90);
INSERT INTO character_spiritual_roots(user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility) VALUES(43,'Common',50,'["Water"]','',100,0,50);
INSERT INTO character_physiques(user_id) VALUES(42);
INSERT INTO character_physiques(user_id) VALUES(43);
INSERT INTO soul_legacy(user_id,memory_seed,awakened_memory,law_echo,special_trait) VALUES(42,10,0,50,'Dao Memory');
INSERT INTO character_fate(user_id,points,lifetime_earned,lifetime_spent,updated_at) VALUES(42,1,1,0,0);
`
	if err := conn.ExecScript(schema); err != nil {
		t.Fatal(err)
	}
	return path
}

func batch4Exec(t *testing.T, path, sql string, args ...any) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(sql, args); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func setupForageEffectAuthorityTables(t *testing.T, path string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
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
`); err != nil {
		t.Fatal(err)
	}
}

func batch4SetCanonicalGameMinute(t *testing.T, path string, gameMinute int64) {
	t.Helper()
	state := fmt.Sprintf(`{"anchor_game_minute":%d,"anchor_real_ts":1,"scale":0}`, gameMinute)
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,0)
		ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, state)
}

func clonePayloadWithoutGameMinute(payload map[string]any) map[string]any {
	out := make(map[string]any, len(payload))
	for key, value := range payload {
		if key != "game_minute" {
			out[key] = value
		}
	}
	return out
}

func batch4Apply(t *testing.T, path, world, op string, seq int, payload map[string]any) ActionResponse {
	t.Helper()
	if rawMinute, ok := payload["game_minute"]; ok {
		batch4SetCanonicalGameMinute(t, path, storage.ParseInt(rawMinute))
		payload = clonePayloadWithoutGameMinute(payload)
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   fmt.Sprintf("batch4-%s-%d", op, seq),
		Operation:  op,
		ActorID:    42,
		Payload:    raw,
	})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	if out.Operation != op || out.StateVersion <= 0 {
		t.Fatalf("%s invalid response: %#v", op, out)
	}
	return out
}

func batch4Result(t *testing.T, out ActionResponse) map[string]any {
	t.Helper()
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("result type %T", out.Result)
	}
	return result
}

func TestBatch4PerfectionOperationsAreAuthoritative(t *testing.T) {
	world := batch4WorldPath(t)
	cases := []struct {
		name      string
		startOp   string
		questOp   string
		trialOp   string
		abandonOp string
		table     string
		maxColumn string
	}{
		{"qi", "perfection.start", "perfection.quest", "perfection.trial", "perfection.abandon", "realm_perfection", "qi_max"},
		{"body", "perfection.body_start", "perfection.body_quest", "perfection.body_trial", "perfection.body_abandon", "body_realm_perfection", "vitality_max"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := setupBatch4AuthorityDB(t)
			start := batch4Result(t, batch4Apply(t, path, world, tc.startOp, 1, map[string]any{"game_minute": 100}))
			if started, _ := start["started"].(bool); !started {
				t.Fatalf("start=%v", start)
			}
			for i := 0; i < 4; i++ {
				prep := batch4Result(t, batch4Apply(t, path, world, tc.questOp, 10+i, map[string]any{"mode": "prepare", "game_minute": 101 + i, "quest_cooldown_seconds": 0}))
				if prep["mode"] != "prepare" {
					t.Fatalf("prepare=%v", prep)
				}
			}
			attempt := batch4Result(t, batch4Apply(t, path, world, tc.questOp, 20, map[string]any{"mode": "attempt", "game_minute": 110, "quest_cooldown_seconds": 0}))
			if success, _ := attempt["success"].(bool); !success {
				t.Fatalf("high-stat quest should succeed: %v", attempt)
			}
			if got := storage.ParseInt(actionScalar(t, path, "SELECT completed_quests FROM "+tc.table+" WHERE user_id=42 AND realm_index=0")); got != 1 {
				t.Fatalf("completed quests=%d", got)
			}

			batch4Exec(t, path, "UPDATE "+tc.table+" SET active=1,completed=0,progress=100,training_progress=20,quest_index=7,quest_preparation=0,completed_quests=7 WHERE user_id=42 AND realm_index=0")
			before := storage.ParseInt(actionScalar(t, path, "SELECT "+tc.maxColumn+" FROM characters WHERE user_id=42"))
			trial := batch4Result(t, batch4Apply(t, path, world, tc.trialOp, 30, map[string]any{"game_minute": 120, "trial_cooldown_seconds": 0}))
			if success, _ := trial["success"].(bool); !success {
				t.Fatalf("high-stat trial should succeed: %v", trial)
			}
			if got := storage.ParseInt(actionScalar(t, path, "SELECT completed FROM "+tc.table+" WHERE user_id=42 AND realm_index=0")); got != 1 {
				t.Fatalf("completed=%d", got)
			}
			after := storage.ParseInt(actionScalar(t, path, "SELECT "+tc.maxColumn+" FROM characters WHERE user_id=42"))
			if after <= before {
				t.Fatalf("%s did not increase: before=%d after=%d", tc.maxColumn, before, after)
			}

			batch4Exec(t, path, "UPDATE "+tc.table+" SET active=1,completed=0 WHERE user_id=42 AND realm_index=0")
			abandon := batch4Result(t, batch4Apply(t, path, world, tc.abandonOp, 40, map[string]any{"game_minute": 130}))
			if abandoned, _ := abandon["abandoned"].(bool); !abandoned {
				t.Fatalf("abandon=%v", abandon)
			}
		})
	}
}

func TestBatch4LawComprehendPersistsLawDaoCooldownAndReceipt(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "UPDATE characters SET realm_index=7,phase=3 WHERE user_id=42")

	result := batch4Result(t, batch4Apply(t, path, world, "law.comprehend", 1, map[string]any{"law": "fire", "game_minute": 200}))
	if result["law"] != "fire" || storage.ParseInt(result["gain"]) < 1 {
		t.Fatalf("result=%v", result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT comprehension FROM law_progress WHERE user_id=42 AND law_id='fire'")); got < 1 {
		t.Fatalf("law comprehension=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT progress FROM dao_progress WHERE user_id=42 AND dao_id='Great Dao of Fire'")); got < 1 {
		t.Fatalf("dao progress=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM cooldowns WHERE user_id=42 AND action='law:fire'")); got != 1 {
		t.Fatalf("cooldown rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='law.comprehend'")); got != 1 {
		t.Fatalf("receipt rows=%d", got)
	}
}

func TestBatch4ConditionTreatConsumesMedicineAndResolvesCondition(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',1)")
	batch4Exec(t, path, "INSERT INTO character_conditions(user_id,condition_key,category,name,severity,state,source_type,source_id,effect_json,created_game_minute,updated_game_minute,created_at,updated_at) VALUES(42,'flesh_wound','Injury','Flesh Wound',2,'active','test','seed','{}',0,0,0,0)")
	batch4Exec(t, path, "INSERT INTO active_effects(user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at) VALUES(42,'condition:flesh_wound','Flesh Wound','condition','flesh_wound','{}',1,0,NULL,0)")

	result := batch4Result(t, batch4Apply(t, path, world, "condition.treat", 1, map[string]any{"condition": "flesh_wound", "game_minute": 300}))
	if success, _ := result["success"].(bool); !success {
		t.Fatalf("high-stat treatment should succeed: %v", result)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'")); got != 0 {
		t.Fatalf("medicine quantity=%d", got)
	}
	if got := actionScalar(t, path, "SELECT state FROM character_conditions WHERE user_id=42 AND condition_key='flesh_wound'"); got != "resolved" {
		t.Fatalf("condition state=%v", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM active_effects WHERE user_id=42 AND source_type='condition' AND source_id='flesh_wound'")); got != 0 {
		t.Fatalf("active condition effects=%d", got)
	}
}

func TestBatch4SenseInspectConcealAndStatusUseGoAuthority(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)

	inspect := batch4Result(t, batch4Apply(t, path, world, "sense.inspect", 1, map[string]any{"mode": "player", "target_user_id": 43, "game_minute": 400}))
	if inspect["mode"] != "player" || storage.ParseInt(inspect["target_realm_index"]) != 1 {
		t.Fatalf("inspect=%v", inspect)
	}
	conceal := batch4Result(t, batch4Apply(t, path, world, "sense.conceal", 2, map[string]any{"active": true, "game_minute": 401}))
	if active, _ := conceal["active"].(bool); !active {
		t.Fatalf("conceal=%v", conceal)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT concealment_active FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("concealment_active=%d", got)
	}

	batch4SetCanonicalGameMinute(t, path, 402)
	raw, _ := json.Marshal(map[string]any{})
	status, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: "sense.status", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	statusResult := batch4Result(t, status)
	if active, _ := statusResult["concealment_active"].(bool); !active {
		t.Fatalf("status=%v", statusResult)
	}
	if storage.ParseInt(statusResult["power"]) <= 0 || storage.ParseInt(statusResult["range_m"]) <= 0 {
		t.Fatalf("status=%v", statusResult)
	}
}

func TestBatch4TribulationPrepareAndAttemptPersistCanonicalOutcome(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "UPDATE characters SET realm_index=7,phase=9,cultivation=20000,body_realm_index=0,body_phase=1,body_cultivation=0 WHERE user_id=42")
	batch4Exec(t, path, "INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',10)")

	prepare := batch4Result(t, batch4Apply(t, path, world, "tribulation.prepare", 1, map[string]any{"game_minute": 500}))
	if storage.ParseInt(prepare["preparation"]) != 1 || storage.ParseInt(prepare["balance"]) != 5 {
		t.Fatalf("prepare=%v", prepare)
	}
	attempt := batch4Result(t, batch4Apply(t, path, world, "tribulation.attempt", 2, map[string]any{"game_minute": 501}))
	if success, _ := attempt["success"].(bool); !success {
		t.Fatalf("high-stat tribulation should succeed: %v", attempt)
	}
	if storage.ParseInt(attempt["successes"]) != 3 {
		t.Fatalf("attempt=%v", attempt)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT cleared FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != 1 {
		t.Fatalf("cleared=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM tribulation_attempts WHERE user_id=42 AND gate_realm_index=7")); got != 1 {
		t.Fatalf("attempt rows=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key='Heavenly Recognition'")); got != 8 {
		t.Fatalf("reputation=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT points FROM character_fate WHERE user_id=42")); got != 3 {
		t.Fatalf("fate=%d", got)
	}
}

func TestBatch4AuthorityOperationNamesHaveNativeCoverage(t *testing.T) {
	ops := []string{
		"perfection.start", "perfection.quest", "perfection.trial", "perfection.abandon",
		"perfection.body_start", "perfection.body_quest", "perfection.body_trial", "perfection.body_abandon",
		"law.comprehend", "condition.treat", "sense.inspect", "sense.conceal", "sense.status",
		"tribulation.prepare", "tribulation.attempt",
	}
	for _, op := range ops {
		if !isAuthoritativeOperation(op) {
			t.Errorf("%s is no longer registered as authoritative", op)
		}
	}
}

func TestForageResolveOwnsRegionalProfileRareLootAndRNG(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Greenriver Town','Mortal World',90)")

	forged, _ := json.Marshal(map[string]any{
		"loot": map[string]int64{"jade_life_herb": 999},
	})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "forage-forged", Operation: "forage.resolve", ActorID: 42, Payload: forged}); err == nil || !strings.Contains(err.Error(), "client-supplied loot is forbidden") {
		t.Fatalf("forged forage err=%v", err)
	}

	for _, field := range []string{"context_bonus", "effect_bonus", "location", "realm_index", "garden_level", "cooldown_seconds"} {
		payload, _ := json.Marshal(map[string]any{field: 1})
		_, err := ApplyWithWorld(path, world, ActionRequest{
			APIVersion: authoritativeAPIVersion,
			ActionID:   "forage-forbidden-" + field,
			Operation:  "forage.resolve",
			ActorID:    42,
			Payload:    payload,
		})
		if err == nil || !strings.Contains(err.Error(), "client-supplied "+field+" is forbidden") {
			t.Fatalf("%s forged forage err=%v", field, err)
		}
	}

	result := batch4Result(t, batch4Apply(t, path, world, "forage.resolve", 2, map[string]any{}))
	if got := storage.ParseInt(result["spirit_resources"]); got != 90 {
		t.Fatalf("spirit_resources=%d", got)
	}
	if got := storage.ParseInt(result["resource_bonus"]); got != 4 {
		t.Fatalf("resource_bonus=%d", got)
	}
	if got := storage.ParseInt(result["tn"]); got != 8 {
		t.Fatalf("tn=%d", got)
	}
	if got := fmt.Sprint(result["location"]); got != "Greenriver Town" {
		t.Fatalf("location=%q", got)
	}
	if got := storage.ParseInt(result["realm_index"]); got != 0 {
		t.Fatalf("realm_index=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")); got < 1 {
		t.Fatalf("spirit herb quantity=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='forage.resolve'")); got != 1 {
		t.Fatalf("forage receipts=%d", got)
	}
}

func TestForageResolveDerivesAccessibleGardenAndBirthFamily(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Greenriver Town','Mortal World',70)")
	batch4Exec(t, path, "INSERT INTO cave_abodes(user_id,location_key,base_location,herb_garden_level) VALUES(43,'guest_cave','Greenriver Town',4)")
	batch4Exec(t, path, "INSERT INTO cave_abode_access(owner_user_id,guest_user_id) VALUES(43,42)")
	batch4Exec(t, path, "UPDATE characters SET location='guest_cave',realm_index=8 WHERE user_id=42")
	batch4Exec(t, path, "INSERT INTO birth_families(archetype) VALUES('alchemy_family')")
	batch4Exec(t, path, "INSERT INTO character_birth_family(user_id,family_id) VALUES(42,1)")
	batch4Exec(t, path, `INSERT INTO active_effects(
		user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at
	) VALUES(42,'alchemy_inspiration','Alchemy Inspiration','test','alchemy_inspiration',
		'{"modifiers":[{"stat":"alchemy_bonus","operation":"add","value":3}]}',1,0,NULL,0)`)

	result := batch4Result(t, batch4Apply(t, path, world, "forage.resolve", 3, map[string]any{}))
	if got := fmt.Sprint(result["physical_location"]); got != "guest_cave" {
		t.Fatalf("physical_location=%q", got)
	}
	if got := fmt.Sprint(result["location"]); got != "Greenriver Town" {
		t.Fatalf("location=%q", got)
	}
	if got := storage.ParseInt(result["realm_index"]); got != 8 {
		t.Fatalf("realm_index=%d", got)
	}
	if got := storage.ParseInt(result["garden_level"]); got != 4 {
		t.Fatalf("garden_level=%d", got)
	}
	if got := storage.ParseInt(result["garden_bonus"]); got != 8 {
		t.Fatalf("garden_bonus=%d", got)
	}
	if got := storage.ParseInt(result["family_bonus"]); got != 2 {
		t.Fatalf("family_bonus=%d", got)
	}
	if got := storage.ParseInt(result["context_bonus"]); got != 13 {
		t.Fatalf("context_bonus=%d", got)
	}
}

func TestForageResolveEnforcesCooldownInGo(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Greenriver Town','Mortal World',50)")

	batch4Apply(t, path, world, "forage.resolve", 4, map[string]any{})
	payload, _ := json.Marshal(map[string]any{})
	_, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "forage-second-attempt",
		Operation:  "forage.resolve",
		ActorID:    42,
		Payload:    payload,
	})
	if err == nil || !strings.Contains(err.Error(), "forage cooldown active") {
		t.Fatalf("cooldown err=%v", err)
	}
}

func TestForageResolveDerivesAllEffectInputsAndSettlesToxicityInGo(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	world := batch4WorldWithForageAptitudeBonus(t)

	batch4Exec(t, path, "INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Greenriver Town','Mortal World',60)")
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at)
		VALUES('world_clock','{"anchor_game_minute":1540,"anchor_real_ts":1,"scale":0}',0)`)
	batch4Exec(t, path, `INSERT INTO alchemy_state(
		user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
		successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
	) VALUES(42,50,100,0,0,0,-99,'',0)`)
	batch4Exec(t, path, `INSERT INTO active_effects(
		user_id,effect_key,name,source_type,source_id,effect_json,stacks,
		starts_game_minute,ends_game_minute,created_at
	) VALUES(42,'stage2_buff','Stage 2 Buff','test','buff',
		'{"modifiers":[{"stat":"alchemy_bonus","operation":"add","value":2}]}',2,0,NULL,0)`)
	batch4Exec(t, path, `INSERT INTO deployed_location_arrays(
		location,item_id,name,owner_user_id,sect_name,effect_json,
		starts_game_minute,ends_game_minute,created_at,updated_at
	) VALUES('Greenriver Town','stage2_array','Stage 2 Array',42,'',
		'{"modifiers":[{"stat":"alchemy_bonus","operation":"add","value":3}]}',
		0,2000,0,0)`)
	batch4Exec(t, path, "UPDATE character_spiritual_roots SET mutation='stage2_forager' WHERE user_id=42")

	result := batch4Result(t, batch4Apply(t, path, world, "forage.resolve", 5, map[string]any{}))
	if got := storage.ParseInt(result["effect_bonus"]); got != 8 {
		t.Fatalf("effect_bonus=%d want 8", got)
	}
	if got := storage.ParseInt(result["context_bonus"]); got != 8 {
		t.Fatalf("context_bonus=%d want 8", got)
	}
	if got := storage.ParseInt(result["game_minute"]); got != 1540 {
		t.Fatalf("game_minute=%d want 1540", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT pill_toxicity FROM alchemy_state WHERE user_id=42")); got != 48 {
		t.Fatalf("pill_toxicity=%d want 48", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT last_toxicity_game_minute FROM alchemy_state WHERE user_id=42")); got != 1540 {
		t.Fatalf("last_toxicity_game_minute=%d want 1540", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM active_effects WHERE user_id=42 AND effect_key='pill_toxicity' AND source_type='alchemy'")); got != 1 {
		t.Fatalf("pill toxicity effect rows=%d want 1", got)
	}
}

func TestEffectsCurrentQueryPreviewsToxicityWithoutMutatingState(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	world := batch4WorldPath(t)

	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at)
		VALUES('world_clock','{"anchor_game_minute":1540,"anchor_real_ts":1,"scale":0}',0)`)
	batch4Exec(t, path, `INSERT INTO alchemy_state(
		user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
		successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
	) VALUES(42,50,100,0,0,0,-99,'',0)`)

	response, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		Operation:  "effects.current",
		ActorID:    42,
		Payload:    json.RawMessage(`{}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	result, ok := response.Result.(map[string]any)
	if !ok {
		t.Fatalf("result type=%T", response.Result)
	}
	if got := storage.ParseInt(result["game_minute"]); got != 1540 {
		t.Fatalf("game_minute=%d want 1540", got)
	}
	if got := storage.ParseInt(result["pill_toxicity"]); got != 48 {
		t.Fatalf("pill_toxicity=%d want 48", got)
	}
	effect, ok := result["pill_toxicity_effect"].(map[string]any)
	if !ok {
		t.Fatalf("pill_toxicity_effect=%T", result["pill_toxicity_effect"])
	}
	modifiers, ok := effect["modifiers"].([]any)
	if !ok || len(modifiers) < 2 {
		t.Fatalf("modifiers=%#v", effect["modifiers"])
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT pill_toxicity FROM alchemy_state WHERE user_id=42")); got != 50 {
		t.Fatalf("query mutated pill_toxicity=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT last_toxicity_game_minute FROM alchemy_state WHERE user_id=42")); got != 100 {
		t.Fatalf("query mutated last_toxicity_game_minute=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM active_effects WHERE user_id=42 AND effect_key='pill_toxicity'")); got != 0 {
		t.Fatalf("query persisted pill toxicity effects=%d", got)
	}
}
