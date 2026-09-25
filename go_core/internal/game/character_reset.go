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
// cost anybody else. `erasureAnonymise` is precisely the set of columns where a
// person's id sits on a row that belongs to everybody, and v1.0.1 refused a
// reset the moment any of them named this character. That made the reset
// unusable minutes into a life - the first place a cultivator discovers writes
// a history row naming them - so v1.0.14, on the owner's call, **releases**
// every one of them instead (`characterResetReleased`): the shared thing stays
// in the world and the link to this account goes, the way an erasure takes it,
// with the name rewritten to an unknown cultivator where it was written into
// prose, and a player family succeeded exactly as when its founder leaves. A
// reset is still refused over an anonymise column that is *not* released, so a
// new one added to erasure is a mark by default until somebody decides what a
// reset does with it.
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
	"sort"
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

// characterResetStatusQuery answers what one account has spent of its restart
// allowance (v1.0.13). It exists because two GM surfaces ask - `/admin player inspect` on
// Discord and the dashboard's Player Editor - and everything the answer is made
// of belongs to the engine.
//
// **It is a query rather than a SELECT in each surface.** The bound is
// `characterResetAllowance` and the row it counts is `characterResetEvent`: a
// Go constant and a Go string, which Python would have had to restate twice
// over. A surface carrying its own copy of the allowance reads "one left" on
// the day the engine refuses, which is rc.46's rule seen from behind the
// counter and exactly why v1.0.11 took the root ladder out of the browser.
// `event_log` has no other reader in Python at all, and two raw readers of a
// Go-owned table is how the count and the limit part company later.
//
// **The subject is the payload's, never the actor's.** Both callers are asking
// about somebody else - Discord's actor is the GM who typed the command and the
// dashboard is not an actor at all - so there is no sensible default, and an
// absent `user_id` is a refusal rather than a quiet answer about the wrong
// person.
//
// **It reads no `characters` row, deliberately.** An account that reset and has
// not begun again has none, and that is precisely the state a GM asks about;
// joining the sheet would answer "nobody" in the one case worth having the
// lever for.
//
// The two count keys are spelled exactly as `character.reset`'s own result
// spells them, so the reply a player gets and the card a GM reads cannot come
// to mean different things.
func characterResetStatusQuery(conn *storage.Conn, raw json.RawMessage) (map[string]any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	userID, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	used, err := characterResetsUsedTx(conn, userID)
	if err != nil {
		return nil, err
	}
	// Newest first, and unbounded: at most `characterResetAllowance` rows can
	// exist, so a LIMIT here would be a second number stating the same bound.
	res, err := conn.Execute(
		`SELECT payload_json,created_at FROM event_log WHERE user_id=? AND event_type=? ORDER BY id DESC`,
		[]any{userID, characterResetEvent})
	if err != nil {
		return nil, err
	}
	lives := make([]map[string]any, 0, len(res.Rows))
	for _, row := range res.Rows {
		if len(row) < 2 {
			continue
		}
		life := map[string]any{"created_at": row[1]}
		// Through the package's own decoder, which has used json.Number since
		// v1.0.12: a float64 is not a thing to decode ids into, even where
		// this payload happens to carry none.
		if record, rerr := decodeMap(json.RawMessage(fmt.Sprint(row[0]))); rerr == nil {
			for key, value := range record {
				life[key] = value
			}
		}
		lives = append(lives, life)
	}
	remaining := characterResetAllowance - used
	if remaining < 0 {
		// Only reachable if the allowance is ever lowered under a live world.
		// Nobody is owed a negative number of fresh starts.
		remaining = 0
	}
	return map[string]any{
		"user_id":          userID,
		"resets_used":      used,
		"resets_remaining": remaining,
		"reset_allowance":  int64(characterResetAllowance),
		"resets":           lives,
	}, nil
}

// characterResetMark is one reason a reset is refused: which column named the
// character, and what that means in a player's words.
type characterResetMark struct {
	Key    string
	Detail string
}

// characterResetReleased are the anonymise columns a reset settles itself
// instead of refusing over, each with what the thing is called when the reset
// reports leaving it behind (v1.0.14, on the owner's call). A history row
// naming a character is written by almost everything a new cultivator does -
// the first place they discover, a world event their explore set off, a trade
// at the inn - so treating it as a mark made the reset unusable minutes into a
// life, which is the opposite of what a way to start over is for. The other
// five are the same kind of thing: each is a credit on something the world
// keeps (a gate, an emptied grave, a written quest, a sect manor) or a house
// other players can belong to, and each has an honest way to outlive its maker.
// History is the one with special handling - its private rows go with the life
// - and the family is the one with an heir (`playerFamilyDepartTx`); the rest
// simply lose the link. An anonymise column missing from this map still
// refuses a reset, so the release is always a decision and never a default.
var characterResetReleased = map[string][2]string{
	"world_history_events.related_user_id": {"a record in the world's history", "records in the world's history"},
	"world_crossings.opened_by_user_id":    {"the gate between worlds they opened", "gates between worlds they opened"},
	"npc_graves.claimed_by_user_id":        {"a grave they emptied", "graves they emptied"},
	"quest_definitions.owner_user_id":      {"a quest they wrote", "quests they wrote"},
	"sect_manors.founded_by_user_id":       {"the sect manor they founded", "sect manors they founded"},
	"player_families.founder_user_id":      {"the family they founded", "families they founded"},
	"stall_sales.buyer_user_id":            {"a purchase at another cultivator's stall", "purchases at other cultivators' stalls"},
}

// characterResetUnknown is who a kept row says did it once the cultivator who
// did has been reset away (v1.0.14, the owner's suggestion). A row that went on
// naming "Xie Kormaq" would remember somebody who, as far as this world is now
// concerned, never existed; the deed stays, the doer does not.
const characterResetUnknown = "an unknown cultivator"

// characterResetUnknownTitle is the same stranger inside a name, such as a
// gate's.
const characterResetUnknownTitle = "Unknown Cultivator"

// characterResetRelease is what the reset did with everything it released.
type characterResetRelease struct {
	HistoryRemoved  int64            // private history rows, gone with the life
	HistoryUnlinked int64            // public history rows, kept and unlinked
	Unlinked        map[string]int64 // every other released column, by key
	Family          playerFamilyDeparture
}

// characterResetReleaseTx settles every released column **before** the sweep,
// and the order is load-bearing: `erasureTargets` walks tables alphabetically,
// so `characters` is deleted before any of these is reached, and with
// `foreign_keys=ON` that delete fires `player_families`' ON DELETE CASCADE -
// taking a founder's whole house, the other members' places in it included -
// and `SET NULL` on the rest before anything could hand them on. Released
// here, there is nothing left for either the cascade or the sweep to touch,
// and the sweep's guard can go on requiring that it anonymised nothing.
func characterResetReleaseTx(conn *storage.Conn, userID int64, name string, targets []erasureTarget) (characterResetRelease, error) {
	out := characterResetRelease{Unlinked: map[string]int64{}}
	if err := characterResetForgetNameTx(conn, userID, name); err != nil {
		return out, err
	}
	if tableExistsTx(conn, "world_history_events") {
		// A row nobody else can see is only this character's knowledge.
		res, err := conn.Execute(`DELETE FROM world_history_events
			WHERE related_user_id=? AND COALESCE(visibility,'public')<>'public'`, []any{userID})
		if err != nil {
			return out, err
		}
		out.HistoryRemoved = res.RowsAffected
	}
	if tableExistsTx(conn, "player_family_members") {
		left, err := playerFamilyDepartTx(conn, userID)
		if err != nil {
			return out, err
		}
		out.Family = left
	}
	for _, target := range targets {
		key := erasureKey(target.Table, target.Column)
		if target.Disposition != erasureAnonymiseRow {
			continue
		}
		if _, released := characterResetReleased[key]; !released {
			continue
		}
		replacement := "NULL"
		if target.NotNull {
			replacement = fmt.Sprint(erasedUserSentinel)
		}
		res, err := conn.Execute(fmt.Sprintf("UPDATE %s SET %s=%s WHERE %s=?",
			quoteIdentifier(target.Table), quoteIdentifier(target.Column), replacement,
			quoteIdentifier(target.Column)), []any{userID})
		if err != nil {
			return out, fmt.Errorf("releasing %s: %w", key, err)
		}
		if res.RowsAffected <= 0 {
			continue
		}
		if key == "world_history_events.related_user_id" {
			out.HistoryUnlinked = res.RowsAffected
		} else {
			out.Unlinked[key] = res.RowsAffected
		}
	}
	return out, nil
}

// characterResetLeftBehind says, in words, what the released rows were, for
// the reply: public history first, then the rest in a stable order. The
// family is reported separately, because it has an heir to name.
func characterResetLeftBehind(released characterResetRelease) []string {
	counts := map[string]int64{}
	for key, n := range released.Unlinked {
		counts[key] = n
	}
	delete(counts, "player_families.founder_user_id")
	keys := make([]string, 0, len(counts))
	for key := range counts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	if released.HistoryUnlinked > 0 {
		const history = "world_history_events.related_user_id"
		counts[history] = released.HistoryUnlinked
		keys = append([]string{history}, keys...)
	}
	var out []string
	for _, key := range keys {
		if n := counts[key]; n == 1 {
			out = append(out, characterResetReleased[key][0])
		} else {
			out = append(out, fmt.Sprintf("%d %s", n, characterResetReleased[key][1]))
		}
	}
	return out
}

// characterResetForgetNameTx rewrites this character's name out of the rows
// that outlive them: the prose and names of every history row linked to them,
// and the name of any gate they opened - which is written from a template as
// "{character}'s Ascension Gate", and which other cultivators' history rows
// quote when they step through it, so those are rewritten too, matched on the
// whole gate name rather than on the bare character name.
func characterResetForgetNameTx(conn *storage.Conn, userID int64, name string) error {
	name = strings.TrimSpace(name)
	if name == "" {
		return nil
	}
	hasHistory := tableExistsTx(conn, "world_history_events")
	if tableExistsTx(conn, "world_crossings") {
		gates, err := conn.Execute(`SELECT location_key,name FROM world_crossings WHERE opened_by_user_id=?`, []any{userID})
		if err != nil {
			return err
		}
		for _, row := range gates.Rows {
			if len(row) < 2 || row[1] == nil {
				continue
			}
			// A gate's name is a title, so it takes the title-case form:
			// "Unknown Cultivator's Ascension Gate", which reads right after
			// the "stepped through the" other cultivators' rows put before it.
			old := fmt.Sprint(row[1])
			renamed := strings.ReplaceAll(old, name, characterResetUnknownTitle)
			if renamed == old {
				continue
			}
			if _, err := conn.Execute(`UPDATE world_crossings SET name=? WHERE location_key=?`, []any{renamed, row[0]}); err != nil {
				return err
			}
			if hasHistory {
				if _, err := conn.Execute(`UPDATE world_history_events SET title=REPLACE(title,?,?),summary=REPLACE(summary,?,?)
					WHERE instr(title,?)>0 OR instr(summary,?)>0`, []any{old, renamed, old, renamed, old, old}); err != nil {
					return err
				}
			}
		}
	}
	if !hasHistory {
		return nil
	}
	res, err := conn.Execute(`SELECT history_id,title,summary,actor_name,target_name,actor_key,target_key
		FROM world_history_events WHERE related_user_id=?`, []any{userID})
	if err != nil {
		return err
	}
	id := fmt.Sprint(userID)
	text := func(v any) string {
		if v == nil {
			return ""
		}
		return fmt.Sprint(v)
	}
	for _, row := range res.Rows {
		if len(row) < 7 {
			continue
		}
		who := func(v string) string {
			if strings.TrimSpace(v) == name {
				return characterResetForgetSentence(characterResetUnknown)
			}
			return v
		}
		key := func(v string) string {
			if strings.TrimSpace(v) == id {
				return ""
			}
			return v
		}
		if _, err := conn.Execute(`UPDATE world_history_events SET title=?,summary=?,actor_name=?,target_name=?,
			actor_key=?,target_key=? WHERE history_id=?`, []any{
			characterResetForget(text(row[1]), name), characterResetForget(text(row[2]), name),
			who(text(row[3])), who(text(row[4])), key(text(row[5])), key(text(row[6])), row[0],
		}); err != nil {
			return err
		}
	}
	return nil
}

// characterResetForget replaces a name inside prose, capitalising where the
// name began a sentence.
func characterResetForget(text, name string) string {
	if !strings.Contains(text, name) {
		return text
	}
	out := strings.ReplaceAll(text, name, characterResetUnknown)
	if strings.HasPrefix(text, name) {
		out = characterResetForgetSentence(out)
	}
	return strings.ReplaceAll(out, ". "+characterResetUnknown, ". "+characterResetForgetSentence(characterResetUnknown))
}

func characterResetForgetSentence(text string) string {
	if text == "" {
		return text
	}
	return strings.ToUpper(text[:1]) + text[1:]
}

// characterResetWorldMarksTx names every shared-world row that would be
// anonymised and that a reset has not been told how to release. With every
// shipped anonymise column released it finds nothing; it is what makes a new
// one a refusal until somebody decides otherwise.
func characterResetWorldMarksTx(conn *storage.Conn, userID int64, targets []erasureTarget) ([]characterResetMark, error) {
	var marks []characterResetMark
	for _, target := range targets {
		key := erasureKey(target.Table, target.Column)
		if target.Disposition != erasureAnonymiseRow {
			continue
		}
		if _, released := characterResetReleased[key]; released {
			continue
		}
		res, err := conn.Execute(fmt.Sprintf("SELECT COUNT(*) FROM %s WHERE %s=?",
			quoteIdentifier(target.Table), quoteIdentifier(target.Column)), []any{userID})
		if err != nil {
			return nil, err
		}
		if len(res.Rows) == 0 || len(res.Rows[0]) == 0 || i64(res.Rows[0][0]) <= 0 {
			continue
		}
		marks = append(marks, characterResetMark{Key: key, Detail: "they are named in " + target.Table})
	}
	return marks, nil
}

// characterResetMarkRefusal is the whole refusal. It answers the two things a
// refused player asks (v1.0.14): why, and how long until they can. The second
// answer is "not by waiting", and it is said outright - the gate is not a
// clock, so any wording that sounded like a wait would be a promise nothing
// keeps.
func characterResetMarkRefusal(name string, marks []characterResetMark) error {
	lines := []string{fmt.Sprintf(
		"%s cannot be reset, and waiting will not change that. This life has already left a mark "+
			"the world keeps that a reset does not yet know how to hand on:", name)}
	for _, mark := range marks {
		lines = append(lines, "• "+mark.Detail)
	}
	lines = append(lines,
		"There is no timer on it. Ask a GM, or play this life on - when it ends, Samsara begins the next one.")
	return errors.New(strings.Join(lines, "\n"))
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
		return authoritativeMutation{}, characterResetMarkRefusal(c.Name, marks)
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
	released, err := characterResetReleaseTx(conn, userID, c.Name, targets)
	if err != nil {
		return authoritativeMutation{}, err
	}
	sweep, err := applyErasureTargets(conn, userID, targets, characterResetKeep())
	if err != nil {
		return authoritativeMutation{}, err
	}
	if sweep.RowsAnonymised > 0 {
		// Unreachable while the mark check and the release above hold, and
		// asserted rather than assumed: a future migration could add an
		// anonymise column the two walk and the sweep touches differently, and
		// a reset that quietly anonymised a shared row it had not released
		// would be the thing the mark check refuses.
		return authoritativeMutation{}, fmt.Errorf(
			"reset would have anonymised %d shared row(s) it had not released; refusing", sweep.RowsAnonymised)
	}
	result := map[string]any{
		"reset":            true,
		"name":             c.Name,
		"resets_used":      used + 1,
		"resets_remaining": characterResetAllowance - (used + 1),
		"rows_deleted":     sweep.RowsDeleted + released.HistoryRemoved,
		"tables_touched":   len(sweep.Deleted),
		"history_removed":  released.HistoryRemoved,
		"history_unlinked": released.HistoryUnlinked,
		"left_behind":      characterResetLeftBehind(released),
	}
	if left := released.Family; left.FamilyID != 0 {
		family := map[string]any{"name": left.Name, "dissolved": left.Dissolved}
		if left.HeirID != 0 {
			family["heir_user_id"] = left.HeirID
		}
		result["family"] = family
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
