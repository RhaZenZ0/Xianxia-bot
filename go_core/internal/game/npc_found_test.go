package game

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
)

// Finding somebody, or finding where they stopped (v1.0.0-rc.24, schema 48).
// None of this is a die: the action reads a location and compares it, so every
// assertion here holds on every run.

const graveSchema = `
CREATE TABLE characters(user_id INTEGER PRIMARY KEY,name TEXT NOT NULL DEFAULT '',spirit_stones INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE inventory(user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,item_id));
CREATE TABLE npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,status TEXT,missing_since_game_minute INTEGER DEFAULT 0,updated_at REAL DEFAULT 0);
CREATE TABLE npc_graves(npc_name TEXT PRIMARY KEY,location TEXT NOT NULL,world_name TEXT NOT NULL DEFAULT '',home_location TEXT NOT NULL DEFAULT '',died_game_minute INTEGER NOT NULL DEFAULT 0,days_missing INTEGER NOT NULL DEFAULT 0,keepsake_item TEXT NOT NULL DEFAULT '',keepsake_stones INTEGER NOT NULL DEFAULT 0,claimed_by_user_id INTEGER,claimed_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE world_history_events(source_key TEXT PRIMARY KEY,event_type TEXT,title TEXT,summary TEXT,significance INTEGER,visibility TEXT,location TEXT,world_name TEXT,faction TEXT,actor_type TEXT,actor_key TEXT,actor_name TEXT,target_type TEXT,target_key TEXT,target_name TEXT,related_user_id INTEGER,related_npc_name TEXT,tags TEXT,game_minute INTEGER,metadata_json TEXT,created_at REAL,updated_at REAL);
INSERT INTO characters(user_id,name,spirit_stones) VALUES(7,'Searcher',100),(8,'Latecomer',100);
INSERT INTO npc_civilization_state VALUES('Lost Lu','Greenriver Town','Lonely Rock','dead',0,0);
INSERT INTO npc_graves(npc_name,location,world_name,home_location,died_game_minute,days_missing,keepsake_item,keepsake_stones,created_at,updated_at)
  VALUES('Lost Lu','Lonely Rock','Mortal World','Greenriver Town',5000,231,'spirit_herb',37,0,0);
`

func graveDB(t *testing.T) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(filepath.Join(t.TempDir(), "graves.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if err := conn.ExecScript(graveSchema); err != nil {
		t.Fatal(err)
	}
	return conn
}

func claim(t *testing.T, conn *storage.Conn, userID int64, npc, location string) map[string]any {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"npc_name": npc, "location": location, "game_minute": 9000})
	if err != nil {
		t.Fatal(err)
	}
	out, err := npcFound(conn, userID, raw)
	if err != nil {
		t.Fatal(err)
	}
	result, ok := out.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result shape %T", out)
	}
	return result
}

func graveScalar(t *testing.T, conn *storage.Conn, sql string, args ...any) any {
	t.Helper()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return nil
	}
	return res.Rows[0][0]
}

func TestAGraveIsOnlyFoundWhereItIs(t *testing.T) {
	conn := graveDB(t)
	got := claim(t, conn, 7, "Lost Lu", "Greenriver Town")
	if got["claimed"] == true {
		t.Fatal("a grave was claimed from the other side of the world")
	}
	if got["elsewhere"] != true {
		t.Fatalf("looking in the wrong place should say so: %v", got)
	}
	if q := storage.ParseInt(graveScalar(t, conn, `SELECT COUNT(*) FROM inventory WHERE user_id=7`)); q != 0 {
		t.Fatalf("the wrong place handed over %d item(s)", q)
	}
	if s := storage.ParseInt(graveScalar(t, conn, `SELECT spirit_stones FROM characters WHERE user_id=7`)); s != 100 {
		t.Fatalf("the wrong place handed over stones: %d", s)
	}
}

func TestAGraveHandsOverWhatTheyWereCarrying(t *testing.T) {
	conn := graveDB(t)
	got := claim(t, conn, 7, "Lost Lu", "Lonely Rock")
	if got["claimed"] != true {
		t.Fatalf("standing at the grave found nothing: %v", got)
	}
	if got["days_missing"] != int64(231) {
		t.Fatalf("the answer does not say how long: %v", got["days_missing"])
	}
	if got["home_location"] != "Greenriver Town" {
		t.Fatalf("the answer does not say where to take it: %v", got["home_location"])
	}
	if q := storage.ParseInt(graveScalar(t, conn, `SELECT quantity FROM inventory WHERE user_id=7 AND item_id='spirit_herb'`)); q != 1 {
		t.Fatalf("the keepsake did not change hands: quantity %d", q)
	}
	if s := storage.ParseInt(graveScalar(t, conn, `SELECT spirit_stones FROM characters WHERE user_id=7`)); s != 137 {
		t.Fatalf("their purse did not change hands: %d", s)
	}
	if got := storage.ParseInt(graveScalar(t, conn, `SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_grave_found'`)); got != 1 {
		t.Fatalf("nobody recorded what became of them: %d", got)
	}
}

func TestOnlyTheFirstSearcherTakesAnything(t *testing.T) {
	conn := graveDB(t)
	if got := claim(t, conn, 7, "Lost Lu", "Lonely Rock"); got["claimed"] != true {
		t.Fatalf("the first searcher found nothing: %v", got)
	}
	second := claim(t, conn, 8, "Lost Lu", "Lonely Rock")
	if second["claimed"] == true {
		t.Fatal("the same grave was emptied twice")
	}
	if second["already_claimed"] != true {
		t.Fatalf("a visited grave should say it has been visited: %v", second)
	}
	// The grave still says what it says; it simply has nothing left to give.
	if second["grave"] != true {
		t.Fatalf("a claimed grave stopped being a grave: %v", second)
	}
	if q := storage.ParseInt(graveScalar(t, conn, `SELECT COUNT(*) FROM inventory WHERE user_id=8`)); q != 0 {
		t.Fatalf("the second searcher took %d item(s)", q)
	}
	if s := storage.ParseInt(graveScalar(t, conn, `SELECT spirit_stones FROM characters WHERE user_id=8`)); s != 100 {
		t.Fatalf("the second searcher took stones: %d", s)
	}
}

func TestSomebodyWhoWasNeverMissingHasNoGrave(t *testing.T) {
	conn := graveDB(t)
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state VALUES('Ordinary Ou','Greenriver Town','Greenriver Town','alive',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	got := claim(t, conn, 7, "Ordinary Ou", "Greenriver Town")
	if got["found"] != false || got["was_missing"] != false {
		t.Fatalf("somebody standing in front of you was reported as found: %v", got)
	}
	if got["grave"] == true {
		t.Fatal("a living NPC was given a grave")
	}
}
