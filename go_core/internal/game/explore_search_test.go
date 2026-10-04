package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// An ordinary explore finds whoever went missing within range of where the
// explorer stands, and the grave of anybody who died there unreached (v1.22.1).
// None of it is a die: the search compares locations against a set read off
// the map, so every assertion holds every run. The places are the shipped
// catalogue's, because the range is a fact about that map: Greenriver Town's
// roads lead to Azure Crown Imperial City and Riverguard City, the Shrine of
// the Patient Ox and the Sunken Bell Ruin lie on its legs, Ashenwall City is
// two roads off and Ironbanner City further still.
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
        ('Gate Watcher Ren','Moonfen City','Greenriver Town East Gate','Mortal World','Watchman','missing',100,0),
        ('Shrine Pilgrim An','Moonfen City','Shrine of the Patient Ox','Mortal World','Pilgrim','missing',100,0),
        ('Neighbour Lin','Moonfen City','Riverguard City West Gate','Mortal World','Clerk','missing',100,0),
        ('Two Roads Gao','Greenriver Town','Ashenwall City','Mortal World','Porter','missing',100,0),
        ('Far Porter Wu','Greenriver Town','Ironbanner City','Mortal World','Porter','missing',100,0),
        ('Home Smith Bo','Greenriver Town','Greenriver Town','Mortal World','Smith','alive',0,0);
INSERT INTO npc_graves(npc_name,location,world_name,home_location,died_game_minute,days_missing,keepsake_item,keepsake_stones,created_at,updated_at)
  VALUES('Buried Lu','Greenriver Town','Mortal World','Moonfen City',500,64,'spirit_herb',7,0,0),
        ('Roadside Grave Hu','Sunken Bell Ruin','Mortal World','Greenriver Town',500,66,'',3,0,0),
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

func TestAnExploreFindsTheMissingWithinRange(t *testing.T) {
	path, world := exploreSearchWorld(t)
	result := exploreHere(t, path, world, 1)
	want := "[Gate Watcher Ren Lost Herbalist Mei Neighbour Lin Shrine Pilgrim An]"
	if got := fmt.Sprint(names(t, result["found_npcs"])); got != want {
		t.Fatalf("exploring Greenriver Town found %s; want %s - the town, its gate, a shrine on its road and a neighbouring city's gate", got, want)
	}
	if got := exploreSearchScalar(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name='Lost Herbalist Mei'`); got != "alive" {
		t.Fatalf("the find answered and the herbalist is still %q on a fresh read", got)
	}
	for name, where := range map[string]string{"Two Roads Gao": "Ashenwall City, two roads off", "Far Porter Wu": "Ironbanner City"} {
		if got := exploreSearchScalar(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name=?`, name); got != "missing" {
			t.Fatalf("an explore at Greenriver Town found %s at %s, out of range (now %q)", name, where, got)
		}
	}
	if got := exploreSearchScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_found' AND related_user_id=42`); got != "4" {
		t.Fatalf("the finds left %s npc_found history row(s), want 4", got)
	}
	if got := exploreSearchScalar(t, path, `SELECT location FROM world_history_events WHERE event_type='npc_found' AND related_npc_name='Neighbour Lin'`); got != "Riverguard City West Gate" {
		t.Fatalf("the history places the find at %q; it records where the person was, not where the explorer stood", got)
	}
	// A second explore finds nobody: the herbalist is no longer missing.
	if got := names(t, exploreHere(t, path, world, 2)["found_npcs"]); len(got) != 0 {
		t.Fatalf("a second explore found %v again", got)
	}
}

func TestAnExploreReachesAGraveWhereItIs(t *testing.T) {
	path, world := exploreSearchWorld(t)
	result := exploreHere(t, path, world, 1)
	if got := fmt.Sprint(names(t, result["found_graves"])); got != "[Buried Lu Roadside Grave Hu]" {
		t.Fatalf("exploring Greenriver Town reached graves %s; want the one in the town and the one at the ruin on its road", got)
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

func TestTheSearchAreaIsTheCityAndOneStepOut(t *testing.T) {
	catalog := districtCatalog(t)
	fromGate := exploreSearchArea(catalog, "Greenriver Town East Gate", 0)
	fromTown := exploreSearchArea(catalog, "Greenriver Town", 0)
	if fmt.Sprint(fromGate) != fmt.Sprint(fromTown) {
		t.Fatal("a search from a city's gate covers different ground from one in its street; a gate is its city")
	}
	in := map[string]bool{}
	for _, place := range fromTown {
		in[place] = true
	}
	for _, place := range []string{"Greenriver Town", "Greenriver Apothecary", "Shrine of the Patient Ox", "Sunken Bell Ruin", "Riverguard City", "Riverguard City West Gate", "Azure Crown Imperial City"} {
		if !in[place] {
			t.Fatalf("a search from Greenriver Town does not reach %s", place)
		}
	}
	for _, place := range []string{"Ashenwall City", "Ironbanner City", "Four-Roads Caravan City"} {
		if in[place] {
			t.Fatalf("a search from Greenriver Town reached %s, more than one step out", place)
		}
	}
	for _, place := range fromTown {
		if loc := catalog.Locations[place]; loc.Private || loc.World != "Mortal World" {
			t.Fatalf("a search from Greenriver Town covers %s, which is private or in another world", place)
		}
	}
	fromShrine := exploreSearchArea(catalog, "Shrine of the Patient Ox", 0)
	got := map[string]bool{}
	for _, place := range fromShrine {
		got[place] = true
	}
	if !got["Greenriver Town East Gate"] || !got["Riverguard City"] {
		t.Fatalf("a search from a road site does not reach both ends of its road: %v", fromShrine)
	}
}
