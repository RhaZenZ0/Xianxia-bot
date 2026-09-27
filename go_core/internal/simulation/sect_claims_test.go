package simulation

import (
	"fmt"
	"path/filepath"
	"testing"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Sects take unclaimed ground (v1.12.0). Every place is seeded neutral and
// the war step moves only on ground a rival holds, so in a world no player had
// claimed from, the sieges never ran at all. These drive the shipped
// catalogue, because the rule is about the real map: a road-less gate, a
// beachhead, and roads out of it.

const claimStrong, claimRival, claimWeak = "Azure Cloud Sect", "Crimson Furnace Sect", "Frozen Moon Palace"

func claimWorld(t *testing.T) (string, *Runner) {
	t.Helper()
	catalog, err := worlddata.Load(filepath.Join("..", "..", "..", "content", "world.json"))
	if err != nil {
		t.Fatalf("the content file is in the repository; the read is broken, not the tree: %v", err)
	}
	path := setupSimulationDB(t, sectWarSchema)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for name := range catalog.Locations {
		if _, err := conn.Execute(`INSERT INTO territory_state(territory_key,name,region) VALUES(?,?,?)`, []any{name, name, name}); err != nil {
			t.Fatal(err)
		}
	}
	for sect, influence := range map[string]int64{claimStrong: 86, claimRival: 86, claimWeak: 20} {
		if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,influence,resources) VALUES(?,?,70)`, []any{sect, influence}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path, &Runner{World: catalog}
}

func runClaims(t *testing.T, path string, r *Runner) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	n, err := r.npcSectClaims(conn, 1, 20000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return n
}

func holdings(t *testing.T, path, sect string) []string {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT territory_key FROM territory_state WHERE controller_type='sect' AND controller_key=? ORDER BY updated_at,territory_key`, []any{sect})
	if err != nil {
		t.Fatal(err)
	}
	out := []string{}
	for _, row := range res.Rows {
		out = append(out, fmt.Sprint(row[0]))
	}
	return out
}

func TestAStrongSectClaimsItsGateThenABeachheadThenOutwardByRoad(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path, r := claimWorld(t)
	gate := game.SectGate(r.World, claimStrong)
	if gate == "" {
		t.Fatal("the sect's gate could not be read; the gate reader is broken, not the tree")
	}

	runClaims(t, path, r)
	if got := holdings(t, path, claimStrong); len(got) != 1 || got[0] != gate {
		t.Fatalf("a sect's first claim is its own gate %q, got %v", gate, got)
	}
	if got := holdings(t, path, claimWeak); len(got) != 0 {
		t.Fatalf("a sect too weak to go to war claimed %v", got)
	}

	runClaims(t, path, r)
	got := holdings(t, path, claimStrong)
	if len(got) != 2 {
		t.Fatalf("the second tick should take a beachhead, holdings %v", got)
	}
	beachhead := ""
	for _, place := range got {
		if place != gate {
			beachhead = place
		}
	}
	if r.World.Locations[beachhead].World != r.World.Locations[gate].World {
		t.Fatalf("the beachhead %q is not in the gate's world", beachhead)
	}
	if !game.TerritoryIsWholePlace(r.World, beachhead) {
		t.Fatalf("the beachhead %q is a street or a room, not a place", beachhead)
	}

	runClaims(t, path, r)
	third := ""
	for _, place := range holdings(t, path, claimStrong) {
		if place != gate && place != beachhead {
			third = place
		}
	}
	if third == "" {
		t.Fatal("the third tick claimed nothing")
	}
	adjacent := false
	for _, step := range game.WhereAnNPCCanWalk(r.World, beachhead, 1<<30) {
		if r.wholePlace(step) == third {
			adjacent = true
		}
	}
	if !adjacent {
		t.Fatalf("after a beachhead a sect grows by road: %q is not one step from %q", third, beachhead)
	}
}

func TestASectStopsAtItsCapAndNeverTakesAnothersGate(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path, r := claimWorld(t)
	gates := map[string]string{}
	for name := range r.World.Sects {
		if g := game.SectGate(r.World, name); g != "" {
			gates[g] = name
		}
	}
	for i := 0; i < 10; i++ {
		runClaims(t, path, r)
	}
	for _, sect := range []string{claimStrong, claimRival} {
		got := holdings(t, path, sect)
		if int64(len(got)) != claimCap(86) {
			t.Fatalf("%s holds %d places after ten ticks, want its cap %d: %v", sect, len(got), claimCap(86), got)
		}
		for _, place := range got {
			if owner, isGate := gates[place]; isGate && owner != sect {
				t.Fatalf("%s took %s's gate %q", sect, owner, place)
			}
		}
	}
	if claimCap(62) != 2 || claimCap(100) != claimMaxHoldings {
		t.Fatalf("the cap rises with influence from 2 to %d: %d..%d", claimMaxHoldings, claimCap(62), claimCap(100))
	}
}

// The point of it: the war step, run after the claims in one tick, now has a
// rival's ground to move on without any player having claimed anything.
func TestClaimsGiveTheWarStepATarget(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path, r := claimWorld(t)
	if n := runWars(t, path, r, 1, 20000); n != 0 {
		t.Fatalf("with every place neutral the war step should find nothing, declared %d", n)
	}
	runClaims(t, path, r)
	if n := runWars(t, path, r, 1, 20000); n != 1 {
		t.Fatalf("after the claims a strong sect should move on a rival's claim, declared %d", n)
	}
	attacker := fmt.Sprint(simScalar(t, path, `SELECT attacker_key FROM territory_wars LIMIT 1`))
	defender := fmt.Sprint(simScalar(t, path, `SELECT defender_key FROM territory_wars LIMIT 1`))
	if attacker == defender || (attacker != claimStrong && attacker != claimRival) || (defender != claimStrong && defender != claimRival) {
		t.Fatalf("the war should be between the two claimants: %s on %s", attacker, defender)
	}
	if n := i64(simScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE event_type='territory_claimed'`)); n != 2 {
		t.Fatalf("each claim is heard of once: %d rows", n)
	}
}

func TestAContestedPlaceIsNotClaimed(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path, r := claimWorld(t)
	gate := game.SectGate(r.World, claimStrong)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_at,updated_at) VALUES('x','y',?,'active',0,0)`, []any{gate}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	runClaims(t, path, r)
	for _, place := range holdings(t, path, claimStrong) {
		if place == gate {
			t.Fatalf("a place under an active war was claimed as though it were quiet")
		}
	}
}

// The gate rule has to be cornered: on the shipped map no road step and no
// beachhead hash happens to land on a rival's gate, so the tests above pass
// just as well without it. Here the only neutral place left is one.
func TestARivalsGateIsNeverABeachhead(t *testing.T) {
	_, r := claimWorld(t)
	own, rival := game.SectGate(r.World, claimStrong), game.SectGate(r.World, claimRival)
	if r.World.Locations[own].World != r.World.Locations[rival].World {
		t.Fatalf("the fixture wants two sects of one world: %q and %q", own, rival)
	}
	gateOf := map[string]string{own: claimStrong, rival: claimRival}
	if got := r.claimTarget(claimStrong, []string{own}, map[string]bool{rival: true}, gateOf); got != "" {
		t.Fatalf("with only a rival's gate neutral, %s claimed %q", claimStrong, got)
	}
	if got := r.claimTarget(claimRival, nil, map[string]bool{rival: true}, gateOf); got != rival {
		t.Fatalf("a sect's own gate is its to claim: got %q", got)
	}
}
