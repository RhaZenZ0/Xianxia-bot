package simulation

import (
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
)

// What time it is was never the caller's to say (v1.0.0-rc.48).
//
// `RunDueRequest.GameMinute` has been accepted-and-ignored since the v0.22.2
// review, with the reason written on the field: "a scheduled tick must not be
// able to tell the world what time it is". `ForceRequest` and
// `BootstrapRequest` carried the same field and *used* it, for twenty-six more
// releases - so the rule held on one of three doors. `docs/TODO.md`
// named the asymmetry and deferred it as "a Go change of its own"; this is it.
//
// Nothing exploited it: every caller in the tree sent the engine's own minute
// straight back to it. That is exactly why a test is the only thing that keeps
// it shut - the fault is invisible until somebody writes the one caller that
// does not, and by then it has aged an NPC to death or stamped a system's
// anchor into next year.
//
// Each case below sends a minute that is wrong by a wild margin and asserts
// the canonical one landed instead. Put `req.GameMinute` back into `Force` or
// `Bootstrap` and these fail with the number the payload asked for.

const callerMinuteSchema = simulationClockSchema + `
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY,world_name TEXT,population INTEGER,prosperity INTEGER,security INTEGER,spirit_resources INTEGER,food_supply INTEGER,migration_pressure INTEGER,unrest INTEGER,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,world_name TEXT,profession TEXT,faction TEXT,wealth INTEGER,influence INTEGER,ambition INTEGER,realm_index INTEGER,phase INTEGER,status TEXT,activity TEXT,missing_since_game_minute INTEGER DEFAULT 0,last_game_minute INTEGER,updated_at REAL);
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
`

func TestForceStampsTheCanonicalMinuteNotTheCallers(t *testing.T) {
	path := filepath.Join(t.TempDir(), "force.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	// Force stamps a row that already exists; Bootstrap is what creates one,
	// so only this case seeds it.
	if err := conn.ExecScript(callerMinuteSchema + `
INSERT INTO world_simulation_state VALUES('npc_civilization',0,720,0,0);`); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()

	const canonical = int64(50_000)
	setSimulationGameMinute(t, path, canonical)
	runner, err := NewRunner(path, "")
	if err != nil {
		t.Fatal(err)
	}
	// A year and a half of world time past where the world actually stands.
	if _, err := runner.Force(ForceRequest{System: "npc_civilization", Steps: 1, GameMinute: 9_999_999}); err != nil {
		t.Fatal(err)
	}
	got := storage.ParseInt(simScalar(t, path, `SELECT last_game_minute FROM world_simulation_state WHERE system='npc_civilization'`))
	if got != canonical {
		t.Fatalf("Force stamped %d; the canonical minute is %d, and the payload's 9999999 is not the engine's to believe", got, canonical)
	}
}

func TestBootstrapSeedsAtTheCanonicalMinuteNotTheCallers(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bootstrap-minute.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(callerMinuteSchema); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()

	const canonical = int64(12_345)
	setSimulationGameMinute(t, path, canonical)
	runner, err := NewRunner(path, "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := runner.Bootstrap(BootstrapRequest{GameMinute: -4_000_000}); err != nil {
		t.Fatal(err)
	}
	got := storage.ParseInt(simScalar(t, path, `SELECT last_game_minute FROM world_simulation_state WHERE system='npc_civilization'`))
	if got != canonical {
		t.Fatalf("Bootstrap anchored at %d; the canonical minute is %d, and a negative payload minute is not the engine's to believe", got, canonical)
	}
}

// And the field itself stays on the wire, deliberately: an older bot mid-upgrade
// still POSTs `game_minute`, and a request refused for carrying it would make a
// rolling deploy an outage. It is the value that is ignored, not the request.
func TestTheWireStillAcceptsAMinuteItIgnores(t *testing.T) {
	path := filepath.Join(t.TempDir(), "wire.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(callerMinuteSchema + `
INSERT INTO world_simulation_state VALUES('npc_civilization',0,720,0,0);`); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()
	setSimulationGameMinute(t, path, 900)
	runner, err := NewRunner(path, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, minute := range []int64{0, 1, -1, 1 << 60} {
		if _, err := runner.Force(ForceRequest{System: "npc_civilization", Steps: 1, GameMinute: minute}); err != nil {
			t.Fatalf("a payload minute of %d was refused rather than ignored: %v", minute, err)
		}
	}
}
