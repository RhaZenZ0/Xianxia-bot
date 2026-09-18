package simulation

// People who walk out of a town and do not arrive anywhere (v1.0.0-rc.24).
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

	"strings"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// Only the already-away can vanish. Somebody standing in their own town
	// is not missing, they are at home.
	missingChance = 3
	missingCap    = 1
	// The disappearance row's significance lives in game
	// (NPCMissingSignificance, 82): above the Forge's default bar of 80,
	// which nothing else in this package has ever cleared, and shared with
	// the GM's lever so a staged disappearance reads the same.
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

	// The world gives up before the wilderness does, and it is kinder that
	// it does. A spouse who has to wait for a body waits the better part of a
	// year; this is the point at which a town stops setting a place at the
	// table, dissolves the marriage and lets the one still living grieve and
	// eventually remarry. The person is still alive out there, and still
	// findable - which is the whole of the tragedy and none of the bug.
	presumedDeadDays         = 120
	presumedDeadSignificance = 66
)

// missingKeepsake is what they were carrying, read off the trade they
// practised rather than invented for the occasion. A herbalist's satchel has
// herbs in it; a scribe's has paper.
func missingKeepsake(profession string) string {
	p := strings.ToLower(profession)
	for _, pair := range [][2]string{
		{"herb", "spirit_herb"}, {"apothec", "spirit_herb"}, {"physician", "spirit_herb"},
		{"alchem", "spirit_herb"}, {"medicine", "spirit_herb"},
		{"forge", "spirit_iron"}, {"smith", "spirit_iron"}, {"blacksmith", "spirit_iron"},
		{"scrib", "talisman_paper"}, {"inscri", "talisman_paper"}, {"talisman", "talisman_paper"},
		{"formation", "array_disk_blank"}, {"array", "array_disk_blank"},
		{"hunt", "beast_core"}, {"beast", "beast_core"}, {"tamer", "beast_core"},
		{"miner", "spirit_crystal_ore"}, {"ore", "spirit_crystal_ore"},
	} {
		if strings.Contains(p, pair[0]) {
			return pair[1]
		}
	}
	// Everybody else was carrying the one thing everybody carries.
	return "talisman_paper"
}

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
	if err := r.npcPresumedDead(conn, gm); err != nil {
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

// recordMissing writes the row the Quest Forge reads - the same row the
// GM's admin.npc.set_missing writes, through the same helper (v1.0.0-rc.38).
// Best-effort, as every NPC history row here is: the disappearance itself
// is already written, and a tick must not fail on an old database with no
// history table.
func (r *Runner) recordMissing(conn *storage.Conn, name, home, where, world string, gm int64, now float64) error {
	_ = world
	_ = game.RecordNPCMissingTx(conn, name, home, where, gm, now)
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
		if err := r.digGrave(conn, name, home, where, days, gm, now); err != nil {
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

// digGrave leaves something to find. `status='dead'` is not a thing a searcher
// can stand in front of; this is, and what it holds is what the NPC was
// actually carrying - their purse off `npc_civilization_state.wealth`, and one
// thing off the trade they practised. Nothing is invented for the occasion.
//
// The purse leaves the NPC's row as it goes into the grave, so the world's
// total does not quietly grow by the value of everybody who ever got lost.
func (r *Runner) digGrave(conn *storage.Conn, name, home, where string, days, gm int64, now float64) error {
	if !simTableExists(conn, "npc_graves") {
		return nil
	}
	profession, world, purse := "", "", int64(0)
	res, err := conn.Execute(`SELECT profession,world_name,wealth FROM npc_civilization_state WHERE npc_name=?`, []any{name})
	if err != nil {
		return err
	}
	if len(res.Rows) > 0 {
		profession = fmt.Sprint(res.Rows[0][0])
		world = fmt.Sprint(res.Rows[0][1])
		purse = i64(res.Rows[0][2])
	}
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET wealth=0,updated_at=? WHERE npc_name=?`,
		[]any{now, name}); err != nil {
		return err
	}
	_, err = conn.Execute(`INSERT INTO npc_graves(
            npc_name,location,world_name,home_location,died_game_minute,days_missing,
            keepsake_item,keepsake_stones,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(npc_name) DO NOTHING`,
		[]any{name, where, world, home, gm, days, missingKeepsake(profession), purse, now, now})
	return err
}

// npcPresumedDead is the town giving up. Past `presumedDeadDays` the marriage
// is dissolved from both sides - the one at home is widowed and may eventually
// marry again, and the one still out there is released from a marriage that is
// no longer waiting for them.
//
// They are not marked dead. They are still missing, still alive, and still
// exactly where they are, which is what makes finding one afterwards worth
// anything at all.
func (r *Runner) npcPresumedDead(conn *storage.Conn, gm int64) error {
	if !simTableExists(conn, "npc_life_state") {
		return nil
	}
	res, err := conn.Execute(`SELECT c.npc_name,c.home_location,l.spouse_name,c.missing_since_game_minute
        FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.status='missing' AND c.missing_since_game_minute>0
          AND ?-c.missing_since_game_minute>=?
          AND l.relationship_status='married'
        ORDER BY c.missing_since_game_minute,c.npc_name LIMIT 20`,
		[]any{gm, int64(presumedDeadDays) * minutesPerDay})
	if err != nil {
		return err
	}
	now := nowFloat()
	for _, row := range res.Rows {
		name, home := fmt.Sprint(row[0]), fmt.Sprint(row[1])
		spouse := fmt.Sprint(row[2])
		days := int64(0)
		if since := i64(row[3]); since > 0 && gm > since {
			days = (gm - since) / minutesPerDay
		}
		// ReleaseNPCBondsTx widows whoever names them as a spouse; this
		// releases the missing one from their side of it too, so somebody
		// found afterwards is single rather than married to a person who has
		// buried them.
		if err := game.ReleaseNPCBondsTx(conn, name, gm, now); err != nil {
			return err
		}
		if _, err := conn.Execute(`UPDATE npc_life_state
            SET relationship_status='single',spouse_name='',last_social_game_minute=?,updated_at=?
            WHERE npc_name=?`, []any{gm, now, name}); err != nil {
			return err
		}
		summary := fmt.Sprintf("%s has been gone from %s for %d day(s). %s has stopped looking.", name, home, days, home)
		if spouse != "" {
			summary = fmt.Sprintf("%s has been gone from %s for %d day(s), and %s has been told to stop waiting.",
				name, home, days, spouse)
		}
		r.recordNPCHistory(conn, "npc_presumed_dead", fmt.Sprintf("npc_presumed_dead:%s:%d", name, gm),
			name+" is given up for dead",
			summary, home, name, presumedDeadSignificance, gm, now)
	}
	return nil
}
