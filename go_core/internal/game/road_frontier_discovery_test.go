package game

import (
	"fmt"
	"sort"
	"strings"
	"testing"
)

// TestExploreDiscoveryStaysOnTheRoadFrontierNotAnywhereInTheWorld guards the
// fix for discoverNextLocationTx picking a candidate from *anywhere* unknown
// in the current world (including cities on the far side of the road
// network, unrelated to where the character actually is) instead of
// following the road graph outward from what they already know.
//
// A fresh character starts knowing only their birthplace (Greenriver Town,
// which itself has no roads) and the Mortal World's realm-hub city (Azure
// Crown Imperial City, min_realm_index 0, always known). Azure Crown's own
// road neighbors are Ashenwall City, Jadewood Medicine City and Riverguard
// City - exactly the frontier the very first /explore should be able to
// surface. A location like Moonfen City, several road-hops deeper into the
// network and never a neighbor of anything the character starts out
// knowing, must never come back from that first exploration.
func TestExploreDiscoveryStaysOnTheRoadFrontierNotAnywhereInTheWorld(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)

	originalIntn := locationDiscoveryIntn
	defer func() { locationDiscoveryIntn = originalIntn }()

	firstRingFrontier := map[string]bool{
		"Ashenwall City":         true,
		"Jadewood Medicine City": true,
		"Riverguard City":        true}
	// A city that's several road-hops away from the starting frontier and
	// was never a neighbor of Greenriver Town or Azure Crown Imperial City -
	// exactly the kind of "random distant city" the old code could surface
	// on turn one.
	distantCity := "Moonfen City"
	if firstRingFrontier[distantCity] {
		t.Fatalf("test setup bug: distantCity must not be in the expected frontier")
	}

	// Only the very first exploration matters for this assertion: each
	// discovery persists and legitimately expands next call's frontier (see
	// TestExploreDiscoveryExpandsTheFrontierAsCitiesAreCharted below), so
	// checking across many calls would eventually and correctly reach
	// farther cities - that is the intended progressive behavior, not a bug.
	// The regression this guards is specifically "turn one, from the
	// starting town, must not surface something arbitrary and distant".
	pickIdx := 2 // force the 3rd (0-indexed) candidate if unfiltered - i.e. would land past the real frontier if the bug were still there
	locationDiscoveryIntn = func(n int) (int, error) {
		if n == 100 {
			return 0, nil // always pass the 45% discovery roll
		}
		idx := pickIdx % n
		pickIdx++
		return idx, nil
	}

	result := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", 1, map[string]any{
		"game_minute":                     600,
		"unexpected_events_enabled":       false,
		"unexpected_event_chance_percent": 0}))
	loc := fmt.Sprint(result["discovered_location"])
	if loc == "" || loc == "<nil>" {
		t.Fatalf("expected the very first exploration to discover a first-ring frontier city, got none")
	}
	if loc == distantCity {
		t.Fatalf("explore surfaced %q, which is not on the road frontier of anything known yet", distantCity)
	}
	// A road-side site (v0.39.0) on a road out of a known city is on the
	// same first ring: its leg touches the capital.
	catalog := districtCatalog(t)
	if a, b, ok := roadSiteEndpoints(catalog, loc); ok {
		if a != "Azure Crown Imperial City" && b != "Azure Crown Imperial City" {
			t.Fatalf("discovered the road-side site %q on the %s-%s road, which touches nothing known", loc, a, b)
		}
		return
	}
	if !firstRingFrontier[loc] {
		t.Fatalf("discovered %q, which is outside the expected first-ring road frontier %v", loc, firstRingFrontier)
	}
}

// TestExploreDiscoveryExpandsTheFrontierAsCitiesAreCharted proves discovery
// is progressive: once a first-ring city is known, exploring can surface
// *its* road neighbors too (the next ring out) - the "explore repeatedly to
// chart those roads" behavior - not just the original three.
func TestExploreDiscoveryExpandsTheFrontierAsCitiesAreCharted(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	// Pretend the character has already charted Riverguard City (as if
	// discovered on a prior exploration or by traveling there and back).
	batch4Exec(t, path, "INSERT INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(42,'Riverguard City','test',599,0)")

	originalIntn := locationDiscoveryIntn
	defer func() { locationDiscoveryIntn = originalIntn }()
	pickIdx := 0
	locationDiscoveryIntn = func(n int) (int, error) {
		if n == 100 {
			return 0, nil
		}
		idx := pickIdx % n
		pickIdx++
		return idx, nil
	}

	// Riverguard City's own road neighbors (Azure Crown Imperial City,
	// Four-Roads Caravan City, Jadewood Medicine City) are second-ring
	// candidates that weren't reachable before Riverguard was known.
	secondRingOnly := "Four-Roads Caravan City"
	found := false
	for i := 0; i < 8; i++ {
		clearCooldowns(t, path, 42)
		result := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", i+1, map[string]any{
			"game_minute":                     700 + i,
			"unexpected_events_enabled":       false,
			"unexpected_event_chance_percent": 0}))
		loc := fmt.Sprint(result["discovered_location"])
		if loc == secondRingOnly {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected %q (a road neighbor of the already-known Riverguard City) to become discoverable", secondRingOnly)
	}
}

// --- from wilds_test.go ---

// A place in the wilds of a city is found by exploring from that city
// (v1.7.7). Moonfen Marsh and Cloudspine Foothills carried no road and no
// writer of a discovery ever named them, so the Drowned Serpent's lair, the
// Nine-Echo Sword Wraith's floor and the two secret realms whose entrances
// they are could be reached by a GM teleport and nothing else.
func TestAPlaceInTheWildsIsFoundFromItsCity(t *testing.T) {
	catalog := crossingCatalog(t)
	has := func(list []string, name string) bool {
		for _, x := range list {
			if x == name {
				return true
			}
		}
		return false
	}
	for city, wild := range map[string]string{"Moonfen City": "Moonfen Marsh", "Cloudblade City": "Cloudspine Foothills"} {
		got := discoveryCandidates(catalog, map[string]bool{city: true}, "Mortal World", 0)
		if !has(got, wild) {
			t.Fatalf("exploring from %s cannot turn up %s; candidates: %v", city, wild, got)
		}
		if has(discoveryCandidates(catalog, map[string]bool{city: true, wild: true}, "Mortal World", 0), wild) {
			t.Fatalf("%s is offered again to somebody who already knows it", wild)
		}
	}
	// Knowing somewhere else is not knowing the city the wilds belong to.
	got := discoveryCandidates(catalog, map[string]bool{"Greenriver Town": true}, "Mortal World", 0)
	if has(got, "Moonfen Marsh") || has(got, "Cloudspine Foothills") {
		t.Fatalf("the wilds were offered from a city they do not belong to: %v", got)
	}
}

// Every raid lair and every secret realm's entrance can be reached: it is on
// the road network, beside a road, a sect's gate, or in the wilds of a city
// the road network reaches. A lair nothing reaches is a boss nobody fights.
func TestEveryLairAndRealmEntranceCanBeReached(t *testing.T) {
	catalog := crossingCatalog(t)
	onRoads := map[string]bool{}
	for name, loc := range catalog.Locations {
		if len(loc.Roads) > 0 {
			onRoads[name] = true
			for _, n := range loc.Roads {
				onRoads[n] = true
			}
		}
	}
	gates := map[string]bool{}
	for sect := range catalog.Sects {
		if gate := sectGate(catalog, sect); gate != "" {
			gates[gate] = true
		}
	}
	reachable := func(place string) bool {
		loc, ok := catalog.Locations[place]
		if !ok {
			return false
		}
		if onRoads[place] || loc.RoadSite != "" || gates[place] {
			return true
		}
		city, ok := catalog.Locations[loc.WildsOf]
		return ok && onRoads[loc.WildsOf] && city.World == loc.World
	}
	places := map[string]string{}
	for key, template := range bossTemplatesGo {
		lair, _ := bossLair(catalog, template)
		places[lair] = "the lair of " + key
	}
	for id, realm := range catalog.SecretRealms {
		if _, taken := places[realm.Location]; !taken {
			places[realm.Location] = "the entrance of " + id
		}
	}
	if !reachable("Greenriver Town") {
		t.Fatal("the starting town reads as unreachable; the check is broken, not the tree")
	}
	var stranded []string
	for place, what := range places {
		if !reachable(place) {
			stranded = append(stranded, place+" ("+what+")")
		}
	}
	sort.Strings(stranded)
	if len(stranded) > 0 {
		t.Fatalf("nothing reaches %s", strings.Join(stranded, ", "))
	}
}
