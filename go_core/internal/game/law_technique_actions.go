package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The out-of-battle half of `/law technique` (v0.23.0, the v0.21 Authority I
// backlog).
//
// In battle the technique has run through combat.technique since v0.21.3.
// Outside battle it applied its own effect row from Discord: the last write in
// the file where the requirement checks also lived, so the gate and the write
// were on the same side of the boundary and neither was enforced by the
// engine. A player who met the requirements in Python's reading of them got
// the buff regardless of what the engine thought.

// lawTechniqueEffectMinutes is how long a self-applied Law technique lasts,
// transcribed from the command it replaces.
const lawTechniqueEffectMinutes = int64(120)

// lawControlCategory is the content's own word for an effect cast at somebody
// else - `special_effects.<id>.category`. It is the one statement of which
// techniques need a target: `app/rules/game.py` asks the same question of the
// same field rather than keeping a list of ids beside this one.
const lawControlCategory = "Law Control"

type lawTechniquePayload struct {
	Technique  string `json:"technique"`
	GameMinute int64  `json:"game_minute"`
}

func lawTechniqueAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p lawTechniquePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	key := strings.TrimSpace(p.Technique)
	technique, ok := catalog.LawSystem.Techniques[key]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown law technique")
	}

	res, err := conn.Execute(
		`SELECT realm_index FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	character := firstRowMap(res)
	if character == nil {
		return authoritativeMutation{}, errors.New("living character not found")
	}
	realm := storage.ParseInt(character["realm_index"])
	if realm < int64(technique.MinRealmIndex) {
		return authoritativeMutation{}, fmt.Errorf("realm index %d required", technique.MinRealmIndex)
	}

	res, err = conn.Execute(
		`SELECT comprehension FROM law_progress WHERE user_id=? AND law_id=?`,
		[]any{userID, technique.Law})
	if err != nil {
		return authoritativeMutation{}, err
	}
	comprehension := int64(0)
	if row := firstRowMap(res); row != nil {
		comprehension = storage.ParseInt(row["comprehension"])
	}
	stage := lawStage(catalog, comprehension)
	if int64(stage.Index) < int64(technique.RequiresStage) {
		return authoritativeMutation{}, fmt.Errorf("law stage %d required, current %d", technique.RequiresStage, stage.Index)
	}

	// World Collapse needs somewhere to collapse.
	if key == "world_collapse" {
		res, err = conn.Execute(`SELECT 1 FROM personal_worlds WHERE user_id=?`, []any{userID})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if len(res.Rows) == 0 {
			return authoritativeMutation{}, errors.New("world collapse requires a stabilized personal world")
		}
	}

	// A technique used with a battle open belongs to combat.technique, which
	// resolves it against the opponent. Silently applying a self-buff instead
	// would let a player take the out-of-battle branch mid-duel.
	res, err = conn.Execute(
		`SELECT battle_id FROM battles WHERE user_id=? AND status='active'`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(res.Rows) > 0 {
		return authoritativeMutation{}, errors.New("you are in a battle; use the technique through the battle panel")
	}

	result := map[string]any{
		"technique":   key,
		"name":        technique.Name,
		"law":         technique.Law,
		"stage_index": stage.Index,
		"effect_id":   technique.Effect,
	}
	if technique.Effect != "" {
		effect, effectName, lookupErr := specialEffectPayload(catalog, technique.Effect)
		if lookupErr != nil {
			return authoritativeMutation{}, fmt.Errorf("law technique %s names an unknown effect %q", key, technique.Effect)
		}

		// A control technique is cast at somebody, and there is nobody here.
		//
		// `combat.technique` resolves those against the opponent - suppression
		// for the lockdown, damage for the strangulation - and writes no
		// effect row at all. This path writes one on the *user*, so a control
		// technique taken out of a battle applied its own debuff (agility -3
		// and escape_bonus -5, for two hours) to the cultivator who used it,
		// with no target anywhere in the world.
		//
		// `app/bot/commands/law.py` has refused that since v0.23.0 and the
		// engine never did, which is rc.48's rule in a third place: a bound
		// that lives in the client is not a bound. The category is read off
		// the content rather than matched against a list of ids here, so the
		// panel and the engine cannot disagree about which techniques those
		// are (`World.law_technique_targets_another` asks the same question).
		if strings.TrimSpace(fmt.Sprint(effect["category"])) == lawControlCategory {
			return authoritativeMutation{}, fmt.Errorf(
				"%s is a control technique and needs a target; use it through the battle panel",
				technique.Name)
		}

		payload := map[string]any{}
		for field, value := range effect {
			payload[field] = value
		}
		payload["effect_key"] = technique.Effect
		payload["special"] = true
		encoded, err := json.Marshal(payload)
		if err != nil {
			return authoritativeMutation{}, err
		}
		name := effectName
		if name == "" {
			name = technique.Name
		}
		if _, err = conn.Execute(
			`INSERT INTO active_effects(
				user_id,effect_key,name,source_type,source_id,effect_json,stacks,
				starts_game_minute,ends_game_minute,created_at
			 ) VALUES(?,?,?,'law',?,?,1,?,?,?)
			 ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET
				name=excluded.name,effect_json=excluded.effect_json,stacks=1,
				starts_game_minute=excluded.starts_game_minute,
				ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at`,
			[]any{
				userID, technique.Effect, name, key, string(encoded),
				p.GameMinute, p.GameMinute + lawTechniqueEffectMinutes, nowSeconds(),
			},
		); err != nil {
			return authoritativeMutation{}, err
		}
		result["effect_name"] = name
		result["duration_game_minutes"] = lawTechniqueEffectMinutes
		result["ends_game_minute"] = p.GameMinute + lawTechniqueEffectMinutes
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain:     "law",
			EventType:  "law_technique_manifested",
			EntityType: "character",
			EntityID:   fmt.Sprint(userID),
			GameMinute: p.GameMinute,
			Payload:    result,
		},
	}, nil
}
