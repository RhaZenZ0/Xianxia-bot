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
	seclusionDailyShare  = 0.60 // around 60% of an active cultivation day
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
	if abode == nil && sectAbode == nil && !safe && manor == nil {
		return nil, 0, errors.New("closed-door seclusion requires a protected/safe location, a residence with a cultivation chamber, or your sect's manor")
	}
	base := 0.85
	switch {
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
	if manor != nil {
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

// seclusionDailyGainGo is the one copy of the background-cultivation rate:
// seclusion.settle pays it per completed day and seclusion.start reports it
// as the projection. Slower than active cultivation on purpose - it runs
// while the player is offline and asks nothing of them.
//
// Since v1.0.0-rc.5 it is a share of the stage being filled, like a hand-sat
// session, rather than a flat number off the character sheet: the old rate
// paid about ten essence a day at every realm, which was a day's work at Body
// Tempering and a rounding error at Nascent Soul.
func seclusionDailyGainGo(catalog worlddata.Catalog, character map[string]any, mode string, environmentMult, soulMult float64) int64 {
	pace, worldMult := characterStagePace(catalog, character, mode)
	attrs := decodeJSONMap(character["attributes_json"])
	attribute := i64(attrs["will"])
	if mode == "body" {
		attribute = i64(attrs["body"])
	}
	env := math.Max(seclusionMultFloor, math.Min(seclusionMultCeiling, environmentMult))
	daily := float64(pace) * seclusionSessionsPerDay * attributeQuality(attribute) * env * soulMult * worldMult
	return max64(1, int64(math.Round(daily*seclusionDailyShare/0.6)))
}
