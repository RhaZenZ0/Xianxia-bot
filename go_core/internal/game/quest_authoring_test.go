package game

import (
	"encoding/json"
	"fmt"
	"testing"
)

// admin.quest.save (v0.24.0) - the verb that did not exist, and the decision
// that comes with it.

func questSave(t *testing.T, path string, admin int64, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := Apply(path, ActionRequest{Operation: "admin.quest.save", ActorID: admin, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func definitionField(t *testing.T, path, questKey, column string) string {
	t.Helper()
	return fmt.Sprint(actionScalar(t, path,
		fmt.Sprintf("SELECT %s FROM quest_definitions WHERE quest_key='%s'", column, questKey)))
}

func heldProgress(t *testing.T, path string, userID int64, questKey string) map[string]int64 {
	t.Helper()
	progress := map[string]int64{}
	raw := fmt.Sprint(questRow(t, path, userID, questKey)["progress_json"])
	if raw != "" && raw != "<nil>" {
		if err := json.Unmarshal([]byte(raw), &progress); err != nil {
			t.Fatalf("progress_json=%q: %v", raw, err)
		}
	}
	return progress
}

func twoObjectiveQuest() []map[string]any {
	return []map[string]any{
		{"id": "talk_1", "type": "talk", "target": "Steward Qiao", "count": 1},
		{"id": "explore_2", "type": "explore", "target": "Greenriver Town", "count": 1},
	}
}

func TestSavingANewQuestCreatesItAsADraft(t *testing.T) {
	path := setupCommissionDB(t)
	batch4SetCanonicalGameMinute(t, path, 100)
	out, err := questSave(t, path, 7, map[string]any{
		"quest_key":  "the_bell_that_rings_itself",
		"title":      "The Bell That Rings Itself",
		"objectives": twoObjectiveQuest(),
		"rewards":    map[string]any{"insight_xp": 20},
		"reason":     "written by hand",
	})
	if err != nil {
		t.Fatal(err)
	}
	if created, _ := out["created"].(bool); !created {
		t.Fatalf("out=%v", out)
	}
	if got := definitionField(t, path, "the_bell_that_rings_itself", "status"); got != "draft" {
		t.Fatalf("status=%s; a saved quest must not reach players until it is approved", got)
	}
	if got := countRows(t, path,
		`SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.quest.save' AND target='the_bell_that_rings_itself'`); got != 1 {
		t.Fatalf("audit rows=%d", got)
	}
}

// Editing must not approve. The two used to be the same status column and the
// same write; keeping them apart is what stops "I fixed a typo" from putting
// something live.
func TestSavingAnApprovedQuestLeavesItApproved(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps", twoObjectiveQuest(), map[string]any{"insight_xp": 10})
	batch4SetCanonicalGameMinute(t, path, 100)
	if _, err := questSave(t, path, 7, map[string]any{
		"quest_key": "first_steps", "title": "First Steps Beneath Heaven",
		"objectives": twoObjectiveQuest(), "rewards": map[string]any{"insight_xp": 12},
	}); err != nil {
		t.Fatal(err)
	}
	if got := definitionField(t, path, "first_steps", "status"); got != "approved" {
		t.Fatalf("status=%s", got)
	}
}

// The default, and the only policy that cannot cost a player anything.
func TestKeepingHoldersLeavesThemOnTheTermsTheyAccepted(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps", twoObjectiveQuest(), map[string]any{"spirit_stones": 25})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)

	out, err := questSave(t, path, 7, map[string]any{
		"quest_key": "first_steps", "title": "First Steps Beneath Heaven",
		"objectives": []map[string]any{{"id": "talk_1", "type": "talk", "target": "Warehouse Clerk Bai", "count": 1}},
		"rewards":    map[string]any{"spirit_stones": 400},
	})
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["holders"]) != 1 || fmt.Sprint(out["hold_policy"]) != "keep" {
		t.Fatalf("out=%v", out)
	}

	batch4SetCanonicalGameMinute(t, path, 130)
	reportProgress(t, path, 42, "first_steps", "talk", "steward qiao")
	reportProgress(t, path, 42, "first_steps", "explore", "greenriver town")
	if stones, _ := characterPurse(t, path, 42); stones != 25 {
		t.Fatalf("stones=%d, want the 25 they took it for", stones)
	}
}

// The subtle half, and the reason the holders are read before the definition is
// written. A player who accepted before terms were pinned has nothing on their
// row; "keep them as they are" has to capture the old definition first, or it
// quietly does the opposite of what it says.
func TestKeepingHoldersPinsTheOldTermsForSomebodyWhoHadNoPin(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps",
		[]map[string]any{{"id": "talk_1", "type": "talk", "target": "Steward Qiao", "count": 1}},
		map[string]any{"spirit_stones": 25})
	// Accepted the way v0.23.x wrote it: no pinned terms at all.
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at)
		VALUES(42,'first_steps','active','{}',0,0,0,0,0)`)
	batch4SetCanonicalGameMinute(t, path, 100)

	if _, err := questSave(t, path, 7, map[string]any{
		"quest_key": "first_steps", "title": "First Steps Beneath Heaven",
		"objectives": []map[string]any{{"id": "talk_1", "type": "talk", "target": "Warehouse Clerk Bai", "count": 1}},
		"rewards":    map[string]any{"spirit_stones": 400},
	}); err != nil {
		t.Fatal(err)
	}

	batch4SetCanonicalGameMinute(t, path, 130)
	transition := reportProgress(t, path, 42, "first_steps", "talk", "steward qiao")
	if complete, _ := transition["complete"].(bool); !complete {
		t.Fatalf("the unpinned holder was moved onto the new objective: %v", transition)
	}
	if stones, _ := characterPurse(t, path, 42); stones != 25 {
		t.Fatalf("stones=%d, want the 25 the old definition offered", stones)
	}
}

func TestMigratingCarriesProgressOnObjectivesThatDidNotChange(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps", twoObjectiveQuest(), map[string]any{"spirit_stones": 25})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)
	// One of the two objectives done: the half-finished state a migration
	// actually meets.
	batch4SetCanonicalGameMinute(t, path, 110)
	reportProgress(t, path, 42, "first_steps", "talk", "steward qiao")

	out, err := questSave(t, path, 7, map[string]any{
		"quest_key": "first_steps", "title": "First Steps Beneath Heaven",
		"hold_policy": "migrate",
		"objectives": []map[string]any{
			{"id": "talk_1", "type": "talk", "target": "Steward Qiao", "count": 1},
			{"id": "explore_2", "type": "explore", "target": "Cloudspine Road", "count": 1},
		},
		"rewards": map[string]any{"spirit_stones": 60},
	})
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["objectives_dropped"]) != 0 {
		t.Fatalf("out=%v; the objective they had finished did not change", out)
	}
	if got := heldProgress(t, path, 42, "first_steps")["talk_1"]; got != 1 {
		t.Fatalf("talk_1=%d; finished work was thrown away", got)
	}
	// And they are on the new deal now, both what it asks and what it pays.
	batch4SetCanonicalGameMinute(t, path, 130)
	reportProgress(t, path, 42, "first_steps", "explore", "cloudspine road")
	if stones, _ := characterPurse(t, path, 42); stones != 60 {
		t.Fatalf("stones=%d, want the new terms they were migrated to", stones)
	}
}

func TestMigratingDropsProgressOnAnObjectiveThatChanged(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps", twoObjectiveQuest(), map[string]any{"spirit_stones": 25})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)
	batch4Exec(t, path, `UPDATE character_quests SET progress_json='{"talk_1":1}' WHERE user_id=42 AND quest_key='first_steps'`)

	out, err := questSave(t, path, 7, map[string]any{
		"quest_key": "first_steps", "title": "First Steps Beneath Heaven",
		"hold_policy": "migrate",
		"objectives": []map[string]any{
			{"id": "talk_1", "type": "talk", "target": "Warehouse Clerk Bai", "count": 1},
			{"id": "explore_2", "type": "explore", "target": "Greenriver Town", "count": 1},
		},
		"rewards": map[string]any{"spirit_stones": 25},
	})
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["objectives_dropped"]) != 1 {
		t.Fatalf("out=%v; the GM was not told anybody lost work", out)
	}
	if _, still := heldProgress(t, path, 42, "first_steps")["talk_1"]; still {
		t.Fatal("progress against Steward Qiao survived a change to somebody else")
	}
}

// A commission's payment is a promise a named person made. Migrating what the
// work is does not migrate what he said he would pay for it.
func TestMigratingACommissionKeepsTheTermsItsGiverAgreedTo(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	batch4Exec(t, path, `UPDATE quest_definitions SET objectives_json=? WHERE quest_key='commission_crate'`,
		`[{"id":"explore_1","type":"explore","target":"Greenriver Town","count":1}]`)
	batch4SetCanonicalGameMinute(t, path, 100)
	if _, err := commissionApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "variant_index": 1, "game_minute": 100}); err != nil {
		t.Fatal(err)
	}

	if _, err := questSave(t, path, 7, map[string]any{
		"quest_key": "commission_crate", "title": "The Replaced Crate", "giver_npc": "Steward Qiao",
		"hold_policy": "migrate",
		"objectives":  []map[string]any{{"id": "explore_1", "type": "explore", "target": "Cloudspine Road", "count": 1}},
		"rewards":     map[string]any{"spirit_stones": 5},
	}); err != nil {
		t.Fatal(err)
	}

	var terms questTerms
	if err := json.Unmarshal([]byte(pinnedTermsJSON(t, path, 42, "commission_crate")), &terms); err != nil {
		t.Fatal(err)
	}
	if i64(terms.Rewards["spirit_stones"]) != 24 || terms.Label != "rushed" {
		t.Fatalf("terms=%v; the giver's agreed payment was rewritten", terms)
	}
	if fmt.Sprint(terms.Objectives[0]["target"]) != "Cloudspine Road" {
		t.Fatalf("objectives=%v; the migration did not move the work", terms.Objectives)
	}
}

// Revoking removes the row rather than closing it, so the quest can be taken
// again from scratch. A closed row would leave "you have taken that quest
// before" standing between the player and the fixed version.
func TestRevokingTakesTheQuestBackAndLetsItBeTakenAgain(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps", twoObjectiveQuest(), map[string]any{"spirit_stones": 25})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)

	out, err := questSave(t, path, 7, map[string]any{
		"quest_key": "first_steps", "title": "First Steps Beneath Heaven",
		"hold_policy": "revoke",
		"objectives":  twoObjectiveQuest(), "rewards": map[string]any{"spirit_stones": 25},
	})
	if err != nil {
		t.Fatal(err)
	}
	if i64(out["holders"]) != 1 {
		t.Fatalf("out=%v", out)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND quest_key='first_steps'`); got != 0 {
		t.Fatalf("rows=%d; a revoked quest was left in the player's log", got)
	}
	batch4SetCanonicalGameMinute(t, path, 140)
	if _, err := commissionApply(t, path, "commission.accept", 42, 2,
		map[string]any{"quest_key": "first_steps", "game_minute": 140}); err != nil {
		t.Fatalf("the fixed version could not be taken again: %v", err)
	}
}

func TestAnUnknownHoldPolicyIsRefusedRatherThanTreatedAsTheDefault(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps", twoObjectiveQuest(), map[string]any{})
	batch4SetCanonicalGameMinute(t, path, 100)
	if _, err := questSave(t, path, 7, map[string]any{
		"quest_key": "first_steps", "title": "First Steps Beneath Heaven",
		"hold_policy": "wipe", "objectives": twoObjectiveQuest(),
	}); err == nil {
		t.Fatal("an unrecognised hold policy was accepted; a typo must not silently keep or silently revoke")
	}
}

// The review action was always general; only its name said commission.
func TestQuestReviewApprovesADefinitionWithNoGiver(t *testing.T) {
	path := setupCommissionDB(t)
	batch4SetCanonicalGameMinute(t, path, 100)
	if _, err := questSave(t, path, 7, map[string]any{
		"quest_key": "the_bell", "title": "The Bell That Rings Itself",
		"objectives": twoObjectiveQuest(), "rewards": map[string]any{"insight_xp": 20},
	}); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"quest_key": "the_bell", "status": "approved", "reason": "reviewed"})
	if _, err := Apply(path, ActionRequest{Operation: "admin.quest.review", ActorID: 7, Payload: raw}); err != nil {
		t.Fatal(err)
	}
	if got := definitionField(t, path, "the_bell", "status"); got != "approved" {
		t.Fatalf("status=%s", got)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.quest.review'`); got != 1 {
		t.Fatalf("audit rows=%d", got)
	}
}
