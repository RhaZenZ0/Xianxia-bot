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
	res, err := conn.Execute(`SELECT is_frozen,is_muted,is_banned,muted_until,frozen_until,moderation_reason FROM characters WHERE user_id=?`, []any{userID})
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
	now := float64(time.Now().UnixNano()) / 1e9
	if i64(row["is_banned"]) != 0 {
		return fmt.Errorf("your character is banned by GM order%s", suffix)
	}
	if moderationActive(row["is_frozen"], row["frozen_until"], now) {
		return fmt.Errorf("your character is frozen by GM order and cannot take actions%s", suffix)
	}
	if operation == "scene.action" && moderationActive(row["is_muted"], row["muted_until"], now) {
		return fmt.Errorf("your character is muted by GM order and cannot take roleplay scene actions%s", suffix)
	}
	return nil
}

// moderationActive is the one reading of a flag-plus-expiry pair. A flag with
// an expiry of 0 holds until a GM lifts it; a flag whose expiry has passed is
// already over, whether or not the simulation tick has come round to clear
// the row (v0.32.0) - so a mute for an hour lasts an hour, not "an hour plus
// however long until the next maintenance pass".
func moderationActive(flag any, until any, now float64) bool {
	if i64(flag) == 0 {
		return false
	}
	expiry := toFloat(until)
	return expiry <= 0 || expiry > now
}

// ExpireDueModerations clears every mute and freeze whose expiry has passed.
// The simulation tick calls it (advanced_maintenance.go) so a lapsed
// moderation is visibly over in the dashboard and in /admin player inspect,
// not only inside checkPlayerModerationTx. A ban has no expiry and is never
// touched here. It returns the ids it cleared so the tick can count them.
func ExpireDueModerations(conn *storage.Conn, now float64) ([]int64, error) {
	probe, err := conn.Execute(`SELECT 1 AS ok FROM pragma_table_info('characters') WHERE name='muted_until' LIMIT 1`, nil)
	if err != nil {
		return nil, err
	}
	if firstRowMap(probe) == nil {
		return nil, nil
	}
	res, err := conn.Execute(`SELECT user_id,is_muted,is_frozen,is_banned,muted_until,frozen_until FROM characters
		WHERE (is_muted=1 AND muted_until>0 AND muted_until<=?) OR (is_frozen=1 AND frozen_until>0 AND frozen_until<=?) ORDER BY user_id`, []any{now, now})
	if err != nil {
		return nil, err
	}
	cleared := []int64{}
	for _, row := range rowsToMaps(res) {
		uid := i64(row["user_id"])
		muted := i64(row["is_muted"])
		frozen := i64(row["is_frozen"])
		mutedUntil := toFloat(row["muted_until"])
		frozenUntil := toFloat(row["frozen_until"])
		if muted == 1 && mutedUntil > 0 && mutedUntil <= now {
			muted, mutedUntil = 0, 0
		}
		if frozen == 1 && frozenUntil > 0 && frozenUntil <= now {
			frozen, frozenUntil = 0, 0
		}
		// The reason shown to the player belongs to the moderation that just
		// lapsed; it only survives while something is still in force.
		clearReason := muted == 0 && frozen == 0 && i64(row["is_banned"]) == 0
		if _, err := conn.Execute(`UPDATE characters SET is_muted=?,muted_until=?,is_frozen=?,frozen_until=?,moderation_reason=CASE WHEN ? THEN '' ELSE moderation_reason END,updated_at=? WHERE user_id=?`,
			[]any{muted, mutedUntil, frozen, frozenUntil, clearReason, now, uid}); err != nil {
			return nil, err
		}
		cleared = append(cleared, uid)
	}
	return cleared, nil
}

// adminSetModeration lets a GM mute, freeze or ban a player, or just update
// the reason shown to them. Only the fields the caller actually supplies are
// changed - the other flag(s) stay at their current value.
//
// A mute or freeze may carry a duration (muted_seconds / frozen_seconds,
// v0.32.0): the flag then expires on its own, lazily in
// checkPlayerModerationTx and durably in the simulation tick. No duration
// (or 0) means "until a GM lifts it". A ban never expires; it blocks every
// authoritative action the player takes until unbanned, and does not touch
// the mute/freeze flags, so lifting it restores whatever they were.
//
// The audit row carries the full before/after of all six columns - actor,
// target, reason and expiry - so admin.audit.undo_last reverses a moderation
// exactly, expiry included.
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
	bannedRaw, hasBanned := p["banned"]
	reasonRaw, hasReason := p["moderation_reason"]
	if !hasMuted && !hasFrozen && !hasBanned && !hasReason {
		return nil, errors.New("muted, frozen, banned, or moderation_reason is required")
	}
	moderationReason := strings.TrimSpace(fmt.Sprint(reasonRaw))
	if moderationReason == "<nil>" {
		moderationReason = ""
	}
	if len(moderationReason) > 300 {
		return nil, errors.New("moderation_reason is limited to 300 characters")
	}
	mutedSeconds, err := optionalDurationSeconds(p, "muted_seconds")
	if err != nil {
		return nil, err
	}
	frozenSeconds, err := optionalDurationSeconds(p, "frozen_seconds")
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
	res, err := conn.Execute(`SELECT name,is_muted,is_frozen,is_banned,muted_until,frozen_until,moderation_reason FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	newMuted := storage.ParseInt(row["is_muted"])
	newMutedUntil := toFloat(row["muted_until"])
	if hasMuted {
		if v, _ := mutedRaw.(bool); v {
			newMuted = 1
			newMutedUntil = 0
			if mutedSeconds > 0 {
				newMutedUntil = now + mutedSeconds
			}
		} else {
			newMuted, newMutedUntil = 0, 0
		}
	}
	newFrozen := storage.ParseInt(row["is_frozen"])
	newFrozenUntil := toFloat(row["frozen_until"])
	if hasFrozen {
		if v, _ := frozenRaw.(bool); v {
			newFrozen = 1
			newFrozenUntil = 0
			if frozenSeconds > 0 {
				newFrozenUntil = now + frozenSeconds
			}
		} else {
			newFrozen, newFrozenUntil = 0, 0
		}
	}
	newBanned := storage.ParseInt(row["is_banned"])
	if hasBanned {
		if v, _ := bannedRaw.(bool); v {
			newBanned = 1
		} else {
			newBanned = 0
		}
	}
	newReason := fmt.Sprint(row["moderation_reason"])
	if newReason == "<nil>" {
		newReason = ""
	}
	if hasReason {
		newReason = moderationReason
	}
	before := moderationSnapshotResult(storage.ParseInt(row["is_muted"]), toFloat(row["muted_until"]),
		storage.ParseInt(row["is_frozen"]), toFloat(row["frozen_until"]), storage.ParseInt(row["is_banned"]), row["moderation_reason"])
	if _, err = conn.Execute(`UPDATE characters SET is_muted=?,muted_until=?,is_frozen=?,frozen_until=?,is_banned=?,moderation_reason=?,updated_at=? WHERE user_id=?`,
		[]any{newMuted, newMutedUntil, newFrozen, newFrozenUntil, newBanned, newReason, now, uid}); err != nil {
		return nil, err
	}
	after := moderationSnapshotResult(newMuted, newMutedUntil, newFrozen, newFrozenUntil, newBanned, newReason)
	if err := auditAdmin(conn, adminUserID, "admin.player.set_moderation", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	result := map[string]any{"user_id": uid, "name": row["name"]}
	for k, v := range after {
		result[k] = v
	}
	return result, nil
}

// moderationSnapshot is the shape of both audit halves and of the action's
// reply: every moderation column, so undo restores the lot.
func moderationSnapshotResult(muted int64, mutedUntil float64, frozen int64, frozenUntil float64, banned int64, reason any) map[string]any {
	r := fmt.Sprint(reason)
	if r == "<nil>" {
		r = ""
	}
	return map[string]any{
		"is_muted": muted, "muted_until": mutedUntil,
		"is_frozen": frozen, "frozen_until": frozenUntil,
		"is_banned": banned, "moderation_reason": r,
	}
}

// optionalDurationSeconds reads a non-negative duration in seconds, absent or
// null meaning 0 ("no expiry"). A year is the ceiling: a longer moderation is
// what an indefinite one is for.
func optionalDurationSeconds(p map[string]any, key string) (float64, error) {
	raw, ok := p[key]
	if !ok || raw == nil {
		return 0, nil
	}
	seconds := toFloat(raw)
	if seconds < 0 {
		return 0, fmt.Errorf("%s cannot be negative", key)
	}
	if seconds > 366*24*3600 {
		return 0, fmt.Errorf("%s is limited to one year; leave it out for an indefinite moderation", key)
	}
	return seconds, nil
}
