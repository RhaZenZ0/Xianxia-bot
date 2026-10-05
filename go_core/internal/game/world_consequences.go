package game

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func tableExistsTx(conn *storage.Conn, name string) bool {
	r, err := conn.Execute(`SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1`, []any{name})
	return err == nil && len(r.Rows) > 0
}

func floatAny(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case float32:
		return float64(x)
	case int64:
		return float64(x)
	case int:
		return float64(x)
	case json.Number:
		f, _ := x.Float64()
		return f
	default:
		var f float64
		_, _ = fmt.Sscan(fmt.Sprint(v), &f)
		return f
	}
}

func clampFloat(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func mapAny(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func applyWorldEventEffectTx(conn *storage.Conn, catalog worlddata.Catalog, eventID, title, location string, gameMinute, severity int64, effect map[string]any, now float64) ([]string, error) {
	if len(effect) == 0 {
		return nil, nil
	}
	sev := clampI64(severity, 1, 10)
	impacts := []string{}
	history := strings.TrimSpace(fmt.Sprint(effect["history"]))
	if history == "" || history == "<nil>" {
		history = title + " changed the region."
	}

	regionEffect := mapAny(effect["region"])
	if len(regionEffect) > 0 && tableExistsTx(conn, "civilization_regions") {
		r, err := conn.Execute(`SELECT population,prosperity,security,spirit_resources,food_supply,migration_pressure,unrest FROM civilization_regions WHERE location=?`, []any{location})
		if err != nil {
			return nil, err
		}
		if len(r.Rows) > 0 {
			row := r.Rows[0]
			popPct := clampFloat(floatAny(regionEffect["population_percent"]), -25, 25)
			pop := int64(math.Round(float64(storage.ParseInt(row[0])) * (1 + popPct/100)))
			if pop < 0 {
				pop = 0
			}
			prosperity := clampI64(storage.ParseInt(row[1])+storage.ParseInt(regionEffect["prosperity"]), 0, 100)
			security := clampI64(storage.ParseInt(row[2])+storage.ParseInt(regionEffect["security"]), 0, 100)
			spirit := clampI64(storage.ParseInt(row[3])+storage.ParseInt(regionEffect["spirit_resources"]), 0, 100)
			food := clampI64(storage.ParseInt(row[4])+storage.ParseInt(regionEffect["food_supply"]), 0, 100)
			migration := clampI64(storage.ParseInt(row[5])+storage.ParseInt(regionEffect["migration_pressure"]), 0, 100)
			unrest := clampI64(storage.ParseInt(row[6])+storage.ParseInt(regionEffect["unrest"]), 0, 100)
			if _, err = conn.Execute(`UPDATE civilization_regions SET population=?,prosperity=?,security=?,spirit_resources=?,food_supply=?,migration_pressure=?,unrest=?,last_game_minute=?,updated_at=? WHERE location=?`, []any{pop, prosperity, security, spirit, food, migration, unrest, gameMinute, now, location}); err != nil {
				return nil, err
			}
			if tableExistsTx(conn, "civilization_events") {
				if _, err = conn.Execute(`INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{location, history, sev, gameMinute, now}); err != nil {
					return nil, err
				}
			}
			impacts = append(impacts, "regional population, security, resources, or unrest changed")
		}
	}

	marketEffect := mapAny(effect["market"])
	if len(marketEffect) > 0 && tableExistsTx(conn, "economy_markets") {
		supply := clampI64(storage.ParseInt(marketEffect["supply"]), -200, 200)
		demand := clampI64(storage.ParseInt(marketEffect["demand"]), -200, 200)
		price := clampFloat(floatAny(marketEffect["price_index"]), -1.5, 1.5)
		res, err := conn.Execute(`UPDATE economy_markets SET supply=MIN(9999,MAX(1,supply+?)),demand=MIN(500,MAX(1,demand+?)),price_index=MIN(5.0,MAX(0.25,price_index+?)),updated_at=? WHERE location=?`, []any{supply, demand, price, now, location})
		if err != nil {
			return nil, err
		}
		if res.RowsAffected > 0 {
			if tableExistsTx(conn, "economy_events") {
				if _, err = conn.Execute(`INSERT INTO economy_events(location,item_id,event_text,game_minute,created_at) VALUES(?,NULL,?,?,?)`, []any{location, history, gameMinute, now}); err != nil {
					return nil, err
				}
			}
			impacts = append(impacts, "local supply, demand, and prices shifted")
		}
	}

	sectEffect := mapAny(effect["sect"])
	if len(sectEffect) > 0 {
		touched, err := ApplyEventSectEffectTx(conn, catalog, location, sectEffect, history, sev, gameMinute, now)
		if err != nil {
			return nil, err
		}
		if touched {
			impacts = append(impacts, "sect influence, cohesion, resources, or recruitment pressure changed")
		}
	}
	_ = eventID
	return impacts, nil
}

func recordWorldHistoryTx(conn *storage.Conn, sourceKey, eventType, title, summary string, significance int64, visibility, location, faction, actorType, actorKey, actorName, targetType, targetKey, targetName string, relatedUserID *int64, relatedNPCName string, tags []string, gameMinute int64, metadata map[string]any, now float64) error {
	if !tableExistsTx(conn, "world_history_events") {
		return nil
	}
	significance = clampI64(significance, 1, 100)
	if visibility == "" {
		visibility = "public"
	}
	tagText := strings.Join(tags, " ")
	if len(tagText) > 700 {
		tagText = tagText[:700]
	}
	enc, _ := json.Marshal(metadata)
	if len(enc) > 5000 {
		enc, _ = json.Marshal(map[string]any{"truncated": true, "preview": string(enc[:4500])})
	}
	var uid any
	if relatedUserID != nil {
		uid = *relatedUserID
	}
	_, err := conn.Execute(`INSERT INTO world_history_events(source_key,event_type,title,summary,significance,visibility,location,world_name,faction,actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO UPDATE SET event_type=excluded.event_type,title=excluded.title,summary=excluded.summary,significance=excluded.significance,visibility=excluded.visibility,location=excluded.location,faction=excluded.faction,actor_type=excluded.actor_type,actor_key=excluded.actor_key,actor_name=excluded.actor_name,target_type=excluded.target_type,target_key=excluded.target_key,target_name=excluded.target_name,related_user_id=excluded.related_user_id,related_npc_name=excluded.related_npc_name,tags=excluded.tags,game_minute=excluded.game_minute,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at`,
		[]any{sourceKey, eventType, title, summary, significance, visibility, location, "", faction, actorType, actorKey, actorName, targetType, targetKey, targetName, uid, relatedNPCName, tagText, gameMinute, string(enc), now, now})
	return err
}

// EventSectTargetsTx is which sects a world event's `sect` effect reaches
// (v1.27.0). Both writers of that effect - the event a player sets off and the
// one the autonomous batch spawns - ran `UPDATE sect_politics_state` with no
// WHERE, so a Demon Invasion in a Mortal village cost every sect in all four
// worlds its resources and could drop the lot below the tribute and war floors
// at once. An event happens somewhere, and these are the sects it is about:
//
//   - a sect whose home is the event's city (its seat, or a wilderness gate the
//     event stands on) - a recruitment drive in Cloudblade City is the Azure
//     Cloud Sect's, the rule recruitingSectFor already reads for the delegation;
//   - else the sect holding the event's ground (`territory_state`);
//   - else every public sect of the event's world.
//
// A place the catalogue does not carry (a household, an inner world) is in no
// world, and an event there reaches no sect - never the Mortal World's by
// default, which is the rc.52 `world_of_location` rule.
func EventSectTargetsTx(conn *storage.Conn, catalog worlddata.Catalog, location string) []string {
	location = strings.TrimSpace(location)
	loc, ok := catalog.Locations[location]
	if !ok {
		return nil
	}
	city := cityOf(catalog, location)
	names := make([]string, 0, len(catalog.Sects))
	for name := range catalog.Sects {
		names = append(names, name)
	}
	sort.Strings(names)
	home := []string{}
	for _, name := range names {
		if h := SectHome(catalog, name); h != "" && (h == city || h == location) {
			home = append(home, name)
		}
	}
	if len(home) > 0 {
		return home
	}
	if tableExistsTx(conn, "territory_state") {
		r, err := conn.Execute(`SELECT controller_key FROM territory_state WHERE territory_key=? AND controller_type='sect' AND controller_key<>'' LIMIT 1`, []any{city})
		if err == nil && len(r.Rows) > 0 {
			return []string{fmt.Sprint(r.Rows[0][0])}
		}
	}
	world := strings.TrimSpace(loc.World)
	if world == "" {
		return nil
	}
	out := []string{}
	for _, name := range names {
		def := catalog.Sects[name]
		if def.Hidden {
			continue
		}
		gate, ok := catalog.Locations[sectGate(catalog, name)]
		if ok && strings.TrimSpace(gate.World) == world {
			out = append(out, name)
		}
	}
	return out
}

// ApplyEventSectEffectTx applies a world event's `sect` deltas to the sects
// EventSectTargetsTx names and writes their politics-event rows. It is the one
// statement of the effect, called by the player-triggered event and by the
// simulation's autonomous one, which used to carry a copy each. It reports
// whether any sect was moved.
func ApplyEventSectEffectTx(conn *storage.Conn, catalog worlddata.Catalog, location string, deltas map[string]any, history string, severity, gameMinute int64, now float64) (bool, error) {
	if len(deltas) == 0 || !tableExistsTx(conn, "sect_politics_state") {
		return false, nil
	}
	touched := false
	for _, sect := range EventSectTargetsTx(conn, catalog, location) {
		res, err := conn.Execute(`UPDATE sect_politics_state SET influence=MIN(100,MAX(0,influence+?)),cohesion=MIN(100,MAX(0,cohesion+?)),resources=MIN(100,MAX(0,resources+?)),recruitment_pressure=MIN(100,MAX(0,recruitment_pressure+?)),doctrine_pressure=MIN(100,MAX(0,doctrine_pressure+?)),updated_at=? WHERE sect_name=?`, []any{storage.ParseInt(deltas["influence"]), storage.ParseInt(deltas["cohesion"]), storage.ParseInt(deltas["resources"]), storage.ParseInt(deltas["recruitment_pressure"]), storage.ParseInt(deltas["doctrine_pressure"]), now, sect})
		if err != nil {
			return false, err
		}
		if res.RowsAffected == 0 {
			continue
		}
		touched = true
		if tableExistsTx(conn, "sect_politics_events") {
			if _, err = conn.Execute(`INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{sect, history, severity, gameMinute, now}); err != nil {
				return false, err
			}
		}
	}
	return touched, nil
}
