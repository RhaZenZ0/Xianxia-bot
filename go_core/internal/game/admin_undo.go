package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

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
//
// "Total" means of what the action wrote. A snapshot is taken whole whether or
// not the action touched every column in it, so a reversal that restores the
// whole snapshot writes over whatever play has changed since in a column the
// lever never wrote - and a reversal that deletes the row a lever made deletes
// whatever play has put on it. An entry restores the columns its action wrote
// (the audit row says which: a key `after` carries, a value that differs
// between the two sides), as an UPDATE onto the same row, and removes a row the
// lever made only through deleteIfIdle; admin_undo_rows.go states the rule and
// TestAReversalDeletesARowOnlyThroughDeleteIfIdle holds it.
var reversibleAdminActions = map[string]reverseFunc{
	// The Player Editor's three progress levers (v1.23.0); admin_player_progress.go.
	"admin.player.set_profession":        reverseSetProfession,
	"admin.player.set_law":               reverseSetLaw,
	"admin.player.set_sect_contribution": reverseSetSectContribution,
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
		stmts := []sqlStmt{{`UPDATE characters SET realm_index=?,phase=? WHERE user_id=?`, []any{i64(snap["realm_index"]), i64(snap["phase"]), uid}}}
		// The body ladder rides the snapshot only when the lever set it
		// (v1.0.0-rc.37); an older row restores the qi pair alone.
		if _, ok := snap["body_realm_index"]; ok {
			stmts = append(stmts, sqlStmt{`UPDATE characters SET body_realm_index=?,body_phase=? WHERE user_id=?`, []any{i64(snap["body_realm_index"]), i64(snap["body_phase"]), uid}})
		}
		return stmts, nil
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
	// The reversal reads the audit row for what the lever wrote and restores that
	// and nothing else; admin_undo_rows.go.
	"admin.player.set_realm_perfection": reverseSetRealmPerfection,
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
		// A redo is the forward action's own upsert, so it lands even on a row
		// its undo deleted; an undo only ever moves a row that is there.
		if redo {
			return []sqlStmt{{`INSERT INTO character_spiritual_roots(user_id,grade,purity,mutation,updated_at) VALUES(?,?,?,?,?)
				ON CONFLICT(user_id) DO UPDATE SET grade=excluded.grade,purity=excluded.purity,mutation=excluded.mutation,updated_at=excluded.updated_at`,
				[]any{uid, fmt.Sprint(snap["grade"]), i64(snap["purity"]), mutation, float64(time.Now().UnixNano()) / 1e9}}}, nil
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
		// The identity too, since v1.0.11 gave the lever one to write - but only
		// when this call wrote it. The snapshot carries the identity whether or
		// not the call changed it, so `before` alone cannot say: `after` carries
		// `physique_id` only when the GM gave one. Writing it back otherwise
		// would put the old life's identity over whatever a samsara has made
		// since, for a lever that only ever edited three numbers.
		//
		// A snapshot from before that release carries no `physique_id` at all,
		// so the three-number statement is kept for it rather than writing an
		// empty id over a real physique: an old audit row must stay undoable on
		// exactly the terms it was written.
		_, wroteIdentity := after["physique_id"]
		if id := strings.TrimSpace(fmt.Sprint(snap["physique_id"])); wroteIdentity && id != "" && id != "<nil>" {
			return []sqlStmt{{`UPDATE character_physiques SET physique_id=?,name=?,evolution_stage=?,progress=?,stability=? WHERE user_id=?`,
				[]any{id, fmt.Sprint(snap["name"]), i64(snap["evolution_stage"]), i64(snap["progress"]), i64(snap["stability"]), uid}}}, nil
		}
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
		// Only the columns the forward action wrote, which `after` names in both
		// directions. A clear leaves `attempts` alone, so its `after` carries
		// none and `i64(nil)` would be a 0 written over the count of real
		// attempts - a redo of a clear zeroed them.
		sets, args := []string{}, []any{}
		for _, col := range []string{"preparation", "attempts", "cleared", "last_result"} {
			if _, wrote := after[col]; !wrote {
				continue
			}
			sets = append(sets, col+"=?")
			if col == "last_result" {
				args = append(args, fmt.Sprint(snap[col]))
			} else {
				args = append(args, i64(snap[col]))
			}
		}
		if len(sets) == 0 {
			return nil, errors.New("this action cannot be safely undone")
		}
		args = append(args, uid, gateRealmIndex)
		return []sqlStmt{{`UPDATE tribulation_state SET ` + strings.Join(sets, ",") + ` WHERE user_id=? AND gate_realm_index=?`, args}}, nil
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
	// A disappearance the GM staged, or a return (v1.0.0-rc.38): the row
	// goes back to the status and minute the snapshot holds. The history
	// row stays; it is the record that something was staged.
	"admin.npc.set_missing": func(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
		name, err := parseNPCTarget(target)
		if err != nil {
			return nil, err
		}
		snap := pickSnapshot(before, after, redo)
		return []sqlStmt{{`UPDATE npc_civilization_state SET status=?,missing_since_game_minute=? WHERE npc_name=?`,
			[]any{fmt.Sprint(snap["status"]), storage.ParseInt(snap["missing_since_game_minute"]), name}}}, nil
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
		// Walk the chain of undos back to the action that was first undone.
		// An undo_last's own before/after record only undone_audit_id and
		// rows_affected - bookkeeping, not game state - so what a GM wants
		// undone is the original, in the direction the chain's length says:
		// one undo is undone by a redo, two by an undo again. Until v1.2.3
		// only a chain of one was followed, so undo, redo, undo refused.
		depth := 0
		origRow := row
		for fmt.Sprint(origRow["action"]) == "admin.audit.undo_last" {
			undoneID := i64(decodeSnapshot(origRow["before_json"])["undone_audit_id"])
			origRes, err := conn.Execute(`SELECT action,target,before_json,after_json FROM admin_audit_log WHERE audit_id=?`, []any{undoneID})
			if err != nil {
				return nil, err
			}
			origRow = firstRowMap(origRes)
			if origRow == nil {
				return nil, errors.New("the undone action's audit row no longer exists")
			}
			depth++
		}
		origAction := fmt.Sprint(origRow["action"])
		fn, ok := reversibleAdminActions[origAction]
		if !ok {
			return nil, errors.New("this action cannot be safely undone")
		}
		origBefore := decodeSnapshot(origRow["before_json"])
		origAfter := decodeSnapshot(origRow["after_json"])
		origTarget := fmt.Sprint(origRow["target"])
		redo := depth%2 == 1
		stmts, err = fn(origBefore, origAfter, origTarget, redo)
		if err != nil {
			return nil, err
		}
		undoneAction = origAction + map[bool]string{true: " (redo)", false: " (undo again)"}[redo]
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
