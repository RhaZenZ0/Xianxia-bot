package game

import (
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Where a catalogue NPC is standing (v1.3.1), the engine's own answer.
//
// Until now this question was Python's alone (`current_npc_location` in
// `app/bot/locations.py`): the circuit a wandering master walks, the
// civilization simulation's row, and the daily schedule the content gives a
// person while they are at home. The engine read only the simulation row, so
// the one bound that turned on it - a catalogue sponsor must be standing where
// the applicant is - lived in the client, which rc.48 says is not a bound.
// This is that rule in Go, in the same order of precedence, so the two answers
// cannot disagree about who is in the room.

const defaultCircuitMonths int64 = 2

// periodForHour is the one statement of the five periods of a world day.
func periodForHour(hour int64) string {
	hour = ((hour % 24) + 24) % 24
	switch {
	case hour >= 5 && hour < 7:
		return "Dawn"
	case hour >= 7 && hour < 12:
		return "Morning"
	case hour >= 12 && hour < 17:
		return "Afternoon"
	case hour >= 17 && hour < 20:
		return "Evening"
	}
	return "Night"
}

func periodForGameMinute(gameMinute int64) string {
	if gameMinute < 0 {
		gameMinute = 0
	}
	return periodForHour((gameMinute / 60) % 24)
}

// circuitStop is where a wandering hidden master stands at `gameMinute`: a
// pure function of the clock, each stop held for `months` world-months and
// `offset` staggering masters who share a road. The Python twin is
// `circuit_stop` in `app/rules/sense.py`.
func circuitStop(circuit []string, gameMinute, months, offset int64) string {
	stops := make([]string, 0, len(circuit))
	for _, stop := range circuit {
		if strings.TrimSpace(stop) != "" {
			stops = append(stops, stop)
		}
	}
	if len(stops) == 0 {
		return ""
	}
	if months < 1 {
		months = 1
	}
	span := months * (minutesPerYear / 12)
	if gameMinute < 0 {
		gameMinute = 0
	}
	elapsed := gameMinute / span
	idx := (elapsed + offset) % int64(len(stops))
	if idx < 0 {
		idx += int64(len(stops))
	}
	return stops[idx]
}

// npcWhereabouts is the answer: Location is where they stand, Dead says the
// simulation has buried them, and Known is false when nothing in the world
// says where they are - a caller reads that as "do not filter by location",
// which is why the registry and a running event's cast both answer rather
// than fall through.
type npcWhereabouts struct {
	Location string
	Dead     bool
	Known    bool
}

func npcWhereaboutsTx(conn *storage.Conn, catalog worlddata.Catalog, name string, gameMinute int64, now float64) (npcWhereabouts, error) {
	npc, inCatalogue := catalog.NPCs[name]
	if inCatalogue && len(npc.Circuit) > 0 {
		months := npc.CircuitMonths
		if months <= 0 {
			months = defaultCircuitMonths
		}
		if stop := circuitStop(npc.Circuit, gameMinute, months, npc.CircuitOffset); stop != "" {
			return npcWhereabouts{Location: stop, Known: true}, nil
		}
	}
	period := periodForGameMinute(gameMinute)
	if tableExistsTx(conn, "npc_civilization_state") {
		res, err := conn.Execute(`SELECT status,home_location,current_location FROM npc_civilization_state WHERE npc_name=?`, []any{name})
		if err != nil {
			return npcWhereabouts{}, err
		}
		if row := firstRowMap(res); row != nil {
			status := strings.TrimSpace(fmt.Sprint(row["status"]))
			current := strings.TrimSpace(fmt.Sprint(row["current_location"]))
			if status != "" && status != "alive" && status != "missing" {
				return npcWhereabouts{Dead: true, Known: true}, nil
			}
			if current != "" {
				// A missing person is exactly where they are (schema 47), and
				// keeps no routine.
				if status == "missing" {
					return npcWhereabouts{Location: current, Known: true}, nil
				}
				home := strings.TrimSpace(fmt.Sprint(row["home_location"]))
				if home == "" {
					home = current
				}
				// The daily schedule holds while they are in their home region;
				// autonomous travel overrides it only once they have left.
				if current == home && inCatalogue {
					if at := strings.TrimSpace(npc.Schedule[period]); at != "" {
						return npcWhereabouts{Location: at, Known: true}, nil
					}
				}
				return npcWhereabouts{Location: current, Known: true}, nil
			}
		}
	}
	// Somebody the world made for itself with no simulation row yet: a
	// starter household's relative stands in the household and nowhere else.
	if tableExistsTx(conn, "npc_registry") {
		res, err := conn.Execute(`SELECT location FROM npc_registry WHERE name=?`, []any{name})
		if err != nil {
			return npcWhereabouts{}, err
		}
		if row := firstRowMap(res); row != nil {
			if at := strings.TrimSpace(fmt.Sprint(row["location"])); at != "" {
				return npcWhereabouts{Location: at, Known: true}, nil
			}
		}
	}
	// A running event's cast stands where the event is (v1.1.0).
	if tableExistsTx(conn, "world_event_npcs") {
		res, err := conn.Execute(`SELECT n.location FROM world_event_npcs n JOIN world_events e ON e.event_key=n.event_key
			WHERE n.name=? AND e.active=1 AND e.ends_at>? LIMIT 1`, []any{name, now})
		if err != nil {
			return npcWhereabouts{}, err
		}
		if row := firstRowMap(res); row != nil {
			if at := strings.TrimSpace(fmt.Sprint(row["location"])); at != "" {
				return npcWhereabouts{Location: at, Known: true}, nil
			}
		}
	}
	// The content's own placement, for a catalogue NPC no simulation has
	// touched: their schedule this period, else where the file puts them.
	if inCatalogue {
		if at := strings.TrimSpace(npc.Schedule[period]); at != "" {
			return npcWhereabouts{Location: at, Known: true}, nil
		}
		if at := strings.TrimSpace(npc.Location); at != "" {
			return npcWhereabouts{Location: at, Known: true}, nil
		}
	}
	return npcWhereabouts{}, nil
}
