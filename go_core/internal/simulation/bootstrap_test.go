package simulation

import (
	"path/filepath"
	"runtime"
	"testing"

	"xianxia/core/internal/storage"
)

func bootstrapWorldPath(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "../../../content/world.json"))
}

func TestBootstrapOwnsSimulationAndClanSeedWrites(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bootstrap.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY,world_name TEXT,population INTEGER,prosperity INTEGER,security INTEGER,spirit_resources INTEGER,food_supply INTEGER,migration_pressure INTEGER,unrest INTEGER,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,world_name TEXT,profession TEXT,faction TEXT,wealth INTEGER,influence INTEGER,ambition INTEGER,realm_index INTEGER,phase INTEGER,status TEXT,activity TEXT,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_mind_state(npc_name TEXT PRIMARY KEY,current_goal TEXT,mood TEXT,focus_target TEXT,recent_event TEXT,goal_progress INTEGER,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_life_state(npc_name TEXT PRIMARY KEY,birth_game_minute INTEGER,age_at_creation_years INTEGER,natural_lifespan_years INTEGER,health INTEGER,injury TEXT,injury_severity INTEGER,sect_rank TEXT,career_progress INTEGER,relationship_status TEXT,spouse_name TEXT,children_count INTEGER,last_social_game_minute INTEGER,last_cultivation_game_minute INTEGER,updated_at REAL);
CREATE TABLE sect_politics_state(sect_name TEXT PRIMARY KEY,alignment TEXT,specialty TEXT,influence INTEGER,cohesion INTEGER,resources INTEGER,recruitment_pressure INTEGER,doctrine_pressure INTEGER,leader_policy TEXT,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE sect_factions(sect_name TEXT,faction_name TEXT,agenda TEXT,power INTEGER,loyalty INTEGER,updated_at REAL,PRIMARY KEY(sect_name,faction_name));
CREATE TABLE sect_relations(sect_a TEXT,sect_b TEXT,relation_score INTEGER,relation_type TEXT,treaty_status TEXT,updated_at REAL,PRIMARY KEY(sect_a,sect_b));
CREATE TABLE economy_markets(location TEXT,item_id TEXT,world_name TEXT,currency_id TEXT,base_price INTEGER,supply INTEGER,demand INTEGER,price_index REAL,last_game_minute INTEGER,updated_at REAL,PRIMARY KEY(location,item_id));
CREATE TABLE birth_families(family_id INTEGER PRIMARY KEY,family_name TEXT,surname TEXT,tier INTEGER,head_name TEXT,head_realm_index INTEGER,branch_count INTEGER,retainer_count INTEGER,line_status TEXT);
CREATE TABLE martial_clan_branches(branch_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER,branch_name TEXT,branch_type TEXT,leader_name TEXT,members_estimate INTEGER,martial_strength INTEGER,wealth_share INTEGER,loyalty INTEGER,status TEXT,updated_at REAL);
CREATE TABLE martial_clan_retainers(retainer_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER,group_name TEXT,leader_name TEXT,role TEXT,members INTEGER,realm_index INTEGER,loyalty INTEGER,upkeep INTEGER,status TEXT,updated_at REAL);
CREATE TABLE martial_clan_relations(relation_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER,partner_family_id INTEGER,partner_name TEXT,relation_type TEXT,relation_score INTEGER,active INTEGER,started_game_minute INTEGER,updated_at REAL);
CREATE TABLE world_history_events(
	source_key TEXT PRIMARY KEY,event_type TEXT,title TEXT,summary TEXT,significance INTEGER,visibility TEXT,
	location TEXT,world_name TEXT,faction TEXT,actor_type TEXT,actor_key TEXT,actor_name TEXT,target_type TEXT,
	target_key TEXT,target_name TEXT,related_user_id INTEGER,related_npc_name TEXT,tags TEXT,game_minute INTEGER,
	metadata_json TEXT,created_at REAL,updated_at REAL
);
INSERT INTO birth_families(family_id,family_name,surname,tier,head_name,head_realm_index,branch_count,retainer_count,line_status)
VALUES(1,'Han Clan','Han',2,'Han Patriarch',3,2,12,'active');
`); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()

	runner, err := NewRunner(path, bootstrapWorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	result, err := runner.Bootstrap(BootstrapRequest{GameMinute: 100})
	if err != nil {
		t.Fatal(err)
	}
	if result.SimulationSystemsCreated != int64(len(SystemIntervals)) {
		t.Fatalf("simulation systems created=%d", result.SimulationSystemsCreated)
	}
	if got := simScalar(t, path, "SELECT COUNT(*) FROM world_simulation_state"); storage.ParseInt(got) != int64(len(SystemIntervals)) {
		t.Fatalf("simulation states=%v", got)
	}
	if got := simScalar(t, path, "SELECT COUNT(*) FROM civilization_regions"); storage.ParseInt(got) == 0 {
		t.Fatal("civilization bootstrap produced no regions")
	}
	if got := simScalar(t, path, "SELECT COUNT(*) FROM npc_civilization_state WHERE npc_name='Elder Su Yan'"); storage.ParseInt(got) != 1 {
		t.Fatalf("npc rows=%v", got)
	}
	if got := simScalar(t, path, "SELECT COUNT(*) FROM martial_clan_branches WHERE family_id=1"); storage.ParseInt(got) != 2 {
		t.Fatalf("branch rows=%v", got)
	}
	if got := simScalar(t, path, "SELECT COALESCE(SUM(members),0) FROM martial_clan_retainers WHERE family_id=1"); storage.ParseInt(got) != 12 {
		t.Fatalf("retainers=%v", got)
	}
	if got := simScalar(t, path, "SELECT COUNT(*) FROM martial_clan_relations WHERE family_id=1"); storage.ParseInt(got) != 1 {
		t.Fatalf("relations=%v", got)
	}
}
