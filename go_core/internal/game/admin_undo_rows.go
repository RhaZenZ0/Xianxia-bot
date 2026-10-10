package game

import (
	"fmt"
	"strings"
	"time"
)

// An undo puts back what the lever wrote and nothing else. The perfection lever
// wrote a bar, and from v1.23.1 a full bar the whole path; the profession, Law
// and contribution levers each wrote a pair of numbers on a row play was
// already writing to. Restoring a column the lever never touched writes over
// whatever the player did there since, and deleting a row the lever made
// deletes whatever the player put on it since - a started path, a perfected
// realm whose reward was paid, the successes an examination reads.
//
// So a row the lever made is deleted by one door, deleteIfIdle, and only when
// every column on it is back at the value its DDL gives a row nobody has
// touched. Those values are the three lists below, and
// tests/python/contracts/test_an_undo_deletes_only_an_idle_row.py holds each
// list equal to the bootstrapped schema: a column added to one of these tables
// and missed here would be deleted with the row whatever play had put in it.

// rowDefault is one column at the value its DDL gives a row nobody has touched.
type rowDefault struct {
	col   string
	value any
}

// perfectionRowDefaults is a perfection row nobody has walked. Every reader
// treats it as no row at all - perfection.start adopts it, checkPerfectionChoice
// and perfectionTraining read active and completed, and the bot draws neither -
// which is what makes deleting it safe. `completed` is in the list on purpose: a
// perfected realm is never idle, whatever the undo wrote over its other columns.
var perfectionRowDefaults = []rowDefault{
	{"active", int64(0)},
	{"completed", int64(0)},
	{"progress", int64(0)},
	{"training_progress", int64(0)},
	{"quest_index", int64(0)},
	{"quest_preparation", int64(0)},
	{"completed_quests", int64(0)},
	{"discovered_json", "[]"},
}

// professionRowDefaults is a trade row nobody has practised. The successes are
// what the examination reads, so a craft on a row the lever made keeps it.
var professionRowDefaults = []rowDefault{
	{"level", int64(0)},
	{"xp", int64(0)},
	{"successes", int64(0)},
	{"failures", int64(0)},
	{"quality_points", int64(0)},
}

// lawRowDefaults is a Law nobody has sat with. The sittings are the insights,
// and the samsara law echo reads them.
var lawRowDefaults = []rowDefault{
	{"comprehension", int64(0)},
	{"insights", int64(0)},
}

// rowDefaultValue is the default a list names for a column. A column the list
// does not name is a reversal written ahead of its list, and refusing the undo
// beats writing a made-up default over a player's row.
func rowDefaultValue(defaults []rowDefault, col string) (any, error) {
	for _, d := range defaults {
		if d.col == col {
			return d.value, nil
		}
	}
	return nil, fmt.Errorf("cannot undo: no row default is recorded for %s", col)
}

// deleteIfIdle deletes the row only when every column on it holds its default.
// It is the one statement in a reversal allowed to remove a row the lever
// made (TestAReversalDeletesARowOnlyThroughDeleteIfIdle).
func deleteIfIdle(table, where string, whereArgs []any, defaults []rowDefault) sqlStmt {
	clauses := []string{where}
	args := append([]any{}, whereArgs...)
	for _, d := range defaults {
		clauses = append(clauses, d.col+"=?")
		args = append(args, d.value)
	}
	return sqlStmt{`DELETE FROM ` + table + ` WHERE ` + strings.Join(clauses, " AND "), args}
}

func perfectionLeverTable(track string) (string, error) {
	switch track {
	case "cultivation":
		return "realm_perfection", nil
	case "body":
		return "body_realm_perfection", nil
	}
	return "", fmt.Errorf("cannot undo: unrecognized track %q", track)
}

// perfectionQuestColumns are the columns a full bar writes beside the bar.
var perfectionQuestColumns = []string{"active", "quest_index", "quest_preparation", "completed_quests", "discovered_json"}

// reverseSetRealmPerfection reverses admin.player.set_realm_perfection. Two
// things the audit row already says decide what the forward action wrote:
//
//   - `after` carries `completed_quests` only on a fill, so a bar below 100
//     wrote `progress` and nothing else, and its undo writes `progress` alone.
//     (Before v1.23.1 a fill wrote the bar alone too, and carries neither.)
//   - `before` says `existed: false` when the lever made the row. A row from
//     before v1.23.1 carries no `existed` at all and is undone on its bar, as it
//     was written.
//
// An undo is an UPDATE, never an upsert: a row play has since removed
// (perfection.abandon) is not the lever's to bring back. A made row is deleted
// only when idle. A redo is the forward action's own upsert, so it lands even on
// a row its undo removed. And `active` is never written onto a perfected realm
// in either direction - the trial has paid a permanent reward, and the quest and
// trial actions read `active` alone, so putting it back would open a second.
func reverseSetRealmPerfection(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	table, err := perfectionLeverTable(fmt.Sprint(before["track"]))
	if err != nil {
		return nil, err
	}
	realm := i64(before["realm_index"])
	snap := pickSnapshot(before, after, redo)
	existed, tracked := before["existed"].(bool)
	if !tracked {
		return []sqlStmt{{`UPDATE ` + table + ` SET progress=? WHERE user_id=? AND realm_index=?`, []any{i64(snap["progress"]), uid, realm}}}, nil
	}
	_, filled := after["completed_quests"]
	cols := []string{"progress"}
	if filled {
		cols = append(cols, perfectionQuestColumns...)
	}
	// What an undo of a made row writes is the row nobody has walked, because
	// the snapshot of a row that did not exist holds a bar of 0 and no quest
	// state to restore.
	value := func(col string) (any, error) {
		if !redo && !existed {
			return rowDefaultValue(perfectionRowDefaults, col)
		}
		if col == "discovered_json" {
			return fmt.Sprint(snap[col]), nil
		}
		return i64(snap[col]), nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if redo {
		names, marks, sets := []string{"user_id", "realm_index"}, []string{"?", "?"}, []string{}
		args := []any{uid, realm}
		for _, c := range cols {
			v, err := value(c)
			if err != nil {
				return nil, err
			}
			names, marks = append(names, c), append(marks, "?")
			args = append(args, v)
			if c == "active" {
				sets = append(sets, "active=CASE WHEN "+table+".completed=1 THEN "+table+".active ELSE excluded.active END")
			} else {
				sets = append(sets, c+"=excluded."+c)
			}
		}
		names, marks = append(names, "updated_at"), append(marks, "?")
		args = append(args, now)
		sets = append(sets, "updated_at=excluded.updated_at")
		return []sqlStmt{{`INSERT INTO ` + table + `(` + strings.Join(names, ",") + `) VALUES(` + strings.Join(marks, ",") + `) ON CONFLICT(user_id,realm_index) DO UPDATE SET ` + strings.Join(sets, ","), args}}, nil
	}
	sets, args := []string{}, []any{}
	for _, c := range cols {
		v, err := value(c)
		if err != nil {
			return nil, err
		}
		if c == "active" {
			sets = append(sets, "active=CASE WHEN completed=1 THEN active ELSE ? END")
		} else {
			sets = append(sets, c+"=?")
		}
		args = append(args, v)
	}
	sets = append(sets, "updated_at=?")
	args = append(args, now, uid, realm)
	stmts := []sqlStmt{{`UPDATE ` + table + ` SET ` + strings.Join(sets, ",") + ` WHERE user_id=? AND realm_index=?`, args}}
	if !existed {
		stmts = append(stmts, deleteIfIdle(table, "user_id=? AND realm_index=?", []any{uid, realm}, perfectionRowDefaults))
	}
	return stmts, nil
}
