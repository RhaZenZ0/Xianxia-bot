package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"xianxia/core/internal/storage"
)

// sqlStmt is one statement in a reversal - most reversible admin actions need
// exactly one, admin.player.grant_currency needs two (currency_wallets, and
// characters.spirit_stones when the currency is low_spirit_stone).
type sqlStmt struct {
	sql  string
	args []any
}

// reverseFunc computes the SQL needed to move an already-applied admin action
// back to a target snapshot. redo=false means "undo": restore to the state
// captured in `before`. redo=true means "redo" (undoing a previous undo):
// restore to the state captured in `after` - i.e. re-apply the original
// action's own result rather than re-running the action from scratch, which
// matters for anything additive like grant_currency (redo must re-add the
// same amount, not merely set a column back to a recorded value).
type reverseFunc func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error)

func pickSnapshot(before, after map[string]any, redo bool) map[string]any {
	if redo {
		return after
	}
	return before
}

func parseTargetUserID(target string) (int64, error) {
	fields := strings.Fields(target)
	if len(fields) == 0 || !strings.HasPrefix(fields[0], "user:") {
		return 0, fmt.Errorf("cannot parse user id from target %q", target)
	}
	return strconv.ParseInt(strings.TrimPrefix(fields[0], "user:"), 10, 64)
}

// parseTargetSuffix finds the "key:value" token (after the leading user:%d
// token) named by key and returns its value verbatim - used for
// bloodline:<id>, gate:<n> and condition:<n> targets, none of which can
// contain whitespace (validated at write time), so splitting on whitespace is
// safe.
func parseTargetSuffix(target, key string) (string, error) {
	prefix := key + ":"
	for _, field := range strings.Fields(target) {
		if strings.HasPrefix(field, prefix) {
			return strings.TrimPrefix(field, prefix), nil
		}
	}
	return "", fmt.Errorf("cannot parse %s from target %q", key, target)
}

func parseNPCTarget(target string) (string, error) {
	name := strings.TrimPrefix(target, "npc:")
	if name == target || name == "" {
		return "", fmt.Errorf("cannot parse npc name from target %q", target)
	}
	return name, nil
}

// reversibleAdminActions lists every admin.* action this endpoint can safely
// undo, and how. This is a deliberately narrow allowlist, not "every admin
// action" - see the exclusions documented next to adminUndoLastAction. Every
// entry here was picked because its handler captures a complete before/after
// snapshot of exactly the row(s) it touches (single-table, or the specific
// dual-table shape grant_currency uses), so restoring that snapshot is a
// faithful, total reversal - not because the action is "safe" in some looser
// sense.
var reversibleAdminActions = map[string]reverseFunc{
	"admin.player.karma": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE characters SET karma_score=? WHERE user_id=?`, []any{i64(snap["karma"]), uid}}}, nil
	},
	"admin.player.set_realm": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE characters SET realm_index=?,phase=? WHERE user_id=?`, []any{i64(snap["realm_index"]), i64(snap["phase"]), uid}}}, nil
	},
	"admin.player.teleport": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE characters SET location=? WHERE user_id=?`, []any{fmt.Sprint(snap["location"]), uid}}}, nil
	},
	"admin.player.grant_currency": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		currency := fmt.Sprint(after["currency"])
		amount := i64(after["amount"])
		snap := pickSnapshot(before, after, redo)
		targetBalance := i64(snap["balance"])
		walletDelta := -amount
		if redo {
			walletDelta = amount
		}
		stmts := []sqlStmt{{`UPDATE currency_wallets SET balance=? WHERE user_id=? AND currency_id=?`, []any{targetBalance, uid, currency}}}
		if currency == "low_spirit_stone" {
			stmts = append(stmts, sqlStmt{`UPDATE characters SET spirit_stones=MAX(0,spirit_stones+?) WHERE user_id=?`, []any{walletDelta, uid}})
		}
		return stmts, nil
	},
	"admin.player.set_realm_perfection": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		var table string
		switch fmt.Sprint(snap["track"]) {
		case "cultivation":
			table = "realm_perfection"
		case "body":
			table = "body_realm_perfection"
		default:
			return nil, fmt.Errorf("cannot undo: unrecognized track %q", fmt.Sprint(snap["track"]))
		}
		return []sqlStmt{{`UPDATE ` + table + ` SET progress=? WHERE user_id=? AND realm_index=?`, []any{i64(snap["progress"]), uid, i64(snap["realm_index"])}}}, nil
	},
	"admin.player.set_spiritual_root": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		if snap["grade"] == nil {
			return []sqlStmt{{`DELETE FROM character_spiritual_roots WHERE user_id=?`, []any{uid}}}, nil
		}
		mutation := fmt.Sprint(snap["mutation"])
		if mutation == "<nil>" {
			mutation = ""
		}
		return []sqlStmt{{`UPDATE character_spiritual_roots SET grade=?,purity=?,mutation=? WHERE user_id=?`, []any{fmt.Sprint(snap["grade"]), i64(snap["purity"]), mutation, uid}}}, nil
	},
	"admin.player.set_bloodline": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		bloodlineID, err := parseTargetSuffix(target, "bloodline")
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE character_bloodlines SET purity=?,evolution_stage=?,progress=? WHERE user_id=? AND bloodline_id=?`,
			[]any{i64(snap["purity"]), i64(snap["evolution_stage"]), i64(snap["progress"]), uid, bloodlineID}}}, nil
	},
	"admin.player.set_physique": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE character_physiques SET evolution_stage=?,progress=?,stability=? WHERE user_id=?`,
			[]any{i64(snap["evolution_stage"]), i64(snap["progress"]), i64(snap["stability"]), uid}}}, nil
	},
	"admin.player.set_tribulation": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		gateRealmIndex, err := parseTargetSuffix(target, "gate")
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE tribulation_state SET preparation=?,attempts=?,cleared=?,last_result=? WHERE user_id=? AND gate_realm_index=?`,
			[]any{i64(snap["preparation"]), i64(snap["attempts"]), i64(snap["cleared"]), fmt.Sprint(snap["last_result"]), uid, gateRealmIndex}}}, nil
	},
	"admin.player.adjust_item": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		itemID := fmt.Sprint(snap["item_id"])
		quantity := i64(snap["quantity"])
		if quantity <= 0 {
			return []sqlStmt{{`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{uid, itemID}}}, nil
		}
		return []sqlStmt{{`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=excluded.quantity`,
			[]any{uid, itemID, quantity}}}, nil
	},
	"admin.player.force_end_scene": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE player_scene_state SET scene_type=?,scene_label=? WHERE user_id=?`,
			[]any{fmt.Sprint(snap["scene_type"]), fmt.Sprint(snap["scene_label"]), uid}}}, nil
	},
	"admin.npc.relocate": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		name, err := parseNPCTarget(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE npc_civilization_state SET current_location=? WHERE npc_name=?`, []any{fmt.Sprint(snap["location"]), name}}}, nil
	},
	// Only the single-condition_id variant of admin.player.clear_condition is
	// reversible - the clear_all variant shares this same action name but
	// stores its before-snapshot as an array (one entry per condition it
	// resolved) and has no "condition:" token in its target, so it's rejected
	// here rather than mis-parsed.
	"admin.player.clear_condition": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		if !strings.Contains(target, "condition:") {
			return nil, errors.New("this action cannot be safely undone")
		}
		conditionIDStr, err := parseTargetSuffix(target, "condition")
		if err != nil {
			return nil, err
		}
		conditionID, err := strconv.ParseInt(conditionIDStr, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("cannot parse condition id from target %q", target)
		}
		snap := pickSnapshot(before, after, redo)
		state := fmt.Sprint(snap["state"])
		if state == "active" {
			return []sqlStmt{{`UPDATE character_conditions SET state='active',severity=?,resolved_game_minute=NULL WHERE condition_id=?`,
				[]any{i64(snap["severity"]), conditionID}}}, nil
		}
		return []sqlStmt{{`UPDATE character_conditions SET state=?,severity=? WHERE condition_id=?`,
			[]any{state, i64(snap["severity"]), conditionID}}}, nil
	},
	"admin.player.set_moderation": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		uid, err := parseTargetUserID(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		reason := fmt.Sprint(snap["moderation_reason"])
		if reason == "<nil>" {
			reason = ""
		}
		// v0.32.0 added the expiry pair and the ban flag to the snapshot; an
		// older row without them reads as 0, which is what it stored.
		return []sqlStmt{{`UPDATE characters SET is_muted=?,muted_until=?,is_frozen=?,frozen_until=?,is_banned=?,moderation_reason=? WHERE user_id=?`,
			[]any{i64(snap["is_muted"]), toFloat(snap["muted_until"]), i64(snap["is_frozen"]), toFloat(snap["frozen_until"]), i64(snap["is_banned"]), reason, uid}}}, nil
	},
}

func decodeSnapshot(raw any) map[string]any {
	var m map[string]any
	if err := json.Unmarshal([]byte(fmt.Sprint(raw)), &m); err != nil {
		return map[string]any{}
	}
	return m
}

// adminUndoLastAction reverses the single most recent admin_audit_log row,
// whatever GM performed it - there is no per-GM filter, since this server has
// exactly one GM account and a "mine only" filter would just add complexity
// with nothing to filter against.
//
// This only covers the narrow allowlist in reversibleAdminActions. Explicitly
// NOT reversible, by design: admin.player.fate (also writes fate_ledger, a
// second table this doesn't reverse), admin.player.set_sect (its ON CONFLICT
// DO NOTHING doesn't record whether it created a row), admin.player.
// set_resource_caps (lowering a cap also clamps a resource down, and that
// clamp isn't captured), admin.player.reset_cooldowns and both admin.bulk.*
// actions (no per-row before-snapshot exists to restore), admin.player.revive
// and admin.player.clear_battle (wide multi-table side effects), admin.world.
// advance_time (before/after record a derived game_minute, not the actual
// world_clock state, and reversing time risks desyncing anything the
// simulation runner already ran against the timeline in between), and
// admin.audit (log-only, nothing to reverse). Calling undo on any of these
// (or on any non-admin action, or when the log is empty) fails with a plain
// "cannot be safely undone" / "no admin action to undo" error rather than
// guessing.
//
// Undo-of-undo is real "redo," not just a second log entry: undoing an
// admin.audit.undo_last row re-fetches the action it undid and re-invokes
// that action's own reversal function against its after_json instead of its
// before_json, landing back on the state the original action produced.
func adminUndoLastAction(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT audit_id,action,target,before_json,after_json FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1`, nil)
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("no admin action to undo")
	}
	auditID := i64(row["audit_id"])
	action := fmt.Sprint(row["action"])
	target := fmt.Sprint(row["target"])
	before := decodeSnapshot(row["before_json"])
	after := decodeSnapshot(row["after_json"])

	var stmts []sqlStmt
	undoneAction := action
	if action == "admin.audit.undo_last" {
		undoneID := i64(before["undone_audit_id"])
		origRes, err := conn.Execute(`SELECT action,target,before_json,after_json FROM admin_audit_log WHERE audit_id=?`, []any{undoneID})
		if err != nil {
			return nil, err
		}
		origRow := firstRowMap(origRes)
		if origRow == nil {
			return nil, errors.New("the undone action's audit row no longer exists")
		}
		origAction := fmt.Sprint(origRow["action"])
		fn, ok := reversibleAdminActions[origAction]
		if !ok {
			return nil, errors.New("this action cannot be safely undone")
		}
		origBefore := decodeSnapshot(origRow["before_json"])
		origAfter := decodeSnapshot(origRow["after_json"])
		origTarget := fmt.Sprint(origRow["target"])
		// Redo: restore the ORIGINAL action's "after" state, not undo_last's
		// own before/after (which only ever record undone_audit_id/undone_action
		// and rows_affected - bookkeeping, not game state).
		stmts, err = fn(origBefore, origAfter, origTarget, true)
		if err != nil {
			return nil, err
		}
		undoneAction = origAction + " (redo)"
	} else {
		fn, ok := reversibleAdminActions[action]
		if !ok {
			return nil, errors.New("this action cannot be safely undone")
		}
		stmts, err = fn(before, after, target, false)
		if err != nil {
			return nil, err
		}
	}

	rowsAffected := int64(0)
	for _, stmt := range stmts {
		r, err := conn.Execute(stmt.sql, stmt.args)
		if err != nil {
			return nil, err
		}
		rowsAffected += r.RowsAffected
	}
	if err := auditAdmin(conn, adminUserID, "admin.audit.undo_last", target,
		map[string]any{"undone_audit_id": auditID, "undone_action": undoneAction},
		map[string]any{"rows_affected": rowsAffected},
		fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"undone_audit_id": auditID, "undone_action": undoneAction, "rows_affected": rowsAffected}, nil
}
