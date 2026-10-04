package game

import (
	"fmt"
	"strings"
	"testing"

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
