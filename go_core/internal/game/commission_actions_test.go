package game

// The v0.22.0 gate from docs/COMMISSIONS_DESIGN.md: commission.accept refuses
// on a held commission and on cooldown and stores the deadline and variant;
// commission.resolve makes failed and abandoned identical in consequence and
// pays only what was locked at accept; the tick expiry produces `failed`
// exactly once. The mutation tests are the point - each of these would still
// pass with a plausible wrong implementation if it only checked the happy path.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupCommissionDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE character_quests(user_id INTEGER NOT NULL,quest_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',progress_json TEXT NOT NULL DEFAULT '{}',accepted_game_minute INTEGER NOT NULL DEFAULT 0,completed_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL,commission INTEGER NOT NULL DEFAULT 0,deadline_game_minute INTEGER,variant_index INTEGER NOT NULL DEFAULT 0,resolved_game_minute INTEGER,PRIMARY KEY(user_id,quest_key));
CREATE TABLE quest_definitions(quest_key TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',source_type TEXT NOT NULL DEFAULT 'forge',source_key TEXT NOT NULL DEFAULT '',objectives_json TEXT NOT NULL DEFAULT '[]',rewards_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'draft',origin TEXT NOT NULL DEFAULT 'gm_prompt',story_prompt TEXT NOT NULL DEFAULT '',model TEXT NOT NULL DEFAULT '',created_by INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,reviewed_by INTEGER,reviewed_at REAL,updated_at REAL NOT NULL,giver_npc TEXT NOT NULL DEFAULT '',realm_band TEXT NOT NULL DEFAULT '',tier INTEGER NOT NULL DEFAULT 1,owner_user_id INTEGER,deadline_game_minutes INTEGER NOT NULL DEFAULT 0,variants_json TEXT NOT NULL DEFAULT '[]',seed_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE npc_relationships(user_id INTEGER NOT NULL,npc_name TEXT NOT NULL,trust INTEGER NOT NULL DEFAULT 0,respect INTEGER NOT NULL DEFAULT 0,fear INTEGER NOT NULL DEFAULT 0,affection INTEGER NOT NULL DEFAULT 0,debt INTEGER NOT NULL DEFAULT 0,grudge INTEGER NOT NULL DEFAULT 0,encounter_count INTEGER NOT NULL DEFAULT 0,last_summary TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL,commission_cooldown_until_game_minute INTEGER NOT NULL DEFAULT 0,commissions_completed INTEGER NOT NULL DEFAULT 0,commissions_failed INTEGER NOT NULL DEFAULT 0,commissions_abandoned INTEGER NOT NULL DEFAULT 0,last_commission_outcome TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,npc_name));
CREATE TABLE event_log(event_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);
CREATE TABLE admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_user_id INTEGER NOT NULL,action TEXT,target TEXT,before_json TEXT,after_json TEXT,reason TEXT,created_at REAL);
CREATE TABLE sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL);
ALTER TABLE quest_definitions ADD COLUMN requires_sect TEXT NOT NULL DEFAULT '';
ALTER TABLE quest_definitions ADD COLUMN reward_visibility TEXT NOT NULL DEFAULT 'shown';
ALTER TABLE quest_definitions ADD COLUMN boast TEXT NOT NULL DEFAULT '';
ALTER TABLE characters ADD COLUMN spirit_stones INTEGER NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN insight_xp INTEGER NOT NULL DEFAULT 0;
`); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()
	return path
}

// A pooled commission with two sets of terms: standard, and rushed - less
// money, one day instead of three. `market_excluded` items are not used here;
// the reward is stones and insight so the payout is easy to read off.
func seedCommission(t *testing.T, path, key string, owner any) {
	t.Helper()
	variants, _ := json.Marshal([]map[string]any{
		{"label": "standard", "rewards": map[string]any{"spirit_stones": 40, "insight_xp": 8}, "deadline_game_minutes": 3 * 1440},
		{"label": "rushed", "rewards": map[string]any{"spirit_stones": 24, "insight_xp": 5}, "deadline_game_minutes": 1440},
	})
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,rewards_json,variants_json,giver_npc,tier,owner_user_id,deadline_game_minutes,created_at,updated_at)
		VALUES(?,?,'approved','{"spirit_stones":40,"insight_xp":8}',?,'Steward Qiao',1,?,?,0,0)`,
		key, "The Replaced Crate", string(variants), owner, 3*1440)
}

func commissionApply(t *testing.T, path, op string, actor int64, seq int, payload map[string]any) (map[string]any, error) {
	t.Helper()
	if rawMinute, ok := payload["game_minute"]; ok {
		batch4SetCanonicalGameMinute(t, path, storage.ParseInt(rawMinute))
		payload = clonePayloadWithoutGameMinute(payload)
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, "", ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   fmt.Sprintf("commission-%s-%d-%d", op, actor, seq),
		Operation:  op,
		ActorID:    actor,
		Payload:    raw,
	})
	if err != nil {
		return nil, err
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("%s result type %T", op, out.Result)
	}
	return result, nil
}

func commissionMustApply(t *testing.T, path, op string, actor int64, seq int, payload map[string]any) map[string]any {
	t.Helper()
	result, err := commissionApply(t, path, op, actor, seq, payload)
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	return result
}

func questRow(t *testing.T, path string, userID int64, key string) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT * FROM character_quests WHERE user_id=? AND quest_key=?`, []any{userID, key})
	if err != nil {
		t.Fatal(err)
	}
	return firstRowMap(res)
}

func relationshipRow(t *testing.T, path string, userID int64, npc string) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT * FROM npc_relationships WHERE user_id=? AND npc_name=?`, []any{userID, npc})
	if err != nil {
		t.Fatal(err)
	}
	return firstRowMap(res)
}

func characterPurse(t *testing.T, path string, userID int64) (int64, int64) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT spirit_stones,insight_xp FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		t.Fatal(err)
	}
	row := firstRowMap(res)
	return i64(row["spirit_stones"]), i64(row["insight_xp"])
}

func TestCommissionAcceptStoresDeadlineAndVariant(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	result := commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "variant_index": 1, "game_minute": 1000})
	if got := fmt.Sprint(result["variant_label"]); got != "rushed" {
		t.Fatalf("variant_label=%s", got)
	}
	row := questRow(t, path, 42, "commission_crate")
	if row == nil {
		t.Fatal("no character_quests row was written")
	}
	if i64(row["commission"]) != 1 {
		t.Fatal("the row is not marked as a commission; the one-at-a-time rule keys on this")
	}
	// The rushed terms are one world-day, not the definition's three: the
	// deadline has to come from the accepted variant.
	if got := i64(row["deadline_game_minute"]); got != 1000+1440 {
		t.Fatalf("deadline_game_minute=%d, want %d", got, 1000+1440)
	}
	if got := i64(row["variant_index"]); got != 1 {
		t.Fatalf("variant_index=%d", got)
	}
	// Nothing is paid at accept - a GM can retire a bad commission before it
	// completes with zero economic effect.
	stones, insight := characterPurse(t, path, 42)
	if stones != 0 || insight != 0 {
		t.Fatalf("accept paid out %d stones / %d insight; rewards move only on completion", stones, insight)
	}
}

func TestCommissionAcceptRefusesASecondOne(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	seedCommission(t, path, "commission_ledger", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1000})
	_, err := commissionApply(t, path, "commission.accept", 42, 2,
		map[string]any{"quest_key": "commission_ledger", "game_minute": 1100})
	if err == nil {
		t.Fatal("a second commission was accepted while one was active")
	}
	if !strings.Contains(err.Error(), "already hold a commission") {
		t.Fatalf("unexpected refusal: %v", err)
	}
	if questRow(t, path, 42, "commission_ledger") != nil {
		t.Fatal("the refused commission still wrote a row")
	}
}

func TestCommissionAcceptRefusesOnCooldown(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	seedCommission(t, path, "commission_ledger", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1000})
	commissionMustApply(t, path, "commission.resolve", 42, 2,
		map[string]any{"quest_key": "commission_crate", "outcome": "abandoned", "game_minute": 1200})
	// The slot is free, but the giver is not dealing with this player yet.
	_, err := commissionApply(t, path, "commission.accept", 42, 3,
		map[string]any{"quest_key": "commission_ledger", "game_minute": 1300})
	if err == nil {
		t.Fatal("a commission was offered during the refusal cooldown")
	}
	if !strings.Contains(err.Error(), "cooldown remaining") {
		t.Fatalf("unexpected refusal: %v", err)
	}
	// ... and it lifts on its own, in game time.
	commissionMustApply(t, path, "commission.accept", 42, 4,
		map[string]any{"quest_key": "commission_ledger", "game_minute": 1200 + commissionCooldownMinutes})
}

// The rule the whole outcome table exists for.
func TestFailedAndAbandonedCostExactlyTheSame(t *testing.T) {
	if commissionOutcomes["failed"] != commissionOutcomes["abandoned"] {
		t.Fatalf("failed %+v and abandoned %+v differ: abandoning would be the cheap reroll",
			commissionOutcomes["failed"], commissionOutcomes["abandoned"])
	}
	paths := map[string]string{}
	for _, outcome := range []string{"failed", "abandoned"} {
		path := setupCommissionDB(t)
		seedCommission(t, path, "commission_crate", nil)
		commissionMustApply(t, path, "commission.accept", 42, 1,
			map[string]any{"quest_key": "commission_crate", "game_minute": 1000})
		commissionMustApply(t, path, "commission.resolve", 42, 2,
			map[string]any{"quest_key": "commission_crate", "outcome": outcome, "game_minute": 1200})
		paths[outcome] = path
	}
	a := relationshipRow(t, paths["failed"], 42, "Steward Qiao")
	b := relationshipRow(t, paths["abandoned"], 42, "Steward Qiao")
	for _, column := range []string{"trust", "respect", "grudge", "commission_cooldown_until_game_minute"} {
		if i64(a[column]) != i64(b[column]) {
			t.Fatalf("%s: failed=%d abandoned=%d", column, i64(a[column]), i64(b[column]))
		}
	}
	// Distinguishable in record, identical in consequence.
	if i64(a["commissions_failed"]) != 1 || i64(b["commissions_abandoned"]) != 1 {
		t.Fatalf("the counters do not record which way it ended: %v / %v", a, b)
	}
	if fmt.Sprint(a["last_commission_outcome"]) == fmt.Sprint(b["last_commission_outcome"]) {
		t.Fatal("last_commission_outcome cannot tell failed from abandoned")
	}
	for _, outcome := range []string{"failed", "abandoned"} {
		stones, insight := characterPurse(t, paths[outcome], 42)
		if stones != 0 || insight != 0 {
			t.Fatalf("%s paid %d stones / %d insight", outcome, stones, insight)
		}
	}
}

func TestCommissionCompletionPaysTheLockedVariantAndNothingElse(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "variant_index": 1, "game_minute": 1000})
	// The GM edits the definition after acceptance. The held row is the
	// contract: the player is paid the rushed terms they took, not the new ones.
	batch4Exec(t, path, `UPDATE quest_definitions SET rewards_json='{"spirit_stones":9000}' WHERE quest_key='commission_crate'`)
	result := commissionMustApply(t, path, "commission.resolve", 42, 2,
		map[string]any{"quest_key": "commission_crate", "outcome": "completed", "game_minute": 1200})
	stones, insight := characterPurse(t, path, 42)
	if stones != 24 || insight != 5 {
		t.Fatalf("paid %d stones / %d insight, want the rushed terms 24 / 5", stones, insight)
	}
	standing, _ := result["standing"].(map[string]any)
	if i64(standing["trust"]) <= 0 || i64(standing["cooldown_until_game_minute"]) != 0 {
		t.Fatalf("completing should raise standing and set no cooldown: %v", standing)
	}
	if got := fmt.Sprint(questRow(t, path, 42, "commission_crate")["status"]); got != "completed" {
		t.Fatalf("status=%s", got)
	}
}

func TestGmRetireClearsTheSlotAtNoCostToThePlayer(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	seedCommission(t, path, "commission_ledger", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1000})
	commissionMustApply(t, path, "commission.resolve", 42, 2,
		map[string]any{"quest_key": "commission_crate", "outcome": "abandoned", "admin_retire": true, "game_minute": 1200})
	row := relationshipRow(t, path, 42, "Steward Qiao")
	if i64(row["trust"]) != 0 || i64(row["grudge"]) != 0 {
		t.Fatalf("a GM retire moved standing: %v", row)
	}
	if i64(row["commission_cooldown_until_game_minute"]) != 0 {
		t.Fatal("a GM retire set a refusal cooldown")
	}
	// The record is still honest about how it ended, and the slot is free.
	if got := fmt.Sprint(questRow(t, path, 42, "commission_crate")["status"]); got != "abandoned" {
		t.Fatalf("status=%s", got)
	}
	commissionMustApply(t, path, "commission.accept", 42, 3,
		map[string]any{"quest_key": "commission_ledger", "game_minute": 1300})
}

func TestExpiryFailsPastDeadlineCommissionsOnceOnTheTick(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "variant_index": 1, "game_minute": 1000})
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// The tick owns the transaction, exactly as advancedMaintenance does.
	expire := func(gm int64) []DueCommission {
		t.Helper()
		expired, err := ExpireDueCommissions(conn, gm)
		if err != nil {
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		return expired
	}
	// Before the deadline nothing happens.
	if expired := expire(1000 + 1439); len(expired) != 0 {
		t.Fatalf("expired %v before the deadline", expired)
	}
	expired := expire(1000 + 1440)
	if len(expired) != 1 || expired[0].QuestKey != "commission_crate" || expired[0].UserID != 42 {
		t.Fatalf("expired=%v", expired)
	}
	// Once, not once per tick: the row has left `active`.
	if again := expire(1000 + 5000); len(again) != 0 {
		t.Fatalf("the same commission expired twice: %v", again)
	}
	row := questRow(t, path, 42, "commission_crate")
	if got := fmt.Sprint(row["status"]); got != "failed" {
		t.Fatalf("status=%s, want failed", got)
	}
	rel := relationshipRow(t, path, 42, "Steward Qiao")
	if i64(rel["commissions_failed"]) != 1 || i64(rel["commission_cooldown_until_game_minute"]) == 0 {
		t.Fatalf("expiry did not apply the failed outcome: %v", rel)
	}
}

func TestADeadlinelessCommissionNeverExpires(t *testing.T) {
	path := setupCommissionDB(t)
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,rewards_json,giver_npc,tier,deadline_game_minutes,created_at,updated_at)
		VALUES('commission_open','A Standing Request','approved','{"insight_xp":5}','Steward Qiao',1,0,0,0)`)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_open", "game_minute": 1000})
	if got := questRow(t, path, 42, "commission_open")["deadline_game_minute"]; got != nil {
		t.Fatalf("deadline_game_minute=%v, want NULL", got)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	expired, err := ExpireDueCommissions(conn, 9_000_000)
	if err != nil {
		t.Fatal(err)
	}
	if len(expired) != 0 {
		t.Fatalf("a commission with no deadline expired: %v", expired)
	}
}

func TestAnInventedCommissionBelongsToOnePlayer(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_personal", 43)
	_, err := commissionApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_personal", "game_minute": 1000})
	if err == nil {
		t.Fatal("another player's personal commission was accepted")
	}
	if !strings.Contains(err.Error(), "unknown commission") {
		t.Fatalf("the refusal leaks that the commission exists: %v", err)
	}
}

func TestADraftCommissionCannotBeAccepted(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	batch4Exec(t, path, `UPDATE quest_definitions SET status='draft' WHERE quest_key='commission_crate'`)
	if _, err := commissionApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1000}); err == nil {
		t.Fatal("an unapproved draft was accepted")
	}
}

// commission.accept replaced DB.accept_quest outright, so it has to accept an
// ordinary quest too: a forged one with a row and no giver, and a static one
// from app/rules/quests.py, which since v0.23.1 is seeded into the same table
// with no giver rather than having no row at all. Neither takes the commission
// slot; the row is what tells the engine the key is real.
func TestAnOrdinaryQuestAcceptsWithoutTakingTheCommissionSlot(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,rewards_json,giver_npc,created_at,updated_at)
		VALUES('forge_ordinary','An Ordinary Errand','approved','{"insight_xp":5}','',0,0)`)
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,status,rewards_json,giver_npc,created_at,updated_at)
		VALUES('first_steps','First Steps Beneath Heaven','approved','{"insight_xp":25}','',0,0)`)
	for seq, key := range []string{"forge_ordinary", "first_steps"} {
		result := commissionMustApply(t, path, "commission.accept", 42, seq+1,
			map[string]any{"quest_key": key, "game_minute": 1000})
		if commission, _ := result["commission"].(bool); commission {
			t.Fatalf("%s was accepted as a commission", key)
		}
		row := questRow(t, path, 42, key)
		if row == nil || i64(row["commission"]) != 0 || row["deadline_game_minute"] != nil {
			t.Fatalf("%s row=%v", key, row)
		}
	}
	// The slot is still free.
	commissionMustApply(t, path, "commission.accept", 42, 3,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1100})
	if i64(questRow(t, path, 42, "commission_crate")["commission"]) != 1 {
		t.Fatal("the commission did not take the slot")
	}
}

func TestTheSameQuestCannotBeAcceptedTwice(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1000})
	commissionMustApply(t, path, "commission.resolve", 42, 2,
		map[string]any{"quest_key": "commission_crate", "outcome": "completed", "game_minute": 1100})
	// Even with the slot free and no cooldown, a finished commission is done.
	if _, err := commissionApply(t, path, "commission.accept", 42, 3,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1200}); err == nil {
		t.Fatal("a completed commission was taken a second time")
	}
}

// Repo rule 6: every Admin Console action writes to admin_audit_log.

func adminApply(t *testing.T, path, op string, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := Apply(path, ActionRequest{Operation: op, ActorID: 99, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func auditRows(t *testing.T, path, action string) []map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT * FROM admin_audit_log WHERE action=? ORDER BY audit_id`, []any{action})
	if err != nil {
		t.Fatal(err)
	}
	return rowsToMaps(res)
}

func TestAdminReviewMovesADefinitionAndAudits(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	batch4Exec(t, path, `UPDATE quest_definitions SET status='draft' WHERE quest_key='commission_crate'`)
	result, err := adminApply(t, path, "admin.commission.review",
		map[string]any{"quest_key": "commission_crate", "status": "approved", "reason": "reads well"})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(result["previous_status"]) != "draft" || fmt.Sprint(result["status"]) != "approved" {
		t.Fatalf("result=%v", result)
	}
	if rows := auditRows(t, path, "admin.commission.review"); len(rows) != 1 {
		t.Fatalf("audit rows=%d", len(rows))
	}
	// It is now acceptable, which is the point of approving it.
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1000})
	// And retiring it hides it from new takers without touching the held row.
	if _, err := adminApply(t, path, "admin.commission.review",
		map[string]any{"quest_key": "commission_crate", "status": "retired"}); err != nil {
		t.Fatal(err)
	}
	if got := fmt.Sprint(questRow(t, path, 42, "commission_crate")["status"]); got != "active" {
		t.Fatalf("retiring the definition changed the held row to %s", got)
	}
}

func TestAdminRetireEndsAHeldCommissionForFreeAndAudits(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_crate", nil)
	commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_crate", "game_minute": 1000})
	batch4SetCanonicalGameMinute(t, path, 1200)
	// quest_key omitted: the GM retires "whatever this player is carrying".
	result, err := adminApply(t, path, "admin.commission.retire",
		map[string]any{"user_id": 42, "reason": "bad commission"})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(result["quest_key"]) != "commission_crate" {
		t.Fatalf("result=%v", result)
	}
	rel := relationshipRow(t, path, 42, "Steward Qiao")
	if i64(rel["trust"]) != 0 || i64(rel["grudge"]) != 0 || i64(rel["commission_cooldown_until_game_minute"]) != 0 {
		t.Fatalf("a GM retire charged the player: %v", rel)
	}
	if rows := auditRows(t, path, "admin.commission.retire"); len(rows) != 1 {
		t.Fatalf("audit rows=%d", len(rows))
	}
	if _, err := adminApply(t, path, "admin.commission.retire", map[string]any{"user_id": 42}); err == nil {
		t.Fatal("retiring twice should say there is nothing to retire")
	}
}

// v0.22.1: sect work is checked at accept, and undisclosed terms are a
// presentation flag that changes nothing about what is locked or paid.

func TestSectWorkIsRefusedToOutsidersAndTakenByDisciples(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_gate_roster", nil)
	batch4Exec(t, path, `UPDATE quest_definitions SET requires_sect='Azure Cloud Sect' WHERE quest_key='commission_gate_roster'`)

	if _, err := commissionApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_gate_roster", "game_minute": 1000}); err == nil {
		t.Fatal("an outsider took sect work")
	}
	// The wrong sect is still an outsider.
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,joined_at) VALUES(42,'Blood River Sect',0)`)
	if _, err := commissionApply(t, path, "commission.accept", 42, 2,
		map[string]any{"quest_key": "commission_gate_roster", "game_minute": 1000}); err == nil {
		t.Fatal("a rival sect's disciple took Azure Cloud work")
	}
	batch4Exec(t, path, `UPDATE sect_membership SET sect_name='Azure Cloud Sect' WHERE user_id=42`)
	commissionMustApply(t, path, "commission.accept", 42, 3,
		map[string]any{"quest_key": "commission_gate_roster", "game_minute": 1000})
	if questRow(t, path, 42, "commission_gate_roster") == nil {
		t.Fatal("a disciple could not take their own sect's work")
	}
}

func TestUndisclosedTermsChangeNothingTheEngineDoes(t *testing.T) {
	path := setupCommissionDB(t)
	seedCommission(t, path, "commission_bowl", nil)
	batch4Exec(t, path, `UPDATE quest_definitions SET reward_visibility='hidden' WHERE quest_key='commission_bowl'`)
	result := commissionMustApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "commission_bowl", "variant_index": 1, "game_minute": 1000})
	// The engine says the terms are undisclosed so the card knows not to print
	// them - and hands over the exact terms anyway, because they are canon.
	if hidden, _ := result["rewards_hidden"].(bool); !hidden {
		t.Fatalf("the accept result does not carry the visibility: %v", result)
	}
	if i64(questRow(t, path, 42, "commission_bowl")["deadline_game_minute"]) != 1000+1440 {
		t.Fatal("hiding the terms changed the deadline")
	}
	commissionMustApply(t, path, "commission.resolve", 42, 2,
		map[string]any{"quest_key": "commission_bowl", "outcome": "completed", "game_minute": 1100})
	stones, insight := characterPurse(t, path, 42)
	if stones != 24 || insight != 5 {
		t.Fatalf("an undisclosed commission paid %d / %d, not the locked rushed terms 24 / 5", stones, insight)
	}
}

// v0.23.1: the engine decides what exists.
//
// `commission.accept` told a legitimate static quest apart from an invented
// key by the same test - neither has a quest_definitions row with a giver - so
// it wrote a character_quests row for any string it was handed. Python
// validated the key first, which protected the Discord path and left the
// authoritative invariant wrong: another caller, or a future one, would create
// orphan quest state with no definition behind it.
func TestAnUnknownQuestKeyIsRefused(t *testing.T) {
	path := setupCommissionDB(t)
	batch4SetCanonicalGameMinute(t, path, 1000)

	_, err := commissionApply(t, path, "commission.accept", 42, 1,
		map[string]any{"quest_key": "totally_fake_foobar"})
	if err == nil {
		t.Fatal("the engine accepted a quest key that does not exist")
	}
	if !strings.Contains(err.Error(), "unknown quest") {
		t.Fatalf("refusal was %q", err)
	}
	if got := countRows(t, path,
		`SELECT COUNT(*) FROM character_quests WHERE user_id=42`); got != 0 {
		t.Fatalf("character_quests rows=%d; orphan quest state was written", got)
	}
}

// The other half: a static quest is seeded with a row and no giver, and must
// still accept as an ordinary quest - no deadline, no commission slot.
func TestASeededStaticQuestStillAcceptsAsAnOrdinaryQuest(t *testing.T) {
	path := setupCommissionDB(t)
	batch4SetCanonicalGameMinute(t, path, 1000)
	// What Database.sync_commission_pool writes for app/rules/quests.py.
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,
		objectives_json,rewards_json,status,origin,created_by,created_at,updated_at,giver_npc)
		VALUES('first_steps','First Steps Beneath Heaven','','system','onboarding','[]','{}','approved','content',0,0,0,'')`)

	result := commissionMustApply(t, path, "commission.accept", 42, 2,
		map[string]any{"quest_key": "first_steps"})
	if result["commission"] != false {
		t.Fatalf("a static quest was accepted as a commission: %v", result)
	}
	row := questRow(t, path, 42, "first_steps")
	if row == nil {
		t.Fatal("no character_quests row was written")
	}
	if storage.ParseInt(row["commission"]) != 0 {
		t.Fatalf("commission flag=%v, want 0", row["commission"])
	}
	if row["deadline_game_minute"] != nil {
		t.Fatalf("an ordinary quest was given a deadline: %v", row["deadline_game_minute"])
	}
}
