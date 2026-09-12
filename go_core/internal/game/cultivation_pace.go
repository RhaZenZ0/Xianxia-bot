package game

import (
	"encoding/json"
	"fmt"
	"math"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Pacing (v1.0.0-rc.5). Until this release a meditation session was worth a
// flat `8 + d7 + attribute`, about fourteen essence, at every realm - while a
// realm's cost grew by about three fifths each time and a character's
// attributes never changed after creation. Body Tempering took 88 sessions
// and Divine Transformation 3,626, so the game ended at the second realm.
//
// A session is now worth a share of the stage it is filling. The stage's cost
// divided by cultivationSessionsPerStage is the pace; attributes, the stance,
// the hour, the ground and the world multiply it. Every realm therefore takes
// about the same number of sessions, the operator's cooldown sets the calendar,
// and every multiplier in the chain keeps its meaning at every realm instead
// of rounding away to nothing.

const (
	// A stage takes about this many sessions at the first realm, before any
	// multiplier, and a quarter-session more with each realm above it
	// (v1.0.0-rc.6). A flat twelve made every realm identical work; the climb
	// should tighten as a cultivator rises, and the qi of a new world should
	// be the relief that makes the next ladder climbable.
	cultivationSessionsPerStage = 8
	// Added to the sessions a stage takes, per realm: five quarters.
	cultivationSessionsPerRealmNumerator   = 5
	cultivationSessionsPerRealmDenominator = 4
	// A day of closed-door cultivation is worth this many sessions, before the
	// environment multiplier - less than sitting down for them by hand.
	seclusionSessionsPerDay = 1.2
	// The floor under a session, so the first stages are never a trickle.
	cultivationPaceFloor = 8
	// An attribute point is worth this much of a session.
	cultivationAttributeShare = 20.0
)

// sessionsForStage is how many sessions a stage of this realm is meant to
// take before the cultivator's own multipliers: eight at Body Tempering,
// rising by five quarters a realm.
func sessionsForStage(realmIndex int64) int64 {
	realm := maxI64(0, realmIndex)
	return cultivationSessionsPerStage + realm*cultivationSessionsPerRealmNumerator/cultivationSessionsPerRealmDenominator
}

// stagePace is what one session of this stage is worth before multipliers.
func stagePace(cost, realmIndex int64) int64 {
	return maxI64(cultivationPaceFloor, cost/sessionsForStage(realmIndex))
}

// worldQiMultiplier is the qi density of a world (v1.0.0-rc.5): the higher
// worlds are thick with it, which is the reward for crossing into one. The
// densities are content (`world_qi_density`); an unlisted world is 1.0.
func worldQiMultiplier(catalog worlddata.Catalog, world string) float64 {
	if v, ok := catalog.WorldQiDensity[world]; ok && v > 0 {
		return round4(v)
	}
	return 1.0
}

// attributeQuality turns an attribute into the multiplier it is worth: will 3
// is 1.15, will 12 is 1.60. It never falls below a third of a session, so a
// crippling debuff slows a cultivator without stopping them dead.
func attributeQuality(value int64) float64 {
	return math.Max(0.35, 1.0+float64(value)/cultivationAttributeShare)
}

// deployedArrayMultiplier is the one reading of a location's deployed array:
// active meditation and closed-door seclusion asked the same question with two
// copies of the same query before v1.0.0-rc.5.
func deployedArrayMultiplier(conn *storage.Conn, location string, gameMinute int64) (string, float64, error) {
	if conn == nil || location == "" {
		return "", 1, nil
	}
	res, err := conn.Execute(
		`SELECT name,effect_json FROM deployed_location_arrays
		 WHERE location=? AND starts_game_minute<=? AND ends_game_minute>?
		 ORDER BY starts_game_minute DESC LIMIT 1`,
		[]any{location, gameMinute, gameMinute},
	)
	if err != nil {
		return "", 1, err
	}
	array := firstRowMap(res)
	if array == nil {
		return "", 1, nil
	}
	mult := multiplicativeEffectJSONStat(fmt.Sprint(array["effect_json"]), "cultivation_gain")
	if mult <= 0 {
		mult = 1
	}
	return fmt.Sprint(array["name"]), round4(mult), nil
}

// characterStagePace is the pace and the world multiplier for a character row
// as the seclusion actions hold it (a `SELECT *` map, not mechanicsCharacter).
func characterStagePace(catalog worlddata.Catalog, character map[string]any, mode string) (int64, float64) {
	realms, index, phase := catalog.Realms, i64(character["realm_index"]), i64(character["phase"])
	if mode == "body" {
		realms, index, phase = catalog.BodyRealms, i64(character["body_realm_index"]), i64(character["body_phase"])
	}
	cost, err := phaseCost(realms, index, phase)
	if err != nil {
		return cultivationPaceFloor, 1
	}
	return stagePace(cost, index), worldQiMultiplier(catalog, realmWorld(realms, index))
}

// pathPrimaryAttribute is the attribute a path is built on - its highest
// starting value, with a fixed order breaking ties so the same path always
// grows the same way.
func pathPrimaryAttribute(catalog worlddata.Catalog, path string) string {
	definition, ok := catalog.Paths[path]
	if !ok {
		return "will"
	}
	best, bestValue := "will", -1
	for _, pair := range []struct {
		name  string
		value int
	}{
		{"body", definition.Body}, {"agility", definition.Agility}, {"spirit", definition.Spirit},
		{"insight", definition.Insight}, {"will", definition.Will}, {"presence", definition.Presence},
	} {
		if pair.value > bestValue {
			best, bestValue = pair.name, pair.value
		}
	}
	return best
}

// growAttributesOnRealmCrossing raises a cultivator's attributes when they
// enter a new realm (v1.0.0-rc.5). Before this, `attributes_json` was written
// exactly once in a character's life, at creation, and only reincarnation ever
// touched it again: a Nascent Soul cultivator rolled the same will as the
// beggar they started as. Crossing a realm is what makes them stronger.
func growAttributesOnRealmCrossing(conn *storage.Conn, catalog worlddata.Catalog, userID int64, c mechanicsCharacter, body bool, now float64) (map[string]any, error) {
	gains := map[string]int64{}
	if body {
		gains["body"] = 1
	} else {
		gains["will"] = 1
	}
	if primary := pathPrimaryAttribute(catalog, c.Path); primary != "" {
		gains[primary] += 1
	}
	attributes := map[string]int64{}
	for name, value := range c.Attributes {
		attributes[name] = value
	}
	for name, delta := range gains {
		attributes[name] += delta
	}
	encoded, err := json.Marshal(attributes)
	if err != nil {
		return nil, err
	}
	if _, err := conn.Execute(`UPDATE characters SET attributes_json=?,updated_at=? WHERE user_id=?`, []any{string(encoded), now, userID}); err != nil {
		return nil, err
	}
	out := map[string]any{}
	for name, delta := range gains {
		out[name] = delta
	}
	return out, nil
}

// currentConditionSeverity is the severity of a condition a character already
// carries, or zero.
func currentConditionSeverity(conn *storage.Conn, userID int64, key string) (int64, error) {
	res, err := conn.Execute(`SELECT severity FROM character_conditions WHERE user_id=? AND condition_key=? AND state='active' ORDER BY severity DESC LIMIT 1`, []any{userID, key})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0, nil
	}
	return storage.ParseInt(res.Rows[0][0]), nil
}
