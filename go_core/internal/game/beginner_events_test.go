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
