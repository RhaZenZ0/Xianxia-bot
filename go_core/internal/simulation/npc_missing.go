package simulation

// People who walk out of a town and do not arrive anywhere (v1.0.0-rc.23).
//
// `npcTravel` deliberately stores no journey - "nothing tracks how long they
// have been away - this is what makes the journey end without storing a
// journey" - and that is the right rule for an errand and the wrong one for a
// disappearance, because how long it has lasted is the whole of what makes a
// disappearance one. Schema 47 keeps the single thing about a journey worth
// keeping: the minute it stopped being one.
//
// `status` carries 'missing' beside 'alive' and 'dead'. Every autonomous batch
// already reads `WHERE status='alive'`, so a missing person stops travelling,
// courting, working and bearing children by construction rather than by a rule
// written four more times.
//
// The significance is the point of the feature. `forge_quests_from_history`
// drafts a quest per notable public history row at or above
// QUEST_FORGE_MIN_SIGNIFICANCE, which defaults to 80 - and the whole of this
// package tops out at 74, so no autonomous event has ever been able to reach
// the Forge at all. A disappearance is the first one that can, and it is the
// right first one: "find them" is a quest a world can ask for and a player can
// answer, and the answer is mechanical, because `/talk` only succeeds where
// the NPC actually is.

import (
	"fmt"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// Only the already-away can vanish. Somebody standing in their own town
	// is not missing, they are at home.
	missingChance = 3
	missingCap    = 1
	// Above the Forge's default bar of 80, which nothing in this package has
	// ever cleared.
	missingSignificance = 82
	foundSignificance   = 58
	// Nobody frees themselves. That is the whole reason the search is worth
	// asking for: if the missing wandered home by themselves the quest would
	// be decoration, and the Forge would be drafting work that resolves
	// itself while the player reads it.
	//
	// What they can do is last. Wherever they have ended up has water and
	// something to eat - for a while, and then it does not. The grace is
	// deliberately long: a disappearance has to survive being noticed,
	// drafted, approved by a GM and travelled to, and still leave time to
	// matter.
	missingGraceDays         = 60
	missingHealthDrain       = 4
	missingDeathCause        = "lost, and not found in time"
	missingDeathSignificance = 74
)

// npcDisappearances loses a few people, and buries the ones nobody went after.
// Returns (vanished this tick, died out there this tick).
func (r *Runner) npcDisappearances(conn *storage.Conn, gm int64) (int64, int64, error) {
	if !simTableExists(conn, "npc_civilization_state") {
		return 0, 0, nil
	}
	// What the surroundings cost them comes first, so somebody cannot vanish
	// and starve inside one tick.
	died, err := r.npcMissingHardship(conn, gm)
	if err != nil {
		return 0, died, err
	}
	now := nowFloat()
	res, err := conn.Execute(`SELECT npc_name,home_location,current_location,world_name
        FROM npc_civilization_state
        WHERE status='alive' AND current_location<>'' AND current_location<>home_location
        ORDER BY npc_name`, nil)
	if err != nil {
		return 0, died, err
	}
	vanished := int64(0)
	for _, row := range res.Rows {
		if vanished >= missingCap {
			break
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return vanished, died, err
		}
		if int64(roll) >= missingChance {
			continue
		}
		name := fmt.Sprint(row[0])
		home, where, world := fmt.Sprint(row[1]), fmt.Sprint(row[2]), fmt.Sprint(row[3])
		if _, err := conn.Execute(`UPDATE npc_civilization_state
            SET status='missing',missing_since_game_minute=?,activity='Whereabouts unknown',
                last_game_minute=?,updated_at=?
            WHERE npc_name=? AND status='alive'`,
			[]any{gm, gm, now, name}); err != nil {
			return vanished, died, err
		}
		// The row names where they were last seen rather than where they are,
		// because the first is what the world knows and the second is what a
		// searcher has to work out. `where` stays on the NPC's own row, so
		// finding them is a matter of standing in the right place.
		if err := r.recordMissing(conn, name, home, where, world, gm, now); err != nil {
			return vanished, died, err
		}
		vanished++
	}
	return vanished, died, nil
}

// recordMissing writes the row the Quest Forge reads.
func (r *Runner) recordMissing(conn *storage.Conn, name, home, where, world string, gm int64, now float64) error {
	_ = world
	r.recordNPCHistory(conn, "npc_missing", fmt.Sprintf("npc_missing:%s:%d", name, gm),
		name+" has not come home",
		fmt.Sprintf("%s left %s and never arrived. The last anyone can place them is the road out of %s, and nobody at %s has seen them since.",
			name, home, where, home),
		home, name, missingSignificance, gm, now)
	return nil
}

// npcMissingHardship spends what the surroundings had.
//
// Past the grace the place stops providing, and health goes with it. A missing
// person who runs out is the world's answer to a search nobody made - which is
// the cost that makes the search mean something.
func (r *Runner) npcMissingHardship(conn *storage.Conn, gm int64) (int64, error) {
	if !simTableExists(conn, "npc_life_state") {
		return 0, nil
	}
	now := nowFloat()
	grace := int64(missingGraceDays) * minutesPerDay
	if _, err := conn.Execute(`UPDATE npc_life_state
        SET health=MAX(0,health-?),injury='going hungry somewhere nobody has looked',
            injury_severity=MIN(10,injury_severity+1),updated_at=?
        WHERE health>0 AND npc_name IN (
            SELECT npc_name FROM npc_civilization_state
            WHERE status='missing' AND missing_since_game_minute>0
              AND ?-missing_since_game_minute>=?)`,
		[]any{missingHealthDrain, now, gm, grace}); err != nil {
		return 0, err
	}
	res, err := conn.Execute(`SELECT c.npc_name,c.home_location,c.current_location,c.missing_since_game_minute
        FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.status='missing' AND l.health<=0
        ORDER BY c.missing_since_game_minute,c.npc_name`, nil)
	if err != nil {
		return 0, err
	}
	died := int64(0)
	for _, row := range res.Rows {
		name := fmt.Sprint(row[0])
		home, where := fmt.Sprint(row[1]), fmt.Sprint(row[2])
		days := int64(0)
		if since := i64(row[3]); since > 0 && gm > since {
			days = (gm - since) / minutesPerDay
		}
		if _, err := conn.Execute(`UPDATE npc_life_state
            SET health=0,death_game_minute=?,cause_of_death=?,updated_at=? WHERE npc_name=?`,
			[]any{gm, missingDeathCause, now, name}); err != nil {
			return died, err
		}
		if _, err := conn.Execute(`UPDATE npc_civilization_state
            SET status='dead',activity='Deceased',last_game_minute=?,updated_at=? WHERE npc_name=?`,
			[]any{gm, now, name}); err != nil {
			return died, err
		}
		if err := game.ReleaseNPCBondsTx(conn, name, gm, now); err != nil {
			return died, err
		}
		// Recorded against the home that was waiting, not the place nobody
		// found: a town learns that somebody is not coming back, and it does
		// not learn where they were.
		r.recordNPCHistory(conn, "npc_missing_death", fmt.Sprintf("npc_missing_death:%s:%d", name, gm),
			name+" is not coming back",
			fmt.Sprintf("%s was never found. They had been missing from %s for %d day(s), and whatever kept them alive out there ran out.",
				name, home, days),
			home, name, missingDeathSignificance, gm, now)
		_ = where
		died++
	}
	return died, nil
}
