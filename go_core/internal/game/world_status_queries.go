package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// World-status queries (v0.30.0, Authority II). These are the reads that
// app/simulation/world.py used to run as raw SQL through a Go-hosted session:
// market rows and quotes, challengeable targets, simulation state, the
// recent-action ledger and the NPC/sect/clan/region status panels. They are
// read-only, carry no ledger event, and where a rule was embedded in the
// Python read - the sell price, the hidden-master filter, the simulation lag
// against the clock - the rule now lives here and nowhere else.
var worldStatusQueries = map[string]bool{
	"market.rows":          true,
	"market.quote":         true,
	"market.catalog":       true,
	"combat.targets":       true,
	"simulation.state":     true,
	"simulation.status":    true,
	"world.recent_actions": true,
	"civilization.status":  true,
	"npc.status":           true,
	"sect.status":          true,
	"clan.status":          true,
	"equipment.power":      true,
}

// marketSellShare is what a regional market pays against its buy price.
// market.trade settles a sale at the same share; a quote must never promise
// more than the trade pays.
const marketSellShare = 0.70

func marketBuyPrice(row map[string]any) int64 {
	return max64(1, int64(math.Round(float64(i64(row["base_price"]))*parseFloat(row["price_index"]))))
}

func marketSellPrice(buy int64) int64 {
	return max64(1, int64(math.Round(float64(buy)*marketSellShare)))
}

func withMarketPrices(row map[string]any) map[string]any {
	buy := marketBuyPrice(row)
	row["buy_price"] = buy
	row["sell_price"] = marketSellPrice(buy)
	return row
}

type worldStatusPayload map[string]any

func (p worldStatusPayload) text(key string) string {
	return strings.TrimSpace(fmt.Sprint(p[key]))
}

func (p worldStatusPayload) has(key string) bool {
	v, ok := p[key]
	return ok && v != nil
}

func (p worldStatusPayload) integer(key string, fallback int64) int64 {
	if !p.has(key) {
		return fallback
	}
	return storage.ParseInt(p[key])
}

func queryResponse(conn *storage.Conn, req ActionRequest, result any) ActionResponse {
	out := ActionResponse{APIVersion: authoritativeAPIVersion, Operation: req.Operation, Result: result}
	if req.ActorID > 0 {
		out.StateVersion, _ = eventledger.CurrentActorVersion(conn, req.ActorID)
	}
	return out
}

func applyWorldStatusQuery(conn *storage.Conn, worldPath string, req ActionRequest) (ActionResponse, error) {
	payload := worldStatusPayload{}
	if len(req.Payload) > 0 && string(req.Payload) != "null" {
		if err := jsonUnmarshalInto(req.Payload, &payload); err != nil {
			return ActionResponse{}, err
		}
	}
	catalog := func() (worlddata.Catalog, error) {
		if strings.TrimSpace(worldPath) == "" {
			return worlddata.Catalog{}, errors.New("world catalog path is required")
		}
		return worlddata.Load(worldPath)
	}
	switch req.Operation {
	case "market.rows":
		location := payload.text("location")
		if location == "" {
			return ActionResponse{}, errors.New("location is required")
		}
		limit := clamp(payload.integer("limit", 25), 1, 100)
		res, err := conn.Execute(`SELECT * FROM economy_markets WHERE location=? ORDER BY (base_price*price_index) DESC LIMIT ?`, []any{location, limit})
		if err != nil {
			return ActionResponse{}, err
		}
		rows := rowsToMaps(res)
		for _, row := range rows {
			withMarketPrices(row)
		}
		return queryResponse(conn, req, map[string]any{"location": location, "rows": rows}), nil
	case "market.quote":
		location, itemID := payload.text("location"), payload.text("item_id")
		if location == "" || itemID == "" {
			return ActionResponse{}, errors.New("location and item_id are required")
		}
		res, err := conn.Execute(`SELECT * FROM economy_markets WHERE location=? AND item_id=?`, []any{location, itemID})
		if err != nil {
			return ActionResponse{}, err
		}
		row := firstRowMap(res)
		if row == nil {
			return queryResponse(conn, req, map[string]any{"traded": false, "location": location, "item_id": itemID}), nil
		}
		withMarketPrices(row)
		row["traded"] = true
		return queryResponse(conn, req, row), nil
	case "market.catalog":
		cat, err := catalog()
		if err != nil {
			return ActionResponse{}, err
		}
		ids := make([]string, 0, len(cat.Items))
		for id, item := range cat.Items {
			if item.MarketTradeable() {
				ids = append(ids, id)
			}
		}
		sort.Strings(ids)
		return queryResponse(conn, req, map[string]any{"item_ids": ids}), nil
	case "combat.targets":
		cat, err := catalog()
		if err != nil {
			return ActionResponse{}, err
		}
		location := payload.text("location")
		if location == "" {
			return ActionResponse{}, errors.New("location is required")
		}
		targets, err := combatTargetsGo(conn, cat, location)
		if err != nil {
			return ActionResponse{}, err
		}
		return queryResponse(conn, req, map[string]any{"location": location, "targets": targets}), nil
	case "simulation.state":
		system := payload.text("system")
		if system == "" {
			return ActionResponse{}, errors.New("system is required")
		}
		res, err := conn.Execute(`SELECT * FROM world_simulation_state WHERE system=?`, []any{system})
		if err != nil {
			return ActionResponse{}, err
		}
		row := firstRowMap(res)
		if row == nil {
			return queryResponse(conn, req, map[string]any{"found": false, "system": system}), nil
		}
		row["found"] = true
		return queryResponse(conn, req, row), nil
	case "simulation.status":
		gameMinute, err := canonicalWorldGameMinute(conn)
		if err != nil {
			return ActionResponse{}, err
		}
		res, err := conn.Execute(`SELECT * FROM world_simulation_state ORDER BY system`, nil)
		if err != nil {
			return ActionResponse{}, err
		}
		rows := rowsToMaps(res)
		for _, row := range rows {
			row["lag_game_minutes"] = max64(0, gameMinute-i64(row["last_game_minute"]))
		}
		return queryResponse(conn, req, map[string]any{"game_minute": gameMinute, "systems": rows}), nil
	case "world.recent_actions":
		limit := clamp(payload.integer("limit", 20), 1, 100)
		res, err := conn.Execute(`SELECT * FROM world_action_events ORDER BY action_id DESC LIMIT ?`, []any{limit})
		if err != nil {
			return ActionResponse{}, err
		}
		rows := rowsToMaps(res)
		for _, row := range rows {
			row["payload"] = decodeJSONMap(row["payload_json"])
			delete(row, "payload_json")
		}
		return queryResponse(conn, req, map[string]any{"actions": rows}), nil
	case "civilization.status":
		location := payload.text("location")
		if location == "" {
			return ActionResponse{}, errors.New("location is required")
		}
		result, err := civilizationStatusGo(conn, location)
		if err != nil {
			return ActionResponse{}, err
		}
		return queryResponse(conn, req, result), nil
	case "npc.status":
		name := payload.text("npc_name")
		if name == "" {
			return ActionResponse{}, errors.New("npc_name is required")
		}
		result, err := npcStatusGo(conn, name)
		if err != nil {
			return ActionResponse{}, err
		}
		return queryResponse(conn, req, result), nil
	case "sect.status":
		name := payload.text("sect_name")
		if name == "" {
			return ActionResponse{}, errors.New("sect_name is required")
		}
		result, err := sectStatusGo(conn, name)
		if err != nil {
			return ActionResponse{}, err
		}
		return queryResponse(conn, req, result), nil
	case "clan.status":
		if !payload.has("family_id") {
			return ActionResponse{}, errors.New("family_id is required")
		}
		result, err := clanStatusGo(conn, payload.integer("family_id", 0))
		if err != nil {
			return ActionResponse{}, err
		}
		return queryResponse(conn, req, result), nil
	case "equipment.power":
		if req.ActorID <= 0 {
			return ActionResponse{}, errors.New("actor_id must be positive")
		}
		power, err := equipmentPowerGo(conn, req.ActorID)
		if err != nil {
			return ActionResponse{}, err
		}
		return queryResponse(conn, req, power), nil
	}
	return ActionResponse{}, fmt.Errorf("unsupported authoritative query: %s", req.Operation)
}

// combatTargetsGo lists the living NPCs and family heads mechanically present
// at a location. Real hidden masters are omitted so the picker cannot become
// a hidden-power detector; they still enter battles through explicit events
// or GM actions.
func combatTargetsGo(conn *storage.Conn, catalog worlddata.Catalog, location string) ([]map[string]any, error) {
	hiddenReal := map[string]bool{}
	for name, npc := range catalog.NPCs {
		if npc.HiddenMaster != nil && npc.HiddenMaster.Kind == "real" {
			hiddenReal[name] = true
		}
	}
	res, err := conn.Execute(`SELECT npc_name AS name,realm_index,phase,'npc' AS target_type
		FROM npc_civilization_state WHERE current_location=? AND status='alive' ORDER BY influence DESC`, []any{location})
	if err != nil {
		return nil, err
	}
	rows := make([]map[string]any, 0)
	for _, row := range rowsToMaps(res) {
		if !hiddenReal[fmt.Sprint(row["name"])] {
			rows = append(rows, row)
		}
	}
	res, err = conn.Execute(`SELECT head_name AS name,head_realm_index AS realm_index,head_phase AS phase,'family_head' AS target_type,family_id,family_name
		FROM birth_families WHERE location=? AND line_status='active' AND head_name!='Vacant Ancestral Seat' ORDER BY influence DESC LIMIT 25`, []any{location})
	if err != nil {
		return nil, err
	}
	rows = append(rows, rowsToMaps(res)...)
	seen := map[string]bool{}
	unique := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		name := fmt.Sprint(row["name"])
		if name == "" || name == "<nil>" || seen[name] {
			continue
		}
		seen[name] = true
		unique = append(unique, row)
		if len(unique) >= 50 {
			break
		}
	}
	return unique, nil
}

func civilizationStatusGo(conn *storage.Conn, location string) (map[string]any, error) {
	res, err := conn.Execute(`SELECT * FROM civilization_regions WHERE location=?`, []any{location})
	if err != nil {
		return nil, err
	}
	out := firstRowMap(res)
	if out == nil {
		return map[string]any{"found": false, "location": location}, nil
	}
	out["found"] = true
	res, err = conn.Execute(`SELECT event_text,severity,game_minute FROM civilization_events WHERE location=? ORDER BY event_id DESC LIMIT 5`, []any{location})
	if err != nil {
		return nil, err
	}
	out["events"] = rowsToMaps(res)
	res, err = conn.Execute(`SELECT c.npc_name,c.profession,c.activity,c.realm_index,c.phase,c.faction,c.influence,
		l.health,l.injury,l.injury_severity,l.sect_rank,l.relationship_status,l.spouse_name,l.children_count
		FROM npc_civilization_state c LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
		WHERE c.current_location=? AND c.status='alive' ORDER BY c.influence DESC LIMIT 8`, []any{location})
	if err != nil {
		return nil, err
	}
	out["npcs"] = rowsToMaps(res)
	return out, nil
}

func npcStatusGo(conn *storage.Conn, name string) (map[string]any, error) {
	res, err := conn.Execute(`SELECT c.*,m.current_goal,m.mood,m.focus_target,m.recent_event,m.goal_progress,
		l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,l.injury_severity,
		l.sect_rank,l.career_progress,l.relationship_status,l.spouse_name,l.children_count,l.last_social_game_minute,
		l.last_cultivation_game_minute,l.death_game_minute,l.cause_of_death
		FROM npc_civilization_state c
		LEFT JOIN npc_mind_state m ON m.npc_name=c.npc_name
		LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
		WHERE c.npc_name=?`, []any{name})
	if err != nil {
		return nil, err
	}
	out := firstRowMap(res)
	if out == nil {
		return map[string]any{"found": false, "npc_name": name}, nil
	}
	out["found"] = true
	res, err = conn.Execute(`SELECT * FROM npc_social_relations WHERE status='active' AND (npc_a=? OR npc_b=?)
		ORDER BY MAX(ABS(affinity),grudge,trust) DESC LIMIT 12`, []any{name, name})
	if err != nil {
		return nil, err
	}
	out["relationships"] = rowsToMaps(res)
	res, err = conn.Execute(`SELECT * FROM npc_disciple_bonds WHERE status='active' AND (master_name=? OR disciple_name=?)
		ORDER BY started_game_minute DESC LIMIT 12`, []any{name, name})
	if err != nil {
		return nil, err
	}
	out["discipleship"] = rowsToMaps(res)
	res, err = conn.Execute(`SELECT * FROM npc_descendants WHERE status='alive' AND (parent_a=? OR parent_b=?)
		ORDER BY birth_game_minute DESC LIMIT 20`, []any{name, name})
	if err != nil {
		return nil, err
	}
	out["descendants"] = rowsToMaps(res)
	out["age_years"] = nil
	out["lifespan_years"] = nil
	if out["birth_game_minute"] != nil {
		gameMinute, err := canonicalWorldGameMinute(conn)
		if err != nil {
			return nil, err
		}
		status := lifespanmodel.Evaluate(lifespanmodel.Subject{
			BirthGameMinute:    i64(out["birth_game_minute"]),
			AgeAtCreationYears: i64(out["age_at_creation_years"]),
			NaturalYears:       i64(out["natural_lifespan_years"]),
			RealmIndex:         i64(out["realm_index"]),
			Phase:              i64(out["phase"]),
		}, gameMinute)
		out["age_years"] = status.AgeYears
		if status.TotalYears != nil {
			out["lifespan_years"] = *status.TotalYears
		}
		out["ageless"] = status.Ageless
		if status.RemainingYears != nil {
			out["remaining_years"] = *status.RemainingYears
		} else {
			out["remaining_years"] = nil
		}
	}
	return out, nil
}

func sectStatusGo(conn *storage.Conn, name string) (map[string]any, error) {
	res, err := conn.Execute(`SELECT * FROM sect_politics_state WHERE sect_name=?`, []any{name})
	if err != nil {
		return nil, err
	}
	out := firstRowMap(res)
	if out == nil {
		return map[string]any{"found": false, "sect_name": name}, nil
	}
	out["found"] = true
	res, err = conn.Execute(`SELECT faction_name,agenda,power,loyalty FROM sect_factions WHERE sect_name=? ORDER BY power DESC`, []any{name})
	if err != nil {
		return nil, err
	}
	out["factions"] = rowsToMaps(res)
	res, err = conn.Execute(`SELECT * FROM sect_relations WHERE sect_a=? OR sect_b=? ORDER BY ABS(relation_score) DESC`, []any{name, name})
	if err != nil {
		return nil, err
	}
	relations := rowsToMaps(res)
	for _, row := range relations {
		other := fmt.Sprint(row["sect_a"])
		if other == name {
			other = fmt.Sprint(row["sect_b"])
		}
		row["other"] = other
	}
	out["relations"] = relations
	res, err = conn.Execute(`SELECT event_text,severity,game_minute FROM sect_politics_events WHERE sect_name=? ORDER BY event_id DESC LIMIT 5`, []any{name})
	if err != nil {
		return nil, err
	}
	out["events"] = rowsToMaps(res)
	return out, nil
}

func clanStatusGo(conn *storage.Conn, familyID int64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT * FROM martial_clan_branches WHERE family_id=? ORDER BY CASE branch_type WHEN 'main' THEN 0 ELSE 1 END, martial_strength DESC`, []any{familyID})
	if err != nil {
		return nil, err
	}
	branches := rowsToMaps(res)
	res, err = conn.Execute(`SELECT * FROM martial_clan_retainers WHERE family_id=? ORDER BY status,loyalty DESC`, []any{familyID})
	if err != nil {
		return nil, err
	}
	retainers := rowsToMaps(res)
	res, err = conn.Execute(`SELECT * FROM martial_clan_relations WHERE family_id=? AND active=1 ORDER BY ABS(relation_score) DESC`, []any{familyID})
	if err != nil {
		return nil, err
	}
	return map[string]any{"family_id": familyID, "branches": branches, "retainers": retainers, "relations": rowsToMaps(res)}, nil
}

func jsonUnmarshalInto(raw []byte, target any) error {
	return json.Unmarshal(raw, target)
}
