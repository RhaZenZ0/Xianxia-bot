package simulation

import (
	"testing"

	"xianxia/core/internal/storage"
)

// The hunter's half of the trail (v1.0.0-rc.18).
//
// The formulas are pinned in the game package; this is the wiring, which is
// the part a later edit can quietly undo while every unit test still passes.

const trackingPursuitSchema = `
CREATE TABLE bounties(
    bounty_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, jurisdiction TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
    source_crime_id INTEGER, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE bounty_hunter_pursuits(
    pursuit_id INTEGER PRIMARY KEY AUTOINCREMENT, bounty_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    hunter_name TEXT NOT NULL, hunter_power INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'tracking',
    pressure INTEGER NOT NULL DEFAULT 0, escape_progress INTEGER NOT NULL DEFAULT 0,
    capture_progress INTEGER NOT NULL DEFAULT 0, next_action_game_minute INTEGER NOT NULL DEFAULT 0,
    created_game_minute INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE crime_records(crime_id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL DEFAULT 'open', updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE inventory(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,item_id));
CREATE TABLE equipment_instances(
    equipment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL,
    slot TEXT NOT NULL DEFAULT 'weapon', durability INTEGER NOT NULL DEFAULT 100, max_durability INTEGER NOT NULL DEFAULT 100,
    quality INTEGER NOT NULL DEFAULT 100, equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE item_provenance(
    provenance_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1, source_type TEXT NOT NULL DEFAULT 'unknown', source_key TEXT NOT NULL DEFAULT '',
    ownership_mark TEXT NOT NULL DEFAULT '', legal_status TEXT NOT NULL DEFAULT 'clean',
    authenticity INTEGER NOT NULL DEFAULT 100, tracking_strength INTEGER NOT NULL DEFAULT 0,
    acquired_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE world_eras(
    era_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    modifiers_json TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1,
    started_game_minute INTEGER NOT NULL DEFAULT 0, ends_game_minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
`

// pursued is one fugitive with a hunter on them. Both quarries in a comparison
// get the same bounty, the same hunter power and the same starting pressure,
// so the only thing that differs is what they are carrying.
func pursued(t *testing.T, path string, userID int64, mark int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO bounties(user_id,jurisdiction,amount,status,updated_at) VALUES(?,'Greenriver',500,'active',0)`, []any{userID}); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO bounty_hunter_pursuits(bounty_id,user_id,hunter_name,hunter_power,status,pressure,next_action_game_minute,updated_at)
        VALUES((SELECT MAX(bounty_id) FROM bounties),?,'Iron Badge Constable',6,'tracking',10,0,0)`, []any{userID}); err != nil {
		t.Fatal(err)
	}
	if mark > 0 {
		if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,'nine_echo_sword_tablet',1)`, []any{userID}); err != nil {
			t.Fatal(err)
		}
		if _, err := conn.Execute(`INSERT INTO item_provenance(user_id,item_id,quantity,source_type,tracking_strength) VALUES(?,'nine_echo_sword_tablet',1,'black_market',?)`,
			[]any{userID, mark}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestAMarkedQuarryIsFoundFaster(t *testing.T) {
	path := setupSimulationDB(t, trackingPursuitSchema)
	r := &Runner{}
	pursued(t, path, 42, 0)  // carrying nothing anyone can follow
	pursued(t, path, 43, 70) // wearing a hidden sect's brand

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.advanceHunters(conn, minutesPerDay); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()

	clean := storage.ParseInt(simScalar(t, path, `SELECT pressure FROM bounty_hunter_pursuits WHERE user_id=42`))
	marked := storage.ParseInt(simScalar(t, path, `SELECT pressure FROM bounty_hunter_pursuits WHERE user_id=43`))
	if clean <= 10 {
		t.Fatalf("the unmarked pursuit made no ground at all: pressure=%d", clean)
	}
	if marked <= clean {
		t.Fatalf("a branded relic made no difference: marked=%d clean=%d", marked, clean)
	}
}

func TestAnUnmarkedQuarryIsPursuedExactlyAsBefore(t *testing.T) {
	// The rule must cost nothing to a cultivator who has taken nothing: every
	// honest provenance writer passes zero, and zero has to keep meaning the
	// pursuit this repo already had.
	path := setupSimulationDB(t, trackingPursuitSchema)
	r := &Runner{}
	pursued(t, path, 42, 0)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.advanceHunters(conn, minutesPerDay); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	// One day on from a next_action of 0 is elapsed=2 ((1440-0)/1440+1), and
	// with hunter_power 6 and no era modifier that is 10 + 2*(8+6) = 38.
	if got := storage.ParseInt(simScalar(t, path, `SELECT pressure FROM bounty_hunter_pursuits WHERE user_id=42`)); got != 38 {
		t.Fatalf("pressure=%d, want the unchanged 38", got)
	}
}
