package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// undoLast is a small convenience wrapper: admin.audit.undo_last takes no
// required fields beyond an optional reason.
func undoLast(t *testing.T, path string) any {
	t.Helper()
	return applyAdmin(t, path, "admin.audit.undo_last", map[string]any{"reason": "GM undo"})
}

func lastAuditAction(t *testing.T, path string) string {
	t.Helper()
	return fmt.Sprint(scalar(t, path, "SELECT action FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1"))
}

func TestAdminUndoLastKarmaRestoresPriorScore(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.karma", map[string]any{"user_id": 42, "delta": 10, "reason": "reward"})
	if got := storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42")); got != 15 {
		t.Fatalf("karma_score=%d, want 15 (5 seed + 10)", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42")); got != 5 {
		t.Fatalf("karma_score=%d, want restored to seed value 5", got)
	}
	if got := lastAuditAction(t, path); got != "admin.audit.undo_last" {
		t.Fatalf("last audit action=%q, want admin.audit.undo_last", got)
	}
}

func TestAdminUndoLastSetRealmRestoresPriorRealmAndPhase(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_realm", map[string]any{"user_id": 42, "realm_index": 9, "phase": 4, "reason": "correction"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT realm_index FROM characters WHERE user_id=42")); got != 2 {
		t.Fatalf("realm_index=%d, want restored to seed value 2", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT phase FROM characters WHERE user_id=42")); got != 3 {
		t.Fatalf("phase=%d, want restored to seed value 3", got)
	}
}

func TestAdminUndoLastTeleportRestoresPriorLocation(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.teleport", map[string]any{"user_id": 42, "location": "Greenriver Town", "reason": "story move"})
	if got := fmt.Sprint(scalar(t, path, "SELECT location FROM characters WHERE user_id=42")); got != "Greenriver Town" {
		t.Fatalf("location=%q, want Greenriver Town", got)
	}
	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT location FROM characters WHERE user_id=42")); got != "Old Place" {
		t.Fatalf("location=%q, want restored to seed value Old Place", got)
	}
}

func TestAdminUndoLastGrantCurrencyRevertsBothWalletAndSpiritStonesMirror(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_stone", "amount": 500, "reason": "reward"})
	if got := storage.ParseInt(scalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got != 500 {
		t.Fatalf("wallet balance=%d, want 500", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42")); got != 510 {
		t.Fatalf("spirit_stones=%d, want 510 (seed 10 + 500)", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got != 0 {
		t.Fatalf("wallet balance=%d, want restored to 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42")); got != 10 {
		t.Fatalf("spirit_stones=%d, want restored to seed value 10", got)
	}

	// A non-low_spirit_stone currency must NOT touch characters.spirit_stones
	// on undo either (mirrors the forward op's own scoping).
	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_crystal", "amount": 20, "reason": "reward"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_crystal'")); got != 0 {
		t.Fatalf("low_spirit_crystal balance=%d, want restored to 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42")); got != 10 {
		t.Fatalf("spirit_stones=%d, want untouched at 10", got)
	}
}

func TestAdminUndoLastSetRealmPerfectionRestoresPriorProgress(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 3, "progress": 80, "reason": "story"})
	applyAdmin(t, path, "admin.player.set_realm_perfection", map[string]any{"user_id": 42, "track": "cultivation", "realm_index": 3, "progress": 20, "reason": "oops"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM realm_perfection WHERE user_id=42 AND realm_index=3")); got != 80 {
		t.Fatalf("progress=%d, want restored to 80", got)
	}
}

func TestAdminUndoLastSetSpiritualRootDeletesRowThatDidNotExistBefore(t *testing.T) {
	path := setupAdminDB(t)
	if got := scalar(t, path, "SELECT grade FROM character_spiritual_roots WHERE user_id=42"); got != nil {
		t.Fatalf("expected no spiritual root row before the test, got %v", got)
	}
	applyAdmin(t, path, "admin.player.set_spiritual_root", map[string]any{"user_id": 42, "grade": "Heaven", "purity": 80, "mutation": "", "reason": "reward"})
	undoLast(t, path)
	if got := scalar(t, path, "SELECT grade FROM character_spiritual_roots WHERE user_id=42"); got != nil {
		t.Fatalf("expected the row to be deleted since it did not exist before the forward action, got %v", got)
	}
}

func TestAdminUndoLastSetSpiritualRootRestoresPriorRowWhenOneAlreadyExisted(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_spiritual_root", map[string]any{"user_id": 42, "grade": "Common", "purity": 50, "mutation": "", "reason": "seed"})
	applyAdmin(t, path, "admin.player.set_spiritual_root", map[string]any{"user_id": 42, "grade": "Heaven", "purity": 90, "mutation": "Phoenix Blood", "reason": "reward"})
	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT grade FROM character_spiritual_roots WHERE user_id=42")); got != "Common" {
		t.Fatalf("grade=%q, want restored to Common", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT purity FROM character_spiritual_roots WHERE user_id=42")); got != 50 {
		t.Fatalf("purity=%d, want restored to 50", got)
	}
}

func TestAdminUndoLastSetBloodlineRestoresPriorStats(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO character_bloodlines(user_id,bloodline_id,name,affinity,state,purity,evolution_stage,progress,updated_at) VALUES(42,'azure_dragon_blood','Azure Dragon Blood','Water','dormant',30,1,10,0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_bloodline", map[string]any{"user_id": 42, "bloodline_id": "azure_dragon_blood", "purity": 90, "evolution_stage": 3, "progress": 70, "reason": "reward"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT purity FROM character_bloodlines WHERE user_id=42 AND bloodline_id='azure_dragon_blood'")); got != 30 {
		t.Fatalf("purity=%d, want restored to 30", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT evolution_stage FROM character_bloodlines WHERE user_id=42 AND bloodline_id='azure_dragon_blood'")); got != 1 {
		t.Fatalf("evolution_stage=%d, want restored to 1", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM character_bloodlines WHERE user_id=42 AND bloodline_id='azure_dragon_blood'")); got != 10 {
		t.Fatalf("progress=%d, want restored to 10", got)
	}
}

func TestAdminUndoLastSetPhysiqueRestoresPriorStats(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO character_physiques(user_id,physique_id,name,state,evolution_stage,progress,stability,updated_at) VALUES(42,'ordinary_mortal_body','Ordinary Mortal Body','ordinary',0,10,100,0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.set_physique", map[string]any{"user_id": 42, "evolution_stage": 2, "progress": 60, "stability": 40, "reason": "reward"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT evolution_stage FROM character_physiques WHERE user_id=42")); got != 0 {
		t.Fatalf("evolution_stage=%d, want restored to 0", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM character_physiques WHERE user_id=42")); got != 10 {
		t.Fatalf("progress=%d, want restored to 10", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT stability FROM character_physiques WHERE user_id=42")); got != 100 {
		t.Fatalf("stability=%d, want restored to 100", got)
	}
}

func TestAdminUndoLastSetTribulationRestoresPriorGateState(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_tribulation", map[string]any{"user_id": 42, "gate_realm_index": 7, "mode": "clear", "reason": "unstick"})
	applyAdmin(t, path, "admin.player.set_tribulation", map[string]any{"user_id": 42, "gate_realm_index": 7, "mode": "reset", "reason": "oops"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT cleared FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != 1 {
		t.Fatalf("cleared=%d, want restored to 1", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT last_result FROM tribulation_state WHERE user_id=42 AND gate_realm_index=7")); got != "cleared" {
		t.Fatalf("last_result=%q, want restored to cleared", got)
	}
}

func TestAdminUndoLastAdjustItemRestoresPriorQuantityAndDeletesWhenZero(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": 5, "reason": "grant"})
	undoLast(t, path)
	if got := scalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'"); got != nil {
		t.Fatalf("expected the inventory row to be gone (it did not exist before), got %v", got)
	}

	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": 5, "reason": "grant"})
	applyAdmin(t, path, "admin.player.adjust_item", map[string]any{"user_id": 42, "item_id": "spirit_herb", "quantity": 3, "reason": "more"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT quantity FROM inventory WHERE user_id=42 AND item_id='spirit_herb'")); got != 5 {
		t.Fatalf("quantity=%d, want restored to 5", got)
	}
}

func TestAdminUndoLastForceEndSceneRestoresPriorSceneTypeAndLabel(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,updated_at) VALUES(42,'Old Place','sect_trial','trial-1','Entrance Trial',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.player.force_end_scene", map[string]any{"user_id": 42, "reason": "stuck"})
	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT scene_type FROM player_scene_state WHERE user_id=42")); got != "sect_trial" {
		t.Fatalf("scene_type=%q, want restored to sect_trial", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT scene_label FROM player_scene_state WHERE user_id=42")); got != "Entrance Trial" {
		t.Fatalf("scene_label=%q, want restored to Entrance Trial", got)
	}
}

func TestAdminUndoLastNpcRelocateRestoresPriorLocation(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = conn.Execute(`INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,updated_at) VALUES('Elder Wu','Sect Hall','Sect Hall','Mortal World',0)`, nil); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	if err = conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	applyAdmin(t, path, "admin.npc.relocate", map[string]any{"npc_name": "Elder Wu", "location": "Market Square", "reason": "story move"})
	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT current_location FROM npc_civilization_state WHERE npc_name='Elder Wu'")); got != "Sect Hall" {
		t.Fatalf("current_location=%q, want restored to Sect Hall", got)
	}
}

func TestAdminUndoLastClearConditionRestoresActiveStateAndSeverity(t *testing.T) {
	path := setupAdminDB(t)
	seedCondition(t, path, 1, "flesh_wound", "Flesh Wound", 2)
	applyAdmin(t, path, "admin.player.clear_condition", map[string]any{"user_id": 42, "condition_id": 1, "reason": "story resolution"})
	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT state FROM character_conditions WHERE condition_id=1")); got != "active" {
		t.Fatalf("state=%q, want restored to active", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT severity FROM character_conditions WHERE condition_id=1")); got != 2 {
		t.Fatalf("severity=%d, want restored to 2", got)
	}
	if got := scalar(t, path, "SELECT resolved_game_minute FROM character_conditions WHERE condition_id=1"); got != nil {
		t.Fatalf("resolved_game_minute=%v, want cleared back to NULL", got)
	}
}

func TestAdminUndoLastClearConditionClearAllVariantCannotBeUndone(t *testing.T) {
	path := setupAdminDB(t)
	seedCondition(t, path, 1, "flesh_wound", "Flesh Wound", 2)
	seedCondition(t, path, 2, "poisoned", "Poisoned", 3)
	applyAdmin(t, path, "admin.player.clear_condition", map[string]any{"user_id": 42, "clear_all": true, "reason": "clean slate"})
	raw, _ := json.Marshal(map[string]any{"reason": "GM undo"})
	_, err := Apply(path, ActionRequest{Operation: "admin.audit.undo_last", Payload: raw})
	if err == nil {
		t.Fatal("expected the clear_all variant to be rejected as not safely undoable")
	}
	if !strings.Contains(err.Error(), "cannot be safely undone") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestAdminUndoLastSetModerationRestoresPriorFlags(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "muted": true, "frozen": false, "moderation_reason": "spamming", "reason": "GM"})
	applyAdmin(t, path, "admin.player.set_moderation", map[string]any{"user_id": 42, "frozen": true, "reason": "escalated"})
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT is_muted FROM characters WHERE user_id=42")); got != 1 {
		t.Fatalf("is_muted=%d, want restored to 1", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT is_frozen FROM characters WHERE user_id=42")); got != 0 {
		t.Fatalf("is_frozen=%d, want restored to 0", got)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT moderation_reason FROM characters WHERE user_id=42")); got != "spamming" {
		t.Fatalf("moderation_reason=%q, want restored to spamming", got)
	}
}

func TestAdminUndoLastRejectsEmptyLogAndUnsupportedActions(t *testing.T) {
	path := setupAdminDB(t)
	raw, _ := json.Marshal(map[string]any{"reason": "GM undo"})
	_, err := Apply(path, ActionRequest{Operation: "admin.audit.undo_last", Payload: raw})
	if err == nil {
		t.Fatal("expected an error when there is no admin action to undo")
	}
	if !strings.Contains(err.Error(), "no admin action to undo") {
		t.Fatalf("unexpected error for an empty audit log: %v", err)
	}

	// Every explicitly excluded action: fate (second table), set_sect (can't
	// tell if it created a row), set_resource_caps (clamp side effect not
	// captured), reset_cooldowns/bulk ops (no per-row snapshot), revive/
	// clear_battle (wide side effects), advance_time (derived game_minute,
	// not real world_clock state), and admin.audit itself (log-only).
	for _, tc := range []struct {
		op      string
		payload map[string]any
	}{
		{"admin.player.fate", map[string]any{"user_id": 42, "delta": 1}},
		{"admin.player.set_sect", map[string]any{"user_id": 42, "sect_name": "Azure Cloud Sect", "rank_name": "Disciple", "rank_level": 0}},
		{"admin.player.set_resource_caps", map[string]any{"user_id": 42, "vitality_max": 200}},
		{"admin.player.reset_cooldowns", map[string]any{"user_id": 42}},
		{"admin.bulk.grant_currency", map[string]any{"currency_id": "low_spirit_stone", "amount": 10}},
		{"admin.bulk.reset_cooldowns", map[string]any{}},
		{"admin.player.revive", map[string]any{"user_id": 42}},
		{"admin.world.advance_time", map[string]any{"minutes": 60}},
		{"admin.audit", map[string]any{"action": "dashboard.note", "target": "x"}},
	} {
		applyAdmin(t, path, tc.op, tc.payload)
		raw, _ := json.Marshal(map[string]any{"reason": "GM undo"})
		_, err := Apply(path, ActionRequest{Operation: "admin.audit.undo_last", Payload: raw})
		if err == nil {
			t.Fatalf("%s: expected undo_last to reject this action as not safely undoable", tc.op)
		}
		if !strings.Contains(err.Error(), "cannot be safely undone") {
			t.Fatalf("%s: unexpected error: %v", tc.op, err)
		}
	}
}

func TestAdminUndoOfUndoRedoesTheOriginalKarmaChange(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.karma", map[string]any{"user_id": 42, "delta": 10, "reason": "reward"})
	if got := storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42")); got != 15 {
		t.Fatalf("karma_score=%d, want 15 after the original grant", got)
	}
	undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42")); got != 5 {
		t.Fatalf("karma_score=%d, want 5 after undo", got)
	}
	// Undo the undo: this must land back on 15 (the state the ORIGINAL action
	// produced), not merely log a second undo of nothing.
	result := undoLast(t, path)
	if got := storage.ParseInt(scalar(t, path, "SELECT karma_score FROM characters WHERE user_id=42")); got != 15 {
		t.Fatalf("karma_score=%d, want 15 after undoing the undo (redo)", got)
	}
	m, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := fmt.Sprint(m["undone_action"]); got != "admin.player.karma (redo)" {
		t.Fatalf("undone_action=%q, want %q", got, "admin.player.karma (redo)")
	}
}

func TestAdminUndoLastRecordsUndoneAuditIDAndAction(t *testing.T) {
	path := setupAdminDB(t)
	applyAdmin(t, path, "admin.player.karma", map[string]any{"user_id": 42, "delta": 10, "reason": "reward"})
	karmaAuditID := storage.ParseInt(scalar(t, path, "SELECT audit_id FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1"))
	result := undoLast(t, path)
	m, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("unexpected result type %T", result)
	}
	if got := storage.ParseInt(m["undone_audit_id"]); got != karmaAuditID {
		t.Fatalf("undone_audit_id=%d, want %d", got, karmaAuditID)
	}
	if got := fmt.Sprint(m["undone_action"]); got != "admin.player.karma" {
		t.Fatalf("undone_action=%q, want admin.player.karma", got)
	}
	beforeJSON := fmt.Sprint(scalar(t, path, "SELECT before_json FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1"))
	if !strings.Contains(beforeJSON, fmt.Sprintf("%d", karmaAuditID)) || !strings.Contains(beforeJSON, "admin.player.karma") {
		t.Fatalf("undo_last's own before_json=%s did not record the undone audit id/action", beforeJSON)
	}
}
