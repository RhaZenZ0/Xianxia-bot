package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A better cultivation system (v1.0.0-rc.3): the meditation stance, the odds
// a breakthrough faces before it is rolled, and the realm gate - crossing
// into a new realm needs an insight banked from Insight XP or a completed
// Realm Perfection, so stage 9 is a bottleneck with a way through and not a
// wall the dice alone open. All of it is engine state: the stance and the
// banked insight live in world_state under the player's id, as the road
// transit does; the odds are computed from the same modifier the roll uses.

const (
	stanceCirculate = "circulate"
	stanceRefine    = "refine"
	stanceForce     = "force"

	// Force stance: the gain is a third larger and, this often in a hundred,
	// the qi runs wild and leaves a deviation the healers must treat.
	forceDeviationChancePercent = 15
	// Refine stance: a fifth slower, but every session banks Insight XP.
	refineInsightXPPerSession = 2
)

type cultivationStanceDefinition struct {
	Key         string
	Label       string
	GainMult    float64
	Description string
}

var cultivationStances = []cultivationStanceDefinition{
	{Key: stanceCirculate, Label: "Circulate", GainMult: 1.0, Description: "The orthodox circulation: the full gain, nothing risked, nothing banked."},
	{Key: stanceRefine, Label: "Refine", GainMult: 0.8, Description: "Slower by a fifth, but every session banks Insight XP toward the realm gate, and stage-9 refinement deepens faster."},
	{Key: stanceForce, Label: "Force", GainMult: 1.3, Description: "A third faster, and fifteen times in a hundred the qi runs wild; each untreated deviation is worse than the last."},
}

func stanceDefinition(key string) (cultivationStanceDefinition, bool) {
	for _, s := range cultivationStances {
		if s.Key == key {
			return s, true
		}
	}
	return cultivationStanceDefinition{}, false
}

func cultivationStanceKey(userID int64) string { return fmt.Sprintf("cultivation_stance:%d", userID) }

// The moment seized (v1.0.0-rc.4): a failed breakthrough leaves the stage's
// essence full in memory for one more roll, bought with Insight XP, once a
// stage. The last failure and the reroll spent live under the player's id.
func cultivationLastFailKey(userID int64) string {
	return fmt.Sprintf("cultivation_last_fail:%d", userID)
}
func cultivationRerollKey(userID int64) string { return fmt.Sprintf("cultivation_reroll:%d", userID) }

// insightRerollCost is the Insight XP a seized moment costs: three at the
// mortal realms, two more a realm, forty at most.
func insightRerollCost(realmIndex int64) int64 {
	return minI64(40, 3+2*maxI64(0, realmIndex))
}

// rerollState says whether the moment can be seized at this stage: the last
// breakthrough here failed and no reroll was spent here yet.
func rerollState(conn *storage.Conn, userID, realm, phase int64) (available bool, err error) {
	fail, err := readWorldStateMap(conn, cultivationLastFailKey(userID))
	if err != nil {
		return false, err
	}
	if fail == nil || storage.ParseInt(fail["realm_index"]) != realm || storage.ParseInt(fail["stage"]) != phase {
		return false, nil
	}
	used, err := readWorldStateMap(conn, cultivationRerollKey(userID))
	if err != nil {
		return false, err
	}
	if used != nil && storage.ParseInt(used["realm_index"]) == realm && storage.ParseInt(used["stage"]) == phase {
		return false, nil
	}
	return true, nil
}

// rollOdds is the chance in a hundred of any 2d10 check - every roll map
// carries it, so a result can say what the odds were beside what fell.
func rollOdds(modifier, tn int64) int64 { return breakthroughOdds(modifier, tn) }
func cultivationInsightKey(userID int64) string {
	return fmt.Sprintf("cultivation_insight:%d", userID)
}

func readWorldStateMap(conn *storage.Conn, key string) (map[string]any, error) {
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{key})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return nil, nil
	}
	out := map[string]any{}
	if err := json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &out); err != nil {
		return nil, fmt.Errorf("invalid world_state %s: %w", key, err)
	}
	return out, nil
}

func writeWorldStateMap(conn *storage.Conn, key string, value map[string]any, now float64) error {
	raw, err := json.Marshal(value)
	if err != nil {
		return err
	}
	_, err = conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, []any{key, string(raw), now})
	return err
}

// loadCultivationStance is the stance the player holds; Circulate when none
// was ever chosen or the stored one is no longer a stance.
func loadCultivationStance(conn *storage.Conn, userID int64) (cultivationStanceDefinition, error) {
	state, err := readWorldStateMap(conn, cultivationStanceKey(userID))
	if err != nil {
		return cultivationStanceDefinition{}, err
	}
	if def, ok := stanceDefinition(strings.ToLower(strings.TrimSpace(fmt.Sprint(state["stance"])))); ok {
		return def, nil
	}
	def, _ := stanceDefinition(stanceCirculate)
	return def, nil
}

type cultivationStancePayload struct {
	GameMinute int64  `json:"game_minute"`
	Stance     string `json:"stance"`
}

func cultivationStanceAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p cultivationStancePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation holds no stance")
	}
	def, ok := stanceDefinition(strings.ToLower(strings.TrimSpace(p.Stance)))
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("unknown stance %q: choose circulate, refine or force", p.Stance)
	}
	previous, err := loadCultivationStance(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if err := writeWorldStateMap(conn, cultivationStanceKey(userID), map[string]any{"stance": def.Key, "chosen_game_minute": p.GameMinute}, now); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"stance": def.Key, "label": def.Label, "gain_mult": def.GainMult, "description": def.Description, "previous": previous.Key, "changed": previous.Key != def.Key}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "stance_set", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// insightGateCost is the Insight XP an insight for crossing out of this
// realm costs: five at the mortal gate, five more per realm, sixty at most.
func insightGateCost(realmIndex int64) int64 {
	return minI64(60, 5+5*maxI64(0, realmIndex))
}

func bankedInsight(conn *storage.Conn, userID int64) (bool, int64, error) {
	state, err := readWorldStateMap(conn, cultivationInsightKey(userID))
	if err != nil {
		return false, 0, err
	}
	if state == nil {
		return false, 0, nil
	}
	banked, _ := state["banked"].(bool)
	return banked, storage.ParseInt(state["realm_index"]), nil
}

func readInsightXP(conn *storage.Conn, userID int64) (int64, error) {
	res, err := conn.Execute(`SELECT insight_xp FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) == 0 {
		return 0, errors.New("create a cultivation character first")
	}
	return storage.ParseInt(res.Rows[0][0]), nil
}

// realmGateOpen says whether the qi path may cross out of this realm: a
// completed Realm Perfection opens it, and so does a banked insight.
func realmGateOpen(conn *storage.Conn, userID, realm int64) (open bool, viaInsight bool, err error) {
	perfect, err := completedPerfection(conn, "realm_perfection", userID, realm)
	if err != nil {
		return false, false, err
	}
	if perfect {
		return true, false, nil
	}
	banked, _, err := bankedInsight(conn, userID)
	if err != nil {
		return false, false, err
	}
	return banked, banked, nil
}

func cultivationInsightAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p cultivationActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation gains no insight")
	}
	if c.RealmIndex < 0 || c.RealmIndex >= int64(len(catalog.Realms)) {
		return authoritativeMutation{}, errors.New("realm index out of range")
	}
	if c.RealmIndex+1 >= int64(len(catalog.Realms)) {
		return authoritativeMutation{}, errors.New("there is no realm beyond this one to gain insight into")
	}
	banked, _, err := bankedInsight(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if banked {
		return authoritativeMutation{}, errors.New("an insight is already banked; it is spent when the realm gate is crossed")
	}
	xp, err := readInsightXP(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	cost := insightGateCost(c.RealmIndex)
	if xp < cost {
		return authoritativeMutation{}, fmt.Errorf("an insight into %s costs %d Insight XP; you have %d", realmName(catalog.Realms, c.RealmIndex+1), cost, xp)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`UPDATE characters SET insight_xp=insight_xp-?,updated_at=? WHERE user_id=?`, []any{cost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if err := writeWorldStateMap(conn, cultivationInsightKey(userID), map[string]any{"banked": true, "realm_index": c.RealmIndex, "cost": cost, "banked_game_minute": p.GameMinute}, now); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"banked": true, "cost": cost, "insight_xp": xp - cost, "realm": realmName(catalog.Realms, c.RealmIndex), "next_realm": realmName(catalog.Realms, c.RealmIndex+1), "stage": c.Phase}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "insight_banked", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

// breakthroughModifier is the one place the breakthrough bonus is composed,
// so the odds shown before the roll and the roll itself cannot disagree.
func breakthroughModifier(c mechanicsCharacter, mods resolvedModifiers, body bool, perfectBonus, resonance, innate int64) int64 {
	if body {
		return mods.value(c.Attributes["body"], "body") + maxI64(1, mods.value(c.Attributes["will"], "will")/2) + 2 + perfectBonus + resonance + innate
	}
	return mods.value(c.Attributes["will"], "will") + 2 + perfectBonus + resonance + innate
}

// breakthroughOdds is the chance in a hundred that 2d10 plus the modifier
// reaches the target number: the 100 ordered pairs of two ten-sided dice,
// counted.
func breakthroughOdds(modifier, tn int64) int64 {
	need := tn - modifier
	if need <= 2 {
		return 100
	}
	if need > 20 {
		return 0
	}
	hits := int64(0)
	for a := int64(1); a <= 10; a++ {
		for b := int64(1); b <= 10; b++ {
			if a+b >= need {
				hits++
			}
		}
	}
	return hits
}

// cultivationOddsResult is the breakthrough as it stands before any roll:
// target, modifier, chance, and the movers that make the modifier.
func cultivationOddsResult(c mechanicsCharacter, catalog worlddata.Catalog, mods resolvedModifiers, body bool, perfect bool) map[string]any {
	realms := catalog.Realms
	realm, phase := c.RealmIndex, c.Phase
	if body {
		realms = catalog.BodyRealms
		realm, phase = c.BodyRealmIndex, c.BodyPhase
	}
	if realm < 0 || realm >= int64(len(realms)) {
		return map[string]any{"tn": 0, "modifier": 0, "probability": 0, "movers": []map[string]any{}}
	}
	perfectBonus := int64(0)
	if perfect {
		perfectBonus = 2
	}
	resonance := dualCheckBonus(c)
	innate := int64(math.Round(mods.Add["breakthrough_bonus"]))
	modifier := breakthroughModifier(c, mods, body, perfectBonus, resonance, innate)
	tn := breakthroughTN(realms, realm, phase)
	movers := []map[string]any{}
	if body {
		movers = append(movers, map[string]any{"label": "Body", "value": mods.value(c.Attributes["body"], "body")})
		movers = append(movers, map[string]any{"label": "Will (half)", "value": maxI64(1, mods.value(c.Attributes["will"], "will")/2)})
	} else {
		movers = append(movers, map[string]any{"label": "Will", "value": mods.value(c.Attributes["will"], "will")})
	}
	movers = append(movers, map[string]any{"label": "Base", "value": int64(2)})
	if perfectBonus != 0 {
		movers = append(movers, map[string]any{"label": "Perfect foundation", "value": perfectBonus})
	}
	if resonance != 0 {
		movers = append(movers, map[string]any{"label": "Dual resonance", "value": resonance})
	}
	if innate != 0 {
		movers = append(movers, map[string]any{"label": "Aptitudes & effects", "value": innate})
	}
	return map[string]any{"tn": tn, "modifier": modifier, "probability": breakthroughOdds(modifier, tn), "movers": movers, "stage_nine": phase == 9}
}

// cultivationStatusQuery is the cultivation sheet: realm and stage on both
// paths, the essence bar, the stance, the cooldowns, the odds of the next
// breakthrough and what moves them, today's multipliers, and the realm gate.
func cultivationStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return nil, err
	}
	gameMinute, err := readCanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	if c.RealmIndex < 0 || c.RealmIndex >= int64(len(catalog.Realms)) {
		return nil, errors.New("realm index out of range")
	}
	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return nil, err
	}
	mods, err := loadEffectModifiers(conn, userID, gameMinute, bundle, c, catalog)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	cooldown, err := cooldownRemaining(conn, userID, "cultivate", now)
	if err != nil {
		return nil, err
	}
	bodyCooldown, err := cooldownRemaining(conn, userID, "body_cultivate", now)
	if err != nil {
		return nil, err
	}
	stance, err := loadCultivationStance(conn, userID)
	if err != nil {
		return nil, err
	}
	cost, err := phaseCost(catalog.Realms, c.RealmIndex, c.Phase)
	if err != nil {
		return nil, err
	}
	perfect, err := completedPerfection(conn, "realm_perfection", userID, c.RealmIndex)
	if err != nil {
		return nil, err
	}
	perfectionActive, err := boolRow(conn, `SELECT 1 FROM realm_perfection WHERE user_id=? AND realm_index=? AND active=1 LIMIT 1`, []any{userID, c.RealmIndex})
	if err != nil {
		return nil, err
	}
	banked, _, err := bankedInsight(conn, userID)
	if err != nil {
		return nil, err
	}
	xp, err := readInsightXP(conn, userID)
	if err != nil {
		return nil, err
	}
	nextRealm, nextStage, hasNext := nextStage(catalog.Realms, c.RealmIndex, c.Phase)
	tm := cultivationTimeModifiers(gameMinute, c.SpiritualRoot)
	soulMult, err := soulCultivationMultiplier(conn, userID)
	if err != nil {
		return nil, err
	}
	eraName, eraMult, err := eraCultivationMultiplier(conn)
	if err != nil {
		return nil, err
	}
	manorName, manorMult, err := manorCultivationMultiplier(conn, userID, c.Location)
	if err != nil {
		return nil, err
	}
	storm, err := qiStormBonus(conn, c.Location, now)
	if err != nil {
		return nil, err
	}
	placeName, placeMult, err := placeCultivationMultiplier(conn, catalog, userID, c.Location, gameMinute)
	if err != nil {
		return nil, err
	}
	deviation, err := currentConditionSeverity(conn, userID, "qi_deviation")
	if err != nil {
		return nil, err
	}
	manualName, manualGrade, manualMult, manualChosen, err := manualCultivationMultiplier(conn, catalog, userID)
	if err != nil {
		return nil, err
	}
	qi, err := settleQi(conn, catalog, userID, gameMinute, now)
	if err != nil {
		return nil, err
	}
	rerollAvailable, err := rerollState(conn, userID, c.RealmIndex, c.Phase)
	if err != nil {
		return nil, err
	}
	odds := cultivationOddsResult(c, catalog, mods, false, perfect)
	result := map[string]any{
		"realm_index": c.RealmIndex, "realm": realmName(catalog.Realms, c.RealmIndex), "stage": c.Phase, "world": realmWorld(catalog.Realms, c.RealmIndex),
		"cultivation": c.Cultivation, "cost": cost, "ready": c.Cultivation >= cost,
		"next_realm": realmName(catalog.Realms, nextRealm), "next_stage": nextStage, "ceiling": !hasNext,
		"realm_gate": hasNext && nextRealm != c.RealmIndex, "gate_open": perfect || banked,
		"insight_banked": banked, "insight_cost": insightGateCost(c.RealmIndex), "insight_xp": xp,
		"perfection_completed": perfect, "perfection_active": perfectionActive,
		"stance": stance.Key, "stance_label": stance.Label, "stance_mult": stance.GainMult, "stance_description": stance.Description,
		"cooldown_remaining": cooldown, "body_cooldown_remaining": bodyCooldown,
		"odds": odds, "dual_resonance": dualResonance(c),
		"period": tm.Period, "season": tm.Season, "time_mult": tm.QiMult, "body_time_mult": tm.BodyMult, "root_resonance": tm.RootResonance,
		"effect_mult": mulOrOne(mods, "cultivation_gain"), "soul_mult": soulMult, "era_name": eraName, "era_mult": eraMult,
		"manor_name": manorName, "manor_mult": manorMult, "storm_bonus": storm,
		"insight_xp_per_refine": refineInsightXPPerSession, "force_deviation_percent": forceDeviationChancePercent,
		"place_name": placeName, "place_mult": placeMult, "place_quality": placeQuality(placeMult),
		"pace": stagePace(cost, c.RealmIndex), "sessions_per_stage": sessionsForStage(c.RealmIndex),
		"world_mult":  worldQiMultiplier(catalog, realmWorld(catalog.Realms, c.RealmIndex)),
		"manual_name": manualName, "manual_grade": manualGrade, "manual_mult": manualMult, "manual_chosen": manualChosen,
		"qi": qi.Qi, "qi_max": qi.Capacity, "qi_regen": qi.Regen, "purity": qi.Body.Purity,
		"purity_ceiling": purityCeilingFor(catalog, c.RealmIndex, manualGrade, qi.Body), "skill_cost_mult": qi.Body.skillCostMultiplier(),
		"meridians_open": qi.Body.MeridiansOpen, "meridians_damaged": qi.Body.MeridiansDamaged,
		"meridian_ceiling": int64(meridianCeiling), "dantian_state": qi.Body.DantianState,
		"breakthrough_qi_cost": maxI64(1, qi.Capacity/breakthroughQiShare),
		"deviation_severity":   deviation, "attribute_quality": round4(attributeQuality(mods.value(c.Attributes["will"], "will"))),
		"reroll_available": rerollAvailable, "reroll_cost": insightRerollCost(c.RealmIndex), "law_insight_cost": lawInsightSpendCost,
	}
	if c.BodyRealmIndex >= 0 && c.BodyRealmIndex < int64(len(catalog.BodyRealms)) {
		bodyCost, err := phaseCost(catalog.BodyRealms, c.BodyRealmIndex, c.BodyPhase)
		if err != nil {
			return nil, err
		}
		bodyPerfect, err := completedPerfection(conn, "body_realm_perfection", userID, c.BodyRealmIndex)
		if err != nil {
			return nil, err
		}
		result["body_realm"] = realmName(catalog.BodyRealms, c.BodyRealmIndex)
		result["body_stage"] = c.BodyPhase
		result["body_cultivation"] = c.BodyCultivation
		result["body_cost"] = bodyCost
		result["body_ready"] = c.BodyCultivation >= bodyCost
		result["body_odds"] = cultivationOddsResult(c, catalog, mods, true, bodyPerfect)
	}
	return result, nil
}

// applyStanceToTraining is the stance's hand on one training session: the
// gain multiplier, the Insight XP a Refine session banks, and the deviation
// a Force session risks. The deviation is a real condition, treated like any
// other (Character → Treatment).
func applyStanceToTraining(conn *storage.Conn, userID int64, stance cultivationStanceDefinition, gameMinute int64, now float64) (insightXP int64, deviation map[string]any, err error) {
	switch stance.Key {
	case stanceRefine:
		if _, err := conn.Execute(`UPDATE characters SET insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`, []any{int64(refineInsightXPPerSession), now, userID}); err != nil {
			return 0, nil, err
		}
		return refineInsightXPPerSession, nil, nil
	case stanceForce:
		roll, err := gamerng.Intn(100)
		if err != nil {
			return 0, nil, err
		}
		if roll < forceDeviationChancePercent {
			// The deviation deepens each time it is taken untreated
			// (v1.0.0-rc.5). Held at severity 1 it was a rounding error and
			// Force was strictly the best stance; at 5 it halves what a
			// session gathers, so forcing is a sprint that has to be paid for.
			held, err := currentConditionSeverity(conn, userID, "qi_deviation")
			if err != nil {
				return 0, nil, err
			}
			dev, err := applyCombatCondition(conn, userID, "qi_deviation", minI64(5, held+1), "cultivation", "force_stance", gameMinute)
			if err != nil {
				return 0, nil, err
			}
			return 0, dev, nil
		}
	}
	return 0, nil, nil
}
