package game

import (
	"encoding/json"
	"fmt"
	"strings"
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

// A graded treatment treats (v1.12.1).
//
// Reported from play as "Can't treat Qi Deviation": the bag held four Heart
// Calming Pills (Mid) and the treatment refused with "treatment requires 1x
// heart_calming_pill". A grade is a suffix on the id (v1.7.0) and the
// treatment looked the bare id up. These drive the shipped catalogue, because
// whether `heart_calming_pill@mid` names anything is the recipe roster's to
// say, and a fixture with no recipes would refuse every grade.

func treatWithCatalog(t *testing.T, path, condition string) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"condition": condition, "game_minute": 500})
	mut, err := conditionTreatAction(conn, shippedCatalog(t), 77, raw)
	if err != nil {
		return nil, err
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any), nil
}

func bagCount(t *testing.T, path, item string) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	r, err := conn.Execute(`SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE user_id=77 AND item_id=?`, []any{item})
	if err != nil {
		t.Fatal(err)
	}
	return storage.ParseInt(r.Rows[0][0])
}

// TestAGradedPillTreats is the report. Drill: put back the bare-id lookup and
// this fails with the player's own refusal.
func TestAGradedPillTreats(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 3, "heart_calming_pill@mid", 4)
	out, err := treatWithCatalog(t, path, "qi_deviation")
	if err != nil {
		t.Fatalf("four Mid Heart Calming Pills in the bag and the treatment refused: %v", err)
	}
	if got := bagCount(t, path, "heart_calming_pill@mid"); got != 3 {
		t.Fatalf("the treatment must spend the pill it treated with: 4 -> %d", got)
	}
	if out["treatment_item"] != "heart_calming_pill@mid" {
		t.Fatalf("the result must name the pill that was swallowed: %v", out["treatment_item"])
	}
	if got, _ := conditionSeverity(t, path, "qi_deviation"); got >= 3 {
		t.Fatalf("the deviation did not mend: still %d", got)
	}
}

// TestThePlainestPillIsSpentFirst: holding both, the Low pill goes and the Mid
// one stays. Drill: pick the highest index instead and this names the Mid.
func TestThePlainestPillIsSpentFirst(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 3, "heart_calming_pill@mid", 2)
	seedTreatableCondition(t, path, "heart_demon", 2, "heart_calming_pill", 1)
	out, err := treatWithCatalog(t, path, "qi_deviation")
	if err != nil {
		t.Fatal(err)
	}
	if out["treatment_item"] != "heart_calming_pill" || bagCount(t, path, "heart_calming_pill@mid") != 2 {
		t.Fatalf("the treatment swallowed %v while a plain pill was in the bag", out["treatment_item"])
	}
}

// TestAGradeTheCatalogueDoesNotKnowIsNotTheTreatment: a suffix that is no rung
// (or the first rung written out) is not a second name for the pill.
func TestAGradeTheCatalogueDoesNotKnowIsNotTheTreatment(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 10)
	seedTreatableCondition(t, path, "qi_deviation", 3, "heart_calming_pill@nonsense", 1)
	if _, err := treatWithCatalog(t, path, "qi_deviation"); err == nil || !strings.Contains(err.Error(), "Heart Calming Pill") {
		t.Fatalf("an unknown grade treated, or the refusal did not name the pill: %v", err)
	}
}

// TestAGradedRecoveryPillRestoresAtItsGrade: the treatment does what the item
// does, and a grade scales what an item does (v1.7.0) - so a Mid Recovery Pill
// restores what `item.use` would, never less than the Low one's 8.
func TestAGradedRecoveryPillRestoresAtItsGrade(t *testing.T) {
	path := setupDefeatSurvivalDB(t, 1)
	seedTreatableCondition(t, path, "flesh_wound", 1, "recovery_pill@mid", 1)
	out, err := treatWithCatalog(t, path, "flesh_wound")
	if err != nil {
		t.Fatal(err)
	}
	catalog := shippedCatalog(t)
	want := gradedAmount(catalog.Items["recovery_pill"].Use.Instant.VitalityRestore, itemEffectMult(catalog, "recovery_pill@mid"))
	restored, _ := out["restored"].(map[string]any)
	if restored["vitality"] != want || want <= catalog.Items["recovery_pill"].Use.Instant.VitalityRestore {
		t.Fatalf("a Mid Recovery Pill restored %v, want %d (graded above the Low pill's)", restored["vitality"], want)
	}
}
