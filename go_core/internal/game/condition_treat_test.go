package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// A treatment always mends (v1.0.16).
//
// Reported from play as six Heart-Calming Pills spent on a severity-3 Qi
// Deviation at a 28% chance, six failures, and "I can't heal injuries". The
// roll was Insight + Spirit against 10 + 2 x severity; a failure mended
// nothing; and the deviation had already taken its severity off Spirit, so the
// worse it was the less anybody could cure it. These run on the defeat-survival
// fixture, which carries production's foreign keys.

// seedCondition gives the fixture's cultivator a condition exactly as the game
// writes one - the character_conditions row *and* its active_effects row,
// through the same helper a Force deviation uses - plus the medicine for it.
func seedTreatableCondition(t *testing.T, path, key string, severity int64, item string, quantity int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := applyCombatCondition(conn, 77, key, severity, "cultivation", "force_stance", 500); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(77,?,?)`, []any{item, quantity}); err != nil {
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
}

func conditionSeverity(t *testing.T, path, key string) (int64, string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	r, err := conn.Execute(`SELECT severity,state FROM character_conditions WHERE user_id=77 AND condition_key=?`, []any{key})
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Rows) == 0 {
		t.Fatalf("no %s row", key)
	}
	return storage.ParseInt(r.Rows[0][0]), fmt.Sprint(r.Rows[0][1])
}

// TestTheWorstDiceStillMend is the finding. Drill: make
// `conditionTreatReduction` return 0 on a failure and this fails with
// "snake eyes mended nothing".
func TestTheWorstDiceStillMend(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 3, "heart_calming_pill", 1)
	defer gamerng.UseRoller(func(int) int { return 0 })()

	out := treatCondition(t, path, "qi_deviation")
	if success, _ := out["success"].(bool); success {
		t.Fatalf("the fixture must fail the roll for this test to mean anything: %v", out["roll"])
	}
	if got, _ := conditionSeverity(t, path, "qi_deviation"); got != 2 {
		t.Fatalf("snake eyes mended nothing: severity 3 -> %d; a pill is medicine and swallowing one always does something", got)
	}
	if mended, _ := out["mended"].(bool); !mended {
		t.Fatalf("the result must say the treatment mended: %v", out)
	}
}

// TestTheAilmentIsLeftOutOfItsOwnCure. Qi Deviation takes its severity off
// Spirit; the cure rolls Spirit. Drill: drop the `itself` skip and this fails
// with "the deviation's own -3 Spirit was counted against its cure".
func TestTheAilmentIsLeftOutOfItsOwnCure(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 3, "heart_calming_pill", 1)
	out := treatCondition(t, path, "qi_deviation")
	roll, _ := out["roll"].(map[string]any)
	// The fixture sheet is insight 3, spirit 2.
	if got := storage.ParseInt(roll["modifier"]); got != 5 {
		t.Fatalf("the deviation's own -3 Spirit was counted against its cure: modifier %d, want the sheet's insight+spirit 5", got)
	}
	if got := storage.ParseInt(roll["tn"]); got != conditionTreatTN(3) || got != 13 {
		t.Fatalf("a severity-3 treatment is rolled against %d, want 13 (10 + severity)", got)
	}
}

// TestAnotherAilmentStillCounts: only the condition being treated is left
// out. A Soul Wound still dulls the mind that treats a deviation.
func TestAnotherAilmentStillCounts(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 2, "heart_calming_pill", 1)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := applyCombatCondition(conn, 77, "soul_wound", 1, "tribulation", "1", 500); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	conn.Close()
	out := treatCondition(t, path, "qi_deviation")
	roll, _ := out["roll"].(map[string]any)
	if got := storage.ParseInt(roll["modifier"]); got != 3 {
		t.Fatalf("a Soul Wound (-1 insight, -1 spirit) must still count against another condition's cure: modifier %d, want 3", got)
	}
}

// TestAStrongTreatmentMendsThree: the best dice take a severity-3 condition
// all the way to resolved, and lift its penalty.
func TestAStrongTreatmentMendsThree(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 3, "heart_calming_pill", 1)
	defer gamerng.UseRoller(func(n int) int { return n - 1 })()

	out := treatCondition(t, path, "qi_deviation")
	if got := storage.ParseInt(out["reduction"]); got != 3 {
		t.Fatalf("a strong success mends three levels, got %d: %v", got, out["roll"])
	}
	if _, state := conditionSeverity(t, path, "qi_deviation"); state != "resolved" {
		t.Fatalf("severity 3 less 3 is resolved, state=%q", state)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	r, err := conn.Execute(`SELECT COUNT(*) FROM active_effects WHERE user_id=77 AND source_type='condition' AND source_id='qi_deviation'`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(r.Rows[0][0]) != 0 {
		t.Fatal("a resolved condition must stop penalising")
	}
}

// TestAConditionCostsAtMostItsSeverityInPills: the promise the reduction
// makes, walked on the worst dice from the worst severity.
func TestAConditionCostsAtMostItsSeverityInPills(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 5, "heart_calming_pill", 5)
	defer gamerng.UseRoller(func(int) int { return 0 })()
	for pill := 1; pill <= 5; pill++ {
		treatCondition(t, path, "qi_deviation")
	}
	if got, state := conditionSeverity(t, path, "qi_deviation"); state != "resolved" {
		t.Fatalf("five pills on the worst dice left a severity-5 deviation at %d (%s)", got, state)
	}
}

func TestTheReductionIsNeverZero(t *testing.T) {
	for _, c := range []struct {
		success bool
		margin  int64
		want    int64
	}{{false, -19, 1}, {false, -1, 1}, {true, 0, 2}, {true, 4, 2}, {true, 5, 3}, {true, 12, 3}} {
		if got := conditionTreatReduction(c.success, c.margin); got != c.want {
			t.Fatalf("success=%v margin=%d mends %d, want %d", c.success, c.margin, got, c.want)
		}
	}
}
