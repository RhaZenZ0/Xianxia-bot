package game

// Abandoning a life you have only just begun (v1.0.1).
//
// Until now the only way out of a character was to die - and dying is not a
// reset. `lifecycle.true_death` has three callers and none of them is
// voluntary: old age, losing a battle at 0 HP with no fate point left, and the
// GM. What it opens is Samsara, which is deliberately *not* a clean slate:
// `reincarnateAction` carries the memory seed, the talent, law and insight
// echoes, the legacy points, the craft echo and a family lineage rolled off the
// dead life's karma. That is the whole point of the mechanic.
//
// So a player who picked the wrong path in their first minute had exactly one
// route, and it needed a GM: `admin.player.erase`, the data-protection lever.
// Using a legal-erasure tool as a restart button is the same class of lie as a
// `sync_world_catalog` that syncs no catalogue - the action's name is what it
// is for, and "somebody asked for their data back" is not "I misclicked".
//
// `character.reset` is the restart button, and the interesting part is not the
// deletion - that already existed - but what bounds it.
//
// **It reuses erasure's own sweep.** `applyErasureTargets` walks the targets
// `erasureTargets` reads off the live schema, so a reset removes exactly what
// an erasure removes and cannot drift from it. Writing a second list of tables
// here would be the hand-written list `erasureTargets` exists to avoid, one
// file over.
//
// **The gate is the anonymise disposition, not a clock.** A time window ("the
// first ten minutes") is arbitrary and says nothing about what the reset would
// cost anybody else. The question that actually matters is whether this
// character has left a mark on a world other players share, and erasure has
// already answered it: `erasureAnonymise` is precisely the set of columns where
// a person's id sits on a row that belongs to everybody - a battle the world
// remembers, a sect other disciples belong to, a gate still standing over a
// named town, a grave somebody reached first. A reset is refused the moment any
// of them names this character, because those rows survive an erasure and so
// cannot honestly survive a reset: the world would go on referring to a
// cultivator who was never there.
//
// **Three per account, ever** - not three per character and not three per
// life. `rollRootGrade` is `Intn(1000)` against thresholds that put Immortal in
// the top 0.7% of a tier-1 household's draw, and v1.0.0-rc.55 made that grade
// worth 0.88x to 1.34x cultivation and -1 to +3 on every breakthrough for the
// character's whole life, so an unbounded reset is a free re-roll of exactly
// that number.
//
// The count lives in `event_log` rows of type `characterResetEvent`, which the
// sweep is told to keep - because **a bound that the bounded action erases is
// not a bound**, which is v1.0.0-rc.48's rule ("a bound that lives in the
// client is not a bound") turned inward. The row is the memory, the way
// `(user_id, quest_key)` is for the beginner path and an `event_log` row is for
// the household lesson and the profession examination.
//
// **A reset is not a small samsara, and the difference is the point.** Samsara
// is what death opens, and it deliberately *remembers*: the memory seed, the
// talent, law and insight echoes, the legacy points, the craft echo and a
// family lineage rolled off the dead life's karma all ride into the next life.
// A reset keeps none of it. `soul_legacy` holds no anonymise disposition, so
// the sweep deletes it with everything else and the account begins again at
// incarnation 1 with nothing behind it. `TestAResetIsNotASmallSamsara` is what
// holds that, because it is the one property that would be invisible if the
// table ever gained a keep.
//
// The obvious alternative - carry the drawn aptitudes across and re-roll only
// what was chosen - was rejected on a fact rather than on taste: `rollFamilyRoot`
// weights the root off the household's archetype, location, bloodline affinity
// and tier, and `rollRootGrade` adds `(familyTier-1)*24` to the grade roll. The
// family *is* a choice and the draw depends on it, so "keep what you drew, change
// what you chose" is not a line that can be drawn here.
//
// A GM erasure still removes the reset rows with everything else, and that is
// right: erasure removes a person, reset removes a character, and somebody who
// has been erased is new to this bot.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
)

// characterResetAllowance is how many times one Discord account may abandon a
// freshly created cultivator, ever.
const characterResetAllowance = 3

// characterResetEvent is the `event_log.event_type` that records a reset. It is
// the allowance, so it is the one thing a reset does not delete about itself.
// Both readers of `event_log` in this package filter on their own event_type,
// so a surviving row is invisible to them.
const characterResetEvent = "character_reset"

// keepEveryRow is the extra predicate for a table the reset does not touch at
// all. The map's values are ANDed onto the DELETE's WHERE clause, so a
// predicate nothing satisfies is how a whole table is kept.
const keepEveryRow = "0=1"

// characterResetKeep is what a reset does not take, keyed "table.column" like
// every other erasure map so it is read against the same targets rather than
// against a table name written out somewhere else.
//
// All three entries are one rule, and it is `erasureKeep`'s rule one level down:
// **the engine's record of a request cannot be the thing the request deletes.**
// `admin_audit_log` is kept from an erasure for exactly that reason; here it is
// the authoritative framework's own bookkeeping, and the reset is the first
// action in this tree whose actor erases *itself*.
//
// The version row is not a nicety. `applyAuthoritative` reads the actor's state
// version before the switch and calls `eventledger.AdvanceActorVersion` with it
// afterwards; delete the row in between and that call finds 0 where it was
// promised 2, so the reset fails with `stale expected_version: expected 2
// current 0` having already done its work. It is also the right answer on its
// own terms - the version is optimistic-concurrency state about a Discord
// account's in-flight requests, not about the character - and resetting it to
// zero would let a client still holding the old version win a race it should
// lose. The receipts are the same argument: they are what makes a duplicate
// delivery a replay instead of a re-run.
//
// `domain_events` is deliberately *not* kept. It is the ledger of what the
// character did, which is the thing a reset is for.
func characterResetKeep() map[string]string {
	return map[string]string{
		"event_log.user_id":                      "event_type<>'" + characterResetEvent + "'",
		"authoritative_actor_versions.actor_id":  keepEveryRow,
		"authoritative_action_receipts.actor_id": keepEveryRow,
	}
}

// householdWelcomeLine is the line a birth household writes into its own
// `history_json` when it takes a child in. It is a function because two places
// need the exact same sentence - the creation that appends it and the reset
// that takes it back out - and a second copy of a format string is a second
// copy free to drift. A reset that left it behind would leave a shared starter
// household remembering a cultivator who does not exist, once per abandoned
// attempt.
func householdWelcomeLine(familyName, characterName string) string {
	return fmt.Sprintf("%s welcomed %s into the household.", familyName, characterName)
}

// characterResetsUsedTx counts the resets this account has already spent.
func characterResetsUsedTx(conn *storage.Conn, userID int64) (int64, error) {
	res, err := conn.Execute(
		`SELECT COUNT(*) FROM event_log WHERE user_id=? AND event_type=?`,
		[]any{userID, characterResetEvent})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0, nil
	}
	return i64(res.Rows[0][0]), nil
}

// characterResetWorldMarksTx names every shared-world row that would be
// anonymised rather than deleted. A reset is refused while any exists; the
// names are returned so the refusal can say which, because "you cannot reset"
// with no reason is the kind of message that sends a player to a GM anyway.
func characterResetWorldMarksTx(conn *storage.Conn, userID int64, targets []erasureTarget) ([]string, error) {
	var marks []string
	for _, target := range targets {
		if target.Disposition != erasureAnonymiseRow {
			continue
		}
		res, err := conn.Execute(fmt.Sprintf("SELECT COUNT(*) FROM %s WHERE %s=?",
			quoteIdentifier(target.Table), quoteIdentifier(target.Column)), []any{userID})
		if err != nil {
			return nil, err
		}
		if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
			continue
		}
		if i64(res.Rows[0][0]) > 0 {
			marks = append(marks, erasureKey(target.Table, target.Column))
		}
	}
	return marks, nil
}

// characterResetRemoveWelcomeTx takes the household's welcome line back out.
// A line that is not found is not an error: the sentence may predate
// `householdWelcomeLine`, and a reset must not fail over flavour text.
func characterResetRemoveWelcomeTx(conn *storage.Conn, userID int64, characterName string, now float64) error {
	res, err := conn.Execute(
		`SELECT bf.family_id, bf.family_name, bf.history_json
		   FROM character_birth_family cbf
		   JOIN birth_families bf ON bf.family_id=cbf.family_id
		  WHERE cbf.user_id=?`, []any{userID})
	if err != nil {
		return err
	}
	if len(res.Rows) == 0 {
		return nil
	}
	row := res.Rows[0]
	familyID := i64(row[0])
	line := householdWelcomeLine(fmt.Sprint(row[1]), characterName)
	var history []string
	if err := json.Unmarshal([]byte(fmt.Sprint(row[2])), &history); err != nil {
		return nil
	}
	kept := make([]string, 0, len(history))
	removed := false
	for _, entry := range history {
		if !removed && entry == line {
			removed = true
			continue
		}
		kept = append(kept, entry)
	}
	if !removed {
		return nil
	}
	encoded, err := json.Marshal(kept)
	if err != nil {
		return err
	}
	_, err = conn.Execute(`UPDATE birth_families SET history_json=?,updated_at=? WHERE family_id=?`,
		[]any{string(encoded), now, familyID})
	return err
}

// characterResetAction is the player's own way back to `/begin`. It takes no
// payload: everything it decides is read from the database, so there is nothing
// a caller could tell it that it does not already know.
func characterResetAction(conn *storage.Conn, userID int64, _ json.RawMessage) (authoritativeMutation, error) {
	c, err := loadLifeSnapshot(conn, userID)
	if err != nil {
		return authoritativeMutation{}, errors.New("you have no cultivator to reset")
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New(
			"this incarnation is already dead, and the road from here is Samsara - " +
				"use /character → Samsara")
	}
	incarnation, err := characterIncarnationCountTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if incarnation > 1 {
		return authoritativeMutation{}, fmt.Errorf(
			"this soul has already turned through Samsara %d times; a reset would take that record "+
				"with it, and the wheel is the road from here", incarnation-1)
	}
	used, err := characterResetsUsedTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if used >= characterResetAllowance {
		return authoritativeMutation{}, fmt.Errorf(
			"you have begun again %d times, which is all this world allows", characterResetAllowance)
	}
	targets, err := erasureTargets(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(targets) == 0 {
		return authoritativeMutation{}, errors.New("no reset targets discovered: the schema looks wrong")
	}
	marks, err := characterResetWorldMarksTx(conn, userID, targets)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(marks) > 0 {
		return authoritativeMutation{}, fmt.Errorf(
			"%s has already left a mark the world keeps (%s); those rows outlive even an erasure, "+
				"so this life can no longer be taken back", c.Name, strings.Join(marks, ", "))
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if err := characterResetRemoveWelcomeTx(conn, userID, c.Name, now); err != nil {
		return authoritativeMutation{}, err
	}
	// Written before the sweep, so the predicate that keeps it is load-bearing
	// on the very first reset rather than only on the second: remove the keep
	// and this row goes with everything else, and the allowance never counts
	// past zero.
	record, _ := json.Marshal(map[string]any{
		"name": c.Name, "path": c.Path, "spiritual_root": c.SpiritualRoot,
		"realm_index": c.RealmIndex, "phase": c.Phase, "reset_number": used + 1,
	})
	if _, err := conn.Execute(
		`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, characterResetEvent, string(record), now}); err != nil {
		return authoritativeMutation{}, err
	}
	sweep, err := applyErasureTargets(conn, userID, targets, characterResetKeep())
	if err != nil {
		return authoritativeMutation{}, err
	}
	if sweep.RowsAnonymised > 0 {
		// Unreachable while the mark check above holds, and asserted rather
		// than assumed: a future migration could add an anonymise column the
		// check walks and the sweep touches differently, and a reset that
		// quietly anonymised a shared row would be the thing this refuses.
		return authoritativeMutation{}, fmt.Errorf(
			"reset would have anonymised %d shared row(s); refusing", sweep.RowsAnonymised)
	}
	result := map[string]any{
		"reset":            true,
		"name":             c.Name,
		"resets_used":      used + 1,
		"resets_remaining": characterResetAllowance - (used + 1),
		"rows_deleted":     sweep.RowsDeleted,
		"tables_touched":   len(sweep.Deleted),
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "character", EventType: characterResetEvent, EntityType: "character",
			EntityID: fmt.Sprint(userID), Payload: result,
		},
	}, nil
}

// characterIncarnationCountTx answers 1 for a soul with no legacy row, which is
// a first life - the same reading `soul_legacy` gets everywhere else.
func characterIncarnationCountTx(conn *storage.Conn, userID int64) (int64, error) {
	res, err := conn.Execute(`SELECT incarnation_count FROM soul_legacy WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 1, nil
	}
	return maxI64(1, i64(res.Rows[0][0])), nil
}
