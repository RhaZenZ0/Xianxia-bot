package game

// The road's pace (v1.2.0).
//
// Player feedback: "instant travels ... I don't have to wait half hour". A road
// journey settled a `road_transit:<uid>` row and refused every mutation until
// the arrival minute, and the length of that wait was the road's own
// `travel_minutes` - a formula in game minutes that at the shipped time scale
// came to a real half hour between neighbouring towns. The arrays and the
// realm hubs were already instant; the road was the one door that made a
// player wait.
//
// TRAVEL_TIME_PERCENT is the share of the road's length a traveller actually
// waits, 0-100, default 0. The road's length is still computed and still
// reported (`travel_minutes` is the cost's basis, and the harness reads it to
// advance the clock past an arrival); what the setting scales is the *wait*,
// `wait_minutes`, which is what the transit row and `traveling` key off. An
// operator who wants the old pace sets 100. Encounters resolve before the wait
// is computed, so a road is still dangerous at any pace - the setting is
// about waiting, not about risk.
//
// It is read the way `clockScaleFromEnv` reads WORLD_TIME_SCALE: the `.env`
// baseline, passed through compose's explicit `environment:` allowlist (a key
// compose is not given is a key the engine cannot read - rc.39), and an
// unreadable or out-of-range value is the default rather than an error,
// because a misspelled setting must not make every road refuse.

import (
	"os"
	"strconv"
	"strings"
)

const (
	travelTimePercentKey     = "TRAVEL_TIME_PERCENT"
	travelTimePercentDefault = int64(0)
)

// travelTimePercentFromEnv is the share of a road's length a traveller waits.
func travelTimePercentFromEnv() int64 {
	raw := strings.TrimSpace(os.Getenv(travelTimePercentKey))
	if raw == "" {
		return travelTimePercentDefault
	}
	percent, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || percent < 0 || percent > 100 {
		return travelTimePercentDefault
	}
	return percent
}

// scaledTravelWait is the wait a road of this length costs at the configured
// pace. A road with no length waits nothing at any pace, and a pace of 0 waits
// nothing on any road.
func scaledTravelWait(travelMinutes int64) int64 {
	if travelMinutes <= 0 {
		return 0
	}
	return travelMinutes * travelTimePercentFromEnv() / 100
}
