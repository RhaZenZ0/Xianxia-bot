package simulation

// Somebody gets there first (v1.0.0-rc.24).
//
// The Tomb-Watch Clan has listed "Grave-robbers" among its troubles since the
// birth families were written, and until now that was a line of prose about a
// thing the world could not do. `npcFindChance` already gives the highest find
// rate in the game to any trade matching grave/tomb/relic/scaveng/prospect/
// digger/miner/salvage - but their finds were abstract, drawn from a catalogue
// pool, because there was nothing in the world to dig up. Schema 48 put graves
// in the ground. This is what walks up to one.
//
// It exists to make the search a race. A disappearance is the first autonomous
// event significant enough to reach the Quest Forge, and a quest a player can
// take at leisure is not really a quest - the missing already die on a clock,
// and now what they left does too. Arriving late is no longer the same as
// arriving; it is finding the ground turned over.
//
// Two rules, both borrowed rather than invented:
//
// The grave keeps a grace. `graveRobGraceDays` is the window in which the
// grave is the player's to find, and it is deliberately several ticks long -
// long enough for the Forge to draft the quest, a GM to pass it and somebody
// to walk there. The world only gets to it after that.
//
// And the deed is `hidden` while the goods are not. Nobody stood in the
// wilderness and watched a grave-robber work, so by the visibility ladder this
// package already keeps, the history row never reaches narrator RAG: the world
// genuinely does not know. What the world does get is the lot, on the floor of
// the nearest house, with `seller_npc_name` on it - which is the one fence of
// the two that records who brought it in. A player who reaches an emptied
// grave and later finds the dead herbalist's satchel under a known digger's
// name has worked it out from the world rather than been told by a log line.

import (
	"fmt"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// How long a grave is the searcher's alone. `npc_life` ticks every seven
	// game-days, so this is three ticks in which nobody else can touch it.
	graveRobGraceDays = 21
	// Per grave, once the grace is spent and somebody who would is in reach.
	graveRobChanceDigger = 20
	// A trade that is not this one still needs a reason. The same reason the
	// crimes step uses: wanting something badly with little enough to lose.
	graveRobChanceDesperate = 5
	// One a tick, for the reason travel and crime are capped: a world where
	// every grave is turned over in a night is not a living world.
	graveRobCap = 1
	// Below the Forge's bar on purpose. A grave nobody witnessed being robbed
	// must not become a quest - there is no one to report it and nothing to
	// find. The number is what the row is worth to the chronicle, not to a
	// player.
	graveRobSignificance = 44
)

// graveRobTrade is who digs. The same word list `npcFindChance` grades a
// find by, read as a predicate rather than a rate, so the two cannot drift
// apart into a person who finds relics for a living and would not open a
// grave.
func graveRobTrade(profession string) bool {
	return npcFindChance(profession) == findChanceDigger
}

// npcGraveRobbing empties the graves nobody came for. Returns how many.
func (r *Runner) npcGraveRobbing(conn *storage.Conn, gm int64) (int64, error) {
	if !simTableExists(conn, "npc_graves") || !simTableExists(conn, "npc_civilization_state") {
		return 0, nil
	}
	cutoff := gm - int64(graveRobGraceDays)*minutesPerDay
	if cutoff <= 0 {
		return 0, nil
	}
	// `claimed_game_minute` is what says a grave has been emptied, by anybody:
	// a player's claim anonymises its finder on erasure and this must not read
	// that as a refilled grave. Same rule as `claimGraveResult`.
	graves, err := conn.Execute(`SELECT npc_name,location,world_name,keepsake_item,keepsake_stones
        FROM npc_graves
        WHERE claimed_game_minute IS NULL AND died_game_minute>0 AND died_game_minute<=?
        ORDER BY died_game_minute,npc_name`, []any{cutoff})
	if err != nil {
		return 0, err
	}
	if len(graves.Rows) == 0 {
		return 0, nil
	}
	people, err := r.aliveNPCs(conn)
	if err != nil {
		return 0, err
	}
	// Who could walk to what. Built once from the people rather than once per
	// grave, and keyed the way the graves are read: the grave's own location.
	inReach := map[string][]npcDeed{}
	for _, person := range people {
		if !graveRobTrade(person.profession) &&
			!(person.ambition >= crimeDesperateAmbition && person.wealth <= crimeDesperateWealth) {
			continue
		}
		for place := range r.romanceReach(person.location, person.world, person.realmIndex) {
			inReach[place] = append(inReach[place], person)
		}
	}
	if len(inReach) == 0 {
		return 0, nil
	}

	now := nowFloat()
	robbed := int64(0)
	for _, row := range graves.Rows {
		if robbed >= graveRobCap {
			break
		}
		where := fmt.Sprint(row[1])
		candidates := inReach[where]
		if len(candidates) == 0 {
			continue
		}
		pick, err := gamerng.Intn(len(candidates))
		if err != nil {
			return robbed, err
		}
		robber := candidates[pick]
		chance := int64(graveRobChanceDesperate)
		if graveRobTrade(robber.profession) {
			chance = graveRobChanceDigger
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return robbed, err
		}
		if int64(roll) >= chance {
			continue
		}
		dead := fmt.Sprint(row[0])
		world, item, stones := fmt.Sprint(row[2]), fmt.Sprint(row[3]), i64(row[4])
		took, err := r.robGrave(conn, robber, dead, where, world, item, stones, gm, now)
		if err != nil {
			return robbed, err
		}
		if took {
			robbed++
		}
	}
	return robbed, nil
}

// robGrave does the one thing, in the order that makes a missed tick safe: the
// grave is closed first, under the same guard a player's claim uses, so
// nothing can be taken out of it twice. Only then is anyone paid.
func (r *Runner) robGrave(
	conn *storage.Conn, robber npcDeed, dead, where, world, item string, stones, gm int64, now float64,
) (bool, error) {
	// `claimed_by_user_id` stays NULL: no cultivator reached this one. The
	// grave still reads as emptied, because it is, and `/talk` answers a
	// player who arrives afterwards with `already_claimed`.
	res, err := conn.Execute(`UPDATE npc_graves SET claimed_game_minute=?,updated_at=?
        WHERE npc_name=? AND claimed_game_minute IS NULL`, []any{gm, now, dead})
	if err != nil {
		return false, err
	}
	if res.RowsAffected == 0 {
		return false, nil
	}
	// The purse left the world when the grave was dug (`digGrave` zeroes the
	// dead NPC's wealth as it writes it in), so this is that money coming back
	// rather than new money being made.
	if stones > 0 {
		if err := r.moveWealth(conn, robber.name, "", stones, gm, now); err != nil {
			return true, err
		}
	}
	// The keepsake goes under the hammer, not into the night market: grave
	// goods here are ordinary materials a legal floor will take, and the
	// auction row is the only one of the two that records who brought it in.
	// If no house fronts this stretch of road they simply keep it, and the
	// world is left with no evidence at all - which is the worse outcome for a
	// player and the right one for the fiction.
	if item != "" {
		if _, err := r.consignToNearestHouse(conn, robber.name, robber.location, item,
			r.World.Items[item], robber.profession, robber.realmIndex, gm); err != nil {
			return true, err
		}
	}
	if err := r.setActivity(conn, robber.name, "Selling on what was not theirs", gm, now); err != nil {
		return true, err
	}
	r.recordDeed(conn, "npc_grave_robbery", fmt.Sprintf("npc_grave_robbery:%s:%d", dead, gm),
		dead+"'s grave is opened",
		fmt.Sprintf("The ground over %s at %s has been turned over and what they were buried with is gone. There was no one out there to see it done.",
			dead, where),
		"hidden", where, world, robber.name, dead, graveRobSignificance, gm, now)
	return true, nil
}
