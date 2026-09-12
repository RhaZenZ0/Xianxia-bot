package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type sensePayload struct {
	Mode              string   `json:"mode"`
	TargetUserID      int64    `json:"target_user_id"`
	NPCName           string   `json:"npc_name"`
	PresentHiddenNPCs []string `json:"present_hidden_npcs"`
	GameMinute        int64    `json:"game_minute"`
}

type senseCharacter struct {
	Realm, Phase                                          int64
	Spirit, Insight, Will                                 int64
	SensePowerBonus, SensePrecisionBonus, SenseRangeBonus int64
	ConcealmentBonus                                      int64
	ConcealmentActive                                     bool
	Location                                              string
}

func loadSenseCharacter(conn *storage.Conn, userID int64) (senseCharacter, error) {
	r, err := conn.Execute(`SELECT realm_index,phase,attributes_json,sense_power_bonus,sense_precision_bonus,sense_range_bonus,concealment_bonus,concealment_active,location FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return senseCharacter{}, err
	}
	if len(r.Rows) == 0 {
		return senseCharacter{}, errors.New("living character not found")
	}
	attrs := map[string]float64{}
	if err = json.Unmarshal([]byte(fmt.Sprint(r.Rows[0][2])), &attrs); err != nil {
		return senseCharacter{}, err
	}
	return senseCharacter{Realm: storage.ParseInt(r.Rows[0][0]), Phase: storage.ParseInt(r.Rows[0][1]), Spirit: int64(math.Round(attrs["spirit"])), Insight: int64(math.Round(attrs["insight"])), Will: int64(math.Round(attrs["will"])), SensePowerBonus: storage.ParseInt(r.Rows[0][3]), SensePrecisionBonus: storage.ParseInt(r.Rows[0][4]), SenseRangeBonus: storage.ParseInt(r.Rows[0][5]), ConcealmentBonus: storage.ParseInt(r.Rows[0][6]), ConcealmentActive: storage.ParseInt(r.Rows[0][7]) != 0, Location: fmt.Sprint(r.Rows[0][8])}, nil
}

func senseExtraModifier(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64, stat string) (int64, error) {
	mods := []effectModifier{}
	er, err := conn.Execute(`SELECT effect_json,stacks FROM active_effects WHERE user_id=? AND starts_game_minute<=? AND (ends_game_minute IS NULL OR ends_game_minute>?)`, []any{userID, gameMinute, gameMinute})
	if err != nil {
		return 0, err
	}
	for _, row := range er.Rows {
		var p effectPayload
		if json.Unmarshal([]byte(fmt.Sprint(row[0])), &p) == nil {
			stacks := maxI64(1, storage.ParseInt(row[1]))
			for i := int64(0); i < stacks; i++ {
				mods = append(mods, p.Modifiers...)
			}
		}
	}
	cr, err := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(cr.Rows) > 0 {
		loc := fmt.Sprint(cr.Rows[0][0])
		lr, e := conn.Execute(`SELECT effect_json FROM deployed_location_arrays WHERE location=? AND starts_game_minute<=? AND ends_game_minute>? ORDER BY starts_game_minute DESC LIMIT 1`, []any{loc, gameMinute, gameMinute})
		if e == nil && len(lr.Rows) > 0 {
			var p effectPayload
			if json.Unmarshal([]byte(fmt.Sprint(lr.Rows[0][0])), &p) == nil {
				mods = append(mods, p.Modifiers...)
			}
		}
	}
	rr, err := conn.Execute(`SELECT mutation FROM character_spiritual_roots WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(rr.Rows) > 0 {
		if def, ok := catalog.SpiritualRootSystem.Mutations[fmt.Sprint(rr.Rows[0][0])]; ok {
			mods = append(mods, toEffectModifiers(def.Modifiers)...)
		}
	}
	br, err := conn.Execute(`SELECT bloodline_id,state,evolution_stage,rejection FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(br.Rows) > 0 {
		id, state := fmt.Sprint(br.Rows[0][0]), fmt.Sprint(br.Rows[0][1])
		stage := int(storage.ParseInt(br.Rows[0][2]))
		if def, ok := catalog.Bloodlines[id]; ok && (state == "awakened" || state == "evolved" || state == "mutated") && len(def.Evolutions) > 0 {
			if stage < 1 {
				stage = 1
			}
			if stage > len(def.Evolutions) {
				stage = len(def.Evolutions)
			}
			mods = append(mods, toEffectModifiers(def.Evolutions[stage-1].Modifiers)...)
		}
		if state == "rejected" || storage.ParseInt(br.Rows[0][3]) >= 60 {
			penalty := -1.0
			if state == "rejected" {
				penalty = -2
			}
			mods = append(mods, effectModifier{Stat: "will", Operation: "add", Value: penalty})
		}
	}
	pr, err := conn.Execute(`SELECT physique_id,state,evolution_stage,instability FROM character_physiques WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(pr.Rows) > 0 {
		id, state := fmt.Sprint(pr.Rows[0][0]), fmt.Sprint(pr.Rows[0][1])
		stage := int(storage.ParseInt(pr.Rows[0][2]))
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
			if storage.ParseInt(pr.Rows[0][3]) >= 60 {
				mods = append(mods, effectModifier{Stat: "will", Operation: "add", Value: -1})
			}
		}
	}
	return int64(math.Round(applyStatModifiers(0, stat, mods))), nil
}

func senseStatsGo(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64) (senseCharacter, int64, int64, int64, error) {
	c, err := loadSenseCharacter(conn, userID)
	if err != nil {
		return c, 0, 0, 0, err
	}
	realmFactor := c.Realm * 6
	stageFactor := maxI64(0, c.Phase-1)
	power := int64(4) + c.Spirit*3 + c.Will + realmFactor + stageFactor + c.SensePowerBonus
	precision := int64(3) + c.Insight*3 + c.Spirit + c.Realm*4 + stageFactor/2 + c.SensePrecisionBonus
	var rng int64
	switch c.Realm {
	case 0:
		rng = 2 + c.Phase*2
	case 1:
		rng = 15 + c.Phase*5
	case 2:
		rng = 70 + c.Phase*15
	case 3:
		rng = 250 + c.Phase*70
	case 4:
		rng = 1000 + c.Phase*450
	case 5:
		rng = 5000 + c.Phase*2000
	default:
		rng = int64(25000 * math.Pow(1.55, float64(c.Realm-6)) * (1 + float64(c.Phase)*0.08))
	}
	rng += c.SenseRangeBonus
	ep, err := senseExtraModifier(conn, catalog, userID, gameMinute, "sense_power")
	if err != nil {
		return c, 0, 0, 0, err
	}
	epr, err := senseExtraModifier(conn, catalog, userID, gameMinute, "sense_precision")
	if err != nil {
		return c, 0, 0, 0, err
	}
	erng, err := senseExtraModifier(conn, catalog, userID, gameMinute, "sense_range")
	if err != nil {
		return c, 0, 0, 0, err
	}
	power += ep
	precision += epr
	rng += erng
	if power < 1 {
		power = 1
	}
	if precision < 1 {
		precision = 1
	}
	if rng < 1 {
		rng = 1
	}
	return c, power, precision, rng, nil
}
func concealmentPowerGo(c senseCharacter) int64 {
	base := c.Will + c.Spirit + c.Realm*5 + c.Phase/2 + c.ConcealmentBonus
	if !c.ConcealmentActive {
		return maxI64(2, base/3)
	}
	return maxI64(3, base+8)
}
func senseTier(m int64) string {
	switch {
	case m >= 16:
		return "overwhelming"
	case m >= 9:
		return "strong"
	case m >= 3:
		return "success"
	case m >= -3:
		return "partial"
	case m >= -9:
		return "failure"
	default:
		return "critical_failure"
	}
}
func precisionResult(d1, d2, precision, targetRealm, extra int64) map[string]any {
	tn := 10 + maxI64(0, targetRealm)*2 + maxI64(0, extra)
	total := d1 + d2 + precision
	margin := total - tn
	return map[string]any{"total": total, "tn": tn, "margin": margin, "tier": senseTier(margin)}
}
func realmNameGo(catalog worlddata.Catalog, index int64) string {
	if index < 0 {
		index = 0
	}
	if index >= int64(len(catalog.Realms)) {
		index = int64(len(catalog.Realms) - 1)
	}
	if index < 0 {
		return "Unknown Realm"
	}
	return catalog.Realms[index].Name
}
func realmWorldGo(catalog worlddata.Catalog, index int64) string {
	if index < 0 {
		index = 0
	}
	if index >= int64(len(catalog.Realms)) {
		index = int64(len(catalog.Realms) - 1)
	}
	if index < 0 {
		return "Unknown World"
	}
	return catalog.Realms[index].World
}
func approximateRealmGo(catalog worlddata.Catalog, index, stage int64, tier string) string {
	name := realmNameGo(catalog, index)
	switch tier {
	case "overwhelming":
		return fmt.Sprintf("%s — Stage %d", name, stage)
	case "strong":
		return fmt.Sprintf("%s — approximately Stage %d-%d", name, maxI64(1, stage-1), minI64(9, stage+1))
	case "success":
		return name
	default:
		return "a cultivator of the " + realmWorldGo(catalog, index)
	}
}
func hiddenSenseReading(catalog worlddata.Catalog, sensor senseCharacter, power, precision int64, npcName string, d1, d2 int64) (map[string]any, error) {
	npc, ok := catalog.NPCs[npcName]
	if !ok || npc.HiddenMaster == nil {
		return nil, errors.New("hidden NPC not found")
	}
	h := npc.HiddenMaster
	target := h.Concealment + h.Deception/2 + 10
	total := d1 + d2 + power
	margin := total - target
	tier := senseTier(margin)
	pt := precisionResult(d1, d2, precision, h.TrueRealmIndex, 0)
	precisionTier := pt["tier"].(string)
	reading := h.SenseFalseReading
	if tier == "critical_failure" || tier == "failure" {
		if h.Kind == "fake" && h.ProjectedRealmIndex != nil {
			reading = approximateRealmGo(catalog, *h.ProjectedRealmIndex, h.ProjectedStage, "success")
		}
	}
	reveal := "none"
	if tier == "partial" {
		reading = "The aura does not behave naturally. Something is being concealed, projected, or deliberately suppressed."
		reveal = "suspicion"
	} else if tier != "critical_failure" && tier != "failure" {
		if h.Kind == "fake" {
			if tier == "success" {
				reading = "The impressive aura is inconsistent. Its rhythm does not match the cultivator's dantian."
				reveal = "projection"
			} else {
				truth := "clearly far weaker than the projected aura, but the exact realm remains unclear"
				if precisionTier == "success" || precisionTier == "strong" || precisionTier == "overwhelming" {
					truth = approximateRealmGo(catalog, h.TrueRealmIndex, h.TrueStage, precisionTier)
				}
				reading = fmt.Sprintf("The projected aura collapses under scrutiny. True reading: %s. A concealed %s is producing the false pressure.", truth, firstNonempty(h.FraudItem, "device"))
				reveal = "fake"
			}
		} else {
			gap := h.TrueRealmIndex - sensor.Realm
			if tier == "success" {
				reading = "Your Spiritual Sense meets a seamless void. The absence itself is too perfect to be natural."
				reveal = "concealed_expert"
			} else if (tier == "strong" || precisionTier == "critical_failure" || precisionTier == "failure" || precisionTier == "partial") && gap > 5 {
				reading = "For one instant you glimpse an aura vastly beyond your realm, then it disappears. You cannot determine the exact cultivation."
				reveal = "vastly_stronger"
			} else {
				reading = "You penetrate part of the concealment: " + approximateRealmGo(catalog, h.TrueRealmIndex, h.TrueStage, precisionTier) + ". The target immediately feels your probing sense."
				reveal = "realm"
			}
		}
	}
	return map[string]any{"target": target, "margin": margin, "tier": tier, "kind": h.Kind, "reading": reading, "reveal": reveal, "precision": pt}, nil
}

func senseInspectAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sensePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	sensor, power, precision, rng, err := senseStatsGo(conn, catalog, userID, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"mode": p.Mode, "power": power, "precision_power": precision, "range_m": rng}
	switch p.Mode {
	case "player":
		target, err := loadSenseCharacter(conn, p.TargetUserID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		tn := 10 + concealmentPowerGo(target)
		roll, err := rollCheck(power, tn)
		if err != nil {
			return authoritativeMutation{}, err
		}
		det := senseTier(roll["margin"].(int64))
		prec := precisionResult(roll["die1"].(int64), roll["die2"].(int64), precision, target.Realm, 0)
		reveal := "none"
		pt := prec["tier"].(string)
		if det == "partial" || pt == "critical_failure" || pt == "failure" {
			reveal = "world"
		} else if det != "critical_failure" && det != "failure" {
			if pt == "partial" {
				reveal = "realm"
			} else if pt == "success" || pt == "strong" {
				reveal = "approx"
			} else {
				reveal = "exact"
			}
		}
		result["roll"] = roll
		result["detection_tier"] = det
		result["precision"] = prec
		result["reveal"] = reveal
		result["target_realm_index"] = target.Realm
		result["target_phase"] = target.Phase
		result["target_concealed"] = target.ConcealmentActive
	case "npc":
		npc, ok := catalog.NPCs[p.NPCName]
		if !ok {
			return authoritativeMutation{}, errors.New("unknown npc")
		}
		if npc.HiddenMaster != nil {
			d1, err := gamerng.D10()
			if err != nil {
				return authoritativeMutation{}, err
			}
			d2, err := gamerng.D10()
			if err != nil {
				return authoritativeMutation{}, err
			}
			hidden, err := hiddenSenseReading(catalog, sensor, power, precision, p.NPCName, d1, d2)
			if err != nil {
				return authoritativeMutation{}, err
			}
			target := hidden["target"].(int64)
			total := d1 + d2 + power
			roll := map[string]any{"die1": d1, "die2": d2, "modifier": power, "tn": target, "total": total, "margin": total - target, "success": total >= target, "degree": ""}
			roll["degree"] = degreeFromMarginGo(total - target)
			result["roll"] = roll
			for k, v := range hidden {
				if k != "target" {
					result[k] = v
				}
			}
		} else {
			roll, err := rollCheck(power, 12)
			if err != nil {
				return authoritativeMutation{}, err
			}
			reading := "You cannot obtain a stable reading from their aura."
			if roll["success"].(bool) {
				reading = "Detected cultivation: **" + npc.Realm + "**."
			}
			result["roll"] = roll
			result["reading"] = reading
			result["reveal"] = map[bool]string{true: "public_realm", false: "none"}[roll["success"].(bool)]
		}
	case "area":
		envTN := int64(14)
		if sensor.Location == "Moonfen Marsh" {
			envTN += 3
		}
		roll, err := rollCheck(power, envTN)
		if err != nil {
			return authoritativeMutation{}, err
		}
		prec := precisionResult(roll["die1"].(int64), roll["die2"].(int64), precision, sensor.Realm, 2)
		hints := []string{}
		if loc, ok := catalog.Locations[sensor.Location]; ok && roll["success"].(bool) {
			count := 1
			pt := prec["tier"].(string)
			if pt == "success" || pt == "strong" {
				count = minInt(2, len(loc.SenseHints))
			} else if pt == "overwhelming" {
				count = minInt(3, len(loc.SenseHints))
			}
			if count > len(loc.SenseHints) {
				count = len(loc.SenseHints)
			}
			hints = append(hints, loc.SenseHints[:count]...)
		}
		signals := []string{}
		for _, name := range p.PresentHiddenNPCs {
			npc, ok := catalog.NPCs[name]
			if !ok || npc.HiddenMaster == nil {
				continue
			}
			hidden, err := hiddenSenseReading(catalog, sensor, power, precision, name, roll["die1"].(int64), roll["die2"].(int64))
			if err != nil {
				continue
			}
			if hidden["reveal"] == "none" {
				continue
			}
			if hidden["kind"] == "fake" && (hidden["reveal"] == "projection" || hidden["reveal"] == "fake") {
				signals = append(signals, "artificial")
			} else {
				signals = append(signals, "concealed")
			}
		}
		result["roll"] = roll
		result["precision"] = prec
		result["hints"] = hints
		result["hidden_signals"] = signals
		result["event_visibility"] = roll["margin"].(int64) >= 0
		result["location"] = sensor.Location
	default:
		return authoritativeMutation{}, errors.New("mode must be player, npc, or area")
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "sense", EventType: "spiritual_sense_resolved", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
func degreeFromMarginGo(m int64) string {
	switch {
	case m >= 10:
		return "Overwhelming Success"
	case m >= 5:
		return "Strong Success"
	case m >= 0:
		return "Success"
	case m >= -3:
		return "Soft Failure"
	case m >= -7:
		return "Hard Failure"
	default:
		return "Severe Failure"
	}
}

type senseConcealPayload struct {
	Active     bool  `json:"active"`
	GameMinute int64 `json:"game_minute"`
}

func senseConcealAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p senseConcealPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadSenseCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	active := int64(0)
	if p.Active {
		active = 1
	}
	if _, err := conn.Execute(`UPDATE characters SET concealment_active=? WHERE user_id=? AND life_status='alive'`, []any{active, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	c.ConcealmentActive = p.Active
	result := map[string]any{
		"active":               p.Active,
		"concealment_strength": concealmentPowerGo(c),
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{
		Domain: "sense", EventType: "aura_concealment_changed", EntityType: "character",
		EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result,
	}}, nil
}
