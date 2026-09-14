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
		stage := intFromDB(br.Rows[0][2])
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
		stage := intFromDB(pr.Rows[0][2])
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
	// The price of hiding: a folded aura does not reach as far.
	if c.ConcealmentActive {
		power = power * concealedSenseNumerator / concealedSenseDenominator
		precision = precision * concealedSenseNumerator / concealedSenseDenominator
		rng = rng * concealedSenseNumerator / concealedSenseDenominator
	}
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

// Folding your aura away costs you the reach of it: while concealed, a
// cultivator senses at three quarters of their power and precision.
//
// Concealment had no cost at all, which made it not a decision - a free toggle
// nobody had a reason to ever turn off. It now trades one thing for the other,
// which is the shape the fiction already has: you cannot be both the quiet one
// in the corner and the one sweeping the room. The one place it was already a
// real choice stays untouched - a forbidden technique used unconcealed is
// witnessed every time, concealed only sometimes, and that remains the reason
// a cultivator with something to hide pays this price gladly.
const concealedSenseNumerator, concealedSenseDenominator = 3, 4

// Concealment rose 7 a realm (will and spirit one each, plus realm*5) against
// a sense power that rose 10, so it fell three behind every realm and stopped
// meaning anything between peers: from realm 4 a concealed cultivator read as
// `exact` 100% of the time. At realm*8 the two rise together, so hiding keeps
// the worth it had at the bottom of the ladder all the way up it, and a master
// stays hidden from a junior the way the fiction says they do.
func concealmentPowerGo(c senseCharacter) int64 {
	base := c.Will + c.Spirit + c.Realm*8 + c.Phase/2 + c.ConcealmentBonus
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

// How much harder each realm of the thing being read makes it to resolve.
//
// These were both 2, and that made two of the five readings /sense can give
// unreachable. Detection against a concealed cultivator gets 7 harder a realm
// (their concealment is will + spirit + realm*5) while resolving *what* they
// are got only 2, so precision was never the binding constraint: by the time a
// target was far enough above the sensor to make the detail roll marginal,
// detection had already failed and the answer was "none" or "world". Swept
// over the realm ladder and ~2,900 attribute builds, `approx` came out at 0.3%
// of outcomes and `realm` at 0.015%, both only for a minimum-stat realm-0
// character; everything else was `exact`.
//
// At 6 the detail roll degrades at a rate detection can outlive, so the ladder
// the readings describe is actually walked. Against a concealed cultivator,
// with concealment now rising 10 a realm alongside sense power, that lands on
// the shape the fiction has: someone well below you reads exact, one realm
// below reads exact or approximate, a peer reads approximate, and anyone above
// you cannot be read at all - only the world they belong to, if that.
//
// 6 and not 5: 5 was tuned against concealment rising 7 a realm, and raising
// it to 10 moved the detection curve out from under that number - at 5 the
// graded readings fell back to 0.2% of outcomes, which is where they started.
// 7 and 8 were tried and turn most of the band into "world".
//
// The area sweep keeps 2. It reads a place, not a cultivator, and its target
// number scales with the *sensor's* own realm, so the same slope there would
// only cancel the sensor's growth and freeze the sweep at one detail level.
// Two is what makes a sweep qualitative early and precise later.
const (
	precisionPerTargetRealm = 6
	precisionPerAreaRealm   = 2
)

func precisionResult(d1, d2, precision, targetRealm, perRealm, extra int64) map[string]any {
	tn := 10 + maxI64(0, targetRealm)*maxI64(0, perRealm) + maxI64(0, extra)
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
	pt := precisionResult(d1, d2, precision, h.TrueRealmIndex, precisionPerTargetRealm, 0)
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

// What a sweep reads off the ground itself: whether the qi here is worth
// sitting in, which is the one thing a spiritual sense is for in the genre and
// the one thing this command could not answer.
//
// It invents nothing. The cultivation engine has priced the ground since
// v1.0.0-rc.4 - a road-side shrine, a temple quarter, a sect gate, a cave
// abode and its gathering array, a deployed array - and `placeQuality` is the
// word the Here line and the cultivation sheet already use for it. The sweep
// simply reads the same number, so what a player senses and what they will
// actually gather sitting there cannot disagree.
//
// The detail is the precision tier, which is what makes the reading
// qualitative early and precise later: a weak sweep gets the word for the
// ground, a better one what is making it so, and only an overwhelming one the
// multiplier and the world's own qi density as numbers. A failed sweep, or one
// whose detail roll failed outright, reads nothing at all.
func senseGroundReading(conn *storage.Conn, catalog worlddata.Catalog, userID int64, sensor senseCharacter, gameMinute int64, roll, prec map[string]any) (map[string]any, error) {
	if success, _ := roll["success"].(bool); !success {
		return nil, nil
	}
	tier, _ := prec["tier"].(string)
	if tier == "critical_failure" || tier == "failure" {
		return nil, nil
	}
	name, mult, err := placeCultivationMultiplier(conn, catalog, userID, sensor.Location, gameMinute)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"quality": placeQuality(mult), "detail": "vague"}
	if tier == "partial" {
		return out, nil
	}
	out["detail"] = "named"
	out["ground"] = name
	// A sweep that can name what gathers the qi can also read the land it
	// gathers over. `terrain` and `climate` are written into the content and
	// were read by the road planner and by nothing at all respectively; this
	// is the second reader `climate` never had.
	if loc, ok := catalog.Locations[sensor.Location]; ok {
		if loc.Terrain != "" {
			out["terrain"] = loc.Terrain
		}
		if loc.Climate != "" {
			out["climate"] = loc.Climate
		}
	}
	if tier == "overwhelming" {
		out["detail"] = "exact"
		out["multiplier"] = mult
		if loc, ok := catalog.Locations[sensor.Location]; ok {
			out["world"] = loc.World
			out["world_qi"] = worldQiMultiplier(catalog, loc.World)
		}
	}
	return out, nil
}

// How far the sense has to carry to leave the place you are standing in. A
// cultivator reaches the next town along the road at 25km, which is where the
// range curve lands around Soul Formation.
const senseNeighbourRangeMeters = 25000

// Whether a sense can reach the place a target is standing in at all.
//
// It could reach anywhere. `/sense @someone` worked across the whole world -
// from the Mortal World to the Celestial, with no check of any kind - while
// range_m was computed with the most elaborate formula in this file, carried
// bonuses and effect modifiers, and was then only ever printed. Sensing an NPC
// already required standing with them; sensing a player required nothing.
//
// Now the place you are is always within reach, the places joined to it by
// road or gate are within reach once the range curve has grown enough to cross
// one, and everywhere else is not. Adjacency is the world's own, so the reach
// of a spiritual sense follows the map a player already walks.
func senseReaches(catalog worlddata.Catalog, sensorLocation, targetLocation string, rangeMeters int64) bool {
	if sensorLocation == "" || targetLocation == "" {
		return false
	}
	if sensorLocation == targetLocation {
		return true
	}
	if rangeMeters < senseNeighbourRangeMeters {
		return false
	}
	loc, ok := catalog.Locations[sensorLocation]
	if !ok {
		return false
	}
	for _, road := range loc.Roads {
		if road == targetLocation {
			return true
		}
	}
	for _, behind := range loc.Gates {
		for _, name := range behind {
			if name == targetLocation {
				return true
			}
		}
	}
	return false
}

// Whether the cultivator being read feels it happen.
//
// The engine already told the sensor "the target immediately feels your
// probing sense" on one branch, and nothing anywhere backed the sentence: a
// probe was silent, free and unlimited, so there was no counter-play to any of
// it. A probe is felt when the one being read is at least as perceptive as the
// one reading them - they notice a sense brush against their own - or when the
// reading went all the way to `exact`, because at that depth it is not a brush
// but a hand laid on the dantian.
func senseTargetNotices(sensorPower, targetPower int64, reveal string) bool {
	return targetPower >= sensorPower || reveal == "exact"
}

func senseInspectAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sensePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	// The clock, from the engine rather than the caller. An authoritative
	// payload may not carry game_minute (rejectCallerGameMinute forbids it),
	// so p.GameMinute was always zero here - which meant every timed lookup
	// under senseStatsGo asked for minute 0 and matched nothing: no active
	// effect and no deployed location array has ever modified a sense reading,
	// and every sense event was filed at the dawn of the world.
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	sensor, power, precision, rng, err := senseStatsGo(conn, catalog, userID, gameMinute)
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
		if !senseReaches(catalog, sensor.Location, target.Location, rng) {
			// Deliberately says nothing about where they are: a sense that
			// could not find them has not learned their whereabouts either.
			result["reveal"] = "out_of_range"
			result["reading"] = "You cast your sense outward and find nothing of them within its reach."
			break
		}
		tn := 10 + concealmentPowerGo(target)
		roll, err := rollCheck(power, tn)
		if err != nil {
			return authoritativeMutation{}, err
		}
		det := senseTier(roll["margin"].(int64))
		prec := precisionResult(roll["die1"].(int64), roll["die2"].(int64), precision, target.Realm, precisionPerTargetRealm, 0)
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
		_, targetPower, _, _, err := senseStatsGo(conn, catalog, p.TargetUserID, gameMinute)
		if err != nil {
			return authoritativeMutation{}, err
		}
		result["target_noticed"] = senseTargetNotices(power, targetPower, reveal)
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
		prec := precisionResult(roll["die1"].(int64), roll["die2"].(int64), precision, sensor.Realm, precisionPerAreaRealm, 2)
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
		ground, err := senseGroundReading(conn, catalog, userID, sensor, gameMinute, roll, prec)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if ground != nil {
			result["ground"] = ground
		}
	default:
		return authoritativeMutation{}, errors.New("mode must be player, npc, or area")
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "sense", EventType: "spiritual_sense_resolved", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: gameMinute, Payload: result}}, nil
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
	// As above: the caller may not send game_minute, so p.GameMinute is zero
	// and every concealment change was filed at minute 0 of the world.
	gameMinute, err := canonicalWorldGameMinute(conn)
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
		EntityID: fmt.Sprint(userID), GameMinute: gameMinute, Payload: result,
	}}, nil
}
