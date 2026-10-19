package game

import (
	"testing"
	"xianxia/core/internal/gamerng"
)

// A session at a full stage 9 refines an open Perfection path (rc.37), and the
// reply has to be able to say so: it is told whether a path is open at all, so
// the bot decides nothing about "start" against "continue". The dice are lent
// high so the session is certain to gather or to refine.

func trainAtAFullStageNine(t *testing.T, op string, body bool, perfection func(path string)) map[string]any {
	t.Helper()
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	phaseColumn, essenceColumn, realmColumn := "phase", "cultivation", "realm_index"
	if body {
		phaseColumn, essenceColumn, realmColumn = "body_phase", "body_cultivation", "body_realm_index"
	}
	realms := catalog.Realms
	if body {
		realms = catalog.BodyRealms
	}
	cost, err := phaseCost(realms, 1, 9)
	if err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, "UPDATE characters SET "+realmColumn+"=1,"+phaseColumn+"=9,"+essenceColumn+"=? WHERE user_id=42", cost)
	perfection(path)
	return batch4Result(t, batch4Apply(t, path, world, op, 1, map[string]any{"game_minute": 600}))
}

func TestTheCultivateReplyKnowsWhetherAPerfectionPathIsOpen(t *testing.T) {
	for _, tc := range []struct {
		name, op, table string
		body            bool
	}{
		{"cultivation", "cultivation.train", "realm_perfection", false},
		{"body", "cultivation.body_train", "body_realm_perfection", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			open := func(training int64) func(string) {
				return func(path string) {
					batch4Exec(t, path, "INSERT INTO "+tc.table+"(user_id,realm_index,active,training_progress,updated_at) VALUES(42,1,1,?,0)", training)
				}
			}
			none := trainAtAFullStageNine(t, tc.op, tc.body, func(string) {})
			if none["stage_full"] != true || none["perfection_active"] != false || i64(none["perfection_gain"]) != 0 {
				t.Fatalf("with no path open: stage_full=%v perfection_active=%v perfection_gain=%v", none["stage_full"], none["perfection_active"], none["perfection_gain"])
			}
			training := trainAtAFullStageNine(t, tc.op, tc.body, open(0))
			if training["stage_full"] != true || training["perfection_active"] != true || i64(training["perfection_gain"]) < 1 {
				t.Fatalf("an open path on a full stage: stage_full=%v perfection_active=%v perfection_gain=%v; the session should have refined it", training["stage_full"], training["perfection_active"], training["perfection_gain"])
			}
			// A path whose training is as deep as a session takes it gains nothing
			// and is still open: "continue the path" is still the right word.
			deep := trainAtAFullStageNine(t, tc.op, tc.body, open(1000000))
			if deep["perfection_active"] != true || i64(deep["perfection_gain"]) != 0 {
				t.Fatalf("a path trained to its cap: perfection_active=%v perfection_gain=%v", deep["perfection_active"], deep["perfection_gain"])
			}
		})
	}
}
