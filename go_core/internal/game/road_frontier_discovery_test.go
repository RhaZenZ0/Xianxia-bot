package game

import (
	"fmt"
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
		"Riverguard City":        true,
	}
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
		"cooldown_seconds":                0,
		"unexpected_events_enabled":       false,
		"unexpected_event_chance_percent": 0,
	}))
	loc := fmt.Sprint(result["discovered_location"])
	if loc == "" || loc == "<nil>" {
		t.Fatalf("expected the very first exploration to discover a first-ring frontier city, got none")
	}
	if loc == distantCity {
		t.Fatalf("explore surfaced %q, which is not on the road frontier of anything known yet", distantCity)
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
		result := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", i+1, map[string]any{
			"game_minute":                     700 + i,
			"cooldown_seconds":                0,
			"unexpected_events_enabled":       false,
			"unexpected_event_chance_percent": 0,
		}))
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
