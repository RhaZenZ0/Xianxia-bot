package simulation

import (
	"fmt"
	"path/filepath"
	"testing"

	"xianxia/core/internal/game"
	"xianxia/core/internal/worlddata"
)

// A sect keeps its people (v1.24.0). No die is rolled anywhere in this step -
// names, realms and places are hashes of the sect, the rank and the slot - so
// every assertion is exact, and the step is driven against the shipped
// catalogue, because "every sect" means the sects the content file has.

func populationWorld(t *testing.T) (string, *Runner) {
	t.Helper()
	catalog, err := worlddata.Load(filepath.Join("..", "..", "..", "content", "world.json"))
	if err != nil {
		t.Fatalf("the content file is in the repository; the read is broken, not the tree: %v", err)
	}
	return maturationDB(t), &Runner{World: catalog}
}

func populatedSects(r *Runner) []string {
	var out []string
	for name := range r.World.Sects {
		if game.SectGate(r.World, name) != "" {
			out = append(out, name)
		}
	}
	return out
}

func TestEverySectIsFilledToItsHall(t *testing.T) {
	path, r := populationWorld(t)
	rule, ok := r.sectPopulationRule()
	if !ok {
		t.Fatal("sect_system.population is missing or unreadable; the gate is broken, not the tree")
	}
	sects := populatedSects(r)
	if len(sects) < 12 {
		t.Fatalf("only %d sects keep a gate; the read is broken, not the tree", len(sects))
	}
	conn := livesConn(t, path)
	made, err := r.sectPopulation(conn, 1000)
	if err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	target := int64(0)
	for _, n := range rule.Ranks {
		target += n
	}
	if made != target*int64(len(sects)) {
		t.Fatalf("seeding made %d people; %d sects of %d is %d", made, len(sects), target, target*int64(len(sects)))
	}
	for _, sect := range sects {
		gate, home := game.SectGate(r.World, sect), game.SectHome(r.World, sect)
		for rank, want := range rule.Ranks {
			got := romanceStr(t, path, `SELECT COUNT(*) FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
                WHERE c.faction=? AND l.sect_rank=? AND c.status='alive'`, sect, rank)
			if got != fmt.Sprint(want) {
				t.Errorf("%s keeps %s %s, want %d", sect, got, rank, want)
			}
			place := home
			if rule.AtGate[rank] {
				place = gate
			}
			elsewhere := romanceStr(t, path, `SELECT COUNT(*) FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
                WHERE c.faction=? AND l.sect_rank=? AND c.current_location<>?`, sect, rank, place)
			if elsewhere != "0" {
				t.Errorf("%s: %s of its %s stand somewhere other than %s", sect, elsewhere, rank, place)
			}
		}
		origin := romanceStr(t, path, `SELECT COUNT(*) FROM npc_registry WHERE sect_affiliation=? AND origin='sect'`, sect)
		if origin != fmt.Sprint(target) {
			t.Errorf("%s has %s registry rows of origin sect, want %d", sect, origin, target)
		}
	}
	// A Spiritual World disciple stands in the Spiritual World's realms.
	floor := r.worldFloorRealm("Spiritual World")
	if floor == 0 {
		t.Fatal("the Spiritual World has no realms; the read is broken, not the tree")
	}
	low := romanceStr(t, path, `SELECT COUNT(*) FROM npc_civilization_state WHERE world_name='Spiritual World' AND realm_index<?`, floor)
	if low != "0" {
		t.Fatalf("%s Spiritual World sect members stand below its first realm", low)
	}
}

func TestASecondTickMakesNobody(t *testing.T) {
	path, r := populationWorld(t)
	conn := livesConn(t, path)
	if _, err := r.sectPopulation(conn, 1000); err != nil {
		t.Fatal(err)
	}
	again, err := r.sectPopulation(conn, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if again != 0 {
		t.Fatalf("a second tick made %d more people; a full hall is full", again)
	}
}

func TestADeadElderIsReplaced(t *testing.T) {
	path, r := populationWorld(t)
	conn := livesConn(t, path)
	if _, err := r.sectPopulation(conn, 1000); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	dead := romanceStr(t, path, `SELECT c.npc_name FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.faction='Azure Cloud Sect' AND l.sect_rank='Elder' ORDER BY c.npc_name LIMIT 1`)
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead' WHERE npc_name=?`, []any{dead}); err != nil {
		t.Fatal(err)
	}
	made, err := r.sectPopulation(conn, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if made != 1 {
		t.Fatalf("a dead Elder was answered by %d new people, want 1", made)
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT COUNT(*) FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.faction='Azure Cloud Sect' AND l.sect_rank='Elder' AND c.status='alive'`); got != "3" {
		t.Fatalf("the Azure Cloud keeps %s living Elders after the replacement", got)
	}
}

func TestTheHallIsCappedNotEveryHole(t *testing.T) {
	path, r := populationWorld(t)
	conn := livesConn(t, path)
	if _, err := r.sectPopulation(conn, 1000); err != nil {
		t.Fatal(err)
	}
	// A promotion moves somebody up and leaves a hole below: the hall is still
	// its whole size, so nobody is made.
	if _, err := conn.Execute(`UPDATE npc_life_state SET sect_rank='Grand Elder' WHERE npc_name=(
        SELECT c.npc_name FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.faction='Azure Cloud Sect' AND l.sect_rank='Elder' ORDER BY c.npc_name LIMIT 1)`, nil); err != nil {
		t.Fatal(err)
	}
	made, err := r.sectPopulation(conn, 2000)
	if err != nil {
		t.Fatal(err)
	}
	if made != 0 {
		t.Fatalf("a promotion grew the hall by %d; only a sect short of its whole target is filled", made)
	}
}

func TestNoNameIsCarriedTwiceAndTheCatalogueKeepsItsOwn(t *testing.T) {
	path, r := populationWorld(t)
	conn := livesConn(t, path)
	if _, err := r.sectPopulation(conn, 1000); err != nil {
		t.Fatal(err)
	}
	romanceCommit(t, conn)
	if got := romanceStr(t, path, `SELECT COUNT(*) - COUNT(DISTINCT npc_name) FROM npc_civilization_state`); got != "0" {
		t.Fatalf("%s names are carried twice", got)
	}
	res, err := conn.Execute(`SELECT name FROM npc_registry WHERE origin='sect'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		t.Fatal("no sect members were registered; the test is vacuous")
	}
	for _, row := range res.Rows {
		if _, taken := r.World.NPCs[fmt.Sprint(row[0])]; taken {
			t.Errorf("%s is the content file's and was made again for a sect", row[0])
		}
	}
}

func TestASectMasterIsNotDemotedByTheirOwnWork(t *testing.T) {
	if got := nextSectRank("Sect Master"); got != "" {
		t.Fatalf("a Sect Master's next rank is %q; above the ladder they stay where they are", got)
	}
	if got := nextSectRank("Independent"); got != "Outer Disciple" {
		t.Fatalf("an independent's next rank is %q", got)
	}
}
