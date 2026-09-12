package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// item.use (v0.21.0, roadmap "Authority I"): the non-battle branch of
// /item use, which Python used to resolve itself with five unguarded writes
// (consume, restore, life extension, effect, toxicity). One transaction now
// does all five in order, and the reply carries what each step did so the
// Discord handler only formats.
//
// combat.recovery_item stays for the battle panel; a player who types
// /item use mid-battle also lands here, and the restore keeps
// battles.player_hp in lockstep exactly as combat.recovery_item does.

type itemUsePayload struct {
	ItemID     string `json:"item_id"`
	GameMinute int64  `json:"game_minute"`
}

// itemHasActiveUse mirrors the Python gate: an item with no instant restore,
// no effect and no lifespan gain "has no implemented active use yet".
// Storage upgrades and array deployment have their own actions.
func itemHasActiveUse(item worlddata.Item) bool {
	u := item.Use
	return u.Instant.QiRestore > 0 || u.Instant.VitalityRestore > 0 || len(u.Effect) > 0 || u.LifespanYears > 0
}

// isPillItem is app/rules/alchemy.py is_pill: a "pill" tag on the effect, or
// "pill" in the name, or an id ending in _pill.
func isPillItem(itemID string, item worlddata.Item) bool {
	if tags, ok := item.Use.Effect["tags"].([]any); ok {
		for _, tag := range tags {
			if strings.EqualFold(strings.TrimSpace(fmt.Sprint(tag)), "pill") {
				return true
			}
		}
	}
	name := item.Name
	if name == "" {
		name = itemID
	}
	return strings.Contains(strings.ToLower(name), "pill") || strings.HasSuffix(strings.ToLower(itemID), "_pill")
}

// pillToxicityValue is app/rules/alchemy.py pill_toxicity_value: strong or
// special medicines are more taxing than common recovery pills, and the
// content can override the number outright.
func pillToxicityValue(itemID string, item worlddata.Item) int64 {
	if !isPillItem(itemID, item) {
		return 0
	}
	if item.PillToxicity != nil {
		return maxI64(0, *item.PillToxicity)
	}
	if item.Use.LifespanYears > 0 {
		return 24
	}
	tags := map[string]bool{}
	if raw, ok := item.Use.Effect["tags"].([]any); ok {
		for _, tag := range raw {
			tags[strings.ToLower(strings.TrimSpace(fmt.Sprint(tag)))] = true
		}
	}
	switch {
	case tags["risky"]:
		return 18
	case tags["cultivation"]:
		return 12
	case tags["mental"]:
		return 10
	case item.Use.Instant.QiRestore > 0 || item.Use.Instant.VitalityRestore > 0:
		return 7
	}
	return 8
}

// toxicityBand is app/rules/alchemy.py toxicity_band (label only).
func toxicityBand(value int64) string {
	value = clamp(value, 0, 100)
	switch {
	case value < 20:
		return "Clear Meridians"
	case value < 40:
		return "Medicine Residue"
	case value < 60:
		return "Pill Saturation"
	case value < 80:
		return "Heavy Pill Toxicity"
	}
	return "Severe Pill Toxicity"
}

// normalizeEffectPayload is app/rules/effects.py normalize_effect_payload:
// the stable serialisable shape every active_effects row carries.
func normalizeEffectPayload(effectKey, name string, raw map[string]any) map[string]any {
	modifiers := []map[string]any{}
	if list, ok := raw["modifiers"].([]any); ok {
		for _, entry := range list {
			m, ok := entry.(map[string]any)
			if !ok || strings.TrimSpace(fmt.Sprint(m["stat"])) == "" || m["stat"] == nil {
				continue
			}
			operation := strings.ToLower(strings.TrimSpace(fmt.Sprint(m["operation"])))
			if operation != "add" && operation != "mul" && operation != "set" && operation != "flag" {
				operation = "add"
			}
			modifiers = append(modifiers, map[string]any{
				"stat": fmt.Sprint(m["stat"]), "operation": operation, "value": toFloat(m["value"]),
			})
		}
	}
	tags := []string{}
	if list, ok := raw["tags"].([]any); ok {
		for _, tag := range list {
			if s := strings.TrimSpace(fmt.Sprint(tag)); s != "" {
				tags = append(tags, s)
			}
		}
	}
	stringOr := func(key, fallback string) string {
		if v, ok := raw[key]; ok && v != nil {
			return fmt.Sprint(v)
		}
		return fallback
	}
	if name == "" {
		name = stringOr("name", effectKey)
	}
	return map[string]any{
		"name":            name,
		"description":     stringOr("description", ""),
		"category":        stringOr("category", "General"),
		"severity":        maxI64(0, storage.ParseInt(raw["severity"])),
		"special":         raw["special"] == true,
		"resistance_stat": stringOr("resistance_stat", ""),
		"modifiers":       modifiers,
		"tags":            tags,
		"stacking":        stringOr("stacking", "replace"),
		"max_stacks":      maxI64(1, storage.ParseInt(raw["max_stacks"])),
	}
}

func itemUseActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p itemUsePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.ItemID = strings.TrimSpace(p.ItemID)
	item, ok := catalog.Items[p.ItemID]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown item")
	}
	if !itemHasActiveUse(item) {
		return authoritativeMutation{}, errors.New("that item has no implemented active use yet")
	}
	itemName := item.Name
	if itemName == "" {
		itemName = p.ItemID
	}
	now := float64(time.Now().UnixNano()) / 1e9

	// 1. consume - one from the carried inventory, the row goes at zero.
	r, e := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if len(r.Rows) == 0 || i64(r.Rows[0][0]) <= 0 {
		return authoritativeMutation{}, errors.New("the item is no longer in your carried inventory")
	}
	if i64(r.Rows[0][0]) == 1 {
		_, e = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	} else {
		_, e = conn.Execute(`UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	}
	if e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"item_id": p.ItemID, "item_name": itemName, "consumed": 1}

	// 2. restore - clamped to the maxima; an active battle's HP bar follows.
	qiRestore, vitRestore := maxI64(0, item.Use.Instant.QiRestore), maxI64(0, item.Use.Instant.VitalityRestore)
	// The qi body (v1.0.0-rc.7): a pill's qi is a base, scaled into the pool
	// the drinker actually has, so it is worth the same share it always was.
	if qiRestore > 0 {
		if state, e := settleQi(conn, catalog, userID, p.GameMinute, now); e == nil {
			qiRestore = state.Restore(qiRestore)
		}
	}
	if qiRestore > 0 || vitRestore > 0 {
		if _, e = conn.Execute(`UPDATE characters SET qi=MIN(qi_max,qi+?),vitality=MIN(vitality_max,vitality+?),updated_at=? WHERE user_id=?`, []any{qiRestore, vitRestore, now, userID}); e != nil {
			return authoritativeMutation{}, e
		}
		st, e := conn.Execute(`SELECT qi,qi_max,vitality,vitality_max FROM characters WHERE user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if len(st.Rows) == 0 {
			return authoritativeMutation{}, errors.New("character not found")
		}
		x := st.Rows[0]
		if vitRestore > 0 {
			if _, e = conn.Execute(`UPDATE battles SET player_hp=?,player_hp_max=MAX(player_hp_max,?),version=version+1,updated_at=? WHERE user_id=? AND status='active'`, []any{i64(x[2]), i64(x[3]), now, userID}); e != nil {
				return authoritativeMutation{}, e
			}
		}
		out["qi"], out["qi_max"], out["vitality"], out["vitality_max"] = i64(x[0]), i64(x[1]), i64(x[2]), i64(x[3])
		out["qi_restore"], out["vitality_restore"] = qiRestore, vitRestore
	}

	// 3. life extension - permanent, reported as the running total.
	if years := maxI64(0, item.Use.LifespanYears); years > 0 {
		if _, e = conn.Execute(`UPDATE characters SET life_extension_years=life_extension_years+?,updated_at=? WHERE user_id=?`, []any{years, now, userID}); e != nil {
			return authoritativeMutation{}, e
		}
		total, e := conn.Execute(`SELECT life_extension_years FROM characters WHERE user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if len(total.Rows) > 0 {
			out["life_extension_years"] = years
			out["life_extension_total"] = i64(total.Rows[0][0])
		}
	}

	// 4. effect - keyed by the content's effect_key (default: the item id),
	// sourced to the item; duration 0 means it does not expire.
	if len(item.Use.Effect) > 0 {
		effectKey := strings.TrimSpace(item.Use.EffectKey)
		if effectKey == "" {
			effectKey = p.ItemID
		}
		effectName := strings.TrimSpace(item.Use.Name)
		if effectName == "" {
			effectName = itemName
		}
		payload := normalizeEffectPayload(effectKey, effectName, item.Use.Effect)
		encoded, e := json.Marshal(payload)
		if e != nil {
			return authoritativeMutation{}, e
		}
		var ends any
		if d := item.Use.DurationGameMinutes; d > 0 {
			ends = p.GameMinute + d
		}
		if _, e = conn.Execute(`INSERT INTO active_effects(user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at) VALUES(?,?,?,?,?,?,1,?,?,?) ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET name=excluded.name,effect_json=excluded.effect_json,stacks=excluded.stacks,starts_game_minute=excluded.starts_game_minute,ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at`,
			[]any{userID, effectKey, effectName, "item", p.ItemID, string(encoded), p.GameMinute, ends, now}); e != nil {
			return authoritativeMutation{}, e
		}
		out["effect_key"], out["effect_name"] = effectKey, effectName
		if ends != nil {
			out["effect_ends_game_minute"] = ends
		}
	}

	// 5. toxicity - settle the decay first (that is what Python's
	// get_alchemy_state did before the add), add the residue, then settle
	// again so the penalty effect matches the new value.
	if gain := pillToxicityValue(p.ItemID, item); gain > 0 {
		if _, e = settlePillToxicityEffectTx(conn, userID, p.GameMinute); e != nil {
			return authoritativeMutation{}, e
		}
		if _, e = conn.Execute(`UPDATE alchemy_state SET pill_toxicity=MIN(100,MAX(0,pill_toxicity+?)),last_toxicity_game_minute=?,updated_at=? WHERE user_id=?`, []any{gain, p.GameMinute, now, userID}); e != nil {
			return authoritativeMutation{}, e
		}
		toxicity, e := settlePillToxicityEffectTx(conn, userID, p.GameMinute)
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["toxicity_gain"], out["pill_toxicity"], out["toxicity_band"] = gain, toxicity, toxicityBand(toxicity)
	}

	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "item", EventType: "item.use", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
