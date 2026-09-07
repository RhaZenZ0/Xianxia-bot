package game

import (
	"encoding/json"
	"fmt"
	"testing"
)

// The terms a player accepted are theirs (v0.24.0).
//
// Every test here fails against the previous release, where quest.progress was
// handed the objectives and the rewards by its caller and read them from the
// definition as it stood at that moment.

func acceptQuest(t *testing.T, path string, actor int64, key string, gameMinute int64) map[string]any {
	t.Helper()
	out, err := commissionApply(t, path, "commission.accept", actor, 1,
		map[string]any{"quest_key": key, "game_minute": gameMinute})
	if err != nil {
		t.Fatalf("accept %s: %v", key, err)
	}
	return out
}

func reportProgress(t *testing.T, path string, actor int64, key, objectiveType, target string) map[string]any {
	t.Helper()
	payload := map[string]any{"quest_key": key, "objective_type": objectiveType, "amount": 1}
	if target != "" {
		payload["target"] = target
	}
	out, err := Apply(path, ActionRequest{Operation: "quest.progress", ActorID: actor, Payload: payloadJSON(t, payload)})
	if err != nil {
		t.Fatalf("progress %s: %v", key, err)
	}
	transition, _ := out.Result.(map[string]any)
	return transition
}

func editQuestRewards(t *testing.T, path, key, rewardsJSON string) {
	t.Helper()
	batch4Exec(t, path, `UPDATE quest_definitions SET rewards_json=? WHERE quest_key=?`, rewardsJSON, key)
}

func pinnedTermsJSON(t *testing.T, path string, actor int64, key string) string {
	t.Helper()
	return fmt.Sprint(questRow(t, path, actor, key)["terms_json"])
}

func TestAcceptingAQuestPinsItsTerms(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps",
		[]map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}},
		map[string]any{"spirit_stones": 40, "insight_xp": 30})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)

	var terms questTerms
	if err := json.Unmarshal([]byte(pinnedTermsJSON(t, path, 42, "first_steps")), &terms); err != nil {
		t.Fatalf("nothing was pinned onto the row: %v", err)
	}
	if len(terms.Objectives) != 1 || fmt.Sprint(terms.Objectives[0]["id"]) != "talk" {
		t.Fatalf("objectives=%v", terms.Objectives)
	}
	if i64(terms.Rewards["spirit_stones"]) != 40 || i64(terms.Rewards["insight_xp"]) != 30 {
		t.Fatalf("rewards=%v", terms.Rewards)
	}
}

// The finding this release exists for. A GM edits a definition; four people
// are already carrying it; the engine used to read the new numbers at the
// moment they finished, so the deal they accepted was rewritten under them.
func TestEditingADefinitionDoesNotChangeWhatAHeldQuestPays(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps",
		[]map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}},
		map[string]any{"spirit_stones": 40, "insight_xp": 30})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)

	// The GM halves the reward after the player took it on.
	editQuestRewards(t, path, "first_steps", `{"spirit_stones":1,"insight_xp":1}`)

	batch4SetCanonicalGameMinute(t, path, 130)
	transition := reportProgress(t, path, 42, "first_steps", "talk", "elder pine")
	if complete, _ := transition["complete"].(bool); !complete {
		t.Fatalf("transition=%v", transition)
	}
	stones, insight := characterPurse(t, path, 42)
	if stones != 40 || insight != 30 {
		t.Fatalf("purse=%d/%d; the edit rewrote a deal that had already been accepted", stones, insight)
	}
}

// The same fault in the other direction, and the worse half of it: a player who
// has done what was asked can be told they have not, because what was asked
// changed while they were doing it.
func TestEditingADefinitionDoesNotChangeWhatAHeldQuestAsksFor(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps",
		[]map[string]any{{"id": "talk", "type": "talk", "target": "Steward Qiao", "count": 1}},
		map[string]any{"spirit_stones": 10})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)

	// The GM points objective 1 at somebody else entirely.
	batch4Exec(t, path, `UPDATE quest_definitions SET objectives_json=? WHERE quest_key='first_steps'`,
		`[{"id":"talk","type":"talk","target":"Warehouse Clerk Bai","count":1}]`)

	batch4SetCanonicalGameMinute(t, path, 130)
	transition := reportProgress(t, path, 42, "first_steps", "talk", "steward qiao")
	if complete, _ := transition["complete"].(bool); !complete {
		t.Fatalf("speaking to the person the quest actually named did not finish it: %v", transition)
	}
}

// Rows accepted before this release carry no pin. They were on the current
// definition's terms, so that is what they are pinned to - once, on the first
// touch, and not again.
func TestALegacyRowIsBackfilledFromTheDefinitionAndThenStopsMoving(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps",
		[]map[string]any{
			{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1},
			{"id": "explore", "type": "explore", "count": 1},
		},
		map[string]any{"spirit_stones": 25})
	// Written the way v0.23.x wrote it: no terms_json at all.
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at)
		VALUES(42,'first_steps','active','{}',0,0,0,0,0)`)
	batch4SetCanonicalGameMinute(t, path, 130)

	reportProgress(t, path, 42, "first_steps", "talk", "elder pine")
	if pinnedTermsJSON(t, path, 42, "first_steps") == "" {
		t.Fatal("the legacy row was not backfilled")
	}

	// Now edit it. The backfill happened; the terms must not move again.
	editQuestRewards(t, path, "first_steps", `{"spirit_stones":9999}`)
	reportProgress(t, path, 42, "first_steps", "explore", "")
	if stones, _ := characterPurse(t, path, 42); stones != 25 {
		t.Fatalf("stones=%d, want the 25 the row was backfilled with", stones)
	}
}

// Retiring a definition takes it out of the pool. It must not also freeze
// everybody already carrying it: before terms were pinned, the catalog Python
// sent objectives from only held approved definitions, so a retired quest
// stopped progressing and could never be finished or paid.
func TestAQuestRetiredWhileHeldCanStillBeFinished(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps",
		[]map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}},
		map[string]any{"spirit_stones": 15})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)

	batch4Exec(t, path, `UPDATE quest_definitions SET status='retired' WHERE quest_key='first_steps'`)

	batch4SetCanonicalGameMinute(t, path, 130)
	transition := reportProgress(t, path, 42, "first_steps", "talk", "elder pine")
	if complete, _ := transition["complete"].(bool); !complete {
		t.Fatalf("a retired quest stranded the player holding it: %v", transition)
	}
	if stones, _ := characterPurse(t, path, 42); stones != 15 {
		t.Fatalf("stones=%d; a retired quest was completed without paying", stones)
	}
}

// The authority half. Python may still send the old fields; they must count for
// nothing, in both directions - a caller cannot inflate a reward and cannot
// invent an objective the quest never had.
func TestCallerSuppliedTermsAreIgnored(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path, "first_steps",
		[]map[string]any{
			{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1},
			{"id": "explore", "type": "explore", "count": 1},
		},
		map[string]any{"spirit_stones": 12})
	batch4SetCanonicalGameMinute(t, path, 100)
	acceptQuest(t, path, 42, "first_steps", 100)
	batch4SetCanonicalGameMinute(t, path, 130)

	// A caller claiming the quest asks for one thing and pays a fortune.
	raw := payloadJSON(t, map[string]any{
		"quest_key":      "first_steps",
		"objectives":     []map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}},
		"objective_type": "talk", "target": "elder pine", "amount": 1,
		"rewards": map[string]any{"spirit_stones": 100000},
	})
	out, err := Apply(path, ActionRequest{Operation: "quest.progress", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	transition, _ := out.Result.(map[string]any)
	if complete, _ := transition["complete"].(bool); complete {
		t.Fatal("the caller's shortened objective list completed a two-objective quest")
	}
	if stones, _ := characterPurse(t, path, 42); stones != 0 {
		t.Fatalf("stones=%d; the caller's rewards were spent", stones)
	}
}

// A commission locked its variant and its deadline from the start. The point of
// this release is that the rest of the agreement is locked with them, so the
// chosen terms cannot be re-read from a definition that has since changed.
func TestACommissionPinsTheVariantItWasTakenOn(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	batch4Exec(t, path, `UPDATE quest_definitions SET objectives_json=? WHERE quest_key='commission_crate'`,
		`[{"id":"explore","type":"explore","target":"Greenriver Town","count":1}]`)
	batch4SetCanonicalGameMinute(t, path, 100)
	if _, err := commissionApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "variant_index": 1, "game_minute": 100}); err != nil {
		t.Fatal(err)
	}

	var terms questTerms
	if err := json.Unmarshal([]byte(pinnedTermsJSON(t, path, 42, "commission_crate")), &terms); err != nil {
		t.Fatalf("a commission pinned no terms: %v", err)
	}
	if terms.Label != "rushed" {
		t.Fatalf("label=%q, want the variant that was accepted", terms.Label)
	}
	if i64(terms.Rewards["spirit_stones"]) != 24 {
		t.Fatalf("rewards=%v, want the rushed terms", terms.Rewards)
	}
	if len(terms.Objectives) != 1 || fmt.Sprint(terms.Objectives[0]["target"]) != "Greenriver Town" {
		t.Fatalf("objectives=%v", terms.Objectives)
	}
}
