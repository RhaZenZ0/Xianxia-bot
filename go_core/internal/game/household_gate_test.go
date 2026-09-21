package game

import (
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A city's gate is that city (v1.0.9).
//
// Reported from live play: standing at Cloudblade City East Gate, `/family
// enter` answered *"the Shen Family household stands in Cloudblade City and
// you are in Cloudblade City East Gate — travel there first"* - a refusal
// naming, as somewhere else, the city the player was standing in.
//
// `familyHouseholdEnterAction` compared the character's location to the
// household's town by bare string equality, while `cityOf` - the engine's one
// statement of which city a place is part of - was already read by
// `explorationTravelAction` and `WhereAnNPCCanWalk`. 317 of the catalogue's
// 477 locations are parts of a household town, 92 of them gates.
//
// The behavioural half. `test_a_gate_is_its_city.py` holds the bot's
// anticipation of this rule to the same answer over the whole catalogue,
// because the fault was one rule with two spellings.

// householdTownWithAGate finds a real city in the shipped catalogue that has a
// gate, so the test drives production content rather than a fixture that could
// not fail the way production fails.
func householdTownWithAGate(t *testing.T) (city string, gate string) {
	t.Helper()
	catalog := districtCatalog(t)
	best := ""
	bestGate := ""
	for name, loc := range catalog.Locations {
		if loc.District != "gate" || loc.OutsideLocation == "" {
			continue
		}
		if _, ok := catalog.Locations[loc.OutsideLocation]; !ok {
			continue
		}
		// Deterministic: the first by name, so the test names the same pair
		// on every run.
		if best == "" || loc.OutsideLocation < best {
			best, bestGate = loc.OutsideLocation, name
		}
	}
	if best == "" {
		t.Fatal("no city in the shipped catalogue has a gate; the fixture is broken, not the tree")
	}
	return best, bestGate
}

func TestAGateOfYourOwnCityIsYourOwnCity(t *testing.T) {
	city, gate := householdTownWithAGate(t)
	catalog := districtCatalog(t)
	if got := cityOf(catalog, gate); got != city {
		t.Fatalf("cityOf(%q) is %q, want %q; the rule this door now asks is broken", gate, got, city)
	}

	path := householdDB(t)
	fid := householdFamily(t, path, 42, gate)
	batch4Exec(t, path, `UPDATE birth_families SET location=? WHERE family_id=?`, city, fid)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, gate)

	_, err := householdApply(t, path, func(conn *storage.Conn) (authoritativeMutation, error) {
		return familyHouseholdEnterAction(conn, catalog, 42, payload(map[string]any{}))
	})
	if err != nil && strings.Contains(err.Error(), "travel there first") {
		t.Fatalf("standing at %q, which is a gate of %q, the household refused with: %v\n"+
			"That refusal names the city the player is standing in as somewhere else, and "+
			"beginner_home reports return_home from this action - so walking home from the "+
			"road, which arrives at a gate, could not finish the beginner path.", gate, city, err)
	}
}

func TestSomewhereElseEntirelyIsStillRefused(t *testing.T) {
	// The other direction, which a test that only proved the gate opens would
	// pass just as well for a door that had stopped checking anything at all.
	city, _ := householdTownWithAGate(t)
	catalog := districtCatalog(t)
	elsewhere := ""
	for name, loc := range catalog.Locations {
		if loc.District == "" && loc.Shop == "" && loc.AuctionHouse == "" && name != city {
			if elsewhere == "" || name < elsewhere {
				elsewhere = name
			}
		}
	}
	if elsewhere == "" {
		t.Fatal("no standalone location in the catalogue; the fixture is broken, not the tree")
	}

	path := householdDB(t)
	fid := householdFamily(t, path, 42, elsewhere)
	batch4Exec(t, path, `UPDATE birth_families SET location=? WHERE family_id=?`, city, fid)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, elsewhere)

	_, err := householdApply(t, path, func(conn *storage.Conn) (authoritativeMutation, error) {
		return familyHouseholdEnterAction(conn, catalog, 42, payload(map[string]any{}))
	})
	if err == nil || !strings.Contains(err.Error(), "travel there first") {
		t.Fatalf("standing at %q, nowhere near %q, the household let the player in (err=%v). "+
			"Presence is the whole of rc.32's rule: the door is not a teleport.", elsewhere, city, err)
	}
}
