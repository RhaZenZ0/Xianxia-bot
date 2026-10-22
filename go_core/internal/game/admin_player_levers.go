package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The three Python-era levers (v1.37.0).
//
// relationship.update, cultivation.reward and combat.apply_damage were the
// bot's own reporters: a player action took the trust, the cultivation or the
// damage figure off the request and wrote it, because when they were written
// the bot computed those numbers and the engine was a store. By v1.36.0 the
// payload sweep (payload_fields_test.go) had them allowlisted as a Python-era
// class with the owner's decision pending, and nothing under app/ called two of
// them; /talk sent `trust: 1` through the third. The owner's word: GM levers,
// audited like every Admin Console action (repo rule 6), and /talk's trust
// becomes a rule the engine owns (npc_talk.go). The engine playtest keeps its
// door - it fills a stage through the reward lever as the GM.
//
// Each lever names the player it acts on (`user_id`), takes a reason, and
// writes admin_audit_log with what the row said before and after.

var relationshipDimensions = []string{"trust", "respect", "fear", "affection", "debt", "grudge"}

// adminPlayerRelationship moves one NPC relationship by the deltas given,
// each dimension clamped to -100..100, and counts an encounter.
func adminPlayerRelationship(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	npc := strings.TrimSpace(stringField(p, "npc_name"))
	if npc == "" {
		return nil, errors.New("npc_name is required")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	before, err := relationshipRowTx(conn, uid, npc)
	if err != nil {
		return nil, err
	}
	after := map[string]int64{}
	for _, dim := range relationshipDimensions {
		after[dim] = clamp(before[dim]+storage.ParseInt(p[dim]), -100, 100)
	}
	encounter := before["encounter_count"] + 1
	summary := []rune(stringField(p, "summary"))
	if len(summary) > 800 {
		summary = summary[:800]
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`INSERT INTO npc_relationships(user_id,npc_name,trust,respect,fear,affection,debt,grudge,encounter_count,last_summary,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,npc_name) DO UPDATE SET trust=excluded.trust,respect=excluded.respect,fear=excluded.fear,affection=excluded.affection,debt=excluded.debt,grudge=excluded.grudge,encounter_count=excluded.encounter_count,last_summary=excluded.last_summary,updated_at=excluded.updated_at`,
		[]any{uid, npc, after["trust"], after["respect"], after["fear"], after["affection"], after["debt"], after["grudge"], encounter, string(summary), now}); err != nil {
		return nil, err
	}
	out := map[string]any{"user_id": uid, "npc_name": npc, "encounter_count": encounter, "last_summary": string(summary)}
	for _, dim := range relationshipDimensions {
		out[dim] = after[dim]
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.relationship", fmt.Sprintf("user:%d npc:%s", uid, npc), before, out, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}

// relationshipRowTx is one relationship's six dimensions and encounter count,
// zero for a pair that has never met.
func relationshipRowTx(conn *storage.Conn, userID int64, npc string) (map[string]int64, error) {
	res, err := conn.Execute(`SELECT trust,respect,fear,affection,debt,grudge,encounter_count FROM npc_relationships WHERE user_id=? AND npc_name=?`, []any{userID, npc})
	if err != nil {
		return nil, err
	}
	current := map[string]int64{"encounter_count": 0}
	for _, dim := range relationshipDimensions {
		current[dim] = 0
	}
	if row := firstRowMap(res); row != nil {
		for k := range current {
			current[k] = i64(row[k])
		}
	}
	return current, nil
}

// adminPlayerCultivationReward grants cultivation (capped at `cultivation_cap`
// when one is given), insight, stones in the base coin and items, and writes
// the event the bot's reward path used to write.
func adminPlayerCultivationReward(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	cultivation := maxI64(0, storage.ParseInt(p["cultivation"]))
	insight := storage.ParseInt(p["insight_xp"])
	stones := storage.ParseInt(p["spirit_stones"])
	eventType := strings.TrimSpace(stringField(p, "event_type"))
	if eventType == "" {
		eventType = "reward"
	}
	items := map[string]int64{}
	if rawItems, ok := p["items"].(map[string]any); ok {
		for item, qty := range rawItems {
			if n := storage.ParseInt(qty); n > 0 {
				items[strings.TrimSpace(item)] = n
			}
		}
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT cultivation,insight_xp FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("player has no character")
	}
	before := map[string]any{"cultivation": i64(row["cultivation"]), "insight_xp": i64(row["insight_xp"])}
	awarded := cultivation
	if _, capped := p["cultivation_cap"]; capped {
		room := maxI64(0, storage.ParseInt(p["cultivation_cap"])-i64(row["cultivation"]))
		if awarded > room {
			awarded = room
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`UPDATE characters SET cultivation=cultivation+?,insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`, []any{awarded, insight, now, uid}); err != nil {
		return nil, err
	}
	if stones != 0 {
		if _, err := characterWalletDeltaTx(conn, catalog, uid, stones, now); err != nil {
			return nil, err
		}
	}
	for item, qty := range items {
		if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`, []any{uid, item, qty}); err != nil {
			return nil, err
		}
	}
	eventPayload, _ := json.Marshal(map[string]any{"cultivation": awarded, "spirit_stones": stones, "insight_xp": insight, "items": items, "admin_user_id": adminUserID})
	if _, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{uid, eventType, string(eventPayload), now}); err != nil {
		return nil, err
	}
	out := map[string]any{"user_id": uid, "cultivation_awarded": awarded, "insight_xp": insight, "spirit_stones": stones, "items": items}
	if err := auditAdmin(conn, adminUserID, "admin.player.cultivation_reward", fmt.Sprintf("user:%d", uid), before, out, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}

// adminPlayerApplyDamage takes `damage` off the player's vitality inside one
// active battle, keeping the battle's own counter in step.
func adminPlayerApplyDamage(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	battleID, err := requiredInt(p, "battle_id")
	if err != nil {
		return nil, err
	}
	damage := maxI64(0, storage.ParseInt(p["damage"]))
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT b.player_hp,c.vitality,c.vitality_max FROM battles b JOIN characters c ON c.user_id=b.user_id WHERE b.battle_id=? AND b.user_id=? AND b.status='active'`, []any{battleID, uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("no active battle of that id for this player")
	}
	current := minI64(i64(row["player_hp"]), i64(row["vitality"]))
	updated := maxI64(0, current-damage)
	vmax := i64(row["vitality_max"])
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`UPDATE characters SET vitality=?,updated_at=? WHERE user_id=?`, []any{updated, now, uid}); err != nil {
		return nil, err
	}
	if _, err := conn.Execute(`UPDATE battles SET player_hp=?,player_hp_max=MAX(player_hp_max,?),version=version+1,updated_at=? WHERE battle_id=? AND user_id=? AND status='active'`, []any{updated, vmax, now, battleID, uid}); err != nil {
		return nil, err
	}
	out := map[string]any{"user_id": uid, "battle_id": battleID, "damage": damage, "vitality": updated, "vitality_max": vmax}
	if err := auditAdmin(conn, adminUserID, "admin.player.apply_damage", fmt.Sprintf("user:%d battle:%d", uid, battleID), map[string]any{"vitality": current}, out, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}
