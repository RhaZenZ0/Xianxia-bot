package simulation

import (
	"fmt"
	"testing"

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
