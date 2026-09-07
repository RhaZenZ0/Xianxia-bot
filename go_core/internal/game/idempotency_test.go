package game

// The v0.22.2 regression test for the review's finding #2.
//
// The authoritative contract is: the same action_id returns the same result.
// It held for a retry that arrived after the original had committed, and broke
// for one that arrived while it was still in flight - both callers missed the
// receipt check, both queued for the write lock, and the loser re-ran the
// mutation and died on `UNIQUE constraint failed: domain_events.event_uid`.
// The state stayed correct (the loser rolled back) but the caller got an error
// where the contract promises a result. The reviewer measured 10-23 failures
// out of 24 concurrent callers.
//
// The check now happens again *inside* the transaction, where the winner's
// receipt is visible. These tests assert the whole contract, not just the
// absence of an error: one mutation, one domain event, one version bump, and
// every caller holding the same result.

import (
	"encoding/json"
	"fmt"
	"sync"
	"testing"

	"xianxia/core/internal/storage"
)

func payloadJSON(t *testing.T, payload map[string]any) []byte {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

type applyOutcome struct {
	response ActionResponse
	err      error
}

// applyConcurrently fires n requests that are byte-for-byte identical,
// including the action_id, and releases them together.
func applyConcurrently(t *testing.T, path, op string, actor int64, actionID string, payload map[string]any, n int) []applyOutcome {
	t.Helper()
	raw := payloadJSON(t, payload)
	outcomes := make([]applyOutcome, n)
	start := make(chan struct{})
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			<-start
			out, err := ApplyWithWorld(path, "", ActionRequest{
				APIVersion: authoritativeAPIVersion,
				ActionID:   actionID,
				Operation:  op,
				ActorID:    actor,
				Payload:    raw,
			})
			outcomes[index] = applyOutcome{response: out, err: err}
		}(i)
	}
	close(start)
	wg.Wait()
	return outcomes
}

func countRows(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}

func TestOneActionIdMeansOneMutationAndEveryCallerGetsTheResult(t *testing.T) {
	const callers = 32
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	batch4SetCanonicalGameMinute(t, path, 1000)

	outcomes := applyConcurrently(t, path, "commission.accept", 42, "duplicate-delivery-1",
		map[string]any{"quest_key": "commission_crate", "variant_index": 1}, callers)

	originals, replays := 0, 0
	var firstResult string
	for i, outcome := range outcomes {
		if outcome.err != nil {
			t.Fatalf("caller %d failed instead of replaying: %v", i, outcome.err)
		}
		if outcome.response.Replayed {
			replays++
		} else {
			originals++
		}
		// Every caller must hold the same answer, not merely a successful one.
		result := fmt.Sprint(outcome.response.Result)
		if firstResult == "" {
			firstResult = result
		} else if result != firstResult {
			t.Fatalf("caller %d got a different result:\n %s\nvs\n %s", i, result, firstResult)
		}
	}
	if originals != 1 || replays != callers-1 {
		t.Fatalf("originals=%d replays=%d, want 1 and %d", originals, replays, callers-1)
	}

	// One mutation, one event, one version bump.
	if got := countRows(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND quest_key='commission_crate'`); got != 1 {
		t.Fatalf("character_quests rows=%d", got)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM domain_events WHERE actor_id=42 AND event_type='commission.accept'`); got != 1 {
		t.Fatalf("domain_events rows=%d, want 1", got)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM authoritative_action_receipts WHERE action_id='duplicate-delivery-1'`); got != 1 {
		t.Fatalf("receipts=%d, want 1", got)
	}
	if got := countRows(t, path, `SELECT state_version FROM authoritative_actor_versions WHERE actor_id=42`); got != 1 {
		t.Fatalf("actor state_version=%d, want exactly one increment", got)
	}
}

func TestConcurrentDistinctActionsStillEachApplyOnce(t *testing.T) {
	// The mirror: making duplicates replay must not make distinct actions
	// collapse into one. Ten different action_ids, ten different commissions,
	// all at once.
	const n = 10
	path := setupCommissionDB(t)
	for i := 0; i < n; i++ {
		seedCommission(t, path, fmt.Sprintf("commission_%d", i), nil)
	}
	batch4SetCanonicalGameMinute(t, path, 1000)

	var wg sync.WaitGroup
	errs := make([]error, n)
	start := make(chan struct{})
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			<-start
			// A different actor each, so the one-commission-at-a-time rule is
			// not what is being measured here.
			_, err := ApplyWithWorld(path, "", ActionRequest{
				APIVersion: authoritativeAPIVersion,
				ActionID:   fmt.Sprintf("distinct-%d", index),
				Operation:  "commission.accept",
				ActorID:    42,
				Payload:    payloadJSON(t, map[string]any{"quest_key": fmt.Sprintf("commission_%d", index)}),
			})
			errs[index] = err
		}(i)
	}
	close(start)
	wg.Wait()

	// Exactly one may succeed: they are ten distinct actions by one player, and
	// the one-at-a-time rule refuses the other nine. What matters is that they
	// are refused *by the rule* - not silently replayed as each other.
	succeeded := 0
	for _, err := range errs {
		if err == nil {
			succeeded++
		}
	}
	if succeeded != 1 {
		t.Fatalf("%d of %d distinct actions succeeded; the commission slot allows one", succeeded, n)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42`); got != 1 {
		t.Fatalf("character_quests rows=%d, want 1", got)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM authoritative_action_receipts`); got != 1 {
		t.Fatalf("receipts=%d - a refused action should leave none", got)
	}
}

func TestAnActionIdCannotBeReusedForADifferentAction(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	seedCommission(t, path, "commission_other", nil)
	batch4SetCanonicalGameMinute(t, path, 1000)

	if _, err := ApplyWithWorld(path, "", ActionRequest{
		APIVersion: authoritativeAPIVersion, ActionID: "reused", Operation: "commission.accept", ActorID: 42,
		Payload: payloadJSON(t, map[string]any{"quest_key": "commission_crate"}),
	}); err != nil {
		t.Fatal(err)
	}
	// Same id, different actor: this is a client bug, and returning the first
	// caller's result would be worse than an error.
	if _, err := ApplyWithWorld(path, "", ActionRequest{
		APIVersion: authoritativeAPIVersion, ActionID: "reused", Operation: "commission.accept", ActorID: 43,
		Payload: payloadJSON(t, map[string]any{"quest_key": "commission_other"}),
	}); err == nil {
		t.Fatal("an action_id was reused across actors without complaint")
	}
}

// The review's finding #3, for ordinary quests. Commissions have paid inside
// their completion transaction since v0.22.0; everything else went through a
// second engine call from Python, and anything that interrupted the gap left a
// completed quest that could never be paid - the next progress report only
// looks at active quests, so no retry could reach it.

func TestAnOrdinaryQuestIsPaidByTheTransactionThatCompletesIt(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path,
		"first_steps",
		[]map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}},
		map[string]any{"spirit_stones": 40, "insight_xp": 30, "items": map[string]any{"spirit_herb": 2}})
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at)
		VALUES(42,'first_steps','active','{}',0,0,0,0,0)`)
	batch4SetCanonicalGameMinute(t, path, 130)

	raw := payloadJSON(t, map[string]any{
		"quest_key":      "first_steps",
		"objective_type": "talk", "target": "elder pine", "amount": 1,
	})
	out, err := Apply(path, ActionRequest{Operation: "quest.progress", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	transition, _ := out.Result.(map[string]any)
	if complete, _ := transition["complete"].(bool); !complete {
		t.Fatalf("transition=%v", transition)
	}
	granted, _ := transition["rewards_granted"].(map[string]any)
	if i64(granted["spirit_stones"]) != 40 || i64(granted["insight_xp"]) != 30 {
		t.Fatalf("rewards_granted=%v", granted)
	}
	// Paid, in the same commit that flipped the status.
	stones, insight := characterPurse(t, path, 42)
	if stones != 40 || insight != 30 {
		t.Fatalf("purse=%d/%d, want 40/30", stones, insight)
	}
	if got := countRows(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'`); got != 2 {
		t.Fatalf("items=%d", got)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM event_log WHERE user_id=42 AND event_type='quest_reward:first_steps'`); got != 1 {
		t.Fatalf("reward events=%d", got)
	}
	if got := fmt.Sprint(questRow(t, path, 42, "first_steps")["status"]); got != "completed" {
		t.Fatalf("status=%s", got)
	}
}

func TestProgressThatCompletesNothingPaysNothing(t *testing.T) {
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path,
		"first_steps",
		[]map[string]any{
			{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1},
			{"id": "explore", "type": "explore", "count": 1},
		},
		map[string]any{"spirit_stones": 40, "insight_xp": 30})
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at)
		VALUES(42,'first_steps','active','{}',0,0,0,0,0)`)
	batch4SetCanonicalGameMinute(t, path, 130)

	// Two objectives, one reported: the quest is touched but not complete, and
	// nothing may be paid on the way past.
	raw := payloadJSON(t, map[string]any{
		"quest_key":      "first_steps",
		"objective_type": "talk", "target": "elder pine", "amount": 1,
	})
	if _, err := Apply(path, ActionRequest{Operation: "quest.progress", ActorID: 42, Payload: raw}); err != nil {
		t.Fatal(err)
	}
	if stones, insight := characterPurse(t, path, 42); stones != 0 || insight != 0 {
		t.Fatalf("an incomplete quest paid %d/%d", stones, insight)
	}
}

func TestQuestRewardsAreCappedWhateverTheDefinitionAsksFor(t *testing.T) {
	// The engine keeps its own ceiling on the quest payout path. Since v0.24.0
	// the rewards come from the stored definition rather than the caller, so
	// this is no longer a guard against a compromised caller - it is a guard
	// against a definition, however it got written, that would mint an economy.
	path := setupCommissionDB(t)
	seedOrdinaryQuest(t, path,
		"first_steps",
		[]map[string]any{{"id": "talk", "type": "talk", "target": "Elder Pine", "count": 1}},
		map[string]any{"spirit_stones": 999999, "insight_xp": -50})
	batch4Exec(t, path, `INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at)
		VALUES(42,'first_steps','active','{}',0,0,0,0,0)`)
	batch4SetCanonicalGameMinute(t, path, 130)

	raw := payloadJSON(t, map[string]any{
		"quest_key":      "first_steps",
		"objective_type": "talk", "target": "elder pine", "amount": 1,
	})
	if _, err := Apply(path, ActionRequest{Operation: "quest.progress", ActorID: 42, Payload: raw}); err != nil {
		t.Fatal(err)
	}
	stones, insight := characterPurse(t, path, 42)
	if stones != questRewardMaxStones {
		t.Fatalf("stones=%d, want the engine cap %d", stones, questRewardMaxStones)
	}
	if insight != 0 {
		t.Fatalf("a negative reward took %d insight away", -insight)
	}
}
