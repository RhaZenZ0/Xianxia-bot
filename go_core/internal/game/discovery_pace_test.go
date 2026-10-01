package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The pace at which /explore fills a map (v1.15.0): a city is found before a
// road site or a wild place, and a find is seven explores in ten. These drive
// the shipped catalogue from Greenriver Town, because the count the rule was
// chosen for is a fact about that content and a fixture ring would not carry
// it. Every roll is lent: the chance roll passes and the pick is the first
// candidate, so what is asserted is the order and the count, never the dice.

// exploreOnce lends the discovery dice and returns what one explore found.
func exploreOnce(t *testing.T, path, world string, seq int, roll int) string {
	t.Helper()
	original := locationDiscoveryIntn
	locationDiscoveryIntn = func(n int) (int, error) {
		if n == 100 {
			return roll, nil
		}
		return 0, nil
	}
	defer func() { locationDiscoveryIntn = original }()
	clearCooldowns(t, path, 42)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.explore", seq, map[string]any{
		"game_minute":                     600 + int64(seq),
		"unexpected_events_enabled":       false,
		"unexpected_event_chance_percent": 0}))
	found := fmt.Sprint(result["discovered_location"])
	if found == "<nil>" {
		return ""
	}
	return found
}

func TestACityIsFoundBeforeAnythingElseAndTheRingIsNineFindsAway(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	cities := map[string]bool{}
	for name, loc := range catalog.Locations {
		if len(loc.Roads) > 0 && loc.World == "Mortal World" {
			cities[name] = true
		}
	}
	if len(cities) != 12 {
		t.Fatalf("the Mortal World should carry twelve cities, found %d; the content reader is broken, not the tree", len(cities))
	}
	finds := []string{}
	for seq := 1; seq <= 40; seq++ {
		found := exploreOnce(t, path, world, seq, 0)
		if found == "" {
			t.Fatalf("explore %d found nothing with the roll lent", seq)
		}
		finds = append(finds, found)
		known := knownMapAt(t, path, catalog)
		unknownCity := false
		for name := range cities {
			if !known[name] {
				unknownCity = true
			}
		}
		if !cities[found] && unknownCity {
			t.Fatalf("explore %d found %q while a city was still unknown; a city is found before anything else", seq, found)
		}
		if !unknownCity {
			break
		}
	}
	if len(finds) > 9 {
		t.Fatalf("the twelve Mortal cities took %d finds from Greenriver Town, want at most 9: %v", len(finds), finds)
	}
	// With every city known, the next find is one of the places the cities
	// were found ahead of: a road site or a wild place.
	next := exploreOnce(t, path, world, 50, 0)
	loc, ok := catalog.Locations[next]
	if !ok || (loc.RoadSite == "" && loc.WildsOf == "") {
		t.Fatalf("with the ring known, the next find should be a road site or a wild place, got %q", next)
	}
}

func TestAFindIsSevenExploresInTen(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	world := batch4WorldPath(t)
	if found := exploreOnce(t, path, world, 1, discoveryChancePercent); found != "" {
		t.Fatalf("a roll of %d found %q; the chance is %d in a hundred", discoveryChancePercent, found, discoveryChancePercent)
	}
	if found := exploreOnce(t, path, world, 2, discoveryChancePercent-1); found == "" {
		t.Fatalf("a roll of %d found nothing; the chance is %d in a hundred", discoveryChancePercent-1, discoveryChancePercent)
	}
	if discoveryChancePercent <= 45 {
		t.Fatalf("the chance is %d, no better than the 45 the pace was measured at", discoveryChancePercent)
	}
}

func TestAPoolWithNoCityIsReturnedWhole(t *testing.T) {
	catalog := districtCatalog(t)
	pool := []string{"Shrine of the Patient Ox", "Moonfen Marsh"}
	got := citiesFirst(catalog, pool)
	if len(got) != 2 {
		t.Fatalf("a pool of sites and wilds was narrowed to %v; only a city narrows it", got)
	}
	mixed := citiesFirst(catalog, append([]string{"Riverguard City"}, pool...))
	if len(mixed) != 1 || mixed[0] != "Riverguard City" {
		t.Fatalf("a pool with a city in it should be that city alone, got %v", mixed)
	}
}

// knownMapAt reads the character's map on a fresh connection, the way the
// next explore will.
func knownMapAt(t *testing.T, path string, catalog worlddata.Catalog) map[string]bool {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	c, err := loadMechanicsCharacter(conn, catalog, 42)
	if err != nil {
		t.Fatal(err)
	}
	known, err := knownLocationsTx(conn, catalog, 42, c)
	if err != nil {
		t.Fatal(err)
	}
	return known
}
