package game

// An ordinary explore searches the ground it stands on (v1.22.1).
//
// Until now a disappearance could be closed one way: `/talk` to the missing
// person by name while standing where they are. That asked a searcher to
// already know who they were looking for, and the talk picker lists a missing
// person only where the searcher stands - so the search was a question nobody
// could ask without the answer. Exploring a place is what looking around it
// means, so an explore now turns up whoever has gone missing *here*, and the
// grave of anybody who died out here and was never reached.
//
// A search has a range, where `/talk` does not: speaking to somebody needs them
// in front of you, while looking around covers ground. The range is the whole
// city the explorer stands in - a city's gate is that city (v1.0.9), and so
// are its districts, shops and halls - and `exploreSearchSteps` steps out from
// it: the road sites on its legs, the wilds beside it, and the cities at the
// far end of its roads, each again taken whole. It is read off the map the
// players walk (canonicalRoadNeighbors, roadSitesOnLeg, wilds_of), filtered to
// the explorer's world and realm, so a search never reaches a place its
// explorer could not walk to. The engine reads each NPC's own location and
// holds it to that set; nothing outside it is found. The writes are
// `markNPCFoundTx` and `claimGraveTx`, the same two statements the
// conversation path runs, inside the explore's own transaction.

import (
	"sort"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// exploreSearchSteps is how many steps out from the explorer's city a search
// reaches. One is the city's own surroundings and its neighbours.
const exploreSearchSteps = 1

// exploreSearchArea is every place a search from `here` covers, sorted: the
// city `here` belongs to and everything that is part of it, then up to
// exploreSearchSteps steps out, each place reached taken with its whole city.
func exploreSearchArea(catalog worlddata.Catalog, here string, realmIndex int64) []string {
	start, ok := catalog.Locations[here]
	if !ok || start.Private {
		return nil
	}
	parts := map[string][]string{}
	wilds := map[string][]string{}
	for name, loc := range catalog.Locations {
		if loc.Private || loc.World != start.World || loc.MinRealmIndex > realmIndex {
			continue
		}
		city := cityOf(catalog, name)
		parts[city] = append(parts[city], name)
		if loc.WildsOf != "" {
			wilds[loc.WildsOf] = append(wilds[loc.WildsOf], name)
		}
	}
	reached := map[string]bool{}
	frontier := []string{cityOf(catalog, here)}
	reached[frontier[0]] = true
	for step := 0; step < exploreSearchSteps; step++ {
		next := []string{}
		visit := func(place string) {
			area := cityOf(catalog, place)
			if area == "" || reached[area] || len(parts[area]) == 0 {
				return
			}
			reached[area] = true
			next = append(next, area)
		}
		for _, area := range frontier {
			for _, neighbour := range canonicalRoadNeighbors(catalog, area, realmIndex) {
				visit(neighbour)
				for _, site := range roadSitesOnLeg(catalog, area, neighbour) {
					visit(site)
				}
			}
			for _, wild := range wilds[area] {
				visit(wild)
			}
			if a, b, ok := roadSiteEndpoints(catalog, area); ok {
				visit(a)
				visit(b)
			}
		}
		sort.Strings(next)
		frontier = next
	}
	out := []string{}
	for area := range reached {
		out = append(out, parts[area]...)
	}
	if len(out) == 0 {
		out = append(out, here)
	}
	sort.Strings(out)
	return out
}

// searchHereTx finds every missing person standing anywhere in the search
// area around `location` and empties every unclaimed grave in it. It returns
// what it found, in name order so a reply reads the same way twice. A world
// without the tables finds nothing.
func searchHereTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, location string, realmIndex, gameMinute int64) (found []map[string]any, graves []map[string]any, err error) {
	area := exploreSearchArea(catalog, strings.TrimSpace(location), realmIndex)
	if len(area) == 0 {
		return nil, nil, nil
	}
	marks := strings.TrimSuffix(strings.Repeat("?,", len(area)), ",")
	args := make([]any, 0, len(area))
	for _, place := range area {
		args = append(args, place)
	}
	if tableExistsTx(conn, "npc_civilization_state") {
		res, err := conn.Execute(`SELECT npc_name,current_location,home_location,missing_since_game_minute
            FROM npc_civilization_state
            WHERE status='missing' AND current_location COLLATE NOCASE IN (`+marks+`)
            ORDER BY npc_name`, args)
		if err != nil {
			return nil, nil, err
		}
		for _, row := range res.Rows {
			name := strings.TrimSpace(nullableText(row[0]))
			if name == "" {
				continue
			}
			out, err := markNPCFoundTx(conn, userID, name, strings.TrimSpace(nullableText(row[1])),
				strings.TrimSpace(nullableText(row[2])), storage.ParseInt(row[3]), gameMinute)
			if err != nil {
				return nil, nil, err
			}
			if ok, _ := out["found"].(bool); ok {
				found = append(found, out)
			}
		}
	}
	if tableExistsTx(conn, "npc_graves") {
		res, err := conn.Execute(`SELECT npc_name,location,home_location,days_missing,keepsake_item,keepsake_stones
            FROM npc_graves
            WHERE location COLLATE NOCASE IN (`+marks+`) AND claimed_game_minute IS NULL
            ORDER BY npc_name`, args)
		if err != nil {
			return nil, nil, err
		}
		for _, row := range res.Rows {
			name := strings.TrimSpace(nullableText(row[0]))
			if name == "" {
				continue
			}
			out, err := claimGraveTx(conn, catalog, userID, name, strings.TrimSpace(nullableText(row[1])),
				strings.TrimSpace(nullableText(row[2])), storage.ParseInt(row[3]),
				strings.TrimSpace(nullableText(row[4])), storage.ParseInt(row[5]), gameMinute)
			if err != nil {
				return nil, nil, err
			}
			if ok, _ := out["claimed"].(bool); ok {
				graves = append(graves, out)
			}
		}
	}
	return found, graves, nil
}

// nullableText reads a column that may be NULL as "", where fmt.Sprint would
// answer "<nil>".
func nullableText(v any) string {
	if v == nil {
		return ""
	}
	if b, ok := v.([]byte); ok {
		return string(b)
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
