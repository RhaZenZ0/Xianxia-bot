package game

import (
	"sort"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Road-side sites (v0.39.0): a place on every road between two cities - a
// waystation with a stall, a hunting ground, a ruin or a shrine. A site is
// on the leg it names: it is found by walking that leg (a waystation or a
// shrine stands on the road itself and is always seen; a hunting ground or
// a ruin lies off it and is found one time in two), or by exploring from
// either city; it is travelled to from either end as half the leg, and
// from it the road leads on to either end.

var roadSiteDiscoveryIntn = gamerng.Intn

// roadSiteEndpoints is the leg a site lies on, in content order.
func roadSiteEndpoints(catalog worlddata.Catalog, site string) (string, string, bool) {
	loc, ok := catalog.Locations[site]
	if !ok || loc.RoadSite == "" || len(loc.RoadLeg) != 2 || loc.RoadLeg[0] == loc.RoadLeg[1] {
		return "", "", false
	}
	if _, ok := catalog.Locations[loc.RoadLeg[0]]; !ok {
		return "", "", false
	}
	if _, ok := catalog.Locations[loc.RoadLeg[1]]; !ok {
		return "", "", false
	}
	return loc.RoadLeg[0], loc.RoadLeg[1], true
}

// roadSitesOnLeg lists the sites on the road between a and b, by name.
func roadSitesOnLeg(catalog worlddata.Catalog, a, b string) []string {
	sites := []string{}
	for name := range catalog.Locations {
		x, y, ok := roadSiteEndpoints(catalog, name)
		if ok && ((x == a && y == b) || (x == b && y == a)) {
			sites = append(sites, name)
		}
	}
	sort.Strings(sites)
	return sites
}

// roadSiteFarEnd is the end of the site's leg that is not from; "" when
// from is neither end.
func roadSiteFarEnd(catalog worlddata.Catalog, site, from string) string {
	a, b, ok := roadSiteEndpoints(catalog, site)
	switch {
	case !ok:
		return ""
	case from == a:
		return b
	case from == b:
		return a
	}
	return ""
}

// roadFacingNeighbour is the road neighbour a city's gate faces for a
// route node: the node itself when it is a city, the far end of the leg
// when it is a site on one of the city's roads.
func roadFacingNeighbour(catalog worlddata.Catalog, city, node string) string {
	if far := roadSiteFarEnd(catalog, node, city); far != "" {
		return far
	}
	return node
}

// roadSiteOnRoadOfKind says whether a site kind stands on the road itself
// (seen by everyone who walks the leg) or off it (found one time in two).
func roadSiteOnTheRoad(kind string) bool {
	return kind == "waystation" || kind == "shrine"
}

// roadSiteHop plans the half leg between a city and a site on one of its
// roads, in either direction, or between two sites on the same leg. It
// is the road planner's profile for the whole leg with the minutes halved,
// so the danger and the encounter chance are the road's own.
func roadSiteHop(catalog worlddata.Catalog, from, to string, realmIndex int64) (roadRoutePlan, bool) {
	if from == to {
		return roadRoutePlan{}, false
	}
	fromLoc, okFrom := catalog.Locations[from]
	toLoc, okTo := catalog.Locations[to]
	if !okFrom || !okTo || (fromLoc.RoadSite == "" && toLoc.RoadSite == "") {
		return roadRoutePlan{}, false
	}
	var a, b string
	switch {
	case fromLoc.RoadSite != "" && toLoc.RoadSite != "":
		x, y, ok := roadSiteEndpoints(catalog, from)
		if !ok {
			return roadRoutePlan{}, false
		}
		p, q, ok := roadSiteEndpoints(catalog, to)
		if !ok || !((x == p && y == q) || (x == q && y == p)) {
			return roadRoutePlan{}, false
		}
		a, b = x, y
	case toLoc.RoadSite != "":
		x, y, ok := roadSiteEndpoints(catalog, to)
		if !ok || (from != x && from != y) {
			return roadRoutePlan{}, false
		}
		a, b = x, y
	default:
		x, y, ok := roadSiteEndpoints(catalog, from)
		if !ok || (to != x && to != y) {
			return roadRoutePlan{}, false
		}
		a, b = x, y
	}
	endA, endB := catalog.Locations[a], catalog.Locations[b]
	if endA.MinRealmIndex > realmIndex || endB.MinRealmIndex > realmIndex || endA.World != endB.World {
		return roadRoutePlan{}, false
	}
	profile := canonicalRoadTravelProfile(endA, endB, realmIndex)
	profile.TravelMinutes = maxI64(1, profile.TravelMinutes/2)
	cost := roadLegCost(profile)
	leg := roadRouteLeg{From: from, To: to, Profile: profile, Cost: cost}
	return roadRoutePlan{Nodes: []string{from, to}, Legs: []roadRouteLeg{leg}, TravelMinutes: profile.TravelMinutes, Cost: cost, MaxDanger: profile.DangerScore}, true
}

// roadSiteView is what a traveller is told about a site they found.
func roadSiteView(catalog worlddata.Catalog, name string) map[string]any {
	loc := catalog.Locations[name]
	view := map[string]any{"name": name, "kind": loc.RoadSite, "leg": append([]string{}, loc.RoadLeg...), "description": loc.Description, "safe_zone": loc.SafeZone}
	if loc.Shop != "" {
		if shop, ok := catalog.Shops[loc.Shop]; ok {
			view["shop"] = loc.Shop
			view["keeper"] = shop.Keeper
		}
	}
	return view
}

// discoverRoadSitesTx records the sites a traveller found walking the legs
// of a route: every waystation and shrine on it, and the hunting grounds
// and ruins the roll turned up. Legs that touch a site (a half leg) find
// nothing new - the site is already known to be there.
func discoverRoadSitesTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, route []string, gameMinute int64, now float64) ([]map[string]any, error) {
	found := []map[string]any{}
	for i := 0; i+1 < len(route); i++ {
		if catalog.Locations[route[i]].RoadSite != "" || catalog.Locations[route[i+1]].RoadSite != "" {
			continue
		}
		for _, site := range roadSitesOnLeg(catalog, route[i], route[i+1]) {
			if !roadSiteOnTheRoad(catalog.Locations[site].RoadSite) {
				roll, err := roadSiteDiscoveryIntn(100)
				if err != nil {
					return nil, err
				}
				if roll >= 50 {
					continue
				}
			}
			r, err := conn.Execute(`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,?,?,?)`, []any{userID, site, "road_side", gameMinute, now})
			if err != nil {
				return nil, err
			}
			if r.RowsAffected > 0 {
				found = append(found, roadSiteView(catalog, site))
			}
		}
	}
	return found, nil
}

// roadSiteCandidatesTx lists the sites on the roads out of every city the
// character knows that they have not found yet - what exploring a city can
// turn up beside the next city along.
func roadSiteCandidates(catalog worlddata.Catalog, known map[string]bool, world string, realmIndex int64) []string {
	seen := map[string]bool{}
	out := []string{}
	for name := range known {
		loc, ok := catalog.Locations[name]
		if !ok || loc.RoadSite != "" || loc.World != world {
			continue
		}
		for _, neighbour := range canonicalRoadNeighbors(catalog, name, realmIndex) {
			for _, site := range roadSitesOnLeg(catalog, name, neighbour) {
				if known[site] || seen[site] || catalog.Locations[site].MinRealmIndex > realmIndex {
					continue
				}
				seen[site] = true
				out = append(out, site)
			}
		}
	}
	sort.Strings(out)
	return out
}

// huntingGroundBonus is the edge a hunting ground gives: the beasts are
// there to be found, so the roll is easier and the spoils richer.
const huntingGroundRollBonus int64 = 2
