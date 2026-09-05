package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

// checkPlayerModerationTx blocks a muted/frozen player's own authoritative
// actions. Freeze blocks every player-initiated authoritative mutation; mute
// blocks only the free-form roleplay scene action (scene.action) - everything
// else, including check.resolve, combat, and cultivation, stays open. Both
// flags live directly on the characters row (added by the
// player_moderation_flags schema migration) and are set only via
// admin.player.set_moderation, so a character with no row at all (not yet
// created) is never blocked.
//
// This runs in applyAuthoritative right after checkPlayerOldAgeDeathTx, and
// only when that call did not already preempt the operation with a death
// mutation - so a character who is genuinely dying of old age still dies
// regardless of a GM moderation flag; moderation never blocks the engine's
// own preemptive logic, only what the player is trying to do.
//
// Scope, stated plainly: this only covers Go's authoritative-dispatch layer
// (the ~150 player-facing ops routed through applyAuthoritative). It
// structurally cannot intercept Python's direct /v1/db/session or
// /v1/db/batch raw-SQL writes, or writes made by the simulation runner -
// those act ON the player (other systems, or a GM, changing the player's
// state) rather than AS the player, which is what mute/freeze is meant to
// stop. This is a moderation nudge for a small table, not an anti-cheat
// mechanism, and should never be assumed to be a stronger guarantee than
// that.
func checkPlayerModerationTx(conn *storage.Conn, userID int64, operation string) error {
	res, err := conn.Execute(`SELECT is_frozen,is_muted,moderation_reason FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil
	}
	reason := strings.TrimSpace(fmt.Sprint(row["moderation_reason"]))
	suffix := ""
	if reason != "" && reason != "<nil>" {
		suffix = ": " + reason
	}
	if i64(row["is_frozen"]) != 0 {
		return fmt.Errorf("your character is frozen by GM order and cannot take actions%s", suffix)
	}
	if operation == "scene.action" && i64(row["is_muted"]) != 0 {
		return fmt.Errorf("your character is muted by GM order and cannot take roleplay scene actions%s", suffix)
	}
	return nil
}

// adminSetModeration lets a GM mute and/or freeze a player, or just update
// the reason shown to them. Only the fields the caller actually supplies are
// changed - the other flag(s) stay at their current value.
func adminSetModeration(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	mutedRaw, hasMuted := p["muted"]
	frozenRaw, hasFrozen := p["frozen"]
	reasonRaw, hasReason := p["moderation_reason"]
	if !hasMuted && !hasFrozen && !hasReason {
		return nil, errors.New("muted, frozen, or moderation_reason is required")
	}
	moderationReason := strings.TrimSpace(fmt.Sprint(reasonRaw))
	if moderationReason == "<nil>" {
		moderationReason = ""
	}
	if len(moderationReason) > 300 {
		return nil, errors.New("moderation_reason is limited to 300 characters")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name,is_muted,is_frozen,moderation_reason FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	newMuted := storage.ParseInt(row["is_muted"])
	if hasMuted {
		if v, _ := mutedRaw.(bool); v {
			newMuted = 1
		} else {
			newMuted = 0
		}
	}
	newFrozen := storage.ParseInt(row["is_frozen"])
	if hasFrozen {
		if v, _ := frozenRaw.(bool); v {
			newFrozen = 1
		} else {
			newFrozen = 0
		}
	}
	newReason := fmt.Sprint(row["moderation_reason"])
	if hasReason {
		newReason = moderationReason
	}
	before := map[string]any{
		"is_muted": storage.ParseInt(row["is_muted"]), "is_frozen": storage.ParseInt(row["is_frozen"]), "moderation_reason": row["moderation_reason"],
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET is_muted=?,is_frozen=?,moderation_reason=?,updated_at=? WHERE user_id=?`,
		[]any{newMuted, newFrozen, newReason, now, uid}); err != nil {
		return nil, err
	}
	after := map[string]any{"is_muted": newMuted, "is_frozen": newFrozen, "moderation_reason": newReason}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_moderation", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "is_muted": newMuted, "is_frozen": newFrozen, "moderation_reason": newReason}, nil
}
