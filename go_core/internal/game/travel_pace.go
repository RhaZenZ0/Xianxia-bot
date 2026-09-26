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
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"xianxia/core/internal/storage"
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

// travelTimePercentTx is the pace the world is set to: the GM's stored choice
// (world_state['travel_pace'], v1.7.3) when there is one, else the .env
// baseline - the rule WORLD_TIME_SCALE and the narration chain already follow.
// An unreadable row is the baseline, never an error: a road must not refuse
// because a setting is damaged.
func travelTimePercentTx(conn *storage.Conn) int64 {
	if conn != nil {
		if res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='travel_pace'`, nil); err == nil {
			if row := firstRowMap(res); row != nil {
				var stored struct {
					Percent *int64 `json:"percent"`
				}
				if text, ok := row["value_json"].(string); ok && json.Unmarshal([]byte(text), &stored) == nil &&
					stored.Percent != nil && *stored.Percent >= 0 && *stored.Percent <= 100 {
					return *stored.Percent
				}
			}
		}
	}
	return travelTimePercentFromEnv()
}

// adminWorldSetTravelPace stores the share of a road's length a traveller
// waits, 0-100, audited. It moves no journey already under way: a transit row
// carries its own arrival minute.
func adminWorldSetTravelPace(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	value, ok := p["percent"]
	if !ok {
		return nil, errors.New("percent is required")
	}
	percent := storage.ParseInt(value)
	if percent < 0 || percent > 100 {
		return nil, errors.New("percent must be between 0 and 100")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	before := travelTimePercentTx(conn)
	encoded, _ := json.Marshal(map[string]any{"percent": percent})
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES('travel_pace',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, []any{string(encoded), now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.world.set_travel_pace", "travel_pace", map[string]any{"percent": before}, map[string]any{"percent": percent}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"percent": percent, "previous": before}, nil
}

// scaledTravelWait is the wait a road of this length costs at the configured
// pace. A road with no length waits nothing at any pace, and a pace of 0 waits
// nothing on any road.
func scaledTravelWait(conn *storage.Conn, travelMinutes int64) int64 {
	if travelMinutes <= 0 {
		return 0
	}
	return travelMinutes * travelTimePercentTx(conn) / 100
}
