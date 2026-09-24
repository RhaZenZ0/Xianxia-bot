package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Every door that tells a cultivator where a sect takes applicants (v1.1.0).
//
// A sect gate is a place no road reaches - all twelve of them are authored
// with no `roads`, and nothing's roads lead to them - so `/explore`, which
// only ever charts one road out from what is known, can never find one. Until
// this release the single player path that wrote a gate onto a travel list was
// a successful NPC recommendation, and it wrote whatever location its *caller*
// named. The envoys' hall said "Their routes are on your map now" and wrote
// nothing a route could be read from, and a Sect Recruitment world event named
// no sect at all.
//
// These helpers are the one statement of that knowledge. The gate is always
// read off the catalogue, never taken from a payload: a location written into
// `character_location_discoveries` becomes an instant jump on `/travel`, so a
// caller who could name it could put any place in the world on a player's map.

// recruitingSectFor decides which sect a recruitment delegation speaks for.
//
// A delegation that stands on a public sect's own gate is that sect's. Any
// other speaks for one of the public sects whose gate is in the world it
// landed in, chosen by a hash of the event key: gates carry no coordinates and
// no road reaches them, so there is no "nearest" for the content to measure,
// and a hash is deterministic - the same event always names the same sect, and
// nothing in it is a roll. An event in a world with no public gate, or at a
// place the catalogue does not carry, speaks for nobody and stays generic.
func recruitingSectFor(catalog worlddata.Catalog, eventKey, location string) string {
	names := make([]string, 0, len(catalog.Sects))
	for name, sect := range catalog.Sects {
		if sect.Hidden || !sect.Recruitment.Public() || strings.TrimSpace(sect.Recruitment.Location) == "" {
			continue
		}
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if catalog.Sects[name].Recruitment.Location == location {
			return name
		}
	}
	here, ok := catalog.Locations[location]
	if !ok || strings.TrimSpace(here.World) == "" {
		return ""
	}
	candidates := make([]string, 0, len(names))
	for _, name := range names {
		gate, ok := catalog.Locations[catalog.Sects[name].Recruitment.Location]
		if ok && gate.World == here.World {
			candidates = append(candidates, name)
		}
	}
	if len(candidates) == 0 {
		return ""
	}
	return candidates[hashString(eventKey+":recruiting")%uint64(len(candidates))]
}

// sectGate is the catalogue's statement of where a sect sits its trial, or ""
// for a sect the catalogue does not carry, a hidden sect, or a gate that is
// not a real place.
func sectGate(catalog worlddata.Catalog, sect string) string {
	def, ok := catalog.Sects[sect]
	if !ok || def.Hidden {
		return ""
	}
	gate := strings.TrimSpace(def.Recruitment.Location)
	if _, ok := catalog.Locations[gate]; !ok {
		return ""
	}
	return gate
}

// revealSectRouteTx records that a cultivator knows a sect and the way to its
// gate. It answers nil, not an error, for a sect with no gate to reveal, so a
// caller never has to refuse an action because the content is thin. The
// public-route question is the caller's: a sponsor reveals a private gate, an
// envoy does not.
func revealSectRouteTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, sect, kind, source string, gameMinute int64, now float64) (map[string]any, error) {
	gate := sectGate(catalog, sect)
	if gate == "" {
		return nil, nil
	}
	known, err := conn.Execute(`INSERT OR IGNORE INTO character_sect_discoveries(user_id,sect_name,discovery_kind,source_key,discovered_game_minute,created_at) VALUES(?,?,?,?,?,?)`,
		[]any{userID, sect, kind, source, gameMinute, now})
	if err != nil {
		return nil, err
	}
	route, err := conn.Execute(`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,?,?,?)`,
		[]any{userID, gate, kind, gameMinute, now})
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"sect_name": sect, "gate": gate,
		"new_sect": known.RowsAffected > 0, "new_route": route.RowsAffected > 0,
	}, nil
}

// resolveRecommenderTx says which sect an NPC may sponsor a cultivator into,
// or refuses. A catalogue NPC answers from the content file; anybody else is
// looked for in the cast of a running world event, where only a cast member
// stamped `can_recommend` at spawn may sponsor, and only in person.
//
// Where a catalogue sponsor is standing is still checked by the bot, because
// schedules and circuits are resolved there; that is deferred in docs/TODO.md
// rather than restated here.
func resolveRecommenderTx(conn *storage.Conn, catalog worlddata.Catalog, npcName string, c mechanicsCharacter, gameMinute int64, now float64) (string, error) {
	if npc, ok := catalog.NPCs[npcName]; ok {
		if !npc.CanRecommend || strings.TrimSpace(npc.SectAffiliation) == "" {
			return "", fmt.Errorf("%s cannot sponsor anybody's entry into a sect", npcName)
		}
		// A catalogue sponsor is asked in person (v1.3.1). The bot checked
		// this and the engine did not, and a bound that lives in the client
		// is not a bound (rc.48).
		where, err := npcWhereaboutsTx(conn, catalog, npcName, gameMinute, now)
		if err != nil {
			return "", err
		}
		if where.Dead {
			return "", fmt.Errorf("%s is dead and recommends nobody", npcName)
		}
		if where.Known && where.Location != c.Location {
			return "", fmt.Errorf("%s is at %s; you are at %s", npcName, where.Location, c.Location)
		}
		return strings.TrimSpace(npc.SectAffiliation), nil
	}
	if !tableExistsTx(conn, "world_event_npcs") {
		return "", fmt.Errorf("%s cannot sponsor anybody's entry into a sect", npcName)
	}
	stamped, err := tableHasColumns(conn, "world_event_npcs", "sect_name", "can_recommend")
	if err != nil {
		return "", err
	}
	if !stamped {
		return "", fmt.Errorf("%s cannot sponsor anybody's entry into a sect", npcName)
	}
	res, err := conn.Execute(`SELECT n.sect_name,n.can_recommend,n.location FROM world_event_npcs n
		JOIN world_events e ON e.event_key=n.event_key
		WHERE n.name=? AND e.active=1 AND e.ends_at>? LIMIT 1`, []any{npcName, now})
	if err != nil {
		return "", err
	}
	row := firstRowMap(res)
	if row == nil || i64(row["can_recommend"]) != 1 || strings.TrimSpace(fmt.Sprint(row["sect_name"])) == "" {
		return "", fmt.Errorf("%s cannot sponsor anybody's entry into a sect", npcName)
	}
	where := fmt.Sprint(row["location"])
	if c.Location != where {
		return "", fmt.Errorf("%s is with the delegation at %s; you are at %s", npcName, where, c.Location)
	}
	return strings.TrimSpace(fmt.Sprint(row["sect_name"])), nil
}

// sectRecruitmentEnvoysActionGo is the envoys' hall of a realm capital: every
// public sect whose gate stands in this world names its gate, and each gate
// goes on the cultivator's travel list. The hall used to be a Python handler
// that recorded the sects and then told the player their routes were on the
// map, which nothing had written.
func sectRecruitmentEnvoysActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p struct {
		GameMinute int64 `json:"game_minute"`
	}
	if len(raw) > 0 {
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
	}
	c, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot call on the sect envoys")
	}
	city := cityOf(catalog, c.Location)
	capital, ok := catalog.Locations[city]
	if !ok || !capital.RealmHub {
		return authoritativeMutation{}, errors.New("the sect envoys keep their halls in the realm capitals, in the temple quarter")
	}
	hall := ""
	for _, part := range cityPartsOf(catalog, city) {
		if catalog.Locations[part].District == "temple" {
			hall = part
			break
		}
	}
	if hall == "" || c.Location != hall {
		where := hall
		if where == "" {
			where = "the temple quarter"
		}
		return authoritativeMutation{}, fmt.Errorf("the envoys' hall is in %s - walk there with /travel", where)
	}
	names := make([]string, 0, len(catalog.Sects))
	for name, sect := range catalog.Sects {
		if sect.Hidden || !sect.Recruitment.Public() {
			continue
		}
		gate := sectGate(catalog, name)
		if gate == "" || catalog.Locations[gate].World != capital.World {
			continue
		}
		names = append(names, name)
	}
	sort.Strings(names)
	now := nowSeconds()
	revealed := make([]map[string]any, 0, len(names))
	for _, name := range names {
		shown, err := revealSectRouteTx(conn, catalog, userID, name, "envoys_hall", hall, p.GameMinute, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if shown != nil {
			revealed = append(revealed, shown)
		}
	}
	out := map[string]any{"hall": hall, "city": city, "world": capital.World, "sects": revealed}
	return authoritativeMutation{Result: out, Event: eventledger.Event{
		Domain: "sect", EventType: "sect.recruitment.envoys", EntityType: "character",
		EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out,
	}}, nil
}
