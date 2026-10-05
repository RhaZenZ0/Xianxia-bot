package game

import (
	"fmt"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
)

const historyWorldDDL = `
CREATE TABLE world_history_events (
	history_id INTEGER PRIMARY KEY AUTOINCREMENT,
	source_key TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
	significance INTEGER NOT NULL DEFAULT 50, visibility TEXT NOT NULL DEFAULT 'public',
	location TEXT NOT NULL DEFAULT '', world_name TEXT NOT NULL DEFAULT '', faction TEXT NOT NULL DEFAULT '',
	actor_type TEXT NOT NULL DEFAULT '', actor_key TEXT NOT NULL DEFAULT '', actor_name TEXT NOT NULL DEFAULT '',
	target_type TEXT NOT NULL DEFAULT '', target_key TEXT NOT NULL DEFAULT '', target_name TEXT NOT NULL DEFAULT '',
	related_user_id INTEGER, related_npc_name TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
	game_minute INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
	created_at REAL NOT NULL, updated_at REAL NOT NULL
);`

const contentLocationsDDL = `
CREATE TABLE content_locations (
	name TEXT PRIMARY KEY, world TEXT, outside_location TEXT, settlement_type TEXT, district TEXT,
	road_site TEXT, shop TEXT, auction_house TEXT, safe_zone INTEGER, private INTEGER, min_realm_index INTEGER,
	data_json TEXT NOT NULL, updated_at REAL NOT NULL
);
INSERT INTO content_locations(name,world,data_json,updated_at) VALUES('Greenriver Town','Mortal World','{}',0);
INSERT INTO content_locations(name,world,data_json,updated_at) VALUES('Mandate Crown Celestial City','Celestial World','{}',0);`

func historyWorldsAfter(t *testing.T, withContent bool, places ...string) map[string]string {
	t.Helper()
	conn, err := storage.Open(filepath.Join(t.TempDir(), "history.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	ddl := historyWorldDDL
	if withContent {
		ddl += contentLocationsDDL
	}
	if err := conn.ExecScript(ddl); err != nil {
		t.Fatal(err)
	}
	for i, place := range places {
		if err := recordWorldHistoryTx(conn, fmt.Sprintf("k%d", i), "test", "t", "s", 50, "public", place, "", "", "", "", "", "", "", nil, "", nil, 1, nil, 1); err != nil {
			t.Fatal(err)
		}
	}
	res, err := conn.Execute(`SELECT location,world_name FROM world_history_events`, nil)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, row := range res.Rows {
		out[fmt.Sprint(row[0])] = fmt.Sprint(row[1])
	}
	return out
}

// v1.31.0: the engine's history door writes the world its place stands in,
// which the narrator's recall now reads; a place no world carries is left
// empty, and a database with no content table records the row anyway.
func TestAHistoryRowNamesItsWorld(t *testing.T) {
	got := historyWorldsAfter(t, true, "Greenriver Town", "Mandate Crown Celestial City", "birth_family:3")
	if got["Greenriver Town"] != "Mortal World" || got["Mandate Crown Celestial City"] != "Celestial World" {
		t.Fatalf("a history row does not name the world its place stands in: %v", got)
	}
	if got["birth_family:3"] != "" {
		t.Fatalf("a household's row was filed under %q; a place no world carries is in no world's news", got["birth_family:3"])
	}
	if bare := historyWorldsAfter(t, false, "Greenriver Town"); bare["Greenriver Town"] != "" {
		t.Fatalf("with no content table the world was %q", bare["Greenriver Town"])
	}
}
