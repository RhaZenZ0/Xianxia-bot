package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/game"
	"xianxia/core/internal/storage"
)

// A siege the world fights on its own is fought from both walls (v1.24.0).
//
// advanceWars struck for one side a tick - the attacker, unless players had
// pushed the defender's force past it - and never moved the attacker's morale,
// so between two sects with nobody at a keyboard every war ended in a fall and
// `defender_holds` was unreachable. These drive the tick on the production
// columns, with no dice: the daily rolls are stablePercent, a hash.

const siegeExtraSchema = `
CREATE TABLE territory_war_actions(
    action_id INTEGER PRIMARY KEY AUTOINCREMENT, war_id INTEGER NOT NULL, user_id INTEGER, side TEXT NOT NULL, tactic TEXT NOT NULL,
    power INTEGER NOT NULL DEFAULT 0, siege_delta INTEGER NOT NULL DEFAULT 0, morale_delta INTEGER NOT NULL DEFAULT 0,
    game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE sect_relations(
    sect_a TEXT NOT NULL, sect_b TEXT NOT NULL, relation_score INTEGER NOT NULL DEFAULT 0, relation_type TEXT NOT NULL DEFAULT 'neutral',
    treaty_status TEXT NOT NULL DEFAULT 'none', updated_at REAL NOT NULL, PRIMARY KEY(sect_a,sect_b));
CREATE TABLE world_eras(
    era_id INTEGER PRIMARY KEY AUTOINCREMENT, world TEXT NOT NULL DEFAULT 'Mortal World', name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    started_game_minute INTEGER NOT NULL DEFAULT 0, ended_game_minute INTEGER, active INTEGER NOT NULL DEFAULT 1,
    modifiers_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL DEFAULT 0);
`

// siegeDB is two sects at war over one place, with the given politics and
// walls, the war opened through the one door.
func siegeDB(t *testing.T, attacker, defender [3]int64, defense int64) string {
	t.Helper()
	path := setupSimulationDB(t, sectWarSchema+siegeExtraSchema)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for _, s := range []struct {
		name string
		p    [3]int64
	}{{"Besieging Sect", attacker}, {"Holding Sect", defender}} {
		if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,influence,resources,cohesion) VALUES(?,?,?,?)`, []any{s.name, s.p[0], s.p[1], s.p[2]}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := conn.Execute(`INSERT INTO sect_relations(sect_a,sect_b,relation_score,updated_at) VALUES('Holding Sect','Besieging Sect',0,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO territory_state(territory_key,name,controller_type,controller_key,defense) VALUES('the_ford','The Ford','sect','Holding Sect',?)`, []any{defense}); err != nil {
		t.Fatal(err)
	}
	if _, err := game.DeclareWarTx(conn, (&Runner{}).World, "Besieging Sect", "Holding Sect", "the_ford", 0, 0); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

// fightOut ticks the war a week at a time until it ends or a year passes, and
// returns its resolution.
func fightOut(t *testing.T, path string) string {
	t.Helper()
	r := &Runner{}
	for week := int64(1); week <= 52; week++ {
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := r.advanceWars(conn, week*7*minutesPerDay); err != nil {
			conn.Close()
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			conn.Close()
			t.Fatal(err)
		}
		conn.Close()
		if fmt.Sprint(simScalar(t, path, `SELECT status FROM territory_wars`)) == "resolved" {
			return fmt.Sprint(simScalar(t, path, `SELECT resolution FROM territory_war_operations`))
		}
	}
	return "unresolved"
}

func TestAStrongDefenderHoldsASiegeNobodyFightsFor(t *testing.T) {
	path := siegeDB(t, [3]int64{62, 55, 40}, [3]int64{85, 80, 80}, 70)
	if got := fightOut(t, path); got != "defender_holds" {
		t.Fatalf("a strong sect behind high walls ended the siege %q; the tick must be able to lose it for the attacker", got)
	}
	if got := fmt.Sprint(simScalar(t, path, `SELECT controller_key FROM territory_state`)); got != "Holding Sect" {
		t.Fatalf("a held siege changed the banner to %q", got)
	}
	if d := i64(simScalar(t, path, `SELECT defense FROM territory_state`)); d <= 70 {
		t.Fatalf("a held siege left the walls at %d; holding raises them", d)
	}
	// And the attacker is now bound by the truce.
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if game.WarTruceUntilTx(conn, (&Runner{}).World, "Besieging Sect", "the_ford", i64(simScalar(t, path, `SELECT updated_game_minute FROM territory_wars`))) == 0 {
		t.Fatal("a sect thrown back from a siege is under no truce")
	}
}

func TestAStrongAttackerTakesWeaklyHeldGround(t *testing.T) {
	path := siegeDB(t, [3]int64{90, 85, 80}, [3]int64{20, 20, 40}, 30)
	if got := fightOut(t, path); got != "attacker_occupation" {
		t.Fatalf("a strong sect against weak walls ended the siege %q", got)
	}
	if got := fmt.Sprint(simScalar(t, path, `SELECT controller_key FROM territory_state`)); got != "Besieging Sect" {
		t.Fatalf("the fallen ground is held by %q", got)
	}
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='territory_war_resolved'`)); n != 1 {
		t.Fatalf("the world heard %d war ending(s)", n)
	}
	// Both walls fought: the blow-by-blow carries the defender too.
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM territory_war_actions WHERE side='defender'`)); n == 0 {
		t.Fatal("the tick fought for the attacker alone")
	}
	// Declaring and ending each cost the two sects standing.
	if s := i64(simScalar(t, path, `SELECT relation_score FROM sect_relations`)); s >= 0 {
		t.Fatalf("a war left the two sects at standing %d", s)
	}
}
