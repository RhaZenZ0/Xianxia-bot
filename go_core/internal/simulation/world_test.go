package simulation

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupSimulationDB(t *testing.T, schema string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "simulation.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(schema); err != nil {
		t.Fatal(err)
	}
	return path
}

func simScalar(t *testing.T, path, sql string, params ...any) any {
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

func TestRunDueCapsWorkWithoutDiscardingBacklog(t *testing.T) {
	path := setupSimulationDB(t, `
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY,world_name TEXT,population INTEGER,prosperity INTEGER,security INTEGER,spirit_resources INTEGER,food_supply INTEGER,migration_pressure INTEGER,unrest INTEGER,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,world_name TEXT,profession TEXT,faction TEXT,wealth INTEGER,influence INTEGER,ambition INTEGER,realm_index INTEGER,phase INTEGER,status TEXT,activity TEXT,last_game_minute INTEGER,updated_at REAL);
INSERT INTO world_simulation_state VALUES('npc_civilization',0,1440,0,0);
INSERT INTO civilization_regions VALUES('Greenriver Town','Mortal World',1000,50,50,50,50,0,0,0,0);
INSERT INTO npc_civilization_state VALUES('Elder Test','Greenriver Town','Greenriver Town','Mortal World','Elder','Independent',20,20,20,1,1,'alive','Cultivating',0,0);
`)
	runner, err := NewRunner(path, "")
	if err != nil {
		t.Fatal(err)
	}
	runs, err := runner.RunDue(RunDueRequest{GameMinute: 200 * minutesPerDay, Automation: map[string]bool{"npc_civilization": true}})
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 1 {
		t.Fatalf("runs=%d", len(runs))
	}
	if runs[0].DueSteps != 200 || runs[0].AppliedSteps != 120 {
		t.Fatalf("run=%+v", runs[0])
	}
	if !strings.Contains(runs[0].Summary, "80 interval(s) remain queued") {
		t.Fatalf("summary=%q", runs[0].Summary)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT last_game_minute FROM world_simulation_state WHERE system='npc_civilization'")); got != 120*minutesPerDay {
		t.Fatalf("last_game_minute=%d", got)
	}
}

func TestNPCLifeBatchAppliesNaturalDeath(t *testing.T) {
	path := setupSimulationDB(t, `
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,current_location TEXT,realm_index INTEGER,status TEXT,activity TEXT,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_life_state(npc_name TEXT PRIMARY KEY,birth_game_minute INTEGER,age_at_creation_years INTEGER,natural_lifespan_years INTEGER,health INTEGER,injury TEXT,injury_severity INTEGER,career_progress INTEGER,relationship_status TEXT,spouse_name TEXT,children_count INTEGER,last_social_game_minute INTEGER,last_cultivation_game_minute INTEGER,death_game_minute INTEGER,cause_of_death TEXT,updated_at REAL);
CREATE TABLE npc_social_relations(npc_a TEXT,npc_b TEXT,affinity INTEGER,trust INTEGER,grudge INTEGER,relation_type TEXT,status TEXT,started_game_minute INTEGER,last_interaction_game_minute INTEGER,updated_at REAL,PRIMARY KEY(npc_a,npc_b));
INSERT INTO world_simulation_state VALUES('npc_life',0,10080,0,0);
INSERT INTO npc_civilization_state VALUES('Old Master','Greenriver Town',0,'alive','Cultivating',0,0);
INSERT INTO npc_life_state VALUES('Old Master',0,90,70,100,'',0,0,'single','',0,0,0,NULL,'',0);
`)
	runner, _ := NewRunner(path, "")
	run, err := runner.Force(ForceRequest{System: "npc_life", Steps: 1, GameMinute: 7 * minutesPerDay})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(run.Summary, "1 natural death") {
		t.Fatalf("summary=%q", run.Summary)
	}
	if got := simScalar(t, path, "SELECT status FROM npc_civilization_state WHERE npc_name='Old Master'"); got != "dead" {
		t.Fatalf("status=%v", got)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT health FROM npc_life_state WHERE npc_name='Old Master'")); got != 0 {
		t.Fatalf("health=%d", got)
	}
}

func TestNPCLifeBatchCanCreateMarriageInOneTransaction(t *testing.T) {
	path := setupSimulationDB(t, `
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,current_location TEXT,realm_index INTEGER,status TEXT,activity TEXT,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE npc_life_state(npc_name TEXT PRIMARY KEY,birth_game_minute INTEGER,age_at_creation_years INTEGER,natural_lifespan_years INTEGER,health INTEGER,injury TEXT,injury_severity INTEGER,career_progress INTEGER,relationship_status TEXT,spouse_name TEXT,children_count INTEGER,last_social_game_minute INTEGER,last_cultivation_game_minute INTEGER,death_game_minute INTEGER,cause_of_death TEXT,updated_at REAL);
CREATE TABLE npc_social_relations(npc_a TEXT,npc_b TEXT,affinity INTEGER,trust INTEGER,grudge INTEGER,relation_type TEXT,status TEXT,started_game_minute INTEGER,last_interaction_game_minute INTEGER,updated_at REAL,PRIMARY KEY(npc_a,npc_b));
INSERT INTO world_simulation_state VALUES('npc_life',0,10080,0,0);
INSERT INTO npc_civilization_state VALUES('A','Greenriver Town',1,'alive','Cultivating',0,0);
INSERT INTO npc_civilization_state VALUES('B','Greenriver Town',1,'alive','Cultivating',0,0);
INSERT INTO npc_life_state VALUES('A',0,20,80,100,'',0,0,'single','',0,0,0,NULL,'',0);
INSERT INTO npc_life_state VALUES('B',0,20,80,100,'',0,0,'single','',0,0,0,NULL,'',0);
`)
	runner, _ := NewRunner(path, "")
	var gm int64
	for candidate := int64(1); candidate < 10000; candidate++ {
		if hash64("A", "B", fmt.Sprint(candidate))%100 < 35 {
			gm = candidate
			break
		}
	}
	if gm == 0 {
		t.Fatal("could not find deterministic marriage minute")
	}
	if _, err := runner.Force(ForceRequest{System: "npc_life", Steps: 120, GameMinute: gm}); err != nil {
		t.Fatal(err)
	}
	if got := simScalar(t, path, "SELECT spouse_name FROM npc_life_state WHERE npc_name='A'"); got != "B" {
		t.Fatalf("A spouse=%v", got)
	}
	if got := simScalar(t, path, "SELECT relation_type FROM npc_social_relations WHERE npc_a='A' AND npc_b='B'"); got != "marriage" {
		t.Fatalf("relation=%v", got)
	}
}

func TestEconomyBatchRepricesAllListingsAndCommitsAnchor(t *testing.T) {
	path := setupSimulationDB(t, `
CREATE TABLE world_simulation_state(system TEXT PRIMARY KEY,last_game_minute INTEGER,interval_game_minutes INTEGER,last_run_real REAL,runs INTEGER);
CREATE TABLE civilization_regions(location TEXT PRIMARY KEY,prosperity INTEGER,spirit_resources INTEGER);
CREATE TABLE economy_markets(location TEXT,item_id TEXT,supply INTEGER,demand INTEGER,price_index REAL,last_game_minute INTEGER,updated_at REAL,PRIMARY KEY(location,item_id));
INSERT INTO world_simulation_state VALUES('dynamic_economy',0,1440,0,0);
INSERT INTO civilization_regions VALUES('Greenriver Town',80,80);
INSERT INTO economy_markets VALUES('Greenriver Town','herb',10,50,1.0,0,0);
INSERT INTO economy_markets VALUES('Greenriver Town','ore',20,40,1.0,0,0);
`)
	runner, _ := NewRunner(path, "")
	if _, err := runner.Force(ForceRequest{System: "dynamic_economy", Steps: 4, GameMinute: 1440}); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT COUNT(*) FROM economy_markets WHERE last_game_minute=1440")); got != 2 {
		t.Fatalf("updated listings=%d", got)
	}
	if got := storage.ParseInt(simScalar(t, path, "SELECT runs FROM world_simulation_state WHERE system='dynamic_economy'")); got != 4 {
		t.Fatalf("runs=%d", got)
	}
}
