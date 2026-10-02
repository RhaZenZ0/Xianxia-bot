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
// catalogue, because the rule is about the real map: since v1.19.0 a sect's
// home is the city it keeps its gate in (its seat), which has roads out, so a
// sect grows out of its own city; the beachhead rule is kept for a gate that
// stands in the wilderness and is cornered with crafted neutral maps below.

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

func TestAStrongSectClaimsItsSeatThenGrowsByRoad(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path, r := claimWorld(t)
	home := game.SectHome(r.World, claimStrong)
	if home == "" || home == game.SectGate(r.World, claimStrong) {
		t.Fatalf("the sect's home %q is not a seat city; the seat reader is broken, not the tree", home)
	}
	if !game.TerritoryIsWholePlace(r.World, home) {
		t.Fatalf("the seat %q is not a whole place", home)
	}

	runClaims(t, path, r)
	if got := holdings(t, path, claimStrong); len(got) != 1 || got[0] != home {
		t.Fatalf("a sect's first claim is its own seat %q, got %v", home, got)
	}
	if got := holdings(t, path, claimWeak); len(got) != 0 {
		t.Fatalf("a sect too weak to go to war claimed %v", got)
	}

	// Every later claim is one walk step from something it already holds,
	// in the seat's world: a seated sect grows out of its own city and never
	// takes a beachhead, because its home has roads.
	for tick := 2; tick <= 3; tick++ {
		before := holdings(t, path, claimStrong)
		runClaims(t, path, r)
		after := holdings(t, path, claimStrong)
		if len(after) != len(before)+1 {
			t.Fatalf("tick %d claimed %d places, want one: %v -> %v", tick, len(after)-len(before), before, after)
		}
		newest := after[len(after)-1]
		if r.World.Locations[newest].World != r.World.Locations[home].World {
			t.Fatalf("tick %d: %q is not in the seat's world", tick, newest)
		}
		if !game.TerritoryIsWholePlace(r.World, newest) {
			t.Fatalf("tick %d: %q is a street or a room, not a place", tick, newest)
		}
		adjacent := false
		for _, held := range before {
			for _, step := range game.WhereAnNPCCanWalk(r.World, held, 1<<30) {
				if r.wholePlace(step) == newest {
					adjacent = true
				}
			}
		}
		if !adjacent {
			t.Fatalf("tick %d: a seated sect grows by road, and %q is not one step from %v", tick, newest, before)
		}
	}
}

func TestASectStopsAtItsCapAndNeverTakesAnothersGate(t *testing.T) {
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path, r := claimWorld(t)
	gates := map[string]string{}
	for name := range r.World.Sects {
		if g := game.SectHome(r.World, name); g != "" {
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
				t.Fatalf("%s took %s's home %q", sect, owner, place)
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
	gate := game.SectHome(r.World, claimStrong)
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

// The home rule has to be cornered: on the shipped map no road step and no
// beachhead hash happens to land on a rival's seat, so the tests above pass
// just as well without it. Here the only neutral place left is one.
func TestARivalsHomeIsNeverABeachhead(t *testing.T) {
	_, r := claimWorld(t)
	own, rival := game.SectHome(r.World, claimStrong), game.SectHome(r.World, claimRival)
	if r.World.Locations[own].World != r.World.Locations[rival].World {
		t.Fatalf("the fixture wants two sects of one world: %q and %q", own, rival)
	}
	homeOf := map[string]string{own: claimStrong, rival: claimRival}
	if got := r.claimTarget(claimStrong, []string{own}, map[string]bool{rival: true}, homeOf); got != "" {
		t.Fatalf("with only a rival's seat neutral, %s claimed %q", claimStrong, got)
	}
	if got := r.claimTarget(claimRival, nil, map[string]bool{rival: true}, homeOf); got != rival {
		t.Fatalf("a sect's own seat is its to claim: got %q", got)
	}
}

// A sect that already holds a beachhead grows by road or not at all (found in
// review). With every road step out of its holdings taken, the old fallback
// reached into the gate's world again and claimed a place cut off from
// everything the sect held.
func TestASectWithNoRoadLeftDoesNotLeapAcrossTheMap(t *testing.T) {
	_, r := claimWorld(t)
	gate := game.SectHome(r.World, claimStrong)
	world := r.World.Locations[gate].World
	beachhead, elsewhere := "", ""
	for name := range r.World.Locations {
		if name == gate || r.World.Locations[name].World != world || !game.TerritoryIsWholePlace(r.World, name) {
			continue
		}
		if beachhead == "" || name < beachhead {
			beachhead = name
		}
	}
	// Somewhere off the roads of both the home and the beachhead: since
	// v1.19.0 the home is a seat city with roads of its own, so a place one
	// step from it is a legitimate claim and not a leap.
	for name := range r.World.Locations {
		if name != gate && name != beachhead && r.World.Locations[name].World == world && game.TerritoryIsWholePlace(r.World, name) {
			walks := false
			for _, from := range []string{gate, beachhead} {
				for _, step := range game.WhereAnNPCCanWalk(r.World, from, 1<<30) {
					if r.wholePlace(step) == name {
						walks = true
					}
				}
			}
			if !walks {
				elsewhere = name
				break
			}
		}
	}
	if beachhead == "" || elsewhere == "" {
		t.Fatalf("the fixture found no beachhead and no place off its roads: %q %q", beachhead, elsewhere)
	}
	neutral := map[string]bool{elsewhere: true}
	if got := r.claimTarget(claimStrong, []string{gate, beachhead}, neutral, map[string]string{gate: claimStrong}); got != "" {
		t.Fatalf("holding %q with no road left, the sect leapt to %q", beachhead, got)
	}
	if got := r.claimTarget(claimStrong, []string{gate}, neutral, map[string]string{gate: claimStrong}); got != elsewhere {
		t.Fatalf("a sect holding only its home may still take a beachhead: got %q", got)
	}
}
