package simulation

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// Children who grow up (v1.0.0-rc.27, schema 49). No die is rolled anywhere in
// this step - who comes of age is entirely a function of when they were born -
// so every assertion below is exact.

const maturationSchema = `
CREATE TABLE npc_registry(name TEXT PRIMARY KEY,origin TEXT NOT NULL DEFAULT 'gm',role TEXT NOT NULL DEFAULT '',realm TEXT NOT NULL DEFAULT '',personality TEXT NOT NULL DEFAULT '',speech TEXT NOT NULL DEFAULT '',want TEXT NOT NULL DEFAULT '',fear TEXT NOT NULL DEFAULT '',secret TEXT NOT NULL DEFAULT '',location TEXT NOT NULL DEFAULT '',sect_affiliation TEXT NOT NULL DEFAULT '',source_key TEXT NOT NULL DEFAULT '',created_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
`

func maturationDB(t *testing.T) string {
	t.Helper()
	path := romanceDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(maturationSchema); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func bornTo(t *testing.T, path, child, parent string, birth int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO npc_descendants
        (child_name,parent_a,parent_b,birth_game_minute,gender,spiritual_root,realm_index,phase,status,generated_as_npc,created_at,updated_at)
        VALUES(?,?,'Cui Ping',?,'neutral','Mortal Root',0,1,'alive',0,0,0)`,
		[]any{child, parent, birth}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func maturationRunner() *Runner {
	r := romanceRunner()
	r.World.GeneratedTraits.Role = []string{"Household hand"}
	r.World.GeneratedTraits.Personality = []string{"Quiet, and slower to speak than to decide."}
	r.World.GeneratedTraits.Speech = []string{"Short sentences."}
	r.World.GeneratedTraits.Want = []string{"To be counted as an adult."}
	r.World.GeneratedTraits.Fear = []string{"Being kept here, politely, forever."}
	return r
}

func TestAChildComesOfAgeAndBecomesSomebody(t *testing.T) {
	path := maturationDB(t)
	r := maturationRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 40, "married")
	born := int64(1000)
	bornTo(t, path, "Bao Xiu", "Bao Lin", born)
	conn := livesConn(t, path)

	// A day short of eighteen: still a child, however many ticks run.
	early := born + maturityYears*minutesPerYear - minutesPerDay
	for tick := 0; tick < 5; tick++ {
		got, err := r.npcMaturation(conn, early)
		if err != nil {
			t.Fatal(err)
		}
		if got != 0 {
			t.Fatalf("a child came of age %d day(s) early", 1)
		}
	}
	romanceCommit(t, conn)

	grown, err := r.npcMaturation(conn, born+maturityYears*minutesPerYear)
	if err != nil {
		t.Fatal(err)
	}
	if grown != 1 {
		t.Fatalf("%d came of age", grown)
	}
	romanceCommit(t, conn)

	// The flag that has existed since the life cycle was written and that
	// nothing has ever set finally means something.
	if got := romanceStr(t, path, `SELECT generated_as_npc FROM npc_descendants WHERE child_name='Bao Xiu'`); got != "1" {
		t.Fatalf("generated_as_npc is %q", got)
	}
	// Somebody /talk can find.
	if got := romanceStr(t, path, `SELECT origin FROM npc_registry WHERE name='Bao Xiu'`); got != "descendant" {
		t.Fatalf("registry origin is %q", got)
	}
	if got := romanceStr(t, path, `SELECT personality FROM npc_registry WHERE name='Bao Xiu'`); got == "" {
		t.Fatal("a grown person was registered with nothing a narrator can speak with")
	}
	// And somebody every batch that reads status='alive' will now offer.
	if got := romanceStr(t, path, `SELECT status FROM npc_civilization_state WHERE npc_name='Bao Xiu'`); got != "alive" {
		t.Fatalf("civilization status is %q", got)
	}
	// They grew up where their household is, not where the tick happened to
	// find them.
	if got := romanceStr(t, path, `SELECT home_location FROM npc_civilization_state WHERE npc_name='Bao Xiu'`); got != "Riverguard City" {
		t.Fatalf("grew up at %q", got)
	}
	// age_at_creation_years is 0 and birth_game_minute is real, so AgeYears
	// gives an age that keeps moving rather than eighteen forever.
	if got := romanceStr(t, path, `SELECT age_at_creation_years FROM npc_life_state WHERE npc_name='Bao Xiu'`); got != "0" {
		t.Fatalf("age_at_creation_years is %q; their age would be frozen", got)
	}
	if got := romanceStr(t, path, `SELECT birth_game_minute FROM npc_life_state WHERE npc_name='Bao Xiu'`); got != fmt.Sprint(born) {
		t.Fatalf("birth_game_minute is %q, expected %d", got, born)
	}
}

func TestComingOfAgeHappensOnlyOnce(t *testing.T) {
	path := maturationDB(t)
	r := maturationRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 40, "married")
	born := int64(1000)
	bornTo(t, path, "Bao Xiu", "Bao Lin", born)
	conn := livesConn(t, path)
	gm := born + maturityYears*minutesPerYear
	if got, err := r.npcMaturation(conn, gm); err != nil || got != 1 {
		t.Fatalf("setup: %d %v", got, err)
	}
	romanceCommit(t, conn)
	for tick := 0; tick < 5; tick++ {
		got, err := r.npcMaturation(conn, gm+int64(tick)*minutesPerDay)
		if err != nil {
			t.Fatal(err)
		}
		if got != 0 {
			t.Fatalf("the same child came of age %d more time(s)", got)
		}
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT COUNT(*) FROM npc_registry WHERE name='Bao Xiu'`); got != "1" {
		t.Fatalf("%s registry rows", got)
	}
}

// A child whose parents are both gone has no household to grow up in. Leaving
// the row alone is right: a later tick may find a parent again, and inventing
// a location would put a stranger in an arbitrary town.
func TestAnOrphanIsLeftForALaterTickRatherThanPlacedAnywhere(t *testing.T) {
	path := maturationDB(t)
	r := maturationRunner()
	born := int64(1000)
	bornTo(t, path, "Nobody's Child", "A Parent Who Does Not Exist", born)
	conn := livesConn(t, path)
	got, err := r.npcMaturation(conn, born+maturityYears*minutesPerYear)
	if err != nil {
		t.Fatal(err)
	}
	if got != 0 {
		t.Fatalf("%d orphan(s) were placed somewhere arbitrary", got)
	}
	romanceCommit(t, conn)
	if s := romanceStr(t, path, `SELECT generated_as_npc FROM npc_descendants WHERE child_name="Nobody's Child"`); s != "0" {
		t.Fatalf("the orphan was marked done and can never grow up: %q", s)
	}
}

func TestTheWorldDoesNotProduceAGenerationOvernight(t *testing.T) {
	path := maturationDB(t)
	r := maturationRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 40, "married")
	born := int64(1000)
	for i := 0; i < 20; i++ {
		bornTo(t, path, fmt.Sprintf("Bao %02d", i), "Bao Lin", born)
	}
	conn := livesConn(t, path)
	got, err := r.npcMaturation(conn, born+maturityYears*minutesPerYear)
	if err != nil {
		t.Fatal(err)
	}
	if got != maturityCap {
		t.Fatalf("one tick grew %d people against a cap of %d", got, maturityCap)
	}
}

// Coming of age must never be able to reach the Quest Forge: it is a thing a
// town notices, not a thing it needs a cultivator sent about.
func TestGrowingUpIsNotAQuest(t *testing.T) {
	path := maturationDB(t)
	r := maturationRunner()
	romanceNPC(t, path, "Bao Lin", "Riverguard City", 0, 40, "married")
	born := int64(1000)
	bornTo(t, path, "Bao Xiu", "Bao Lin", born)
	conn := livesConn(t, path)
	if _, err := r.npcMaturation(conn, born+maturityYears*minutesPerYear); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	high := storage.ParseInt(simScalar(t, path,
		`SELECT COUNT(*) FROM world_history_events WHERE event_type='npc_coming_of_age' AND significance>=80`))
	if high != 0 {
		t.Fatalf("%d coming-of-age row(s) can reach the Quest Forge", high)
	}
}

// A realm index out of range names no realm and panics on nothing, at any word
// size. The first version narrowed the index to `int` before the bound check,
// which on a 32-bit build lets a stored value above 2^31 wrap small, pass the
// test, and then panic when the slice is indexed with the full 64-bit value.
func TestARealmIndexOutOfRangeNamesNothingRatherThanPanicking(t *testing.T) {
	r := maturationRunner()
	for _, index := range []int64{-1, int64(len(r.World.Realms)), 1 << 31, 1 << 40, 1<<63 - 1} {
		t.Run(fmt.Sprint(index), func(t *testing.T) {
			if got := r.realmName(index); got != "Mortal" {
				t.Fatalf("realm index %d named %q", index, got)
			}
		})
	}
	// And a real one still answers, so the guard did not simply refuse
	// everything.
	if len(r.World.Realms) > 0 && r.World.Realms[0].Name != "" {
		if got := r.realmName(0); got != r.World.Realms[0].Name {
			t.Fatalf("realm 0 named %q, expected %q", got, r.World.Realms[0].Name)
		}
	}
}
