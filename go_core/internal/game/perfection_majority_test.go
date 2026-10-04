package game

import (
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

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
