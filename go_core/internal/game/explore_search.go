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
// in front of you, while looking around covers ground. The range is the
// surroundings of where the explorer stands, and it stops before the next
// city. Inside a city - a gate, district, shop or hall is that city (v1.0.9) -
// it is the whole city, the road sites on the roads leaving it and the wilds
// beside it. In the wilds of a city it is that same ground. On a road site it
// is that road: the sites along it, not the cities at either end. It is read
// off the map the players walk (canonicalRoadNeighbors, roadSitesOnLeg,
// wilds_of), filtered to the explorer's world and realm, so a search never
// reaches a place its explorer could not walk to. The engine reads each NPC's
// own location and holds it to that set; nothing outside it is found. The
// writes are `markNPCFoundTx` and `claimGraveTx`, the same two statements the
// conversation path runs, inside the explore's own transaction.

import (
	"sort"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// exploreSearchArea is every place a search from `here` covers, sorted (see
// the file's header for the rule).
func exploreSearchArea(catalog worlddata.Catalog, here string, realmIndex int64) []string {
	start, ok := catalog.Locations[here]
	if !ok || start.Private {
		return nil
	}
	seen := map[string]bool{}
	out := []string{}
	add := func(name string) {
		loc, ok := catalog.Locations[name]
		if !ok || seen[name] || loc.Private || loc.World != start.World || loc.MinRealmIndex > realmIndex {
			return
		}
		seen[name] = true
		out = append(out, name)
	}
	if a, b, onRoad := roadSiteEndpoints(catalog, here); onRoad {
		// On the road: the road, and not the cities it joins.
		add(here)
		for _, site := range roadSitesOnLeg(catalog, a, b) {
			add(site)
		}
		sort.Strings(out)
		return out
	}
	city := cityOf(catalog, here)
	if start.WildsOf != "" {
		city = start.WildsOf
	}
	for _, name := range sortedLocationNames(catalog) {
		loc := catalog.Locations[name]
		if cityOf(catalog, name) == city || loc.WildsOf == city {
			add(name)
		}
	}
	for _, neighbour := range canonicalRoadNeighbors(catalog, city, realmIndex) {
		for _, site := range roadSitesOnLeg(catalog, city, neighbour) {
			add(site)
		}
	}
	add(here)
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
