package simulation

// The world's own people moving through it: NPCs walking the roads, and NPCs
// joining and leaving the sects.
//
// Both were tables that existed and never changed. `npc_civilization_state`
// has carried `home_location`, `current_location` and `faction` since the
// simulation was built, and the daily civilization tick moved wealth,
// influence, ambition, activity and phase - never any of those three. Nothing
// autonomous had ever written `current_location`: it was set at bootstrap and
// then only ever touched by a merchant relocating, one game action, and an
// admin undo. So every NPC in the world stood exactly where they were born,
// and `current_npc_location`'s branch for "autonomous civilization travel has
// moved them away from their home region" was unreachable code guarding a
// thing that could not happen. `faction` was the same: chosen at bootstrap and
// frozen, so no sect ever gained or lost a single member.

import (
	"fmt"
	"strings"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// How likely an NPC is to be on the road at all, by what they do for a living.
// A peddler is almost always travelling and a gate guard almost never is.
const (
	travelChanceTrader   = 55
	travelChanceWanderer = 35
	travelChanceOrdinary = 14
	travelChanceRooted   = 4
	// Once away from home, the chance each tick that their business is done
	// and they simply go back. Nothing tracks how long they have been away -
	// this is what makes the journey end without storing a journey.
	travelChanceGoHome = 38
	// No more than this many NPCs move in one tick, however long the tick.
	// A world where four hundred people relocate overnight is not a living
	// world, it is a stampede.
	travelMovedCap = 40
	// The chance, per tick, that an NPC standing where a raised crossing
	// stands is the one who walks through it, when the content does not say.
	defaultNPCCrossingChance = 6
	// How many realms either side of the cultivator who tore a seam still fit
	// through it, when the content does not say.
	defaultNPCCrossingRealmReach = 2
)

func travelChanceFor(profession string) int64 {
	p := strings.ToLower(profession)
	switch {
	case strings.Contains(p, "merchant"), strings.Contains(p, "broker"),
		strings.Contains(p, "peddler"), strings.Contains(p, "trader"),
		strings.Contains(p, "caravan"):
		return travelChanceTrader
	case strings.Contains(p, "hunter"), strings.Contains(p, "courier"),
		strings.Contains(p, "scout"), strings.Contains(p, "wanderer"),
		strings.Contains(p, "pilgrim"):
		return travelChanceWanderer
	case strings.Contains(p, "guard"), strings.Contains(p, "warden"),
		strings.Contains(p, "keeper"), strings.Contains(p, "innkeeper"),
		strings.Contains(p, "steward"), strings.Contains(p, "elder"),
		strings.Contains(p, "sovereign"), strings.Contains(p, "empress"),
		strings.Contains(p, "emperor"):
		return travelChanceRooted
	}
	return travelChanceOrdinary
}

// neighbours is where an NPC standing here could walk next. It is the
// engine's own map rule (game.WhereAnNPCCanWalk), not a second copy of it: a
// first attempt here matched only `roads` and `gates` and so connected the
// forty-eight cities while leaving 429 of the world's 477 places - every
// district, waystation, shrine and shop - with no neighbour at all, which
// would have meant almost every NPC alive could never move.
func (r *Runner) neighbours(location, worldName string, realmIndex int64) []string {
	def, ok := r.World.Locations[location]
	if !ok || (worldName != "" && def.World != worldName) {
		return nil
	}
	return game.WhereAnNPCCanWalk(r.World, location, realmIndex)
}

// npcTravel walks the living NPCs along the roads for this tick.
//
// A journey is not stored anywhere. Each tick an NPC either stays, steps to a
// neighbouring place, or - if they are already away - goes home. That keeps
// the whole thing in one pass with no new columns and no half-finished
// journeys to reconcile if a tick is missed or replayed.
func (r *Runner) npcTravel(conn *storage.Conn, steps, gm int64) (int64, error) {
	res, err := conn.Execute(`SELECT npc_name,home_location,current_location,world_name,profession,ambition,realm_index
        FROM npc_civilization_state WHERE status='alive' ORDER BY npc_name`, nil)
	if err != nil {
		return 0, err
	}
	// The gates players have torn open (v1.0.0-rc.44). Loaded once, not once
	// per NPC: there are single figures of these against five hundred and
	// seventy-four people, and the whole point of a batched tick is that the
	// second number never becomes a query count.
	gates, err := game.OpenCrossings(conn)
	if err != nil {
		return 0, err
	}
	crossingChance := r.World.WorldCrossing.NPCCrossingChance
	if crossingChance <= 0 {
		crossingChance = defaultNPCCrossingChance
	}
	crossingReach := r.World.WorldCrossing.NPCCrossingRealmReach
	if crossingReach <= 0 {
		crossingReach = defaultNPCCrossingRealmReach
	}
	now := nowFloat()
	moved := int64(0)
	for _, row := range res.Rows {
		if moved >= travelMovedCap {
			break
		}
		name := fmt.Sprint(row[0])
		// A hidden master with a circuit is placed by content against the
		// canonical clock; the tick must not also move them.
		if def, ok := r.World.NPCs[name]; ok && len(def.Circuit) > 0 {
			continue
		}
		home, current := fmt.Sprint(row[1]), fmt.Sprint(row[2])
		worldName, profession := fmt.Sprint(row[3]), fmt.Sprint(row[4])
		ambition, realmIndex := i64(row[5]), i64(row[6])

		chance := travelChanceFor(profession)
		if ambition > 70 {
			chance += 8
		}
		// A longer tick is more days in which to have set out.
		chance = min64(85, chance*min64(3, max1(steps)))
		roll, err := gamerng.Intn(100)
		if err != nil {
			return moved, err
		}
		if int64(roll) >= chance {
			continue
		}

		destination := ""
		crossed := game.Crossing{}
		// A raised crossing is a road, and it is the only road in the game
		// that leaves a world - `WhereAnNPCCanWalk` refuses a destination in
		// another world by construction, which is right for content roads and
		// is exactly what a torn seam is an exception to.
		//
		// It is not a road for everybody standing on it. A seam is cut to the
		// measure of the cultivator who survived the storm that made it
		// (`game.NPCMayCross`), so the people who can follow them through are
		// the ones whose own cultivation is near theirs - never the village
		// smith who happened to be in the town that day.
		//
		// Somebody who does step through is a visitor: their `world_name` is
		// unchanged, so the far side offers them no onward neighbours and the
		// going-home roll brings them back. That is a journey through the gate
		// and out again, which is what a gate is for.
		if gate, standing := gates[current]; standing && game.NPCMayCross(gate, realmIndex, crossingReach) {
			crossRoll, err := gamerng.Intn(100)
			if err != nil {
				return moved, err
			}
			if int64(crossRoll) < crossingChance {
				destination, crossed = gate.Destination, gate
			}
		}
		if destination == "" && current != home {
			homeRoll, err := gamerng.Intn(100)
			if err != nil {
				return moved, err
			}
			if int64(homeRoll) < travelChanceGoHome {
				destination = home
			}
		}
		if destination == "" {
			options := r.neighbours(current, worldName, realmIndex)
			if len(options) == 0 {
				continue
			}
			pick, err := gamerng.Intn(len(options))
			if err != nil {
				return moved, err
			}
			destination = options[pick]
		}
		if destination == "" || destination == current {
			continue
		}
		activity := "Travelling"
		if destination == home {
			activity = "Returning home"
		}
		if crossed.Location != "" {
			activity = "Crossing over"
		}
		if _, err := conn.Execute(
			`UPDATE npc_civilization_state SET current_location=?,activity=?,last_game_minute=?,updated_at=? WHERE npc_name=?`,
			[]any{destination, activity, gm, now, name}); err != nil {
			return moved, err
		}
		if crossed.Location != "" {
			if err := game.RecordNPCCrossingTx(conn, r.World, name, crossed.Name, crossed.Location, crossed.Destination, crossed.ToWorld, gm, now); err != nil {
				return moved, err
			}
		}
		moved++
	}
	return moved, nil
}

// How a sect gains and loses people. Both are gated on the sect's own state,
// which the politics tick is already maintaining and nothing was reading: a
// sect under recruitment pressure actually recruits, and a sect that has come
// apart actually loses members.
const (
	sectJoinPressure  = 60 // recruitment_pressure above this and the sect is hunting
	sectLeaveCohesion = 35 // cohesion below this and people start walking out
	sectJoinAmbition  = 45 // an NPC below this is not looking for a banner
	sectChangeCap     = 12 // per tick, for the same reason travel is capped
	sectJoinChance    = 30
	sectLeaveChance   = 22
)

// npcSectChanges moves NPCs into and out of sects, and writes what happened
// where the world can see it: a line in the sect's own politics log, and a
// public world-history row, because a named cultivator changing banner is the
// sort of thing a town talks about.
func (r *Runner) npcSectChanges(conn *storage.Conn, steps, gm int64) (int64, int64, error) {
	if !simTableExists(conn, "sect_politics_state") {
		return 0, 0, nil
	}
	sects, err := conn.Execute(`SELECT sect_name,recruitment_pressure,cohesion FROM sect_politics_state ORDER BY sect_name`, nil)
	if err != nil {
		return 0, 0, err
	}
	recruiting := []string{}
	crumbling := map[string]bool{}
	for _, row := range sects.Rows {
		name := fmt.Sprint(row[0])
		if i64(row[1]) >= sectJoinPressure {
			recruiting = append(recruiting, name)
		}
		if i64(row[2]) <= sectLeaveCohesion {
			crumbling[name] = true
		}
	}

	joined, left := int64(0), int64(0)
	now := nowFloat()
	changed := 0

	if len(recruiting) > 0 {
		free, err := conn.Execute(`SELECT npc_name,current_location,world_name,ambition FROM npc_civilization_state
            WHERE status='alive' AND (faction='' OR faction='Independent') AND ambition>=?
            ORDER BY ambition DESC,npc_name LIMIT 120`, []any{sectJoinAmbition})
		if err != nil {
			return 0, 0, err
		}
		for _, row := range free.Rows {
			if changed >= sectChangeCap {
				break
			}
			roll, err := gamerng.Intn(100)
			if err != nil {
				return joined, left, err
			}
			if int64(roll) >= min64(70, sectJoinChance*max1(min64(3, steps))) {
				continue
			}
			pick, err := gamerng.Intn(len(recruiting))
			if err != nil {
				return joined, left, err
			}
			sect := recruiting[pick]
			name, where := fmt.Sprint(row[0]), fmt.Sprint(row[1])
			if _, err := conn.Execute(
				`UPDATE npc_civilization_state SET faction=?,activity='Newly sworn to a sect',last_game_minute=?,updated_at=? WHERE npc_name=?`,
				[]any{sect, gm, now, name}); err != nil {
				return joined, left, err
			}
			r.recordSectMove(conn, sect, name, where, "joined", gm, now)
			joined++
			changed++
		}
	}

	if len(crumbling) > 0 {
		names := make([]string, 0, len(crumbling))
		args := make([]any, 0, len(crumbling))
		for sect := range crumbling {
			names = append(names, "?")
			args = append(args, sect)
		}
		leavers, err := conn.Execute(`SELECT npc_name,current_location,faction FROM npc_civilization_state
            WHERE status='alive' AND faction IN (`+strings.Join(names, ",")+`) ORDER BY npc_name LIMIT 120`, args)
		if err != nil {
			return joined, left, err
		}
		for _, row := range leavers.Rows {
			if changed >= sectChangeCap {
				break
			}
			roll, err := gamerng.Intn(100)
			if err != nil {
				return joined, left, err
			}
			if int64(roll) >= min64(60, sectLeaveChance*max1(min64(3, steps))) {
				continue
			}
			name, where, sect := fmt.Sprint(row[0]), fmt.Sprint(row[1]), fmt.Sprint(row[2])
			if _, err := conn.Execute(
				`UPDATE npc_civilization_state SET faction='Independent',activity='Masterless',last_game_minute=?,updated_at=? WHERE npc_name=?`,
				[]any{gm, now, name}); err != nil {
				return joined, left, err
			}
			r.recordSectMove(conn, sect, name, where, "left", gm, now)
			left++
			changed++
		}
	}
	return joined, left, nil
}

// recordSectMove writes the change where people can find it. Best-effort on
// purpose: a tick must not fail because a history table is absent on an old
// database, and the membership change itself is already committed.
func (r *Runner) recordSectMove(conn *storage.Conn, sect, npcName, location, verb string, gm int64, now float64) {
	title := npcName + " " + verb + " " + sect
	summary := npcName + " has left " + sect + "; the sect is coming apart and they are not the first."
	if verb == "joined" {
		summary = npcName + " has sworn to " + sect + ", which is recruiting hard."
	}
	if simTableExists(conn, "sect_politics_events") {
		_, _ = conn.Execute(
			`INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`,
			[]any{sect, summary, 1, gm, now})
	}
	if simTableExists(conn, "world_history_events") {
		source := fmt.Sprintf("sect_move:%s:%s:%d", sect, npcName, gm)
		_, _ = conn.Execute(`INSERT INTO world_history_events(
            source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
            actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
            related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
            VALUES(?,?,?,?,?, 'public', ?,'',?, 'npc',?,?, 'faction',?,?, NULL,?,?,?,?,?,?)
            ON CONFLICT(source_key) DO NOTHING`,
			[]any{source, "sect_membership", title, summary, 35, location, sect,
				npcName, npcName, sect, sect, npcName, "sect membership " + verb, gm, "{}", now, now})
	}
}
