package game

// Authority II (v0.30.0): the engine derives what Python used to compute
// for it, and answers the world-status reads Python used to run as raw SQL.

import (
	"encoding/json"
	"math"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupAuthority2DB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	schema := `
ALTER TABLE cave_abodes ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE cave_abodes ADD COLUMN property_type TEXT NOT NULL DEFAULT 'cave_abode';
ALTER TABLE cave_abodes ADD COLUMN cultivation_level INTEGER NOT NULL DEFAULT 1;
ALTER TABLE cave_abodes ADD COLUMN formation_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN family_name TEXT NOT NULL DEFAULT '';
ALTER TABLE birth_families ADD COLUMN location TEXT NOT NULL DEFAULT '';
ALTER TABLE birth_families ADD COLUMN line_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE birth_families ADD COLUMN head_name TEXT NOT NULL DEFAULT '';
ALTER TABLE birth_families ADD COLUMN head_realm_index INTEGER NOT NULL DEFAULT 0;
ALTER TABLE birth_families ADD COLUMN head_phase INTEGER NOT NULL DEFAULT 1;
ALTER TABLE birth_families ADD COLUMN influence INTEGER NOT NULL DEFAULT 0;
CREATE TABLE battles(battle_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE seclusion_sessions(user_id INTEGER PRIMARY KEY,mode TEXT NOT NULL,started_game_minute INTEGER NOT NULL,ends_game_minute INTEGER NOT NULL,last_settled_game_minute INTEGER NOT NULL,start_location TEXT NOT NULL DEFAULT '',environment_mult REAL NOT NULL DEFAULT 1.0,accumulated_gain INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'active',ended_reason TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT '',rank_level INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sect_manors(sect_name TEXT PRIMARY KEY,name TEXT NOT NULL,base_location TEXT NOT NULL,qi_array_level INTEGER NOT NULL DEFAULT 0);
CREATE TABLE economy_markets(location TEXT NOT NULL,item_id TEXT NOT NULL,world_name TEXT NOT NULL DEFAULT 'Mortal World',currency_id TEXT NOT NULL DEFAULT 'low_grade_spirit_stone',base_price INTEGER NOT NULL DEFAULT 1,supply INTEGER NOT NULL DEFAULT 10,demand INTEGER NOT NULL DEFAULT 40,price_index REAL NOT NULL DEFAULT 1.0,last_game_minute INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0,PRIMARY KEY(location,item_id));
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,current_location TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'alive',influence INTEGER NOT NULL DEFAULT 0,realm_index INTEGER NOT NULL DEFAULT 0,phase INTEGER NOT NULL DEFAULT 1,profession TEXT NOT NULL DEFAULT '',activity TEXT NOT NULL DEFAULT '',faction TEXT NOT NULL DEFAULT '');
CREATE TABLE npc_life_state(npc_name TEXT PRIMARY KEY,birth_game_minute INTEGER,age_at_creation_years INTEGER NOT NULL DEFAULT 20,natural_lifespan_years INTEGER NOT NULL DEFAULT 80,health INTEGER NOT NULL DEFAULT 100,injury TEXT NOT NULL DEFAULT '',injury_severity INTEGER NOT NULL DEFAULT 0,sect_rank TEXT NOT NULL DEFAULT '',career_progress INTEGER NOT NULL DEFAULT 0,relationship_status TEXT NOT NULL DEFAULT 'single',spouse_name TEXT NOT NULL DEFAULT '',children_count INTEGER NOT NULL DEFAULT 0,last_social_game_minute INTEGER NOT NULL DEFAULT 0,last_cultivation_game_minute INTEGER NOT NULL DEFAULT 0,death_game_minute INTEGER,cause_of_death TEXT NOT NULL DEFAULT '');
CREATE TABLE npc_mind_state(npc_name TEXT PRIMARY KEY,current_goal TEXT NOT NULL DEFAULT '',mood TEXT NOT NULL DEFAULT '',focus_target TEXT NOT NULL DEFAULT '',recent_event TEXT NOT NULL DEFAULT '',goal_progress INTEGER NOT NULL DEFAULT 0);
CREATE TABLE npc_social_relations(npc_a TEXT NOT NULL,npc_b TEXT NOT NULL,affinity INTEGER NOT NULL DEFAULT 0,trust INTEGER NOT NULL DEFAULT 0,grudge INTEGER NOT NULL DEFAULT 0,relation_type TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE npc_disciple_bonds(master_name TEXT NOT NULL,disciple_name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',started_game_minute INTEGER NOT NULL DEFAULT 0);
CREATE TABLE npc_descendants(descendant_id INTEGER PRIMARY KEY AUTOINCREMENT,child_name TEXT NOT NULL,parent_a TEXT NOT NULL,parent_b TEXT NOT NULL DEFAULT '',birth_game_minute INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'alive');
CREATE TABLE civilization_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,location TEXT NOT NULL,event_text TEXT NOT NULL,severity INTEGER NOT NULL DEFAULT 1,game_minute INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sect_politics_state(sect_name TEXT PRIMARY KEY,influence INTEGER NOT NULL DEFAULT 0,leader_policy TEXT NOT NULL DEFAULT '');
CREATE TABLE sect_factions(faction_id INTEGER PRIMARY KEY AUTOINCREMENT,sect_name TEXT NOT NULL,faction_name TEXT NOT NULL,agenda TEXT NOT NULL DEFAULT '',power INTEGER NOT NULL DEFAULT 0,loyalty INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sect_relations(sect_a TEXT NOT NULL,sect_b TEXT NOT NULL,relation_score INTEGER NOT NULL DEFAULT 0,relation_type TEXT NOT NULL DEFAULT 'neutral');
CREATE TABLE sect_politics_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,sect_name TEXT NOT NULL,event_text TEXT NOT NULL,severity INTEGER NOT NULL DEFAULT 1,game_minute INTEGER NOT NULL DEFAULT 0);
CREATE TABLE martial_clan_branches(branch_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER NOT NULL,branch_name TEXT NOT NULL,branch_type TEXT NOT NULL DEFAULT 'cadet',martial_strength INTEGER NOT NULL DEFAULT 20);
CREATE TABLE martial_clan_retainers(retainer_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER NOT NULL,retainer_name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',loyalty INTEGER NOT NULL DEFAULT 50);
CREATE TABLE martial_clan_relations(relation_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER NOT NULL,other_family TEXT NOT NULL,relation_score INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER NOT NULL DEFAULT 0,interval_game_minutes INTEGER NOT NULL DEFAULT 1440,last_run_real REAL NOT NULL DEFAULT 0,runs INTEGER NOT NULL DEFAULT 0);
CREATE TABLE world_action_events(action_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,action_type TEXT NOT NULL,target_type TEXT NOT NULL,target_key TEXT NOT NULL,location TEXT NOT NULL,severity INTEGER NOT NULL DEFAULT 1,game_minute INTEGER NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,slot TEXT NOT NULL DEFAULT 'weapon',durability INTEGER NOT NULL DEFAULT 100,max_durability INTEGER NOT NULL DEFAULT 100,quality INTEGER NOT NULL DEFAULT 100,equipped INTEGER NOT NULL DEFAULT 0);
`
	if err := conn.ExecScript(schema); err != nil {
		t.Fatal(err)
	}
	return path
}

func authority2Query(t *testing.T, path, world, op string, actor int64, payload map[string]any) map[string]any {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: op, ActorID: actor, Payload: raw})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("%s result type %T", op, out.Result)
	}
	return result
}

func authority2Rows(t *testing.T, result map[string]any, key string) []map[string]any {
	t.Helper()
	raw, ok := result[key].([]map[string]any)
	if !ok {
		t.Fatalf("%s=%T want []map[string]any", key, result[key])
	}
	return raw
}

func nearly(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

// --- seclusion.start ---------------------------------------------------------

func TestSeclusionStartRefusesACallerSuppliedEnvironment(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`)
	raw, _ := json.Marshal(map[string]any{"mode": "qi", "duration_game_minutes": 1440, "environment_mult": 1.75})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "a2-seclusion-forged", Operation: "seclusion.start", ActorID: 42, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "environment_mult is derived by the engine") {
		t.Fatalf("forged environment accepted: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM seclusion_sessions")); got != 0 {
		t.Fatalf("sessions=%d want 0", got)
	}
}

func TestSeclusionStartRequiresAProtectedSite(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	// Greenriver Town is not a safe zone, and the character owns no abode
	// and belongs to no sect with a manor there.
	raw, _ := json.Marshal(map[string]any{"mode": "qi", "duration_game_minutes": 1440})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "a2-seclusion-exposed", Operation: "seclusion.start", ActorID: 42, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "requires a protected/safe location") {
		t.Fatalf("exposed seclusion accepted: %v", err)
	}
}

func TestSeclusionStartDerivesTheEnvironmentFromState(t *testing.T) {
	cases := []struct {
		name    string
		mode    string
		prepare func(path string)
		mult    float64
		site    string
	}{
		{"safe zone alone", "qi", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`)
		}, 1.0, "safe_zone"},
		{"abode chamber level 3", "qi", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='abode:42' WHERE user_id=42`)
			batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,property_type,cultivation_level) VALUES(42,'abode:42','Greenriver Town','Quiet Cave','cave_abode',3)`)
		}, 1.20, "abode"},
		{"abode chamber is capped", "qi", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='abode:42' WHERE user_id=42`)
			batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,property_type,cultivation_level) VALUES(42,'abode:42','Greenriver Town','Quiet Cave','cave_abode',12)`)
		}, 1.45, "abode"},
		{"sect manor array level 2 in an exposed town", "qi", func(path string) {
			batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name) VALUES(42,'Azure Cloud Sect')`)
			batch4Exec(t, path, `INSERT INTO sect_manors(sect_name,name,base_location,qi_array_level) VALUES('Azure Cloud Sect','Azure Hall','Greenriver Town',2)`)
		}, 0.85 * 1.16, "manor"},
		{"manor elsewhere does not count", "qi", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`)
			batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name) VALUES(42,'Azure Cloud Sect')`)
			batch4Exec(t, path, `INSERT INTO sect_manors(sect_name,name,base_location,qi_array_level) VALUES('Azure Cloud Sect','Azure Hall','Greenriver Town',5)`)
		}, 1.0, "safe_zone"},
		{"deployed array multiplies qi seclusion", "qi", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`)
			batch4Exec(t, path, `INSERT INTO deployed_location_arrays(location,item_id,name,effect_json,starts_game_minute,ends_game_minute) VALUES('Spirit Jade Capital','minor_qi_gathering_array_disk','Minor Qi Gathering Array','{"modifiers":[{"stat":"cultivation_gain","operation":"mul","value":1.10},{"stat":"spirit","operation":"add","value":1}]}',0,5000)`)
		}, 1.10, "safe_zone"},
		{"deployed array leaves body seclusion alone", "body", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`)
			batch4Exec(t, path, `INSERT INTO deployed_location_arrays(location,item_id,name,effect_json,starts_game_minute,ends_game_minute) VALUES('Spirit Jade Capital','minor_qi_gathering_array_disk','Minor Qi Gathering Array','{"modifiers":[{"stat":"cultivation_gain","operation":"mul","value":1.10}]}',0,5000)`)
		}, 1.0, "safe_zone"},
		{"an expired array is not counted", "qi", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`)
			batch4Exec(t, path, `INSERT INTO deployed_location_arrays(location,item_id,name,effect_json,starts_game_minute,ends_game_minute) VALUES('Spirit Jade Capital','minor_qi_gathering_array_disk','Minor Qi Gathering Array','{"modifiers":[{"stat":"cultivation_gain","operation":"mul","value":1.10}]}',0,500)`)
		}, 1.0, "safe_zone"},
		{"everything at once is clamped at the ceiling", "qi", func(path string) {
			batch4Exec(t, path, `UPDATE characters SET location='abode:42' WHERE user_id=42`)
			batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,property_type,cultivation_level) VALUES(42,'abode:42','Greenriver Town','Quiet Cave','cave_abode',5)`)
			batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name) VALUES(42,'Azure Cloud Sect')`)
			batch4Exec(t, path, `INSERT INTO sect_manors(sect_name,name,base_location,qi_array_level) VALUES('Azure Cloud Sect','Azure Hall','abode:42',5)`)
			batch4Exec(t, path, `INSERT INTO deployed_location_arrays(location,item_id,name,effect_json,starts_game_minute,ends_game_minute) VALUES('abode:42','minor_qi_gathering_array_disk','Minor Qi Gathering Array','{"modifiers":[{"stat":"cultivation_gain","operation":"mul","value":1.10}]}',0,5000)`)
		}, 1.75, "abode"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := setupAuthority2DB(t)
			world := batch4WorldPath(t)
			tc.prepare(path)
			result := batch4Result(t, batch4Apply(t, path, world, "seclusion.start", 1, map[string]any{"mode": tc.mode, "duration_game_minutes": 1440, "game_minute": 1000}))
			got := parseFloat(result["environment_mult"])
			if !nearly(got, tc.mult) {
				t.Fatalf("environment_mult=%v want %v (result %#v)", got, tc.mult, result["environment"])
			}
			env, ok := result["environment"].(map[string]any)
			if !ok {
				t.Fatalf("environment=%T", result["environment"])
			}
			if env["site"] != tc.site {
				t.Fatalf("site=%v want %s", env["site"], tc.site)
			}
			if !nearly(parseFloat(actionScalar(t, path, "SELECT environment_mult FROM seclusion_sessions WHERE user_id=42")), tc.mult) {
				t.Fatal("the stored session does not carry the derived multiplier")
			}
			if storage.ParseInt(result["projected_daily_gain"]) < 1 {
				t.Fatalf("projected_daily_gain=%v", result["projected_daily_gain"])
			}
		})
	}
}

func TestSeclusionProjectionMatchesWhatSettlePays(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `UPDATE characters SET location='abode:42' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO cave_abodes(user_id,location_key,base_location,name,property_type,cultivation_level) VALUES(42,'abode:42','Greenriver Town','Quiet Cave','cave_abode',2)`)
	start := batch4Result(t, batch4Apply(t, path, world, "seclusion.start", 1, map[string]any{"mode": "qi", "duration_game_minutes": 3 * 1440, "game_minute": 1000}))
	projected := storage.ParseInt(start["projected_daily_gain"])
	settle := batch4Result(t, batch4Apply(t, path, world, "seclusion.settle", 2, map[string]any{"minutes_per_day": 1440, "game_minute": 1000 + 2*1440}))
	if got := storage.ParseInt(settle["daily_gain"]); got != projected {
		t.Fatalf("settle daily_gain=%d, start projected %d", got, projected)
	}
	if got := storage.ParseInt(settle["settled_days_now"]); got != 2 {
		t.Fatalf("settled_days_now=%d want 2", got)
	}
}

func TestMultiplicativeEffectJSONStat(t *testing.T) {
	raw := `{"modifiers":[{"stat":"cultivation_gain","operation":"mul","value":1.1},{"stat":"cultivation_gain","operation":"mul","value":1.5},{"stat":"cultivation_gain","operation":"add","value":9},{"stat":"spirit","operation":"mul","value":3}]}`
	if got := multiplicativeEffectJSONStat(raw, "cultivation_gain"); !nearly(got, 1.65) {
		t.Fatalf("product=%v want 1.65", got)
	}
	if got := multiplicativeEffectJSONStat("not json", "cultivation_gain"); got != 1 {
		t.Fatalf("unreadable payload=%v want 1", got)
	}
}

// --- market ------------------------------------------------------------------

func TestMarketQuoteAndRowsCarryTheEnginePrices(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO economy_markets(location,item_id,base_price,supply,demand,price_index) VALUES('Greenriver Town','qi_pill',10,5,40,1.37)`)
	batch4Exec(t, path, `INSERT INTO economy_markets(location,item_id,base_price,supply,demand,price_index) VALUES('Greenriver Town','spirit_herb',3,50,20,0.9)`)

	quote := authority2Query(t, path, world, "market.quote", 0, map[string]any{"location": "Greenriver Town", "item_id": "qi_pill"})
	if quote["traded"] != true {
		t.Fatalf("traded=%v", quote["traded"])
	}
	// 10 * 1.37 = 13.7 -> 14; 14 * 0.70 = 9.8 -> 10: the same share market.trade pays.
	if got := storage.ParseInt(quote["buy_price"]); got != 14 {
		t.Fatalf("buy_price=%d want 14", got)
	}
	if got := storage.ParseInt(quote["sell_price"]); got != 10 {
		t.Fatalf("sell_price=%d want 10", got)
	}
	missing := authority2Query(t, path, world, "market.quote", 0, map[string]any{"location": "Greenriver Town", "item_id": "bugslayer_sword"})
	if missing["traded"] != false {
		t.Fatalf("untraded quote=%#v", missing)
	}
	rows := authority2Rows(t, authority2Query(t, path, world, "market.rows", 0, map[string]any{"location": "Greenriver Town", "limit": 10}), "rows")
	if len(rows) != 2 || rows[0]["item_id"] != "qi_pill" {
		t.Fatalf("rows=%#v", rows)
	}
	if got := storage.ParseInt(rows[1]["sell_price"]); got != 2 {
		t.Fatalf("spirit_herb sell_price=%d want 2", got)
	}
}

func TestMarketCatalogAppliesTheOneTradeableRule(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	result := authority2Query(t, path, world, "market.catalog", 0, map[string]any{})
	ids, ok := result["item_ids"].([]string)
	if !ok {
		t.Fatalf("item_ids=%T", result["item_ids"])
	}
	set := map[string]bool{}
	for _, id := range ids {
		set[id] = true
	}
	if !set["qi_pill"] {
		t.Fatal("qi_pill must be tradeable")
	}
	for _, excluded := range []string{"bugslayer_sword", "lesser_stygian_seal"} {
		if set[excluded] {
			t.Fatalf("%s must not reach ordinary markets", excluded)
		}
	}
}

// --- combat targets -------------------------------------------------------------

func TestCombatTargetsHideRealMastersAndDeduplicate(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,current_location,status,influence,realm_index,phase) VALUES('Old Beggar Chen','Greenriver Town','alive',90,0,1)`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,current_location,status,influence,realm_index,phase) VALUES('Old Gou','Greenriver Town','alive',80,0,2)`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,current_location,status,influence,realm_index,phase) VALUES('Merchant Wu','Greenriver Town','alive',70,1,3)`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,current_location,status,influence,realm_index,phase) VALUES('Dead Man','Greenriver Town','dead',99,1,3)`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,current_location,status,influence,realm_index,phase) VALUES('Far Away','Moonfen Marsh','alive',99,1,3)`)
	batch4Exec(t, path, `INSERT INTO birth_families(archetype,family_name,location,line_status,head_name,head_realm_index,head_phase,influence) VALUES('martial_household','Han Family','Greenriver Town','active','Han Wei',2,4,60)`)
	batch4Exec(t, path, `INSERT INTO birth_families(archetype,family_name,location,line_status,head_name,head_realm_index,head_phase,influence) VALUES('martial_household','Wu Family','Greenriver Town','active','Merchant Wu',0,1,10)`)
	batch4Exec(t, path, `INSERT INTO birth_families(archetype,family_name,location,line_status,head_name,head_realm_index,head_phase,influence) VALUES('martial_household','Gone Family','Greenriver Town','active','Vacant Ancestral Seat',0,1,10)`)
	targets := authority2Rows(t, authority2Query(t, path, world, "combat.targets", 0, map[string]any{"location": "Greenriver Town"}), "targets")
	names := make([]string, 0, len(targets))
	for _, row := range targets {
		names = append(names, row["name"].(string))
	}
	want := []string{"Old Gou", "Merchant Wu", "Han Wei"}
	if strings.Join(names, ",") != strings.Join(want, ",") {
		t.Fatalf("targets=%v want %v", names, want)
	}
	if targets[2]["target_type"] != "family_head" || storage.ParseInt(targets[2]["family_id"]) != 1 {
		t.Fatalf("family head row=%#v", targets[2])
	}
}

// --- simulation and world status ---------------------------------------------------

func TestSimulationStatusLagIsMeasuredAgainstTheEngineClock(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 10000)
	batch4Exec(t, path, `INSERT INTO world_simulation_state(system,last_game_minute,interval_game_minutes,runs) VALUES('npc_life',8560,10080,3)`)
	batch4Exec(t, path, `INSERT INTO world_simulation_state(system,last_game_minute,interval_game_minutes,runs) VALUES('dynamic_economy',12000,1440,9)`)
	status := authority2Query(t, path, world, "simulation.status", 0, map[string]any{})
	if got := storage.ParseInt(status["game_minute"]); got != 10000 {
		t.Fatalf("game_minute=%d want 10000", got)
	}
	systems := authority2Rows(t, status, "systems")
	if len(systems) != 2 || systems[0]["system"] != "dynamic_economy" {
		t.Fatalf("systems=%#v", systems)
	}
	if got := storage.ParseInt(systems[0]["lag_game_minutes"]); got != 0 {
		t.Fatalf("future anchor lag=%d want 0", got)
	}
	if got := storage.ParseInt(systems[1]["lag_game_minutes"]); got != 1440 {
		t.Fatalf("npc_life lag=%d want 1440", got)
	}
	state := authority2Query(t, path, world, "simulation.state", 0, map[string]any{"system": "npc_life"})
	if state["found"] != true || storage.ParseInt(state["runs"]) != 3 {
		t.Fatalf("state=%#v", state)
	}
	if missing := authority2Query(t, path, world, "simulation.state", 0, map[string]any{"system": "weather"}); missing["found"] != false {
		t.Fatalf("missing state=%#v", missing)
	}
	raw, _ := json.Marshal(map[string]any{"game_minute": 5})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: "simulation.status", Payload: raw}); err == nil {
		t.Fatal("a caller-supplied game_minute must be refused")
	}
}

func TestRecentActionsDecodeTheirPayload(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	for i := 1; i <= 3; i++ {
		batch4Exec(t, path, `INSERT INTO world_action_events(user_id,action_type,target_type,target_key,location,severity,game_minute,payload_json) VALUES(42,'trade','market','qi_pill','Greenriver Town',?,?,?)`, i, i*100, `{"quantity":`+string(rune('0'+i))+`}`)
	}
	actions := authority2Rows(t, authority2Query(t, path, world, "world.recent_actions", 0, map[string]any{"limit": 2}), "actions")
	if len(actions) != 2 || storage.ParseInt(actions[0]["severity"]) != 3 {
		t.Fatalf("actions=%#v", actions)
	}
	payload, ok := actions[0]["payload"].(map[string]any)
	if !ok || storage.ParseInt(payload["quantity"]) != 3 {
		t.Fatalf("payload=%#v", actions[0]["payload"])
	}
	if _, still := actions[0]["payload_json"]; still {
		t.Fatal("payload_json must be decoded, not echoed")
	}
}

func TestStatusPanelsAssembleTheirSections(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 525600*3)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES('Greenriver Town','Mortal World',61)`)
	batch4Exec(t, path, `INSERT INTO civilization_events(location,event_text,severity,game_minute) VALUES('Greenriver Town','A caravan arrived.',1,10)`)
	batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,current_location,status,influence,realm_index,phase,profession) VALUES('Merchant Wu','Greenriver Town','alive',70,1,3,'merchant')`)
	batch4Exec(t, path, `INSERT INTO npc_life_state(npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health) VALUES('Merchant Wu',0,30,80,88)`)
	batch4Exec(t, path, `INSERT INTO npc_mind_state(npc_name,current_goal,mood) VALUES('Merchant Wu','sell everything','content')`)
	batch4Exec(t, path, `INSERT INTO npc_social_relations(npc_a,npc_b,affinity,trust,grudge) VALUES('Merchant Wu','Old Gou',40,20,0)`)
	batch4Exec(t, path, `INSERT INTO npc_disciple_bonds(master_name,disciple_name,status) VALUES('Merchant Wu','Apprentice Li','active')`)
	batch4Exec(t, path, `INSERT INTO npc_descendants(child_name,parent_a,parent_b,birth_game_minute) VALUES('Wu Junior','Merchant Wu','',100)`)
	batch4Exec(t, path, `INSERT INTO sect_politics_state(sect_name,influence,leader_policy) VALUES('Azure Cloud Sect',55,'expansion')`)
	batch4Exec(t, path, `INSERT INTO sect_factions(sect_name,faction_name,agenda,power,loyalty) VALUES('Azure Cloud Sect','Sword Hall','dominance',30,70)`)
	batch4Exec(t, path, `INSERT INTO sect_relations(sect_a,sect_b,relation_score) VALUES('Azure Cloud Sect','Moonfen Sect',-20)`)
	batch4Exec(t, path, `INSERT INTO sect_relations(sect_a,sect_b,relation_score) VALUES('Iron Sect','Azure Cloud Sect',35)`)
	batch4Exec(t, path, `INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute) VALUES('Azure Cloud Sect','An elder retired.',1,5)`)
	batch4Exec(t, path, `INSERT INTO martial_clan_branches(family_id,branch_name,branch_type,martial_strength) VALUES(7,'Cadet Line','cadet',30)`)
	batch4Exec(t, path, `INSERT INTO martial_clan_branches(family_id,branch_name,branch_type,martial_strength) VALUES(7,'Main Line','main',20)`)
	batch4Exec(t, path, `INSERT INTO martial_clan_retainers(family_id,retainer_name,loyalty) VALUES(7,'Steward Fang',80)`)
	batch4Exec(t, path, `INSERT INTO martial_clan_relations(family_id,other_family,relation_score,active) VALUES(7,'Han Family',12,1)`)
	batch4Exec(t, path, `INSERT INTO martial_clan_relations(family_id,other_family,relation_score,active) VALUES(7,'Old Feud',-90,0)`)

	region := authority2Query(t, path, world, "civilization.status", 0, map[string]any{"location": "Greenriver Town"})
	if region["found"] != true || storage.ParseInt(region["spirit_resources"]) != 61 {
		t.Fatalf("region=%#v", region)
	}
	if len(authority2Rows(t, region, "events")) != 1 || len(authority2Rows(t, region, "npcs")) != 1 {
		t.Fatalf("region sections=%#v", region)
	}
	if authority2Rows(t, region, "npcs")[0]["health"] == nil {
		t.Fatal("region npcs must join life state")
	}
	if missing := authority2Query(t, path, world, "civilization.status", 0, map[string]any{"location": "Nowhere"}); missing["found"] != false {
		t.Fatalf("missing region=%#v", missing)
	}

	npc := authority2Query(t, path, world, "npc.status", 0, map[string]any{"npc_name": "Merchant Wu"})
	if npc["found"] != true || npc["mood"] != "content" || npc["current_goal"] != "sell everything" {
		t.Fatalf("npc=%#v", npc)
	}
	for _, key := range []string{"relationships", "discipleship", "descendants"} {
		if len(authority2Rows(t, npc, key)) != 1 {
			t.Fatalf("%s=%#v", key, npc[key])
		}
	}
	// Born at minute 0 aged 30, three game years on: the lifespan model answers inline.
	if got := parseFloat(npc["age_years"]); got < 32.9 || got > 33.1 {
		t.Fatalf("age_years=%v want ~33", got)
	}
	if npc["lifespan_years"] == nil {
		t.Fatal("lifespan_years missing")
	}
	if missing := authority2Query(t, path, world, "npc.status", 0, map[string]any{"npc_name": "Nobody"}); missing["found"] != false {
		t.Fatalf("missing npc=%#v", missing)
	}

	sect := authority2Query(t, path, world, "sect.status", 0, map[string]any{"sect_name": "Azure Cloud Sect"})
	if sect["found"] != true || sect["leader_policy"] != "expansion" {
		t.Fatalf("sect=%#v", sect)
	}
	relations := authority2Rows(t, sect, "relations")
	if len(relations) != 2 || relations[0]["other"] != "Iron Sect" || relations[1]["other"] != "Moonfen Sect" {
		t.Fatalf("relations=%#v", relations)
	}
	if len(authority2Rows(t, sect, "factions")) != 1 || len(authority2Rows(t, sect, "events")) != 1 {
		t.Fatalf("sect sections=%#v", sect)
	}

	clan := authority2Query(t, path, world, "clan.status", 0, map[string]any{"family_id": 7})
	branches := authority2Rows(t, clan, "branches")
	if len(branches) != 2 || branches[0]["branch_type"] != "main" {
		t.Fatalf("branches=%#v", branches)
	}
	if len(authority2Rows(t, clan, "retainers")) != 1 || len(authority2Rows(t, clan, "relations")) != 1 {
		t.Fatalf("clan sections=%#v", clan)
	}
}

func TestEquipmentPowerQueryIsTheEngineFigure(t *testing.T) {
	path := setupAuthority2DB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,quality,equipped) VALUES(42,'spirit_iron_sword','weapon',60,120,100,1)`)
	batch4Exec(t, path, `INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,quality,equipped) VALUES(42,'spirit_iron_armor','armor',160,160,100,0)`)
	batch4Exec(t, path, `INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,quality,equipped) VALUES(42,'cloud_stepping_boots','boots',0,100,100,1)`)
	power := authority2Query(t, path, world, "equipment.power", 42, map[string]any{})
	// Sword at half durability: attack 4 * 0.5 = 2, spirit 1 * 0.5 -> 1 (round half up).
	// Unequipped armour and broken boots add nothing.
	if got := storage.ParseInt(power["attack"]); got != 2 {
		t.Fatalf("attack=%d want 2", got)
	}
	if got := storage.ParseInt(power["defense"]); got != 0 {
		t.Fatalf("defense=%d want 0", got)
	}
	if got := storage.ParseInt(power["agility"]); got != 0 {
		t.Fatalf("agility=%d want 0", got)
	}
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: "equipment.power", Payload: json.RawMessage(`{}`)}); err == nil {
		t.Fatal("equipment.power needs an actor")
	}
}
