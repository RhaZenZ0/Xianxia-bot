package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// Sects that want a thing go and take it (v1.0.0-rc.15).
//
// `territory_wars` had one writer in the codebase and it always made the
// acting player's sect the attacker, so the siege resolver beside it - scores,
// morale, occupations, the era's war-pressure modifier - only ever ran on wars
// a player started. Thirteen sects and none of them could move on another.

const sectWarSchema = `
CREATE TABLE sect_politics_state(
    sect_name TEXT PRIMARY KEY, influence INTEGER NOT NULL DEFAULT 50, cohesion INTEGER NOT NULL DEFAULT 50,
    resources INTEGER NOT NULL DEFAULT 50, recruitment_pressure INTEGER NOT NULL DEFAULT 50,
    doctrine_pressure INTEGER NOT NULL DEFAULT 50, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE territory_state(
    territory_key TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '',
    controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '',
    resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50,
    defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0,
    updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE territory_wars(
    war_id INTEGER PRIMARY KEY AUTOINCREMENT, attacker_key TEXT NOT NULL, defender_key TEXT NOT NULL,
    territory_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    attacker_score INTEGER NOT NULL DEFAULT 0, defender_score INTEGER NOT NULL DEFAULT 0,
    created_game_minute INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE territory_war_operations(
    war_id INTEGER PRIMARY KEY, siege_progress INTEGER NOT NULL DEFAULT 0, attacker_morale INTEGER NOT NULL DEFAULT 100,
    defender_morale INTEGER NOT NULL DEFAULT 100, attacker_force INTEGER NOT NULL DEFAULT 0,
    defender_force INTEGER NOT NULL DEFAULT 0, last_tick_game_minute INTEGER NOT NULL DEFAULT 0,
    winner_key TEXT NOT NULL DEFAULT '', resolution TEXT NOT NULL DEFAULT '',
    occupation_until_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
CREATE TABLE world_history_events(
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, significance INTEGER NOT NULL, visibility TEXT NOT NULL,
    location TEXT NOT NULL, world_name TEXT NOT NULL, faction TEXT NOT NULL, actor_type TEXT NOT NULL,
    actor_key TEXT NOT NULL, actor_name TEXT NOT NULL, target_type TEXT NOT NULL, target_key TEXT NOT NULL,
    target_name TEXT NOT NULL, related_user_id INTEGER, related_npc_name TEXT NOT NULL, tags TEXT NOT NULL,
    game_minute INTEGER NOT NULL, metadata_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
`

func sectWarDB(t *testing.T) string {
	t.Helper()
	path := setupSimulationDB(t, sectWarSchema)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
INSERT INTO sect_politics_state(sect_name,influence,cohesion,resources) VALUES('Azure Reed Sect',80,60,70);
INSERT INTO sect_politics_state(sect_name,influence,cohesion,resources) VALUES('Quiet Fen Hall',20,60,20);
INSERT INTO territory_state(territory_key,controller_type,controller_key,defense) VALUES('reed_marches','sect','Quiet Fen Hall',30);
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func runWars(t *testing.T, path string, r *Runner, steps, gm int64) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	n, err := r.npcSectWars(conn, steps, gm)
	if err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()
	return n
}

func TestAnAmbitiousSectMovesOnAWeakNeighbour(t *testing.T) {
	path := sectWarDB(t)
	r := &Runner{}
	// One week, and the week the sect moves. Before this the test rolled the
	// real 12% chance up to sixty times and asserted that one of them landed,
	// which came up empty about one run in two thousand - a failure that says
	// nothing about the code and costs somebody an afternoon. The rules being
	// tested are who moves and on what, and neither is a matter of chance.
	defer gamerng.UseRoller(func(int) int { return 0 })()
	if declared := runWars(t, path, r, 1, 10000); declared != 1 {
		t.Fatalf("an ambitious sect beside a weakly-held border declared %d war(s)", declared)
	}
	if got := fmt.Sprint(simScalar(t, path, `SELECT attacker_key FROM territory_wars LIMIT 1`)); got != "Azure Reed Sect" {
		t.Fatalf("the weak sect declared instead: %q", got)
	}
	if got := fmt.Sprint(simScalar(t, path, `SELECT defender_key FROM territory_wars LIMIT 1`)); got != "Quiet Fen Hall" {
		t.Fatalf("defender=%q", got)
	}
	// The siege tick needs an operation row or the war it inherits cannot be
	// fought; a player's war gets one from `ensureWarOperationGo`.
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM territory_war_operations`)); n != 1 {
		t.Fatalf("operation rows=%d", n)
	}
	if n := i64(simScalar(t, path, `SELECT attacker_morale FROM territory_war_operations`)); n != 100 {
		t.Fatalf("a war that starts demoralised: %d", n)
	}
	// And the world hears about it.
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='territory_war'`)); n == 0 {
		t.Fatal("a war began in silence")
	}
}

func TestNobodyDeclaresOnGroundAlreadyContested(t *testing.T) {
	path := sectWarDB(t)
	r := &Runner{}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_at,updated_at) VALUES('Somebody Else','Quiet Fen Hall','reed_marches','active',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	for tick := 0; tick < 60; tick++ {
		runWars(t, path, r, 1, int64(10000+tick*10080))
	}
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM territory_wars`)); n != 1 {
		t.Fatalf("a second war opened on contested ground: %d", n)
	}
}

func TestAWellHeldBorderIsLeftAlone(t *testing.T) {
	path := sectWarDB(t)
	r := &Runner{}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	// Defended past the ceiling: not worth the war.
	if _, err := conn.Execute(`UPDATE territory_state SET defense=95 WHERE territory_key='reed_marches'`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	for tick := 0; tick < 60; tick++ {
		runWars(t, path, r, 1, int64(10000+tick*10080))
	}
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM territory_wars`)); n != 0 {
		t.Fatalf("a sect threw itself at a wall: %d wars", n)
	}
}
