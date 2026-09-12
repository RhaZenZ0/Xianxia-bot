package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type effectModifier struct {
	Stat      string  `json:"stat"`
	Operation string  `json:"operation"`
	Value     float64 `json:"value"`
}

type effectPayload struct {
	Modifiers []effectModifier `json:"modifiers"`
}

func applyStatModifiers(base float64, stat string, modifiers []effectModifier) float64 {
	set := false
	setValue := 0.0
	add := 0.0
	mult := 1.0
	for _, mod := range modifiers {
		if strings.TrimSpace(mod.Stat) != stat {
			continue
		}
		switch strings.ToLower(strings.TrimSpace(mod.Operation)) {
		case "set":
			set = true
			setValue = mod.Value
		case "mul":
			mult *= mod.Value
		case "add", "":
			add += mod.Value
		}
	}
	if set {
		base = setValue
	}
	return (base + add) * mult
}

func canonicalAttribute(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64, attr string) (int64, error) {
	attr = strings.ToLower(strings.TrimSpace(attr))
	allowed := map[string]bool{"body": true, "agility": true, "spirit": true, "insight": true, "will": true, "presence": true, "heart": true}
	if !allowed[attr] {
		return 0, fmt.Errorf("unknown attribute: %s", attr)
	}
	cr, err := conn.Execute(`SELECT attributes_json,path,location FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(cr.Rows) == 0 {
		return 0, errors.New("living character not found")
	}
	attrs := map[string]float64{}
	if err := json.Unmarshal([]byte(fmt.Sprint(cr.Rows[0][0])), &attrs); err != nil {
		return 0, err
	}
	value := attrs[attr]
	path := fmt.Sprint(cr.Rows[0][1])
	location := fmt.Sprint(cr.Rows[0][2])
	mods := []effectModifier{}

	// Persisted effects (conditions, medicine, temporary buffs/debuffs).
	er, err := conn.Execute(`SELECT effect_json,stacks FROM active_effects WHERE user_id=? AND starts_game_minute<=? AND (ends_game_minute IS NULL OR ends_game_minute>?)`, []any{userID, gameMinute, gameMinute})
	if err != nil {
		return 0, err
	}
	for _, row := range er.Rows {
		var payload effectPayload
		if json.Unmarshal([]byte(fmt.Sprint(row[0])), &payload) == nil {
			stacks := maxI64(1, storage.ParseInt(row[1]))
			for i := int64(0); i < stacks; i++ {
				mods = append(mods, payload.Modifiers...)
			}
		}
	}

	// Deployed location arrays affect everyone currently at that location.
	lr, err := conn.Execute(`SELECT effect_json FROM deployed_location_arrays WHERE location=? AND starts_game_minute<=? AND ends_game_minute>? ORDER BY starts_game_minute DESC LIMIT 1`, []any{location, gameMinute, gameMinute})
	if err == nil && len(lr.Rows) > 0 {
		var payload effectPayload
		if json.Unmarshal([]byte(fmt.Sprint(lr.Rows[0][0])), &payload) == nil {
			mods = append(mods, payload.Modifiers...)
		}
	}

	// Innate spiritual-root mutation modifiers.
	rr, err := conn.Execute(`SELECT mutation FROM character_spiritual_roots WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(rr.Rows) > 0 {
		mutation := fmt.Sprint(rr.Rows[0][0])
		if def, ok := catalog.SpiritualRootSystem.Mutations[mutation]; ok {
			mods = append(mods, toEffectModifiers(def.Modifiers)...)
		}
	}

	// Primary bloodline effects mirror app/aptitudes.py aptitude_effects().
	br, err := conn.Execute(`SELECT bloodline_id,state,evolution_stage,rejection FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(br.Rows) > 0 {
		id, state := fmt.Sprint(br.Rows[0][0]), fmt.Sprint(br.Rows[0][1])
		stage, rejection := int(storage.ParseInt(br.Rows[0][2])), int(storage.ParseInt(br.Rows[0][3]))
		if def, ok := catalog.Bloodlines[id]; ok {
			if (state == "awakened" || state == "evolved" || state == "mutated") && len(def.Evolutions) > 0 {
				if stage < 1 {
					stage = 1
				}
				if stage > len(def.Evolutions) {
					stage = len(def.Evolutions)
				}
				mods = append(mods, toEffectModifiers(def.Evolutions[stage-1].Modifiers)...)
			}
		}
		if state == "rejected" || rejection >= 60 {
			penalty := -1.0
			if state == "rejected" {
				penalty = -2
			}
			mods = append(mods, effectModifier{Stat: "will", Operation: "add", Value: penalty})
		}
	}

	// Physique advantages/drawbacks.
	pr, err := conn.Execute(`SELECT physique_id,state,evolution_stage,instability FROM character_physiques WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(pr.Rows) > 0 {
		id, state := fmt.Sprint(pr.Rows[0][0]), fmt.Sprint(pr.Rows[0][1])
		stage, instability := int(storage.ParseInt(pr.Rows[0][2])), int(storage.ParseInt(pr.Rows[0][3]))
		if id != "ordinary_mortal_body" && (state == "awakened" || state == "evolved") {
			if def, ok := catalog.Physiques[id]; ok {
				if len(def.Evolutions) > 0 {
					if stage < 1 {
						stage = 1
					}
					if stage > len(def.Evolutions) {
						stage = len(def.Evolutions)
					}
					mods = append(mods, toEffectModifiers(def.Evolutions[stage-1].Modifiers)...)
				}
				mods = append(mods, toEffectModifiers(def.DrawbackModifiers)...)
			}
			if instability >= 60 {
				mods = append(mods, effectModifier{Stat: "will", Operation: "add", Value: -1})
			}
		}
	}
	_ = path // retained for future path-compatibility effects; current attribute modifiers do not require it.
	return int64(math.Round(applyStatModifiers(value, attr, mods))), nil
}

func toEffectModifiers(in []worlddata.Modifier) []effectModifier {
	out := make([]effectModifier, 0, len(in))
	for _, m := range in {
		out = append(out, effectModifier{Stat: m.Stat, Operation: m.Operation, Value: m.Value})
	}
	return out
}

func rollCheck(modifier, tn int64) (map[string]any, error) {
	d1, err := gamerng.D10()
	if err != nil {
		return nil, err
	}
	d2, err := gamerng.D10()
	if err != nil {
		return nil, err
	}
	total := d1 + d2 + modifier
	margin := total - tn
	degree := "Severe Failure"
	switch {
	case margin >= 10:
		degree = "Overwhelming Success"
	case margin >= 5:
		degree = "Strong Success"
	case margin >= 0:
		degree = "Success"
	case margin >= -3:
		degree = "Soft Failure"
	case margin >= -7:
		degree = "Hard Failure"
	}
	return map[string]any{"die1": d1, "die2": d2, "modifier": modifier, "tn": tn, "total": total, "margin": margin, "success": total >= tn, "degree": degree, "probability": rollOdds(modifier, tn)}, nil
}

type canonicalCheckPayloadV2 struct {
	Attribute  string `json:"attribute"`
	TN         int64  `json:"tn"`
	GameMinute int64  `json:"game_minute"`
	Label      string `json:"label"`
}

func resolveCanonicalCheckV2(conn *storage.Conn, catalog worlddata.Catalog, actorID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p canonicalCheckPayloadV2
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	base, err := canonicalAttribute(conn, catalog, actorID, p.GameMinute, p.Attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result, err := rollCheck(base+2, p.TN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result["label"] = p.Label
	result["attribute"] = strings.ToLower(strings.TrimSpace(p.Attribute))
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "character", EventType: "check_resolved", EntityType: "character", EntityID: fmt.Sprint(actorID), GameMinute: p.GameMinute, Payload: result}}, nil
}

type sceneActionProfile struct {
	Label, Attribute string
	TN               int64
}

var sceneActionProfiles = map[string]sceneActionProfile{
	"observe": {"Observe", "insight", 11}, "investigate": {"Investigate", "insight", 14}, "influence": {"Influence", "presence", 14},
	"stealth": {"Stealth", "agility", 14}, "physical": {"Physical Feat", "body", 14}, "qi": {"Qi Control", "spirit", 14},
	"resolve": {"Resolve", "will", 14}, "aid": {"Aid", "presence", 11},
}

type sceneActionPayload struct {
	ActionKey  string `json:"action_key"`
	Target     string `json:"target"`
	Detail     string `json:"detail"`
	GameMinute int64  `json:"game_minute"`
}

func resolveSceneAction(conn *storage.Conn, catalog worlddata.Catalog, actorID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sceneActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	profile, ok := sceneActionProfiles[p.ActionKey]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown scene action")
	}
	target := strings.TrimSpace(p.Target)
	if target == "" {
		target = "Environment"
	}
	detail := strings.TrimSpace(p.Detail)
	if detail == "" {
		return authoritativeMutation{}, errors.New("scene action detail is required")
	}
	result := map[string]any{"action_key": p.ActionKey, "label": profile.Label, "attribute": profile.Attribute, "tn": profile.TN, "target": target, "automatic": false}
	if target == "Self" {
		result["automatic"] = true
		result["success"] = true
		result["degree"] = "Automatic Success"
		result["margin"] = int64(0)
		result["modifier"] = int64(0)
		result["total"] = int64(0)
		result["die1"] = int64(0)
		result["die2"] = int64(0)
	} else {
		attr, err := canonicalAttribute(conn, catalog, actorID, p.GameMinute, profile.Attribute)
		if err != nil {
			return authoritativeMutation{}, err
		}
		rolled, err := rollCheck(attr+2, profile.TN)
		if err != nil {
			return authoritativeMutation{}, err
		}
		for k, v := range rolled {
			result[k] = v
		}
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "scene", EventType: "scene_action_resolved", EntityType: "character", EntityID: fmt.Sprint(actorID), GameMinute: p.GameMinute, Payload: map[string]any{"result": result, "detail": detail}}}, nil
}
