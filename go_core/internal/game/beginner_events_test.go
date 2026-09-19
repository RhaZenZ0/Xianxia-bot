package game

import (
	"testing"

	"xianxia/core/internal/worlddata"
)

// The band is only real if the draw honours it (v1.0.0-rc.49).
//
// `UnexpectedEvent` has carried `min_realm_index` and `max_realm_index` since
// the roster was written and `eligibleUnexpectedEvents` has filtered on both -
// and every one of the forty-four events left both unset, so the filter ran on
// every draw and excluded nobody. rc.49 is the content that finally uses it.
//
// This drives the function. The Python half
// (`tests/python/unit/test_beginner_world_events.py`) holds that the content
// sets the bands, and cannot see whether the code still reads them: its first
// version asserted the source contained `c.RealmIndex < e.MinRealmIndex`, and
// passed happily when that line was changed to `if false && ...`. A grep
// cannot see a disabled condition, so the half that has to be behavioural is
// here.

func eventsForBandTest() worlddata.Catalog {
	ceiling := int64(2)
	return worlddata.Catalog{UnexpectedEvents: []worlddata.UnexpectedEvent{
		{ID: "village", Kind: "world_event", Severity: 1, MinRealmIndex: 0, MaxRealmIndex: &ceiling},
		{ID: "middling", Kind: "world_event", Severity: 5, MinRealmIndex: 1},
		{ID: "dragon", Kind: "world_event", Severity: 10, MinRealmIndex: 5},
	}}
}

func idsOf(events []worlddata.UnexpectedEvent) map[string]bool {
	out := map[string]bool{}
	for _, e := range events {
		out[e.ID] = true
	}
	return out
}

func TestTheDrawRefusesWhatTheAskerIsNotReadyFor(t *testing.T) {
	catalog := eventsForBandTest()
	got := idsOf(eligibleUnexpectedEvents(catalog, mechanicsCharacter{RealmIndex: 0}))
	if !got["village"] {
		t.Error("a Body Tempering cultivator cannot draw the village band, which is the whole point")
	}
	if got["middling"] || got["dragon"] {
		t.Errorf("a character three minutes old was handed something pitched well above them: %v", got)
	}
}

func TestTheVillageBandFadesRatherThanFollowingYouUp(t *testing.T) {
	catalog := eventsForBandTest()
	if !idsOf(eligibleUnexpectedEvents(catalog, mechanicsCharacter{RealmIndex: 2}))["village"] {
		t.Error("the band stopped one realm early")
	}
	if idsOf(eligibleUnexpectedEvents(catalog, mechanicsCharacter{RealmIndex: 3}))["village"] {
		t.Error("a caravan going over is still being offered to somebody who could carry the cart")
	}
}

func TestTheTopOfTheLadderStillGetsEverythingAboveTheBand(t *testing.T) {
	got := idsOf(eligibleUnexpectedEvents(eventsForBandTest(), mechanicsCharacter{RealmIndex: 9}))
	if !got["middling"] || !got["dragon"] {
		t.Errorf("a floor became a ceiling: %v", got)
	}
}

// A secret realm's band lives on the realm, not on the event (v1.0.0-rc.53).
//
// `eligibleUnexpectedEvents` has a second branch for `kind: "secret_realm"`
// that reads the *realm's* own `min_realm_index` and `location` rather than
// anything written on the event, so the twelve secret-realm events carry no
// `min_realm_index` and must not grow one: a band on the event would be a
// second statement of a rule the realm already owns, free to drift from it -
// the fault rc.39 removed for the world clock and rc.44 for world currencies.
//
// Nothing drove that branch until now. rc.49's fixture above is all
// `world_event`, so deleting any of its three conditions failed no test, and
// the Python half cannot see a disabled condition - which is the lesson rc.49
// was written to record in the first place.
func realmsForEntranceTest() worlddata.Catalog {
	return worlddata.Catalog{
		SecretRealms: map[string]worlddata.SecretRealm{
			"shallow": {Location: "Greenriver Town", MinRealmIndex: 0},
			"deep":    {Location: "Greenriver Town", MinRealmIndex: 24},
			"far":     {Location: "Celestial Mandate Palace", MinRealmIndex: 0},
		},
		UnexpectedEvents: []worlddata.UnexpectedEvent{
			{ID: "shallow", Kind: "secret_realm", SecretRealmID: "shallow"},
			{ID: "deep", Kind: "secret_realm", SecretRealmID: "deep"},
			{ID: "far", Kind: "secret_realm", SecretRealmID: "far"},
			{ID: "typo", Kind: "secret_realm", SecretRealmID: "no_such_realm"},
		},
	}
}

func TestASecretRealmIsDrawnOnTheRealmsOwnFloor(t *testing.T) {
	catalog := realmsForEntranceTest()
	here := mechanicsCharacter{RealmIndex: 0, Location: "Greenriver Town"}
	got := idsOf(eligibleUnexpectedEvents(catalog, here))
	if !got["shallow"] {
		t.Error("a realm whose floor the character meets, at its own entrance, was not offered")
	}
	if got["deep"] {
		t.Error("a realm-0 cultivator was offered a realm that opens at Dao Saint; the floor is read off the realm")
	}
}

func TestASecretRealmIsDrawnOnlyAtItsEntrance(t *testing.T) {
	catalog := realmsForEntranceTest()
	got := idsOf(eligibleUnexpectedEvents(catalog, mechanicsCharacter{RealmIndex: 31, Location: "Greenriver Town"}))
	if got["far"] {
		t.Error("a realm was offered to somebody standing a world away from its entrance")
	}
	if !got["shallow"] {
		t.Error("the entrance check swallowed the realm the character is actually standing at")
	}
}

func TestASecretRealmEventNamingNoRealmIsNeverOffered(t *testing.T) {
	// A typo'd `secret_realm_id` is silently undrawable for ever rather than an
	// error anywhere - the same class as the peach nothing could produce - so
	// the Python half holds that every id names a realm that exists.
	//
	// Worth knowing which condition does the work, because the drill said so and
	// the obvious reading is wrong: `!ok` is belt-and-braces, not the guard. A
	// missing realm yields the zero value, whose Location is "", and no
	// character stands at "" - so the entrance check already excludes it, and
	// disabling `!ok` alone leaves this test passing. Both have to go before the
	// event is offered. The behaviour is what is held here; `!ok` is a second
	// lock on a door the first one already shut.
	catalog := realmsForEntranceTest()
	got := idsOf(eligibleUnexpectedEvents(catalog, mechanicsCharacter{RealmIndex: 31, Location: "Greenriver Town"}))
	if got["typo"] {
		t.Error("an event naming a realm the catalogue does not carry was offered")
	}
}
