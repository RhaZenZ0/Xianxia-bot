package game

import (
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A body mends on its own (v1.0.4).
//
// Nothing in this tree restored vitality with time. Twelve `SET vitality`
// statements in `go_core`, four of them damage, and not one keyed on rest,
// cultivation, seclusion or the scheduled tick: four pills and one technique
// were the whole of it. So a cultivator who lost a fight - which is nine
// defeats in ten, `fatalChance` being `min(75, 8+gap*3)` - sat on the number
// the fight left them with until they bought their way off it, and somebody
// with no stones and no pill had no way up at all.
//
// The share is of the cultivator's own maximum, so the same wound costs the
// same number of world days at every realm and what changes with cultivation
// is what that share is worth.

// vitalityRecoveryDefaults is what an unreadable or unauthored content block
// answers: **no recovery**, not a rate of this function's invention. A
// fallback that looks like a value is not a sentinel (the `seller_user_id=0`
// lesson), and a healing rate nobody authored is exactly the kind of number
// that would then be tuned by editing Go.
func vitalityRecoverySettings(catalog worlddata.Catalog) (percent, minutesPerDay int64, ok bool) {
	v := catalog.VitalityRecovery
	if v.PercentPerGameDay <= 0 || v.MinutesPerGameDay <= 0 {
		return 0, 0, false
	}
	return v.PercentPerGameDay, v.MinutesPerGameDay, true
}

// settleVitalityRecoveryTx brings a character's vitality up to what the clock
// says they have mended, and moves the anchor by exactly the minutes that
// bought it.
//
// It is called from `applyAuthoritative` beside `ensureRoadTransitReadyTx`,
// and it is deliberately **not** a step of the simulation: `orderedSystems`
// are the world's own batches, daily and behind an automation flag a GM can
// switch off, and rc.56 already wrote down that a flag-gated sweep must not be
// the only end for state a player is sitting behind. A lazy settle works with
// the simulation off.
//
// It never fails an action. A missing column, an unreadable row or an
// unauthored rate all mean "no recovery this time", the way
// `loadSeclusionCarried` answers 1 for a term it cannot read - being hurt must
// not become a reason a command refuses.
func settleVitalityRecoveryTx(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64) (int64, error) {
	percent, minutesPerDay, ok := vitalityRecoverySettings(catalog)
	if !ok || gameMinute <= 0 {
		return 0, nil
	}
	res, err := conn.Execute(
		`SELECT vitality,vitality_max,life_status,vitality_recovered_game_minute FROM characters WHERE user_id=?`,
		[]any{userID})
	if err != nil {
		return 0, nil
	}
	row := firstRowMap(res)
	if row == nil || fmt.Sprint(row["life_status"]) != "alive" {
		return 0, nil
	}
	vitality, maxVitality := i64(row["vitality"]), i64(row["vitality_max"])
	if maxVitality <= 0 {
		return 0, nil
	}

	// A closed door is not rest, and neither is a fight. `combatTurnAction`
	// keeps `battles.player_hp` and `characters.vitality` in lockstep, so
	// mending behind an active battle's back would silently desync the two and
	// the next turn would write the stale number back.
	if inBattle, err := hasActiveBattleTx(conn, userID); err != nil || inBattle {
		return 0, nil
	}

	// At full, the clock is simply kept current: time spent whole must not
	// bank into the next wound.
	if vitality >= maxVitality {
		_, _ = conn.Execute(`UPDATE characters SET vitality_recovered_game_minute=? WHERE user_id=?`, []any{gameMinute, userID})
		return 0, nil
	}

	anchor, hasAnchor := i64(row["vitality_recovered_game_minute"]), row["vitality_recovered_game_minute"] != nil
	// `anchor < 0`, not `<= 0`: minute zero is a real minute - the moment a
	// fresh world's clock starts - and `hasAnchor` above is what distinguishes
	// a NULL from it, because `i64(nil)` is also 0.
	if !hasAnchor || anchor < 0 || anchor > gameMinute {
		// NULL is a character from before schema 59, and an anchor ahead of the
		// clock is a world whose time was wound back by the GM's own lever.
		// Neither can say how long this body has been mending, so both start
		// the clock and bank nothing rather than inventing a span.
		_, _ = conn.Execute(`UPDATE characters SET vitality_recovered_game_minute=? WHERE user_id=?`, []any{gameMinute, userID})
		return 0, nil
	}

	elapsed := gameMinute - anchor
	denom := 100 * minutesPerDay
	gain := (maxVitality * percent * elapsed) / denom
	if gain <= 0 {
		// The remainder is carried, never discarded: the anchor does not move
		// until it has bought a whole point, so resting in short spans is worth
		// exactly what resting in one span is (rc.56's `seclusionGainForSpan`).
		return 0, nil
	}
	if gain > maxVitality-vitality {
		gain = maxVitality - vitality
	}
	updated := vitality + gain
	newAnchor := gameMinute
	if updated < maxVitality {
		// Only the minutes that actually bought the gain are spent.
		newAnchor = anchor + (gain*denom)/(maxVitality*percent)
	}
	if _, err := conn.Execute(
		`UPDATE characters SET vitality=?,vitality_recovered_game_minute=? WHERE user_id=?`,
		[]any{updated, newAnchor, userID}); err != nil {
		return 0, nil
	}
	return gain, nil
}

func hasActiveBattleTx(conn *storage.Conn, userID int64) (bool, error) {
	res, err := conn.Execute(`SELECT 1 FROM battles WHERE user_id=? AND status='active' LIMIT 1`, []any{userID})
	if err != nil {
		return false, err
	}
	return firstRowMap(res) != nil, nil
}
