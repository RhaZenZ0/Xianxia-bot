package game

import (
	"fmt"
	"strings"
	"testing"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// A GM setting perfection to 100% is setting the whole path (v1.23.1). The
// trial's gate is every quest completed *and* progress 100, so a lever that
// wrote the number alone left the bar full and the trial refusing "final trial
// is locked". These tests drive the real trial through the production dispatch
// against the shipped content, because "the row says 7" passes just as well
// for a gate that reads a column the lever never wrote.

const perfectionAuditDDL = `CREATE TABLE IF NOT EXISTS admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_user_id INTEGER NOT NULL,action TEXT,target TEXT,before_json TEXT,after_json TEXT,reason TEXT,created_at REAL)`

func TestAFullPerfectionBarOpensTheFinalTrial(t *testing.T) {
	world := batch4WorldPath(t)
	for _, tc := range []struct{ track, startOp, trialOp, table string }{
		{"cultivation", "perfection.start", "perfection.trial", "realm_perfection"},
		{"body", "perfection.body_start", "perfection.body_trial", "body_realm_perfection"},
	} {
		t.Run(tc.track, func(t *testing.T) {
			path := setupBatch4AuthorityDB(t)
			batch4Exec(t, path, perfectionAuditDDL)
			batch4Apply(t, path, world, tc.startOp, 1, map[string]any{"game_minute": 100})
			if _, err := batch4ApplyErr(path, world, tc.trialOp, 42, 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), "final trial is locked") {
				t.Fatalf("a fresh path should hold its trial locked, got %v", err)
			}

			out, err := applyAdminRaw(t, path, "admin.player.set_realm_perfection", 0, map[string]any{"user_id": 42, "track": tc.track, "realm_index": 0, "progress": 100, "reason": "story"})
			if err != nil {
				t.Fatal(err)
			}
			res := out.Result.(map[string]any)
			if open, _ := res["trial_open"].(bool); !open {
				t.Fatalf("the lever did not report the trial open: %v", res)
			}
			quests := storage.ParseInt(actionScalar(t, path, "SELECT completed_quests FROM "+tc.table+" WHERE user_id=42 AND realm_index=0"))
			if quests != 7 {
				t.Fatalf("completed_quests=%d after 100%%, want all 7", quests)
			}
			if clues := storage.ParseInt(actionScalar(t, path, "SELECT json_array_length(discovered_json) FROM "+tc.table+" WHERE user_id=42 AND realm_index=0")); clues != quests {
				t.Fatalf("%d clue(s) recorded for %d quests; a completed quest has always left its clue, and the shipped quests each carry one", clues, quests)
			}
			if _, err := batch4ApplyErr(path, world, tc.trialOp, 42, 3, map[string]any{}); err != nil {
				t.Fatalf("the bar is full and every quest marked done, and the trial still refused: %v", err)
			}
		})
	}
}

func TestAFullBarBeforeThePathBeganStillOpensTheTrial(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	if _, err := batch4ApplyErr(path, world, "perfection.trial", 42, 1, map[string]any{}); err != nil {
		t.Fatalf("a GM who sets 100%% on a path nobody started asked for the trial to open: %v", err)
	}
}

func TestBelowAFullBarTheQuestsAreLeftAlone(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Apply(t, path, world, "perfection.start", 1, map[string]any{"game_minute": 100})
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 99, "reason": "story"})
	if got := storage.ParseInt(actionScalar(t, path, "SELECT completed_quests FROM realm_perfection WHERE user_id=42 AND realm_index=0")); got != 0 {
		t.Fatalf("completed_quests=%d at 99%%; only a full bar is the whole path", got)
	}
	if _, err := batch4ApplyErr(path, world, "perfection.trial", 42, 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), "final trial is locked") {
		t.Fatalf("at 99%% the trial must stay locked, got %v", err)
	}
}

func TestAPerfectedRealmIsNotReopened(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Exec(t, path, "INSERT INTO realm_perfection(user_id,realm_index,active,completed,progress,completed_quests,quest_index,updated_at) VALUES(42,0,0,1,100,7,7,0)")
	out, err := applyAdminRaw(t, path, "admin.player.set_realm_perfection", 0, map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	if err != nil {
		t.Fatal(err)
	}
	if open, _ := out.Result.(map[string]any)["trial_open"].(bool); open {
		t.Fatal("a perfected realm reported its trial open again")
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT active FROM realm_perfection WHERE user_id=42 AND realm_index=0")); got != 0 {
		t.Fatalf("active=%d: a perfected realm's path was reopened for a second reward", got)
	}
}

func TestUndoingAFullBarPutsThePathBack(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Apply(t, path, world, "perfection.start", 1, map[string]any{"game_minute": 100})
	batch4Exec(t, path, "UPDATE realm_perfection SET progress=30,quest_index=2,completed_quests=2,quest_preparation=1,discovered_json='[\"a\"]' WHERE user_id=42 AND realm_index=0")
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	undoLast(t, path)
	row := func(col string) string {
		return strings.TrimSpace(fmt.Sprint(actionScalar(t, path, "SELECT "+col+" FROM realm_perfection WHERE user_id=42 AND realm_index=0")))
	}
	for col, want := range map[string]string{"progress": "30", "quest_index": "2", "completed_quests": "2", "quest_preparation": "1", "discovered_json": `["a"]`, "active": "1"} {
		if got := row(col); got != want {
			t.Fatalf("%s=%q after undo, want %q", col, got, want)
		}
	}
	if _, err := batch4ApplyErr(path, world, "perfection.trial", 42, 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), "final trial is locked") {
		t.Fatalf("after the undo the trial must be locked again, got %v", err)
	}

	// A path the lever itself made is removed by the undo.
	fresh := setupBatch4AuthorityDB(t)
	batch4Exec(t, fresh, perfectionAuditDDL)
	applyAdmin(t, fresh, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "body", "realm_index": 0, "progress": 100, "reason": "story"})
	undoLast(t, fresh)
	if got := storage.ParseInt(actionScalar(t, fresh, "SELECT COUNT(*) FROM body_realm_perfection WHERE user_id=42")); got != 0 {
		t.Fatalf("%d row(s) left after undoing a path the lever made", got)
	}
}

// --- from perfection_majority_test.go ---

// The final Perfection trial passes on a majority of its checks (v1.23.2, on
// the owner's call): two of the shipped three. It used to fail on any one
// check, so a cultivator holding Meridian Stability and Heart-Demon Resolve
// was turned back by a Qi Compression that missed by one. The dice are lent,
// because whether a roll landed is the whole question.

func TestTheFinalPerfectionTrialPassesOnTwoOfThree(t *testing.T) {
	if got := perfectionTrialsNeeded(3); got != 2 {
		t.Fatalf("three checks need %d, want 2", got)
	}
	for _, tc := range []struct {
		name    string
		low     int // the first `low` dice draws roll a 1, the rest a 10
		success bool
		passed  int64
	}{
		{"two of three hold", 2, true, 2},
		{"one of three holds", 4, false, 1},
	} {
		for _, track := range []struct{ name, trialOp, table string }{
			{"cultivation", "perfection.trial", "realm_perfection"},
			{"body", "perfection.body_trial", "body_realm_perfection"},
		} {
			t.Run(tc.name+"/"+track.name, func(t *testing.T) {
				path := setupBatch4AuthorityDB(t)
				world := batch4WorldPath(t)
				batch4Exec(t, path, perfectionAuditDDL)
				// Ordinary attributes, so the dice decide each check: the fixture's
				// hundreds would hold every one whatever was rolled.
				batch4Exec(t, path, `UPDATE characters SET attributes_json='{"body":3,"spirit":3,"will":3,"insight":3,"agility":3,"presence":3}' WHERE user_id=42`)
				applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": track.name, "realm_index": 0, "progress": 100, "reason": "story"})

				draws := 0
				restore := gamerng.UseRoller(func(n int) int {
					draws++
					if draws <= tc.low {
						return 0
					}
					return n - 1
				})
				defer restore()
				out, err := batch4ApplyErr(path, world, track.trialOp, 42, 2, map[string]any{})
				if err != nil {
					t.Fatal(err)
				}
				res := out.Result.(map[string]any)
				if got, _ := res["success"].(bool); got != tc.success {
					t.Fatalf("success=%v with %d of 3 checks held, want %v: %v", got, tc.passed, tc.success, res["rolls"])
				}
				if got := storage.ParseInt(res["passed"]); got != tc.passed {
					t.Fatalf("passed=%d, want %d", got, tc.passed)
				}
				if got := storage.ParseInt(res["needed"]); got != 2 {
					t.Fatalf("needed=%d, want 2", got)
				}
				want := int64(0)
				if tc.success {
					want = 1
				}
				if got := storage.ParseInt(actionScalar(t, path, "SELECT completed FROM "+track.table+" WHERE user_id=42 AND realm_index=0")); got != want {
					t.Fatalf("completed=%d, want %d", got, want)
				}
			})
		}
	}
}

// --- the lever's undo and redo ---

// An undo writes back the columns its forward action wrote, onto the row it
// wrote them to (admin_undo_rows.go). Every test here lets real play happen
// between the lever and the undo, because an undo that is only ever driven
// straight after its lever cannot tell "restore what the lever wrote" from
// "restore the whole row the lever read" - which is the fault.
//
// The waits are cleared with a DELETE and never with admin.player.reset_cooldowns:
// the lever would write the audit row the undo then acts on.

// withHighDice lends the dice for one call and gives them back.
func withHighDice(t *testing.T, fn func()) {
	t.Helper()
	restore := gamerng.UseRoller(func(n int) int { return n - 1 })
	defer restore()
	fn()
}

// walkFirstPerfectionQuest prepares the current quest to the bar the content
// sets and attempts it with the dice lent high, so the quest lands by play.
func walkFirstPerfectionQuest(t *testing.T, path, world, questOp string, seq *int) {
	t.Helper()
	for {
		batch4Exec(t, path, "DELETE FROM cooldowns WHERE user_id=42")
		out, err := batch4ApplyErr(path, world, questOp, 42, *seq, map[string]any{"mode": "prepare"})
		*seq++
		if err != nil {
			t.Fatal(err)
		}
		res := out.Result.(map[string]any)
		if storage.ParseInt(res["preparation"]) >= storage.ParseInt(res["preparation_required"]) {
			break
		}
	}
	batch4Exec(t, path, "DELETE FROM cooldowns WHERE user_id=42")
	var out ActionResponse
	var err error
	withHighDice(t, func() {
		out, err = batch4ApplyErr(path, world, questOp, 42, *seq, map[string]any{"mode": "attempt"})
	})
	*seq++
	if err != nil {
		t.Fatal(err)
	}
	if ok, _ := out.Result.(map[string]any)["success"].(bool); !ok {
		t.Fatalf("the quest was attempted with the dice lent high and missed: %v", out.Result)
	}
}

// passThePerfectionTrial takes the final trial with the dice lent high.
func passThePerfectionTrial(t *testing.T, path, world, trialOp string, seq int) {
	t.Helper()
	var out ActionResponse
	var err error
	withHighDice(t, func() {
		out, err = batch4ApplyErr(path, world, trialOp, 42, seq, map[string]any{})
	})
	if err != nil {
		t.Fatal(err)
	}
	if ok, _ := out.Result.(map[string]any)["success"].(bool); !ok {
		t.Fatalf("the trial was taken with the dice lent high and failed: %v", out.Result)
	}
}

// perfectionColumn reads one column of the realm-0 row, or nil when there is none.
func perfectionColumn(t *testing.T, path, table, col string) any {
	t.Helper()
	return actionScalar(t, path, "SELECT "+col+" FROM "+table+" WHERE user_id=42 AND realm_index=0")
}

func TestUndoingABarBelowFullTakesBackOnlyTheBar(t *testing.T) {
	world := batch4WorldPath(t)
	for _, tc := range []struct{ track, startOp, questOp, table string }{
		{"cultivation", "perfection.start", "perfection.quest", "realm_perfection"},
		{"body", "perfection.body_start", "perfection.body_quest", "body_realm_perfection"},
	} {
		t.Run(tc.track, func(t *testing.T) {
			path := setupBatch4AuthorityDB(t)
			batch4Exec(t, path, perfectionAuditDDL)
			batch4Apply(t, path, world, tc.startOp, 1, map[string]any{"game_minute": 100})
			applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": tc.track, "realm_index": 0, "progress": 50, "reason": "story"})
			seq := 2
			walkFirstPerfectionQuest(t, path, world, tc.questOp, &seq)
			undoLast(t, path)
			q := func(col string) int64 { return storage.ParseInt(perfectionColumn(t, path, tc.table, col)) }
			if got := q("progress"); got != 0 {
				t.Fatalf("progress=%d after undo; the bar the lever set goes back to the 0 it replaced", got)
			}
			if got := q("completed_quests"); got != 1 {
				t.Fatalf("completed_quests=%d after undo: a bar below 100%% wrote the progress alone, and the undo erased the quest the player finished after it", got)
			}
			if got := q("quest_index"); got != 1 {
				t.Fatalf("quest_index=%d after undo, want the 1 play left", got)
			}
			if got := q("json_array_length(discovered_json)"); got != 1 {
				t.Fatalf("%d clue(s) after undo; the quest's clue was play's", got)
			}
			if got := q("active"); got != 1 {
				t.Fatalf("active=%d after undo; a bar below 100%% never touched the path", got)
			}
		})
	}
}

func TestARedoPutsBackARowItsUndoDeleted(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, perfectionAuditDDL)
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "body", "realm_index": 0, "progress": 40, "reason": "story"})
	undoLast(t, path)
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM body_realm_perfection WHERE user_id=42")); got != 0 {
		t.Fatalf("%d row(s) after undoing a bar on a path nobody had; the lever made the row and the undo takes it", got)
	}
	redo := undoLast(t, path).(map[string]any)
	if got := perfectionColumn(t, path, "body_realm_perfection", "progress"); got == nil || storage.ParseInt(got) != 40 {
		t.Fatalf("the redo left progress %v and reported rows_affected=%v; a redo of a bar its undo removed must put the bar back", got, redo["rows_affected"])
	}
	undoLast(t, path) // undo again
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM body_realm_perfection WHERE user_id=42")); got != 0 {
		t.Fatalf("%d row(s) after undo, redo, undo", got)
	}
}

func TestAnUndoLeavesAPathThePlayerStartedOnTheLeversRow(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 40, "reason": "story"})
	batch4Apply(t, path, world, "perfection.start", 1, map[string]any{"game_minute": 100})
	undoLast(t, path)
	if got := perfectionColumn(t, path, "realm_perfection", "active"); got == nil || storage.ParseInt(got) != 1 {
		t.Fatalf("active=%v after undo: the player started the path on the row the lever made, and the undo deleted it", got)
	}
	if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", "progress")); got != 0 {
		t.Fatalf("progress=%d after undo; the bar was the lever's", got)
	}
}

// A fill on a path the player had not started, undone after the player trained
// on it, leaves the row: nothing on it is the lever's any more but what it wrote.
func TestAnUndoOfAFillKeepsWhatPlayDidOnTheRow(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, perfectionAuditDDL)
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	batch4Exec(t, path, "UPDATE realm_perfection SET training_progress=3 WHERE user_id=42 AND realm_index=0")
	undoLast(t, path)
	if got := perfectionColumn(t, path, "realm_perfection", "training_progress"); got == nil || storage.ParseInt(got) != 3 {
		t.Fatalf("training_progress=%v after undo: the player trained on the row the lever made, and the undo deleted it", got)
	}
	if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", "completed_quests")); got != 0 {
		t.Fatalf("completed_quests=%d after undoing a fill; the fill's quests go back", got)
	}
}

func TestAnUndoNeverReopensOrErasesAPerfectedRealm(t *testing.T) {
	world := batch4WorldPath(t)
	t.Run("a started path", func(t *testing.T) {
		path := setupBatch4AuthorityDB(t)
		batch4Exec(t, path, perfectionAuditDDL)
		batch4Apply(t, path, world, "perfection.start", 1, map[string]any{"game_minute": 100})
		applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
		passThePerfectionTrial(t, path, world, "perfection.trial", 2)
		undoLast(t, path)
		batch4Exec(t, path, "DELETE FROM cooldowns WHERE user_id=42")
		if _, err := batch4ApplyErr(path, world, "perfection.quest", 42, 3, map[string]any{"mode": "prepare"}); err == nil || !strings.Contains(err.Error(), "not active") {
			t.Fatalf("after the undo a perfected realm's quests took a step (%v); its trial has paid a permanent reward, and the path is open for a second", err)
		}
		if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", "completed")); got != 1 {
			t.Fatalf("completed=%d after undo", got)
		}
	})
	t.Run("a path the lever made", func(t *testing.T) {
		path := setupBatch4AuthorityDB(t)
		batch4Exec(t, path, perfectionAuditDDL)
		applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
		passThePerfectionTrial(t, path, world, "perfection.trial", 1)
		undoLast(t, path)
		if got := perfectionColumn(t, path, "realm_perfection", "completed"); got == nil || storage.ParseInt(got) != 1 {
			t.Fatalf("completed=%v after undo: the undo deleted a perfected realm whose reward was already paid", got)
		}
		if _, err := batch4ApplyErr(path, world, "perfection.start", 42, 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), "already perfected") {
			t.Fatalf("after the undo the realm could be started again (%v); that is a second permanent reward", err)
		}
	})
}

// Abandoning a path deletes its row, and the undo is an UPDATE: it does not
// bring back a row the player has since removed.
func TestAnUndoDoesNotBringBackAPathThePlayerAbandoned(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Apply(t, path, world, "perfection.start", 1, map[string]any{"game_minute": 100})
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	batch4Apply(t, path, world, "perfection.abandon", 2, map[string]any{"game_minute": 100})
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM realm_perfection WHERE user_id=42")); got != 0 {
		t.Fatalf("%d row(s) after the player abandoned the path", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM realm_perfection WHERE user_id=42")); got != 0 {
		t.Fatalf("%d row(s) after the undo: it brought back a path the player had abandoned", got)
	}
}

// An audit row from before v1.23.1 carries no "existed": it is undone and
// redone on its bar alone, the terms it was written on.
func TestAPerfectionRowFromBeforeTheFillIsUndoneOnItsBarAlone(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Exec(t, path, "INSERT INTO realm_perfection(user_id,realm_index,active,progress,quest_index,completed_quests,discovered_json,updated_at) VALUES(42,0,1,60,2,2,'[\"a\",\"b\"]',0)")
	batch4Exec(t, path, `INSERT INTO admin_audit_log(admin_user_id,action,target,before_json,after_json,reason,created_at) VALUES(0,'admin.player.set_realm_perfection','user:42','{"track":"cultivation","realm_index":0,"progress":30}','{"track":"cultivation","realm_index":0,"progress":60}','old',0)`)
	undoLast(t, path)
	if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", "progress")); got != 30 {
		t.Fatalf("progress=%d, want 30", got)
	}
	if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", "completed_quests")); got != 2 {
		t.Fatalf("completed_quests=%d; an old row touches the bar alone", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", "progress")); got != 60 {
		t.Fatalf("progress=%d after redo, want 60", got)
	}
}

// A row the lever wrote between v1.23.1 and this release - a full bar on an
// existing path, written by the same forward action - is undone and redone.
func TestAFillRowFromTheFillReleasesIsUndoneAndRedone(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Apply(t, path, world, "perfection.start", 1, map[string]any{"game_minute": 100})
	batch4Exec(t, path, "UPDATE realm_perfection SET progress=30,quest_index=2,completed_quests=2,quest_preparation=1,discovered_json='[\"a\"]' WHERE user_id=42 AND realm_index=0")
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	undoLast(t, path)
	redo := undoLast(t, path).(map[string]any)
	for col, want := range map[string]int64{"progress": 100, "completed_quests": 7, "quest_index": 7, "active": 1, "quest_preparation": 0} {
		if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", col)); got != want {
			t.Fatalf("%s=%d after undo and redo, want %d (redo reported %v)", col, got, want, redo)
		}
	}
	if _, err := batch4ApplyErr(path, world, "perfection.trial", 42, 2, map[string]any{}); err != nil {
		t.Fatalf("the redo of a full bar should leave the trial open: %v", err)
	}
}

// The redo is the fill's own upsert, and a fill on a perfected realm already
// keeps its path closed; the redo keeps it closed too, or the trial's reward
// could be taken twice by undoing and redoing a lever.
func TestARedoNeverReopensAPerfectedRealm(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, perfectionAuditDDL)
	batch4Apply(t, path, world, "perfection.start", 1, map[string]any{"game_minute": 100})
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "story"})
	passThePerfectionTrial(t, path, world, "perfection.trial", 2)
	undoLast(t, path)
	undoLast(t, path) // the redo
	batch4Exec(t, path, "DELETE FROM cooldowns WHERE user_id=42")
	if _, err := batch4ApplyErr(path, world, "perfection.quest", 42, 3, map[string]any{"mode": "prepare"}); err == nil || !strings.Contains(err.Error(), "not active") {
		t.Fatalf("after undo and redo a perfected realm's quests took a step (%v); the redo opened the path for a second reward", err)
	}
	if got := storage.ParseInt(perfectionColumn(t, path, "realm_perfection", "completed")); got != 1 {
		t.Fatalf("completed=%d after undo and redo", got)
	}
}
