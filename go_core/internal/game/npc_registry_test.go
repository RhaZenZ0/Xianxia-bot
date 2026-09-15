package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// People this world made for itself (v1.0.0-rc.27, schema 49). Nothing here
// rolls a die - the prose is picked by a hash of the name on purpose - so
// every assertion is exact.

const registrySchema = `
CREATE TABLE npc_registry(name TEXT PRIMARY KEY,origin TEXT NOT NULL DEFAULT 'gm',role TEXT NOT NULL DEFAULT '',realm TEXT NOT NULL DEFAULT '',personality TEXT NOT NULL DEFAULT '',speech TEXT NOT NULL DEFAULT '',want TEXT NOT NULL DEFAULT '',fear TEXT NOT NULL DEFAULT '',secret TEXT NOT NULL DEFAULT '',location TEXT NOT NULL DEFAULT '',sect_affiliation TEXT NOT NULL DEFAULT '',source_key TEXT NOT NULL DEFAULT '',created_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE catalog_npcs(name TEXT PRIMARY KEY,data_json TEXT NOT NULL,updated_at REAL NOT NULL);
`

func registryConn(t *testing.T) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(t.TempDir() + "/registry.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	if err := conn.ExecScript(registrySchema); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return conn
}

func registryString(t *testing.T, conn *storage.Conn, sql string, args ...any) string {
	t.Helper()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		return ""
	}
	return fmt.Sprint(res.Rows[0][0])
}

func TestAPersonIsWrittenOnce(t *testing.T) {
	conn := registryConn(t)
	person := RegisteredNPC{Name: "Xie Ruolan", Origin: NPCOriginBirthFamily, Role: "Elder Sister", Location: "birth_family:3"}
	first, err := RegisterNPCTx(conn, person, 100)
	if err != nil || !first {
		t.Fatalf("the first registration did not take: %v %v", first, err)
	}
	// The callers are re-entrant by nature: a household is ensured on every
	// creation that picks it, and maturation runs every tick. A second call
	// must not rewrite who somebody is.
	person.Role = "Something Else Entirely"
	again, err := RegisterNPCTx(conn, person, 200)
	if err != nil {
		t.Fatal(err)
	}
	if again {
		t.Fatal("the same person was registered twice")
	}
	if got := registryString(t, conn, `SELECT role FROM npc_registry WHERE name='Xie Ruolan'`); got != "Elder Sister" {
		t.Fatalf("a second registration rewrote who they are: %q", got)
	}
}

// The catalogue always wins. Two different people answering to one name is
// worse than a birth refused, and `/talk` resolves the catalogue first - so a
// registry row under a catalogue name would be permanently unreachable and
// would quietly shadow nothing.
func TestTheContentFileKeepsItsNames(t *testing.T) {
	conn := registryConn(t)
	if _, err := conn.Execute(`INSERT INTO catalog_npcs(name,data_json,updated_at) VALUES('Elder Su Yan','{}',0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	took, err := RegisterNPCTx(conn, RegisteredNPC{Name: "Elder Su Yan", Origin: NPCOriginDescendant}, 100)
	if err != nil {
		t.Fatal(err)
	}
	if took {
		t.Fatal("a name the content file already carries was taken by the registry")
	}
	if got := registryString(t, conn, `SELECT COUNT(*) FROM npc_registry`); got != "0" {
		t.Fatalf("%s row(s) written", got)
	}
}

func TestAnUnknownOriginIsRefused(t *testing.T) {
	conn := registryConn(t)
	_, err := RegisterNPCTx(conn, RegisteredNPC{Name: "Nobody", Origin: "spontaneous"}, 100)
	if err == nil {
		t.Fatal("an origin nothing knows how to age or explain was accepted")
	}
}

func TestPeopleWithNoNameAreNotPeople(t *testing.T) {
	conn := registryConn(t)
	took, err := RegisterNPCTx(conn, RegisteredNPC{Name: "   ", Origin: NPCOriginGM}, 100)
	if err != nil || took {
		t.Fatalf("a blank name was registered: %v %v", took, err)
	}
}

func TestTheSameWorldMakesTheSamePersonTwice(t *testing.T) {
	traits := worlddata.GeneratedTraits{
		Role:        []string{"a", "b", "c"},
		Personality: []string{"p1", "p2", "p3", "p4"},
		Speech:      []string{"s1", "s2"},
		Want:        []string{"w1", "w2", "w3"},
		Fear:        []string{"f1", "f2", "f3", "f4", "f5"},
	}
	first := GenerateNPCTraits(traits, "Bao Lin")
	second := GenerateNPCTraits(traits, "Bao Lin")
	if first != second {
		t.Fatalf("the same name produced two different people:\n%+v\n%+v", first, second)
	}
	// Each field is drawn separately. One index across all five pools would
	// tie fear to personality forever and make the world's own people read as
	// a handful of fixed archetypes.
	differs := false
	for _, other := range []string{"Cui Ping", "Wen Shu", "Han Yi", "Mo Qing"} {
		if GenerateNPCTraits(traits, other) != first {
			differs = true
			break
		}
	}
	if !differs {
		t.Fatal("every name produced the same person")
	}
}

// An empty pool yields an empty string rather than a placeholder: a narrator
// handed "" says nothing about the trait, and one handed "unknown" says
// something false.
func TestAnEmptyPoolSaysNothingRatherThanSomethingFalse(t *testing.T) {
	got := GenerateNPCTraits(worlddata.GeneratedTraits{}, "Nobody At All")
	if got.Personality != "" || got.Speech != "" || got.Want != "" || got.Fear != "" || got.Role != "" {
		t.Fatalf("an empty roster invented prose: %+v", got)
	}
}

func TestTheShippedTraitPoolsAreRealProse(t *testing.T) {
	catalog, err := worlddata.Load("../../../content/world.json")
	if err != nil {
		t.Fatal(err)
	}
	traits := catalog.GeneratedTraits
	for name, pool := range map[string][]string{
		"role": traits.Role, "personality": traits.Personality,
		"speech": traits.Speech, "want": traits.Want, "fear": traits.Fear,
	} {
		if len(pool) < 5 {
			t.Fatalf("the %s pool has %d entries; a world's own people would all read alike", name, len(pool))
		}
		seen := map[string]bool{}
		for _, line := range pool {
			if len(line) < 8 {
				t.Fatalf("the %s pool carries %q, which is not prose", name, line)
			}
			if seen[line] {
				t.Fatalf("the %s pool repeats %q, so that trait is twice as likely as the rest", name, line)
			}
			seen[line] = true
		}
	}
}
