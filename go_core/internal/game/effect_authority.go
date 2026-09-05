package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const (
	pillToxicityDecayMinutes = int64(12 * 60)
	pillToxicityDecayAmount  = int64(1)
)

type canonicalWorldClock struct {
	AnchorGameMinute int64   `json:"anchor_game_minute"`
	AnchorRealTS     float64 `json:"anchor_real_ts"`
	Scale            int64   `json:"scale"`
}

func canonicalWorldGameMinute(conn *storage.Conn) (int64, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='world_clock'`, nil)
	if err != nil {
		return 0, err
	}

	state := canonicalWorldClock{
		AnchorGameMinute: 8 * 60,
		AnchorRealTS:     now,
		Scale:            4,
	}
	if row := firstRowMap(res); row != nil {
		if err := json.Unmarshal([]byte(fmt.Sprint(row["value_json"])), &state); err != nil {
			return 0, fmt.Errorf("invalid canonical world clock: %w", err)
		}
	} else {
		encoded, _ := json.Marshal(state)
		if _, err := conn.Execute(
			`INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,?)`,
			[]any{string(encoded), now},
		); err != nil {
			return 0, err
		}
	}

	if state.Scale < 0 {
		state.Scale = 0
	}
	if state.AnchorRealTS <= 0 {
		state.AnchorRealTS = now
	}
	elapsedRealMinutes := (now - state.AnchorRealTS) / 60.0
	if elapsedRealMinutes < 0 {
		elapsedRealMinutes = 0
	}
	gameMinute := state.AnchorGameMinute + int64(elapsedRealMinutes*float64(state.Scale))
	if gameMinute < 0 {
		gameMinute = 0
	}
	return gameMinute, nil
}

func readCanonicalWorldGameMinute(conn *storage.Conn) (int64, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='world_clock'`, nil)
	if err != nil {
		return 0, err
	}

	state := canonicalWorldClock{
		AnchorGameMinute: 8 * 60,
		AnchorRealTS:     now,
		Scale:            4,
	}
	if row := firstRowMap(res); row != nil {
		if err := json.Unmarshal([]byte(fmt.Sprint(row["value_json"])), &state); err != nil {
			return 0, fmt.Errorf("invalid canonical world clock: %w", err)
		}
	}
	if state.Scale < 0 {
		state.Scale = 0
	}
	if state.AnchorRealTS <= 0 {
		state.AnchorRealTS = now
	}
	elapsedRealMinutes := (now - state.AnchorRealTS) / 60.0
	if elapsedRealMinutes < 0 {
		elapsedRealMinutes = 0
	}
	gameMinute := state.AnchorGameMinute + int64(elapsedRealMinutes*float64(state.Scale))
	if gameMinute < 0 {
		gameMinute = 0
	}
	return gameMinute, nil
}

// readCanonicalWorldClock is the read-only counterpart of canonicalWorldGameMinute:
// it never inserts a default row (queries shouldn't need write access), and it
// hands back the anchor/scale themselves, not just the derived current minute,
// so a caller can convert *any* absolute game-minute (like a travel arrival) to
// a real-world Unix timestamp with the same anchor - not just "now".
func readCanonicalWorldClock(conn *storage.Conn) (canonicalWorldClock, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='world_clock'`, nil)
	if err != nil {
		return canonicalWorldClock{}, err
	}
	state := canonicalWorldClock{
		AnchorGameMinute: 8 * 60,
		AnchorRealTS:     now,
		Scale:            4,
	}
	if row := firstRowMap(res); row != nil {
		if err := json.Unmarshal([]byte(fmt.Sprint(row["value_json"])), &state); err != nil {
			return canonicalWorldClock{}, fmt.Errorf("invalid canonical world clock: %w", err)
		}
	}
	if state.Scale < 0 {
		state.Scale = 0
	}
	if state.AnchorRealTS <= 0 {
		state.AnchorRealTS = now
	}
	return state, nil
}

// realTimestampForGameMinute converts an absolute game-clock minute to a real
// Unix timestamp (seconds) using the world clock's own anchor/scale, the
// inverse of the anchor + elapsed*scale formula the clock itself advances by.
// ok is false when the clock is frozen (scale 0) and the target lies in the
// future relative to the anchor, since there is then no real time at which it
// is ever reached.
func realTimestampForGameMinute(clock canonicalWorldClock, targetGameMinute int64) (ts float64, ok bool) {
	if clock.Scale <= 0 {
		if targetGameMinute <= clock.AnchorGameMinute {
			return clock.AnchorRealTS, true
		}
		return 0, false
	}
	gameMinutesFromAnchor := float64(targetGameMinute - clock.AnchorGameMinute)
	return clock.AnchorRealTS + (gameMinutesFromAnchor/float64(clock.Scale))*60.0, true
}

func previewPillToxicity(conn *storage.Conn, userID, gameMinute int64) (int64, error) {
	res, err := conn.Execute(
		`SELECT pill_toxicity,last_toxicity_game_minute FROM alchemy_state WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, nil
	}
	toxicity := clamp(storage.ParseInt(row["pill_toxicity"]), 0, 100)
	last := storage.ParseInt(row["last_toxicity_game_minute"])
	if last <= 0 {
		return toxicity, nil
	}
	current := maxI64(last, gameMinute)
	steps := maxI64(0, (current-last)/pillToxicityDecayMinutes)
	return maxI64(0, toxicity-steps*pillToxicityDecayAmount), nil
}

func pillToxicityEffectView(userID, toxicity, gameMinute int64) (map[string]any, error) {
	if toxicity < 40 {
		return nil, nil
	}
	raw, err := medicineToxicityEffectJSON(toxicity)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{}
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return nil, err
	}
	payload["effect_key"] = "pill_toxicity"
	payload["name"] = "Pill Toxicity"
	payload["source_type"] = "alchemy"
	payload["source_id"] = "pill_toxicity"
	payload["stacks"] = int64(1)
	payload["starts_game_minute"] = gameMinute
	payload["ends_game_minute"] = nil
	payload["user_id"] = userID
	return payload, nil
}

func currentEffectsAuthorityQuery(conn *storage.Conn, userID int64) (map[string]any, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("actor_id must be positive")
	}
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	toxicity, err := previewPillToxicity(conn, userID, gameMinute)
	if err != nil {
		return nil, err
	}
	effect, err := pillToxicityEffectView(userID, toxicity, gameMinute)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"game_minute":          gameMinute,
		"pill_toxicity":        toxicity,
		"pill_toxicity_effect": effect,
	}, nil
}

func settlePillToxicityEffectTx(conn *storage.Conn, userID, gameMinute int64) (int64, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(
		`SELECT pill_toxicity,last_toxicity_game_minute FROM alchemy_state WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}

	toxicity := int64(0)
	last := maxI64(0, gameMinute)
	if row := firstRowMap(res); row == nil {
		if _, err := conn.Execute(
			`INSERT INTO alchemy_state(
				user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
				successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
			) VALUES(?,0,?,0,0,0,-99,'',?)`,
			[]any{userID, last, now},
		); err != nil {
			return 0, err
		}
	} else {
		toxicity = clamp(storage.ParseInt(row["pill_toxicity"]), 0, 100)
		last = storage.ParseInt(row["last_toxicity_game_minute"])
		current := maxI64(last, gameMinute)
		if last <= 0 {
			last = current
			if _, err := conn.Execute(
				`UPDATE alchemy_state SET last_toxicity_game_minute=?,updated_at=? WHERE user_id=?`,
				[]any{last, now, userID},
			); err != nil {
				return 0, err
			}
		} else {
			steps := maxI64(0, (current-last)/pillToxicityDecayMinutes)
			if steps > 0 {
				toxicity = maxI64(0, toxicity-steps*pillToxicityDecayAmount)
				last += steps * pillToxicityDecayMinutes
				if _, err := conn.Execute(
					`UPDATE alchemy_state
					 SET pill_toxicity=?,last_toxicity_game_minute=?,updated_at=?
					 WHERE user_id=?`,
					[]any{toxicity, last, now, userID},
				); err != nil {
					return 0, err
				}
			}
		}
	}

	if toxicity < 40 {
		if _, err := conn.Execute(
			`DELETE FROM active_effects
			 WHERE user_id=? AND effect_key='pill_toxicity'
			   AND source_type='alchemy' AND source_id='pill_toxicity'`,
			[]any{userID},
		); err != nil {
			return 0, err
		}
		return toxicity, nil
	}

	effectJSON, err := medicineToxicityEffectJSON(toxicity)
	if err != nil {
		return 0, err
	}
	if _, err := conn.Execute(
		`INSERT INTO active_effects(
			user_id,effect_key,name,source_type,source_id,effect_json,stacks,
			starts_game_minute,ends_game_minute,created_at
		) VALUES(?,'pill_toxicity','Pill Toxicity','alchemy','pill_toxicity',?,1,?,NULL,?)
		ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET
			name=excluded.name,effect_json=excluded.effect_json,stacks=excluded.stacks,
			starts_game_minute=excluded.starts_game_minute,
			ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at`,
		[]any{userID, effectJSON, gameMinute, now},
	); err != nil {
		return 0, err
	}
	return toxicity, nil
}

func medicineToxicityEffectJSON(toxicity int64) (string, error) {
	toxicity = clamp(toxicity, 0, 100)
	cultivationMult := 0.95
	willPenalty := int64(0)
	switch {
	case toxicity >= 80:
		cultivationMult = 0.70
		willPenalty = -2
	case toxicity >= 60:
		cultivationMult = 0.85
		willPenalty = -1
	}
	alchemyPenalty := -maxI64(2, toxicity/10)
	modifiers := []map[string]any{
		{"stat": "cultivation_gain", "operation": "mul", "value": cultivationMult},
		{"stat": "alchemy_bonus", "operation": "add", "value": alchemyPenalty},
	}
	if willPenalty != 0 {
		modifiers = append(modifiers, map[string]any{
			"stat": "will", "operation": "add", "value": willPenalty,
		})
	}
	payload := map[string]any{
		"name":            "Pill Toxicity",
		"description":     "Accumulated medicinal residue makes further cultivation and medicine harder to assimilate.",
		"category":        "Medicine",
		"severity":        maxI64(1, toxicity/20),
		"special":         false,
		"resistance_stat": "",
		"modifiers":       modifiers,
		"tags":            []string{"pill", "toxicity", "medicine"},
		"stacking":        "replace",
		"max_stacks":      1,
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	return string(encoded), nil
}

func canonicalAdditiveEffectBonus(
	conn *storage.Conn,
	catalog worlddata.Catalog,
	userID int64,
	location string,
	gameMinute int64,
	stat string,
) (int64, error) {
	total := 0.0

	res, err := conn.Execute(
		`SELECT effect_json,stacks FROM active_effects
		 WHERE user_id=? AND starts_game_minute<=?
		   AND (ends_game_minute IS NULL OR ends_game_minute>?)`,
		[]any{userID, gameMinute, gameMinute},
	)
	if err != nil {
		return 0, err
	}
	for _, row := range res.Rows {
		total += additiveEffectJSONStat(fmt.Sprint(row[0]), stat, maxI64(1, storage.ParseInt(row[1])))
	}

	location = strings.TrimSpace(location)
	if location != "" {
		res, err = conn.Execute(
			`SELECT effect_json FROM deployed_location_arrays
			 WHERE location=? AND starts_game_minute<=? AND ends_game_minute>?
			 ORDER BY starts_game_minute DESC LIMIT 1`,
			[]any{location, gameMinute, gameMinute},
		)
		if err != nil {
			return 0, err
		}
		if len(res.Rows) > 0 {
			total += additiveEffectJSONStat(fmt.Sprint(res.Rows[0][0]), stat, 1)
		}
	}

	res, err = conn.Execute(`SELECT mutation FROM character_spiritual_roots WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) > 0 {
		if definition, ok := catalog.SpiritualRootSystem.Mutations[fmt.Sprint(res.Rows[0][0])]; ok {
			total += additiveWorldModifiersStat(definition.Modifiers, stat)
		}
	}

	res, err = conn.Execute(
		`SELECT bloodline_id,state,evolution_stage FROM character_bloodlines
		 WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	if len(res.Rows) > 0 {
		id := fmt.Sprint(res.Rows[0][0])
		state := strings.TrimSpace(fmt.Sprint(res.Rows[0][1]))
		stage := int(storage.ParseInt(res.Rows[0][2]))
		if definition, ok := catalog.Bloodlines[id]; ok &&
			(state == "awakened" || state == "evolved" || state == "mutated") &&
			len(definition.Evolutions) > 0 {
			if stage < 1 {
				stage = 1
			}
			if stage > len(definition.Evolutions) {
				stage = len(definition.Evolutions)
			}
			total += additiveWorldModifiersStat(definition.Evolutions[stage-1].Modifiers, stat)
		}
	}

	res, err = conn.Execute(
		`SELECT physique_id,state,evolution_stage FROM character_physiques WHERE user_id=?`,
		[]any{userID},
	)
	if err != nil {
		return 0, err
	}
	if len(res.Rows) > 0 {
		id := fmt.Sprint(res.Rows[0][0])
		state := strings.TrimSpace(fmt.Sprint(res.Rows[0][1]))
		stage := int(storage.ParseInt(res.Rows[0][2]))
		if id != "ordinary_mortal_body" && (state == "awakened" || state == "evolved") {
			if definition, ok := catalog.Physiques[id]; ok {
				if len(definition.Evolutions) > 0 {
					if stage < 1 {
						stage = 1
					}
					if stage > len(definition.Evolutions) {
						stage = len(definition.Evolutions)
					}
					total += additiveWorldModifiersStat(definition.Evolutions[stage-1].Modifiers, stat)
				}
				total += additiveWorldModifiersStat(definition.DrawbackModifiers, stat)
			}
		}
	}

	return int64(total), nil
}

func additiveEffectJSONStat(raw, stat string, stacks int64) float64 {
	var payload effectPayload
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return 0
	}
	if stacks < 1 {
		stacks = 1
	}
	total := 0.0
	for _, modifier := range payload.Modifiers {
		if strings.TrimSpace(modifier.Stat) != stat {
			continue
		}
		operation := strings.ToLower(strings.TrimSpace(modifier.Operation))
		if operation == "" || operation == "add" {
			total += modifier.Value * float64(stacks)
		}
	}
	return total
}

func additiveWorldModifiersStat(modifiers []worlddata.Modifier, stat string) float64 {
	total := 0.0
	for _, modifier := range modifiers {
		if strings.TrimSpace(modifier.Stat) != stat {
			continue
		}
		operation := strings.ToLower(strings.TrimSpace(modifier.Operation))
		if operation == "" || operation == "add" {
			total += modifier.Value
		}
	}
	return total
}
