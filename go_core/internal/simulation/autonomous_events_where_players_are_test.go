package simulation

import (
	"path/filepath"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The world's own events land where the players are (v1.18.0). The daily
// autonomous event picked one place uniformly from every non-interior
// location in all four worlds, so on a server whose cultivators were all in
// the Mortal World three events in four manifested where no player could go.
// The batch draws only from the worlds a living cultivator has set foot in,
// and from every world when nobody living knows any place at all.

const livingWorldsSchema = `
CREATE TABLE character_location_discoveries(user_id INTEGER NOT NULL, location TEXT NOT NULL,
    discovery_kind TEXT NOT NULL DEFAULT 'exploration', discovered_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id, location),
    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
`

func livingWorldsRunner(path string) *Runner {
	return &Runner{DatabasePath: path, World: worlddata.Catalog{}, Catalog: Catalog{
		Locations: map[string]Location{
			"Greenriver Town":     {World: "Mortal World"},
			"Spirit Jade Capital": {World: "Spiritual World"},
		},
		NPCs: map[string]NPC{}, Sects: map[string]Sect{}, Items: map[string]Item{},
		UnexpectedEvents: []UnexpectedEvent{{ID: "festival", Kind: "world_event", Category: "festival", Title: "Lantern Night", Severity: 1, Weight: 1, DurationHours: 2}},
	}}
}

func livingWorldsDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "living.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(simulationClockSchema + maintenanceRotationSchema + livingWorldsSchema); err != nil {
		t.Fatal(err)
	}
	return path
}

func seedLivingCharacter(t *testing.T, path string, userID int64, location string, known ...string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO characters(user_id,discord_name,name,origin,path,spiritual_root,concept,location,attributes_json,created_at,updated_at)
		VALUES(?,?,?,?,?,?,?,?,'{}',0,0)`, []any{userID, "d", "n", "o", "Qi Refiner", "Mortal Root", "c", location}); err != nil {
		t.Fatal(err)
	}
	for _, place := range known {
		if _, err := conn.Execute(`INSERT INTO character_location_discoveries(user_id,location) VALUES(?,?)`, []any{userID, place}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

// spawnOnce runs the batch with the dice lent so an event always manifests and
// the first eligible candidate is taken, and answers where it landed.
func spawnOnce(t *testing.T, path string, r *Runner) (string, string) {
	t.Helper()
	restore := gamerng.UseRoller(func(n int) int { return 0 })
	defer restore()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	summary, events, err := r.autonomousWorldEvents(conn, 1, 5000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 {
		return summary, ""
	}
	return summary, events[0].Location
}

func TestAnAutonomousEventLandsInAWorldSomebodyLivingHasReached(t *testing.T) {
	// The dice take the first candidate, and the candidates come off a map,
	// so without the filter the Spiritual capital is picked about half the
	// time. Twenty runs of a one-place filter are twenty landings at home.
	for i := 0; i < 20; i++ {
		path := livingWorldsDB(t)
		seedLivingCharacter(t, path, 42, "birth_family:7", "Greenriver Town")
		summary, where := spawnOnce(t, path, livingWorldsRunner(path))
		if where != "Greenriver Town" {
			t.Fatalf("run %d: the event landed at %q (%s); the only living cultivator has never left the Mortal World", i, where, summary)
		}
	}
}

func TestAWorldACultivatorHasCrossedIntoIsInPlay(t *testing.T) {
	path := livingWorldsDB(t)
	seedLivingCharacter(t, path, 42, "Greenriver Town", "Greenriver Town", "Spirit Jade Capital")
	r := livingWorldsRunner(path)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	worlds := r.worldsWithLivingCharacters(conn)
	if !worlds["Mortal World"] || !worlds["Spiritual World"] || len(worlds) != 2 {
		t.Fatalf("a cultivator who knows a place in each world keeps both in play, got %v", worlds)
	}
}

func TestWithNobodyLivingEveryWorldIsEligible(t *testing.T) {
	path := livingWorldsDB(t)
	r := livingWorldsRunner(path)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if worlds := r.worldsWithLivingCharacters(conn); worlds != nil {
		t.Fatalf("no living character, yet a preference: %v", worlds)
	}
	conn.Close()
	summary, where := spawnOnce(t, path, r)
	if where == "" {
		t.Fatalf("nothing manifested on an empty server: %s", summary)
	}
}

func TestADeadCultivatorKeepsNoWorldInPlay(t *testing.T) {
	path := livingWorldsDB(t)
	seedLivingCharacter(t, path, 42, "Spirit Jade Capital", "Spirit Jade Capital")
	seedLivingCharacter(t, path, 43, "Greenriver Town", "Greenriver Town")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE characters SET life_status='dead' WHERE user_id=42`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	for i := 0; i < 20; i++ {
		_, where := spawnOnce(t, livingWorldsDBCopy(t, path), livingWorldsRunner(path))
		if where != "Greenriver Town" {
			t.Fatalf("run %d: the dead cultivator's world drew an event at %q", i, where)
		}
	}
}

// livingWorldsDBCopy is the same database again on a fresh file, so a run
// that spawned one event does not suppress the next as a duplicate.
func livingWorldsDBCopy(t *testing.T, path string) string {
	t.Helper()
	copyPath := livingWorldsDB(t)
	src, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer src.Close()
	rows, err := src.Execute(`SELECT user_id,location,life_status FROM characters`, nil)
	if err != nil {
		t.Fatal(err)
	}
	known, err := src.Execute(`SELECT user_id,location FROM character_location_discoveries`, nil)
	if err != nil {
		t.Fatal(err)
	}
	dst, err := storage.Open(copyPath)
	if err != nil {
		t.Fatal(err)
	}
	defer dst.Close()
	for _, row := range rows.Rows {
		if _, err := dst.Execute(`INSERT INTO characters(user_id,discord_name,name,origin,path,spiritual_root,concept,location,life_status,attributes_json,created_at,updated_at)
			VALUES(?,?,?,?,?,?,?,?,?,'{}',0,0)`, []any{row[0], "d", "n", "o", "Qi Refiner", "Mortal Root", "c", row[1], row[2]}); err != nil {
			t.Fatal(err)
		}
	}
	for _, row := range known.Rows {
		if _, err := dst.Execute(`INSERT INTO character_location_discoveries(user_id,location) VALUES(?,?)`, []any{row[0], row[1]}); err != nil {
			t.Fatal(err)
		}
	}
	if err := dst.Commit(); err != nil {
		t.Fatal(err)
	}
	return copyPath
}
