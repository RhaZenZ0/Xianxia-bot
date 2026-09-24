package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Closed-door seclusion (v0.30.0, Authority II). Until this release the
// Discord handler computed the environment multiplier - abode chamber level,
// safe zone, sect manor array, deployed formation - and sent it in the
// payload for the engine to clamp. The engine holds every one of those facts,
// so it derives the multiplier itself here, and a payload that still carries
// one is refused rather than trusted.

const (
	seclusionMultFloor   = 0.5
	seclusionMultCeiling = 1.75
	manorFacilityMaxGo   = 5
)

// seclusionEnvironmentGo derives the multiplier and the parts it was built
// from, or refuses when the location offers no protected site.
func seclusionEnvironmentGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, location, mode string, gameMinute int64) (map[string]any, float64, error) {
	env := map[string]any{
		"site": "", "abode_name": "", "abode_property_type": "", "abode_level": int64(0),
		"safe_zone": false, "manor_name": "", "manor_level": int64(0), "manor_mult": 1.0,
		"array_name": "", "array_mult": 1.0, "base_mult": 1.0, "abode_array_mult": 1.0,
	}
	r, e := conn.Execute(`SELECT name,property_type,cultivation_level,formation_level FROM cave_abodes WHERE location_key=?`, []any{location})
	if e != nil {
		return nil, 0, e
	}
	abode := firstRowMap(r)
	// The residence a sect assigns (v0.30.1) is a site too: its chamber
	// counts like a homestead's, and the sect manor's array reaches it
	// because the residence stands inside the sect, at the manor's seat.
	residenceBase := location
	var sectAbode map[string]any
	if abode == nil && strings.HasPrefix(location, "sect_abode:") {
		r, e = conn.Execute(`SELECT name,base_location,cultivation_level,formation_level FROM sect_abodes WHERE location_key=? AND user_id=?`, []any{location, userID})
		if e != nil {
			return nil, 0, e
		}
		if sectAbode = firstRowMap(r); sectAbode != nil {
			residenceBase = fmt.Sprint(sectAbode["base_location"])
		}
	}
	safe := false
	if def, ok := catalog.Locations[location]; ok {
		safe = def.SafeZone
	}
	env["safe_zone"] = safe
	r, e = conn.Execute(`SELECT m.name,m.base_location,m.qi_array_level FROM sect_membership sm JOIN sect_manors m ON m.sect_name=sm.sect_name WHERE sm.user_id=?`, []any{userID})
	if e != nil {
		return nil, 0, e
	}
	manor := firstRowMap(r)
	if manor != nil && fmt.Sprint(manor["base_location"]) != residenceBase {
		manor = nil
	}
	// The birth household (v1.0.0-rc.32): the family's walls are a protected
	// site, worth what the hearth is worth to active cultivation.
	hearth, hearthMult, e := birthFamilyCultivationMultiplier(conn, location)
	if e != nil {
		return nil, 0, e
	}
	if abode == nil && sectAbode == nil && !safe && manor == nil && hearth == "" {
		return nil, 0, errors.New("closed-door seclusion requires a protected/safe location, a residence with a cultivation chamber, your sect's manor, or your birth household")
	}
	base := 0.85
	switch {
	case hearth != "":
		base = hearthMult
		env["site"] = "household"
		env["abode_name"] = hearth
	case abode != nil:
		level := max64(0, i64(abode["cultivation_level"]))
		base = math.Min(1.45, 1.05+0.05*float64(level))
		env["site"] = "abode"
		env["abode_name"] = fmt.Sprint(abode["name"])
		env["abode_property_type"] = fmt.Sprint(abode["property_type"])
		env["abode_level"] = level
	case sectAbode != nil:
		level := max64(0, i64(sectAbode["cultivation_level"]))
		base = math.Min(1.45, 1.05+0.05*float64(level))
		env["site"] = "sect_abode"
		env["abode_name"] = fmt.Sprint(sectAbode["name"])
		env["abode_level"] = level
	case safe:
		base = 1.0
		env["site"] = "safe_zone"
	}
	// The property's own spirit-gathering array (v1.0.0-rc.6), raised through
	// the `formation` facility - the same array active meditation reads.
	abodeArray := 1.0
	switch {
	case abode != nil:
		abodeArray = abodeArrayMultiplier(abode)
	case sectAbode != nil:
		abodeArray = abodeArrayMultiplier(sectAbode)
	}
	env["abode_array_mult"] = abodeArray
	env["base_mult"] = base
	mult := base * abodeArray
	// One split for both doors (v1.2.3): the ground - the site and the
	// abode's own array - counts for both paths, and a qi-gathering array,
	// the sect manor's or one somebody deployed here, is qi-path weather.
	// Until v1.2.3 this door applied the manor to a body retreat while a
	// hand-sat body session did not, and that session applied a deployed
	// array while a body retreat did not.
	if manor != nil && mode == "qi" {
		level := clamp(i64(manor["qi_array_level"]), 0, manorFacilityMaxGo)
		manorMult := 1.0 + 0.08*float64(level)
		mult *= manorMult
		env["manor_name"] = fmt.Sprint(manor["name"])
		env["manor_level"] = level
		env["manor_mult"] = manorMult
		if env["site"] == "" {
			env["site"] = "manor"
		}
	}
	if mode == "qi" {
		arrayName, arrayMult, e := deployedArrayMultiplier(conn, location, gameMinute)
		if e != nil {
			return nil, 0, e
		}
		if arrayName != "" {
			mult *= arrayMult
			env["array_name"] = arrayName
			env["array_mult"] = arrayMult
		}
	}
	mult = math.Max(seclusionMultFloor, math.Min(seclusionMultCeiling, mult))
	env["environment_mult"] = mult
	return env, mult, nil
}

// multiplicativeEffectJSONStat is the product of every `mul` modifier on a
// stat in an effect payload - the multiplicative counterpart of
// additiveEffectJSONStat. An unreadable payload multiplies by one.
func multiplicativeEffectJSONStat(raw, stat string) float64 {
	var payload struct {
		Modifiers []struct {
			Stat      string  `json:"stat"`
			Operation string  `json:"operation"`
			Value     float64 `json:"value"`
		} `json:"modifiers"`
	}
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return 1
	}
	product := 1.0
	for _, modifier := range payload.Modifiers {
		if strings.TrimSpace(modifier.Stat) != stat {
			continue
		}
		if strings.ToLower(strings.TrimSpace(modifier.Operation)) == "mul" {
			product *= modifier.Value
		}
	}
	return product
}

// seclusionDailyGainGo is the one copy of the background-cultivation rate,
// quoted per game day: `seclusion.settle` spends it per completed game hour
// through seclusionGainForSpan and `seclusion.start` reports it as the
// projection. It is *faster* than active cultivation since v1.0.0-rc.56 - the
// doors are shut, a secluded cultivator can do nothing else, and that is the
// trade. It used to call itself "slower on purpose ... it runs while the
// player is offline and asks nothing of them", which was true while the
// lockout was half a gate and stopped being true when it became one.
//
// Since v1.0.0-rc.5 it is a share of the stage being filled, like a hand-sat
// session, rather than a flat number off the character sheet: the old rate
// paid about ten essence a day at every realm, which was a day's work at Body
// Tempering and a rounding error at Nascent Soul.
// carriedMult (v1.0.0-rc.55) is everything that holds for the whole retreat -
// active effects, the era, the method practised and what the root makes of its
// qi, and what the root itself is worth. This function called itself "the one
// copy of the background-cultivation rate" while applying six of the ten terms
// a hand-sat session applies, so a cultivator gathered at one rate sitting down
// and another behind a closed door, and nothing said which was right.
//
// What is still left out is left out on a rule: a retreat carries what holds
// for its whole length. The hour of the day averages out across days, a qi
// storm is momentary, and seclusion has no stance. The manor array stays out
// because environmentMult is already this function's statement of where the
// cultivator sat, and stacking the manor on top would price the site twice.
//
// `scale` is the world clock's rate, which is not a multiplier on the gain
// but the thing that decides how much wall-clock a game day is - see
// seclusionSessionsPerGameDay, which is why the rate is a share at last.
func seclusionDailyGainGo(catalog worlddata.Catalog, character map[string]any, mode string, environmentMult, soulMult, carriedMult float64, scale int64) int64 {
	pace, worldMult := characterStagePace(catalog, character, mode)
	attrs := decodeJSONMap(character["attributes_json"])
	attribute := i64(attrs["will"])
	if mode == "body" {
		attribute = i64(attrs["body"])
	}
	env := math.Max(seclusionMultFloor, math.Min(seclusionMultCeiling, environmentMult))
	if carriedMult <= 0 {
		carriedMult = 1
	}
	daily := float64(pace) * seclusionSessionsPerGameDay(scale) * attributeQuality(attribute) * env * soulMult * worldMult * carriedMult
	return max64(1, int64(math.Round(daily)))
}

// seclusionSessionsPerGameDay is how many hand-sat sessions a game day behind
// a closed door is worth, and it is the whole of what v1.0.0-rc.56 changed
// about the rate. Active play fits one session per cultivate cooldown of real
// time; a game day is `gameMinutesPerDay / scale` real minutes; so the count
// follows from those two and the share, and the share therefore holds at
// every world time scale rather than at the one it was measured on.
func seclusionSessionsPerGameDay(scale int64) float64 {
	rate := float64(scale)
	if rate <= 0 {
		// A stopped world clock is a supported state, and with one no game
		// minute ever passes - so a retreat measured in them never advances
		// and this rate is never actually spent. It is still read, by the
		// projection `seclusion.start` prints, and dividing by zero there
		// would print an infinity. The world's own baseline rate answers
		// instead, which is the number the projection would have shown the
		// moment the clock was started again.
		rate = float64(fallbackClockScale)
	}
	cooldownMinutes := float64(cooldownSecondsFor(cooldownCultivate)) / 60
	if cooldownMinutes < 1 {
		cooldownMinutes = 1
	}
	realMinutesInAGameDay := float64(gameMinutesPerDay) / rate
	return seclusionShareOfActive * realMinutesInAGameDay / cooldownMinutes
}

// How long a retreat may last, and the unit it is paid in (v1.0.0-rc.56).
//
// `duration_game_minutes` was floored at 1 and bounded by nothing. The only
// limit in the game was `days: Range[int, 1, 365]` on the slash command - and
// a bound that lives in the client is not a bound, the same fault the action
// cooldowns had in fourteen places. Any other caller could seclude for a
// millennium, which is also a lockout of a millennium once the doors are
// actually shut.
const (
	// Two real hours. A retreat is a session of play a cultivator commits to,
	// not a way to be absent from the game for a week.
	seclusionMaxRealMinutes = int64(120)
	// And it is paid per completed game hour. The day it replaces cannot
	// work: two real hours at the shipped time scale is 480 game minutes, a
	// third of a day, so a retreat run to its cap would have paid nothing at
	// all under whole-day accounting.
	seclusionSettleUnitGameMinutes = int64(60)
)

// seclusionRealMinutes is how long the caller asked for, in real minutes,
// refused rather than clamped when it is over the cap.
//
// House style splits on intent: a number where landing near it is fine is
// clamped, and one where the player must know they got something else is a
// sentence. A player who asked for a year and was silently given two hours
// would be told twice over that they had what they asked for.
func seclusionRealMinutes(p seclusionStartPayload, clock canonicalWorldClock) (int64, error) {
	minutes := p.DurationRealMinutes
	if minutes <= 0 && p.DurationGameMinutes > 0 {
		// A client from before this release asks in game minutes. Converting
		// at the world's rate is what makes the refusal below honest for it
		// too: ten world-days at the shipped scale really is sixty real
		// hours, and it should be told the cap rather than handed two hours
		// it did not ask for.
		scale := clock.Scale
		if scale <= 0 {
			scale = fallbackClockScale
		}
		minutes = (p.DurationGameMinutes + scale - 1) / scale
	}
	if minutes <= 0 {
		// Nothing asked for is the longest allowed: every door in the game
		// sends a length, so a caller that sends none wants the retreat, not
		// a retreat that ends the instant it begins (which is what the old
		// floor of one minute gave them).
		minutes = seclusionMaxRealMinutes
	}
	if minutes > seclusionMaxRealMinutes {
		return 0, fmt.Errorf("a retreat lasts at most %d hours; you asked for %.1f. Seclusion pays %.0f%% of what the same time spent cultivating by hand would",
			seclusionMaxRealMinutes/60, float64(minutes)/60, seclusionShareOfActive*100)
	}
	return minutes, nil
}

// seclusionRealDeadline reads the stored real deadline. A NULL - a retreat
// started before schema 57 - is not a deadline of zero: it means this retreat
// keeps the game-minute end it was given, because a new rule must never
// shorten something a player already committed to.
func seclusionRealDeadline(session map[string]any) (float64, bool) {
	raw, present := session["ends_real_ts"]
	if !present || raw == nil {
		return 0, false
	}
	ts, err := strconvFloat(raw)
	if err != nil || ts <= 0 {
		return 0, false
	}
	return ts, true
}

// seclusionGainForSpan turns the daily rate into what a span of game minutes
// is worth. One statement, so the projection the start prints and the payment
// the settle makes cannot answer differently - the fault this release removed
// from the rate itself.
func seclusionGainForSpan(dailyGain, gameMinutes int64) int64 {
	if dailyGain <= 0 || gameMinutes <= 0 {
		return 0
	}
	return int64(math.Round(float64(dailyGain) * float64(gameMinutes) / float64(gameMinutesPerDay)))
}
