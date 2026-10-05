package game

// The trip before it is taken (v1.26.0).
//
// A road journey charged its toll and rolled its encounters the moment it was
// asked for, and the reply never named the toll it had taken or the roads it
// had walked - `travel_cost_spirit_stones` was read by nothing in the bot. On
// the owner's call a player is shown the trip first: the route, the road hops,
// the toll in the money of the world they stand in, the danger and the
// encounter risk, and then goes or does not.
//
// exploration.travel_preview is a read, and it answers through planTravelTx -
// the same refusals and the same road exploration.travel walks - so the card a
// player is shown and the journey they then take cannot disagree. A refusal is
// the preview's answer too: a destination the engine will not go to is refused
// here in the words the journey would have used.

import (
	"encoding/json"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// travelEnds is where a road journey arrives and leaves by: the gate facing
// the road it came in on, and the gate facing its first leg (v0.36.0). A city
// with no gates, or a journey with no road, lands on the destination itself.
func travelEnds(catalog worlddata.Catalog, destination, originCity string, route []string) (arrivedAt, arrivalGate, leftBy string) {
	arrivedAt = destination
	if len(route) < 2 {
		return arrivedAt, "", ""
	}
	if gate, direction, ok := gateFacing(catalog, destination, roadFacingNeighbour(catalog, destination, route[len(route)-2])); ok {
		arrivedAt, arrivalGate = gate, direction
	}
	if _, direction, ok := gateFacing(catalog, originCity, roadFacingNeighbour(catalog, originCity, route[1])); ok {
		leftBy = direction
	}
	return arrivedAt, arrivalGate, leftBy
}

func travelPreviewQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (map[string]any, error) {
	var p travelPayload
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &p); err != nil {
			return nil, err
		}
	}
	t, err := planTravelTx(conn, catalog, userID, p.Destination, p.Mode)
	if err != nil {
		return nil, err
	}
	out := map[string]any{
		"destination": p.Destination, "from": t.c.Location, "mode": t.mode,
		"road_connection": t.found, "travel_mode": t.modeName,
	}
	if !t.found {
		// No road: a part of your own city, a road-less place, a capital by
		// the realm gate. Free and immediate, so there is nothing to preview.
		out["road_hops"], out["travel_cost_spirit_stones"] = 0, 0
		return out, nil
	}
	legs := make([]map[string]any, 0, len(t.plan.Legs))
	maxChance := int64(0)
	for _, leg := range t.plan.Legs {
		maxChance = maxI64(maxChance, leg.Profile.EncounterChance)
		legs = append(legs, map[string]any{
			"from": leg.From, "to": leg.To, "base_travel_minutes": leg.Profile.TravelMinutes,
			"danger": leg.Profile.DangerScore, "encounter_chance_percent": leg.Profile.EncounterChance,
			"cost_spirit_stones": leg.Cost,
		})
	}
	if len(t.plan.Legs) > 0 {
		out["travel_mode"] = t.plan.Legs[0].Profile.Mode
		out["travel_mount"] = t.plan.Legs[0].Profile.Mount
	}
	currency, err := characterBaseCurrencyTx(conn, catalog, userID)
	if err != nil {
		return nil, err
	}
	balance, err := characterWalletBalanceTx(conn, catalog, userID)
	if err != nil {
		return nil, err
	}
	arrivedAt, arrivalGate, leftBy := travelEnds(catalog, p.Destination, t.originCity, t.plan.Nodes)
	out["road_route"] = t.plan.Nodes
	out["road_legs"] = legs
	out["road_hops"] = len(t.plan.Legs)
	out["travel_cost_spirit_stones"] = t.plan.Cost
	out["currency"] = currency
	out["currency_name"] = firstNonempty(catalog.Currencies[currency].Name, currency)
	out["balance"] = balance
	out["affordable"] = balance >= t.plan.Cost
	out["road_danger"] = t.plan.MaxDanger
	out["road_encounter_chance_percent"] = maxChance
	out["travel_minutes"] = t.plan.TravelMinutes
	out["wait_minutes"] = scaledTravelWait(conn, t.plan.TravelMinutes)
	out["arrives_at"] = arrivedAt
	out["arrival_gate"] = arrivalGate
	out["left_by_gate"] = leftBy
	return out, nil
}
