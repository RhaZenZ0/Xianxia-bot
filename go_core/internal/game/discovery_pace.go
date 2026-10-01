package game

import "xianxia/core/internal/worlddata"

// The pace at which /explore fills a map (v1.14.2).
//
// Travel goes to any *known* city of the world in one command, plans the
// road route itself and, at the shipped TRAVEL_TIME_PERCENT of 0, waits
// nothing; the capital is a free jump from anywhere. So what stood between a
// player and the far side of their world was never the roads - it was that
// a city has to be known first, and an explore found one place in 45 at
// random from a pool where every city competed with the two road sites on
// each of its roads and the wilds beside it. Measured off the shipped
// content, a cultivator starting in Greenriver Town needed about 45 explores
// (57 at the ninetieth percentile) on a twenty-minute cooldown to put the
// twelve Mortal cities on their map: fifteen hours of play before the ring
// was open. Roading every city to every city was considered and refused - it
// would not change that pool, and six systems read the road graph as a
// shape. The owner's call was the smaller change, and this file is the whole
// of it:
//
//   - a city is found before anything else. While the road frontier still
//     holds a city the character does not know, an explore turns up a city;
//     the sites on the roads out of a known city and the wilds beside it are
//     found once the cities around them are. Nothing is lost by the order:
//     a hunt works anywhere and is only richer on a hunting ground, and a
//     road's sites are also found by walking it (v0.39.0).
//   - the roll is discoveryChancePercent in a hundred rather than 45.
//
// From Greenriver Town the twelve Mortal cities are now nine finds away at
// seven explores in ten, about thirteen explores where it was forty-five.
// `discovery_pace_test.go` holds both rules and the count.

// discoveryChancePercent is the chance, in a hundred, that an explore turns
// up a place the character did not know. Stated once; the roll reads it.
const discoveryChancePercent = 70

// citiesFirst narrows a discovery pool to its cities while it has any: a
// location the road network reaches (one carrying `roads`) is a city, and
// a road site or a wild place is not. A pool with no city in it is returned
// whole, so the sites and the wilds are found in the order they always were
// once the cities around them are known.
func citiesFirst(catalog worlddata.Catalog, candidates []string) []string {
	cities := make([]string, 0, len(candidates))
	for _, name := range candidates {
		if loc, ok := catalog.Locations[name]; ok && len(loc.Roads) > 0 {
			cities = append(cities, name)
		}
	}
	if len(cities) == 0 {
		return candidates
	}
	return cities
}
