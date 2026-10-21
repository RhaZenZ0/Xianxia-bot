package simulation

import (
	"fmt"
	"sort"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// The two capped walks over the whole world's people (v1.34.0).
//
// The road is walked in name order to a cap of forty and the finds to a cap of
// six, and for as long as each walk started at the top of the alphabet every
// tick, the names past the first few never took a step or turned anything up:
// over four hundred ticks of the real roster the sixths that ever moved were
// [98 97 97 17 0 0]. With the dice lent so that everybody acts, the only thing
// left to decide who does is where the tick starts, so across a run of ticks
// every person in a hundred must have had a turn. Both are driven through
// `rosterTurn`'s callers, not through the helper, so a walk that stops calling
// it fails here.

// lastUnreached is the final name in the roster the set does not hold, which is
// what a failing roster test prints: the alphabet's tail is where the fault
// shows.
func lastUnreached(names []string, reached map[string]bool) string {
	last := ""
	for _, name := range names {
		if !reached[name] {
			last = name
		}
	}
	return last
}

func TestTheWholeRosterGetsATurnOnTheRoad(t *testing.T) {
	defer everyRollLands()()
	path := movementDB(t)
	r := movementRunner()
	names := make([]string, 100)
	for i := range names {
		names[i] = fmt.Sprintf("Peddler %03d", i)
		addNPC(t, path, names[i], "Greenriver Town", "Greenriver Town",
			"Mortal World", "Travelling Peddler", 50, "Independent")
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	where := func() map[string]string {
		res, err := conn.Execute(`SELECT npc_name,current_location FROM npc_civilization_state`, nil)
		if err != nil {
			t.Fatal(err)
		}
		out := map[string]string{}
		for _, row := range res.Rows {
			out[fmt.Sprint(row[0])] = fmt.Sprint(row[1])
		}
		return out
	}
	roved := map[string]bool{}
	before := where()
	for tick := int64(1); tick <= 60; tick++ {
		if _, err := r.npcTravel(conn, 3, tick*1440); err != nil {
			t.Fatal(err)
		}
		after := where()
		for name, place := range after {
			if place != before[name] {
				roved[name] = true
			}
		}
		before = after
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if len(roved) != len(names) {
		t.Fatalf("%d of %d people never took the road in sixty ticks; the last who never moved is %s",
			len(names)-len(roved), len(names), lastUnreached(names, roved))
	}
}

// The floor is emptied between ticks so its own cap of active lots does not
// stand in for the roster's.
func TestEveryFinderEventuallyHasATurn(t *testing.T) {
	path := findsDB(t)
	r := findsRunner()
	names := make([]string, 100)
	for i := range names {
		names[i] = fmt.Sprintf("Grave-Robber %03d", i)
		addFinder(t, path, names[i], "Greenriver Town", "grave-robber", 0)
	}
	defer everyFinderFinds()()
	found := map[string]bool{}
	for tick := int64(1); tick <= 700; tick++ {
		runFinds(t, path, r, 1, tick*1440)
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		res, err := conn.Execute(`SELECT seller_npc_name FROM auctions`, nil)
		if err != nil {
			conn.Close()
			t.Fatal(err)
		}
		for _, row := range res.Rows {
			found[fmt.Sprint(row[0])] = true
		}
		if _, err := conn.Execute(`DELETE FROM auctions`, nil); err != nil {
			conn.Close()
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			conn.Close()
			t.Fatal(err)
		}
		conn.Close()
	}
	if len(found) != len(names) {
		t.Fatalf("%d of %d finders never consigned anything in 700 ticks; the last who never did is %s",
			len(names)-len(found), len(names), lastUnreached(names, found))
	}
}

// The other three capped walks (v1.36.0). Crimes and hunts read the whole
// alive roster to a cap of five, childbirth reads up to a hundred and fifty
// couples to a cap of six; each walked in name order from the top every tick,
// so the same five criminals robbed, the same five hunters hunted and the same
// six couples bore, and the rest of the world never did.

func TestEveryCriminalEventuallyRobsSomebody(t *testing.T) {
	defer everyRollLands()()
	path := deedsDB(t)
	r := deedsRunner()
	names := make([]string, 100)
	for i := range names {
		names[i] = fmt.Sprintf("Cutpurse %03d", i)
		addPerson(t, path, names[i], "Greenriver Town", "cutpurse", 10, 50, 0)
	}
	// Somebody worth robbing beside each of them; a victim keeps nothing a
	// robbery could make a richer target of the thief, so the merchants stay
	// the richest neighbours for the whole run.
	for i := 0; i < 100; i++ {
		addPerson(t, path, fmt.Sprintf("Merchant %03d", i), "Greenriver Town", "cloth merchant", 100000, 10, 0)
	}
	// A start hashed from the minute is a fair draw, not a round robin: five
	// of a hundred a tick needs a few hundred ticks before every name has
	// been inside the window once, as the finds (six of a hundred) took 700.
	robbed := map[string]bool{}
	for tick := 0; tick < 400; tick++ {
		runCrimesFrom(t, path, r, 1, int64(1000+tick*1440))
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		res, err := conn.Execute(`SELECT npc_name FROM npc_civilization_state WHERE profession='cutpurse' AND wealth<>10`, nil)
		conn.Close()
		if err != nil {
			t.Fatal(err)
		}
		for _, row := range res.Rows {
			robbed[fmt.Sprint(row[0])] = true
		}
	}
	if len(robbed) != len(names) {
		t.Fatalf("%d of %d criminals never robbed anybody in four hundred ticks at a cap of %d; the last who never did is %s",
			len(names)-len(robbed), len(names), crimeCap, lastUnreached(names, robbed))
	}
}

func TestEveryHunterEventuallyHunts(t *testing.T) {
	defer everyHuntLands()()
	path := deedsDB(t)
	r := deedsRunner()
	names := make([]string, 100)
	for i := range names {
		names[i] = fmt.Sprintf("Trapper %03d", i)
		addPerson(t, path, names[i], "Greenriver Town", "beast hunter", 10, 50, 6)
	}
	hunted := map[string]bool{}
	for tick := 0; tick < 400; tick++ {
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		if _, _, _, err := r.npcBeastHunts(conn, int64(2000+tick*1440)); err != nil {
			t.Fatal(err)
		}
		// Who went out is the deed's own record; the floor is emptied so its
		// cap of active lots does not stand in for the roster's.
		res, err := conn.Execute(`SELECT DISTINCT seller_npc_name FROM auctions`, nil)
		if err != nil {
			t.Fatal(err)
		}
		for _, row := range res.Rows {
			hunted[fmt.Sprint(row[0])] = true
		}
		if _, err := conn.Execute(`DELETE FROM auctions`, nil); err != nil {
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		conn.Close()
	}
	if len(hunted) != len(names) {
		t.Fatalf("%d of %d hunters never went out in four hundred ticks at a cap of %d; the last who never did is %s",
			len(names)-len(hunted), len(names), huntCap, lastUnreached(names, hunted))
	}
}

// Childbirth consumes the state it walks - a couple that bore moves down the
// list - so given enough ticks every couple bears whether or not the walk
// turns, and "who never got a turn" proves nothing here. What the turn changes
// is *which* six bear first: the tick's start is rosterTurn's, so the first
// births are the six couples from that start and not the six at the top of the
// alphabet.
func TestTheFirstBirthsOfATickStartWhereTheRosterTurns(t *testing.T) {
	path := livesDB(t)
	r := livesRunner()
	couples := make([]string, 100)
	for i := range couples {
		a, b := fmt.Sprintf("Chen%03d A", i), fmt.Sprintf("Chen%03d B", i)
		couples[i] = a
		addNPC(t, path, a, "Greenriver Town", "Greenriver Town", "Mortal World", "Farmer", 50, "Independent")
		addNPC(t, path, b, "Greenriver Town", "Greenriver Town", "Mortal World", "Farmer", 50, "Independent")
		addLife(t, path, a, map[string]any{"relationship_status": "married", "spouse_name": b, "health": 90})
		addLife(t, path, b, map[string]any{"relationship_status": "married", "spouse_name": a, "health": 90})
	}
	defer gamerng.UseRoller(func(int) int { return 0 })()
	const gm = int64(50000)
	start := int(hash64("roster-turn", fmt.Sprint(gm)) % uint64(len(couples)))
	if start == 0 {
		t.Fatalf("minute %d turns the roster to its top, where an unturned walk also starts; pick another minute", gm)
	}
	conn := livesConn(t, path)
	born, err := r.npcChildbirth(conn, 4, gm)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if born != childbirthCap {
		t.Fatalf("a hundred healthy couples produced %d child(ren), cap is %d", born, childbirthCap)
	}
	res, err := conn.Execute(`SELECT npc_name FROM npc_life_state WHERE children_count>0 AND npc_name<spouse_name ORDER BY npc_name`, nil)
	if err != nil {
		t.Fatal(err)
	}
	bore := map[string]bool{}
	for _, row := range res.Rows {
		bore[fmt.Sprint(row[0])] = true
	}
	for i := 0; i < int(childbirthCap); i++ {
		want := couples[(start+i)%len(couples)]
		if !bore[want] {
			t.Fatalf("the tick at minute %d starts at %s (roster position %d) and should have reached %s; it bore for %v - the walk is not turning",
				gm, couples[start], start, want, keysOf(bore))
		}
	}
	if bore[couples[0]] && start > int(childbirthCap) {
		t.Fatalf("the alphabet's first couple bore on a tick that starts at position %d: the walk starts at the top", start)
	}
}

func keysOf(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
