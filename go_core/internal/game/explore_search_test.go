package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// An ordinary explore finds whoever went missing where the explorer stands,
// and the grave of anybody who died there unreached (v1.22.1). None of it is a
// die: the search compares two locations, so every assertion holds every run.
// It goes through ApplyWithWorld and reads back on a fresh connection, the
// shape `TestAFindThroughTheSwitchPathPersists` uses - a find that answered
// and never committed is the rc.38 fault, and only a fresh read can see it.

// The production DDL (schema 47 adds `missing_since_game_minute`, schema 48
// the graves), so the fixture refuses what production refuses.
const exploreSearchSchema = `
CREATE TABLE IF NOT EXISTS npc_civilization_state (
    npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL, current_location TEXT NOT NULL, world_name TEXT NOT NULL,
    profession TEXT NOT NULL, faction TEXT NOT NULL DEFAULT 'Independent', wealth INTEGER NOT NULL DEFAULT 20,
    influence INTEGER NOT NULL DEFAULT 10, ambition INTEGER NOT NULL DEFAULT 50, realm_index INTEGER NOT NULL DEFAULT 0,
    phase INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'alive', activity TEXT NOT NULL DEFAULT 'Following established routine',
    last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
    missing_since_game_minute INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS npc_graves (
    npc_name TEXT PRIMARY KEY, location TEXT NOT NULL, world_name TEXT NOT NULL DEFAULT '',
    home_location TEXT NOT NULL DEFAULT '', died_game_minute INTEGER NOT NULL DEFAULT 0,
    days_missing INTEGER NOT NULL DEFAULT 0, keepsake_item TEXT NOT NULL DEFAULT '',
    keepsake_stones INTEGER NOT NULL DEFAULT 0, claimed_by_user_id INTEGER, claimed_game_minute INTEGER,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS world_history_events (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, significance INTEGER NOT NULL DEFAULT 50,
    visibility TEXT NOT NULL DEFAULT 'public', location TEXT NOT NULL DEFAULT '', world_name TEXT NOT NULL DEFAULT '',
    faction TEXT NOT NULL DEFAULT '', actor_type TEXT NOT NULL DEFAULT '', actor_key TEXT NOT NULL DEFAULT '',
    actor_name TEXT NOT NULL DEFAULT '', target_type TEXT NOT NULL DEFAULT '', target_key TEXT NOT NULL DEFAULT '',
    target_name TEXT NOT NULL DEFAULT '', related_user_id INTEGER, related_npc_name TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', game_minute INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,status,missing_since_game_minute,updated_at)
  VALUES('Lost Herbalist Mei','Moonfen City','Greenriver Town','Mortal World','Herbalist','missing',100,0),
        ('Far Porter Wu','Greenriver Town','Ironbanner City','Mortal World','Porter','missing',100,0),
        ('Home Smith Bo','Greenriver Town','Greenriver Town','Mortal World','Smith','alive',0,0);
INSERT INTO npc_graves(npc_name,location,world_name,home_location,died_game_minute,days_missing,keepsake_item,keepsake_stones,created_at,updated_at)
  VALUES('Buried Lu','Greenriver Town','Mortal World','Moonfen City',500,64,'spirit_herb',7,0,0),
        ('Distant Grave Qi','Ironbanner City','Mortal World','Greenriver Town',500,70,'spirit_herb',9,0,0);
`

func exploreSearchWorld(t *testing.T) (string, string) {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(exploreSearchSchema); err != nil {
		t.Fatal(err)
	}
	return path, batch4WorldPath(t)
}

func exploreSearchScalar(t *testing.T, path, sql string, args ...any) string {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		return ""
	}
	return fmt.Sprint(res.Rows[0][0])
}

func exploreHere(t *testing.T, path, world string, seq int) map[string]any {
	t.Helper()
	clearCooldowns(t, path, 42)
	return batch4Result(t, batch4Apply(t, path, world, "exploration.explore", seq, map[string]any{
		"game_minute":                     20000 + int64(seq),
		"unexpected_events_enabled":       false,
		"unexpected_event_chance_percent": 0}))
}

func names(t *testing.T, v any) []string {
	t.Helper()
	rows, _ := v.([]map[string]any)
	out := []string{}
	for _, row := range rows {
		out = append(out, fmt.Sprint(row["npc_name"]))
	}
	return out
}

func TestAnExploreFindsTheMissingWhereTheyAre(t *testing.T) {
	path, world := exploreSearchWorld(t)
	result := exploreHere(t, path, world, 1)
	if got := names(t, result["found_npcs"]); len(got) != 1 || got[0] != "Lost Herbalist Mei" {
		t.Fatalf("exploring Greenriver Town found %v; want only the herbalist missing there", got)
	}
	if got := exploreSearchScalar(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name='Lost Herbalist Mei'`); got != "alive" {
		t.Fatalf("the find answered and the herbalist is still %q on a fresh read", got)
	}
	if got := exploreSearchScalar(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name='Far Porter Wu'`); got != "missing" {
		t.Fatalf("an explore at Greenriver Town found somebody missing at Ironbanner City (now %q)", got)
	}
	if got := exploreSearchScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_found' AND related_user_id=42`); got != "1" {
		t.Fatalf("the find left %s npc_found history row(s), want 1", got)
	}
	// A second explore finds nobody: the herbalist is no longer missing.
	if got := names(t, exploreHere(t, path, world, 2)["found_npcs"]); len(got) != 0 {
		t.Fatalf("a second explore found %v again", got)
	}
}

func TestAnExploreReachesAGraveWhereItIs(t *testing.T) {
	path, world := exploreSearchWorld(t)
	result := exploreHere(t, path, world, 1)
	if got := names(t, result["found_graves"]); len(got) != 1 || got[0] != "Buried Lu" {
		t.Fatalf("exploring Greenriver Town reached graves %v; want only the one there", got)
	}
	if got := exploreSearchScalar(t, path, `SELECT claimed_by_user_id FROM npc_graves WHERE npc_name='Buried Lu'`); got != "42" {
		t.Fatalf("the grave here was reached and is claimed by %q on a fresh read", got)
	}
	if got := exploreSearchScalar(t, path, `SELECT claimed_game_minute IS NULL FROM npc_graves WHERE npc_name='Distant Grave Qi'`); got != "1" {
		t.Fatal("an explore at Greenriver Town emptied a grave at Ironbanner City")
	}
	if got := exploreSearchScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`); got == "" || got == "0" {
		t.Fatal("the grave's keepsake did not reach the explorer's bag")
	}
	if got := names(t, exploreHere(t, path, world, 2)["found_graves"]); len(got) != 0 {
		t.Fatalf("a second explore emptied %v again", got)
	}
}
