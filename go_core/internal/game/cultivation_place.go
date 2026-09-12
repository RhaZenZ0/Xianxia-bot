package game

import (
	"fmt"
	"math"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The place matters (v1.0.0-rc.4): where a cultivator sits changes what a
// session gathers, and the result says so. The sect manor's array is its own
// multiplier already (manorCultivationMultiplier); this is everything else
// the engine knows about the ground - a road-side shrine, a city's temple
// quarter, a sect gate, a residence with a chamber, a deployed array.

const (
	placeShrineMult   = 1.15
	placeTempleMult   = 1.10
	placeSectGateMult = 1.10
	placeMultCeiling  = 1.75
	// The spirit-gathering array a player raises in their own property
	// (v1.0.0-rc.6): the `formation` facility, six percent a level, nine
	// levels, so a finished array is worth half again.
	abodeArrayPerLevel = 0.06
)

// placeCultivationMultiplier is the ground's name and its multiplier, or
// an empty name and one where the ground is nothing in particular.
func placeCultivationMultiplier(conn *storage.Conn, catalog worlddata.Catalog, userID int64, location string, gameMinute int64) (string, float64, error) {
	name := ""
	mult := 1.0
	if conn == nil {
		return name, mult, nil
	}
	if def, ok := catalog.Locations[location]; ok {
		switch {
		case def.RoadSite == "shrine":
			name, mult = "the shrine", placeShrineMult
		case def.District == "temple":
			name, mult = "the temple quarter", placeTempleMult
		}
	}
	if name == "" {
		for sectName, sect := range catalog.Sects {
			if sect.Recruitment.Location != "" && sect.Recruitment.Location == location {
				name, mult = "the gate of the "+sectName, placeSectGateMult
				break
			}
		}
	}
	if name == "" {
		r, err := conn.Execute(`SELECT name,cultivation_level,formation_level FROM cave_abodes WHERE location_key=?`, []any{location})
		if err != nil {
			return "", 1, err
		}
		home := firstRowMap(r)
		if home == nil {
			r, err = conn.Execute(`SELECT name,cultivation_level,formation_level FROM sect_abodes WHERE location_key=? AND user_id=?`, []any{location, userID})
			if err != nil {
				return "", 1, err
			}
			home = firstRowMap(r)
		}
		if home != nil {
			level := max64(0, i64(home["cultivation_level"]))
			name, mult = fmt.Sprint(home["name"]), math.Min(1.45, 1.05+0.05*float64(level))
			if array := abodeArrayMultiplier(home); array > 1 {
				mult *= array
				name += " and its gathering array"
			}
		}
	}
	arrayName, arrayMult, err := deployedArrayMultiplier(conn, location, gameMinute)
	if err != nil {
		return "", 1, err
	}
	if arrayMult != 1 {
		mult *= arrayMult
		if name == "" {
			name = arrayName
		} else {
			name += " under " + arrayName
		}
	}
	mult = math.Min(placeMultCeiling, math.Max(0.5, mult))
	return name, round4(mult), nil
}

// abodeArrayMultiplier is what the spirit-gathering array a player raised in
// their own home or sect residence is worth (v1.0.0-rc.6): the `formation`
// facility, which until now only let them inscribe formations.
func abodeArrayMultiplier(home map[string]any) float64 {
	if home == nil {
		return 1
	}
	level := clampI64(i64(home["formation_level"]), 0, 9)
	if level <= 0 {
		return 1
	}
	return round4(1 + abodeArrayPerLevel*float64(level))
}

// placeQuality is the word the Here line and the sheet use for the ground.
func placeQuality(mult float64) string {
	switch {
	case mult >= 1.25:
		return "rich"
	case mult > 1.0:
		return "good"
	case mult < 1.0:
		return "thin"
	}
	return "ordinary"
}
