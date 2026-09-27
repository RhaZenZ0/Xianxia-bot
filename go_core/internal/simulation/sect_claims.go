package simulation

// Sects take unclaimed ground (v1.12.0).
//
// Every one of the world's places is a `territory_state` row, seeded neutral,
// and the only writer that ever turned one into a sect's was `territory.claim`
// - a player standing on it. `npcSectWars` moves only on ground a *rival*
// holds, so in a world where no player had claimed anything it had nothing to
// look at: the war step, its sieges and its occupations never ran at all.
// Strong sects claim neutral ground here, before the war step in the same
// tick, and that ground is what the next ambitious sect has to fight for.
//
// Four rules hold it:
//
//   - **Home first.** A sect's first claim is its own gate, and nobody else
//     may take a sect's gate while it lies neutral - home ground is not up for
//     grabs by a stranger who walked past it.
//   - **Then outward by road.** Afterwards it claims only neutral ground one
//     step from something it already holds (`game.WhereAnNPCCanWalk`, the
//     map's own rule). Every gate is road-less, so a sect holding only its
//     gate reaches once into its gate's world for a beachhead - the neutral
//     place a stable hash of the sect's name picks, so a sect always starts
//     from the same town and an empire grows contiguous from there.
//   - **A place, not a street** (`game.TerritoryIsWholePlace`): a city, a road
//     site, the wilds, a gate; never a district, a shop or a private room.
//   - **Bounded.** Only a sect strong enough to go to war claims, at most one
//     place a tick, and never past `2 + (influence-62)/12` holdings, capped at
//     four - so six Mortal sects cannot hold more than 24 of its 28 places,
//     and players always have neutral ground left to stand on.
//
// A claimed place keeps its defence, which starts at 50: under the war step's
// ceiling of 62, so every claim is a place a rival can move on.

import (
	"fmt"
	"sort"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	claimChance       = 25
	claimBaseHoldings = int64(2)
	claimHoldingsStep = int64(12)
	claimMaxHoldings  = int64(4)
	claimUnrest       = 10
)

// claimCap is how many places a sect of this influence will hold.
func claimCap(influence int64) int64 {
	return min64(claimMaxHoldings, claimBaseHoldings+max64(0, influence-warMinInfluence)/claimHoldingsStep)
}

// npcSectClaims lets each strong sect claim at most one neutral place.
func (r *Runner) npcSectClaims(conn *storage.Conn, steps, gm int64) (int64, error) {
	if !simTableExists(conn, "territory_state") || !simTableExists(conn, "sect_politics_state") {
		return 0, nil
	}
	res, err := conn.Execute(`SELECT sect_name,influence FROM sect_politics_state
        WHERE influence>=? AND resources>=? ORDER BY sect_name`, []any{warMinInfluence, warMinResources})
	if err != nil || len(res.Rows) == 0 {
		return 0, err
	}
	// Who holds what, and what is still neutral - read once, not per sect.
	terr, err := conn.Execute(`SELECT t.territory_key,t.controller_type,t.controller_key,
        EXISTS(SELECT 1 FROM territory_wars w WHERE w.territory_key=t.territory_key AND w.status='active')
        FROM territory_state t ORDER BY t.territory_key`, nil)
	if err != nil {
		return 0, err
	}
	held := map[string][]string{}
	neutral := map[string]bool{}
	for _, row := range terr.Rows {
		key, ctype, ckey := fmt.Sprint(row[0]), fmt.Sprint(row[1]), fmt.Sprint(row[2])
		if ctype == "sect" && ckey != "" {
			held[ckey] = append(held[ckey], key)
			continue
		}
		if (ctype == "neutral" || ckey == "") && storage.ParseInt(row[3]) == 0 {
			neutral[key] = true
		}
	}
	// A neutral gate belongs to its own sect alone.
	gateOf := map[string]string{}
	for name := range r.World.Sects {
		if gate := game.SectGate(r.World, name); gate != "" {
			gateOf[gate] = name
		}
	}
	chance := min64(60, claimChance*max1(min64(3, steps)))
	claimed := int64(0)
	now := nowFloat()
	for _, row := range res.Rows {
		sect, influence := fmt.Sprint(row[0]), storage.ParseInt(row[1])
		if int64(len(held[sect])) >= claimCap(influence) {
			continue
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return claimed, err
		}
		if int64(roll) >= chance {
			continue
		}
		target := r.claimTarget(sect, held[sect], neutral, gateOf)
		if target == "" {
			continue
		}
		upd, err := conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MIN(100,unrest+?),updated_game_minute=?,updated_at=?
            WHERE territory_key=? AND (controller_type='neutral' OR controller_key='')`,
			[]any{sect, claimUnrest, gm, now, target})
		if err != nil {
			return claimed, err
		}
		if upd.RowsAffected != 1 {
			continue
		}
		delete(neutral, target)
		held[sect] = append(held[sect], target)
		claimed++
		r.recordTerritoryClaimed(conn, sect, target, gm, now)
	}
	return claimed, nil
}

// claimTarget is the one place this sect claims next, or "" for none.
func (r *Runner) claimTarget(sect string, holdings []string, neutral map[string]bool, gateOf map[string]string) string {
	gate := game.SectGate(r.World, sect)
	if gate == "" {
		return ""
	}
	open := func(place string) bool {
		if !neutral[place] || !game.TerritoryIsWholePlace(r.World, place) {
			return false
		}
		owner, isGate := gateOf[place]
		return !isGate || owner == sect
	}
	// Home first.
	if open(gate) {
		return gate
	}
	world := r.World.Locations[gate].World
	// Then one road step from what it already holds.
	frontier := map[string]bool{}
	for _, place := range holdings {
		for _, next := range game.WhereAnNPCCanWalk(r.World, place, 1<<30) {
			if city := r.wholePlace(next); open(city) && r.World.Locations[city].World == world {
				frontier[city] = true
			}
		}
	}
	if len(frontier) > 0 {
		options := make([]string, 0, len(frontier))
		for place := range frontier {
			options = append(options, place)
		}
		sort.Strings(options)
		pick, err := gamerng.Intn(len(options))
		if err != nil {
			return ""
		}
		return options[pick]
	}
	// Nothing it holds has a road out (a gate never does): one beachhead in
	// its own world, the same one every time, so a sect's expansion starts
	// from a place of its own rather than wherever the dice fell this week.
	best, bestHash := "", uint64(0)
	for place := range neutral {
		if !open(place) || r.World.Locations[place].World != world {
			continue
		}
		if h := hash64(sect, "beachhead", place); best == "" || h < bestHash || (h == bestHash && place < best) {
			best, bestHash = place, h
		}
	}
	return best
}

// wholePlace answers the place a walk step belongs to: a district a road
// step reaches stands for its city.
func (r *Runner) wholePlace(location string) string {
	if game.TerritoryIsWholePlace(r.World, location) {
		return location
	}
	if loc, ok := r.World.Locations[location]; ok && loc.OutsideLocation != "" {
		return loc.OutsideLocation
	}
	return location
}

// recordTerritoryClaimed puts a claim where the world can hear about it -
// quieter than a war (60 against 78), because nobody was driven off.
func (r *Runner) recordTerritoryClaimed(conn *storage.Conn, sect, territory string, gm int64, now float64) {
	if !simTableExists(conn, "world_history_events") {
		return
	}
	world := ""
	if loc, ok := r.World.Locations[territory]; ok {
		world = loc.World
	}
	title := sect + " claims " + territory
	summary := fmt.Sprintf("%s has raised its banners over %s, which answered to no sect before.", sect, territory)
	source := fmt.Sprintf("sect_claim:%s:%s:%d", sect, territory, gm)
	_, _ = conn.Execute(`INSERT INTO world_history_events(
        source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
        actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
        related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?, 'public', ?,?,?, 'faction',?,?, 'territory',?,?, NULL,'',?,?,?,?,?)
        ON CONFLICT(source_key) DO NOTHING`,
		[]any{source, "territory_claimed", title, summary, 60, territory, world, sect,
			sect, sect, territory, territory, "territory claim " + territory, gm, "{}", now, now})
}
