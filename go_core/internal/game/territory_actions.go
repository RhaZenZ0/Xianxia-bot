package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type territoryClaimPayload struct {
	TerritoryKey string `json:"territory_key"`
	GameMinute   int64  `json:"game_minute"`
}
type warActPayload struct {
	WarID      int64  `json:"war_id"`
	Tactic     string `json:"tactic"`
	GameMinute int64  `json:"game_minute"`
}
type caravanDispatchPayload struct {
	Destination   string `json:"destination"`
	ItemID        string `json:"item_id"`
	Quantity      int64  `json:"quantity"`
	Escort        int64  `json:"escort"`
	Smuggle       bool   `json:"smuggle"`
	GameMinute    int64  `json:"game_minute"`
	MinutesPerDay int64  `json:"minutes_per_day"`
}
type caravanSettlePayload struct {
	GameMinute int64 `json:"game_minute"`
}

func currentEraModifierGo(conn *storage.Conn, key string, def float64) float64 {
	r, e := conn.Execute(`SELECT modifiers_json FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1`, nil)
	if e != nil {
		return def
	}
	row := firstRowMap(r)
	if row == nil {
		return def
	}
	m := decodeJSONMap(row["modifiers_json"])
	if v, ok := m[key]; ok {
		if f, e := strconv.ParseFloat(fmt.Sprint(v), 64); e == nil {
			return f
		}
	}
	return def
}
func sectMembershipRow(conn *storage.Conn, userID int64) (map[string]any, error) {
	r, e := conn.Execute(`SELECT * FROM sect_membership WHERE user_id=?`, []any{userID})
	if e != nil {
		return nil, e
	}
	return firstRowMap(r), nil
}
func manorDefensePowerGo(conn *storage.Conn, sect string, territory string) int64 {
	r, e := conn.Execute(`SELECT defense_array_level,qi_array_level,base_location FROM sect_manors WHERE sect_name=?`, []any{sect})
	if e != nil {
		return 0
	}
	m := firstRowMap(r)
	if m == nil || fmt.Sprint(m["base_location"]) != territory {
		return 0
	}
	return i64(m["defense_array_level"])*6 + i64(m["qi_array_level"])*2
}
func ensureWarOperationGo(conn *storage.Conn, warID, gm int64, now float64) error {
	_, e := conn.Execute(`INSERT INTO territory_war_operations(war_id,siege_progress,attacker_morale,defender_morale,attacker_force,defender_force,last_tick_game_minute,winner_key,resolution,occupation_until_game_minute,updated_at) VALUES(?,0,100,100,0,0,?,'','',0,?) ON CONFLICT(war_id) DO NOTHING`, []any{warID, gm, now})
	return e
}
func territoryClaimActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p territoryClaimPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.TerritoryKey = strings.TrimSpace(p.TerritoryKey)
	mem, e := sectMembershipRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("sect membership is required")
	}
	sect := fmt.Sprint(mem["sect_name"])
	r, e := conn.Execute(`SELECT * FROM territory_state WHERE territory_key=?`, []any{p.TerritoryKey})
	if e != nil {
		return authoritativeMutation{}, e
	}
	t := firstRowMap(r)
	if t == nil {
		return authoritativeMutation{}, errors.New("territory not found")
	}
	controller := strings.TrimSpace(fmt.Sprint(t["controller_key"]))
	now := nowSeconds()
	out := map[string]any{"territory_key": p.TerritoryKey, "sect_name": sect}
	if controller == "" || fmt.Sprint(t["controller_type"]) == "neutral" {
		_, e = conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MIN(100,unrest+10),updated_game_minute=?,updated_at=? WHERE territory_key=?`, []any{sect, p.GameMinute, now, p.TerritoryKey})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["claimed"] = true
		out["controller_key"] = sect
	} else if controller == sect {
		return authoritativeMutation{}, errors.New("your sect already controls that territory")
	} else {
		r, e = conn.Execute(`SELECT war_id FROM territory_wars WHERE territory_key=? AND status='active' LIMIT 1`, []any{p.TerritoryKey})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("an active war already contests that territory")
		}
		c, e := conn.Execute(`INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_game_minute,updated_game_minute,created_at,updated_at) VALUES(?,?,?,'active',?,?,?,?)`, []any{sect, controller, p.TerritoryKey, p.GameMinute, p.GameMinute, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if e = ensureWarOperationGo(conn, c.LastInsertID, p.GameMinute, now); e != nil {
			return authoritativeMutation{}, e
		}
		out["war_id"] = c.LastInsertID
		out["attacker_key"] = sect
		out["defender_key"] = controller
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "territory", EventType: "territory.claim", EntityType: "territory", EntityID: p.TerritoryKey, GameMinute: p.GameMinute, Payload: out}}, nil
}
func territoryWarActActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p warActPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.Tactic = strings.ToLower(strings.TrimSpace(p.Tactic))
	if _, ok := map[string]bool{"assault": true, "siege": true, "sabotage": true, "fortify": true, "repel": true}[p.Tactic]; !ok {
		return authoritativeMutation{}, errors.New("unknown war tactic")
	}
	mem, e := sectMembershipRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("sect membership is required")
	}
	r, e := conn.Execute(`SELECT * FROM territory_wars WHERE war_id=? AND status='active'`, []any{p.WarID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	war := firstRowMap(r)
	if war == nil {
		return authoritativeMutation{}, errors.New("active war not found")
	}
	sect := fmt.Sprint(mem["sect_name"])
	side := ""
	if sect == fmt.Sprint(war["attacker_key"]) {
		side = "attacker"
	} else if sect == fmt.Sprint(war["defender_key"]) {
		side = "defender"
	} else {
		return authoritativeMutation{}, errors.New("your sect is not a belligerent in that war")
	}
	now := nowSeconds()
	cdkey := fmt.Sprintf("war_action:%d", p.WarID)
	r, e = conn.Execute(`SELECT available_at FROM cooldowns WHERE user_id=? AND action=?`, []any{userID, cdkey})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if row := firstRowMap(r); row != nil {
		f, _ := strconv.ParseFloat(fmt.Sprint(row["available_at"]), 64)
		if f > now {
			return authoritativeMutation{}, errors.New("war contribution is still on cooldown")
		}
	}
	cr, e := conn.Execute(`SELECT realm_index,phase,attributes_json FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	c := firstRowMap(cr)
	if c == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	attrs := decodeJSONMap(c["attributes_json"])
	equip, e := equipmentPowerGo(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	power := max64(5, i64(c["realm_index"])*8+i64(c["phase"])+max64(i64(attrs["body"]), i64(attrs["spirit"]))+equip["attack"]+equip["defense"])
	manorBonus := int64(0)
	if side == "defender" && (p.Tactic == "fortify" || p.Tactic == "repel") {
		manorBonus = manorDefensePowerGo(conn, sect, fmt.Sprint(war["territory_key"]))
		power += manorBonus
	}
	power = max64(1, int64(math.Round(float64(power)*math.Max(.25, currentEraModifierGo(conn, "war_pressure", 1)))))
	if e = ensureWarOperationGo(conn, p.WarID, p.GameMinute, now); e != nil {
		return authoritativeMutation{}, e
	}
	r, e = conn.Execute(`SELECT * FROM territory_war_operations WHERE war_id=?`, []any{p.WarID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	op := firstRowMap(r)
	siege := i64(op["siege_progress"])
	am := i64(op["attacker_morale"])
	dm := i64(op["defender_morale"])
	af := i64(op["attacker_force"])
	df := i64(op["defender_force"])
	roll := stablePercentGo(p.WarID, userID, side, p.Tactic, p.GameMinute)
	impact := max64(2, power/4+roll/15)
	siegeDelta := int64(0)
	moraleDelta := int64(0)
	if side == "attacker" {
		af += power
		switch p.Tactic {
		case "siege":
			siegeDelta = impact + 5
			dm -= impact / 2
		case "assault":
			siegeDelta = impact
			dm -= impact
		case "sabotage":
			siegeDelta = impact / 2
			dm -= impact + 4
		case "fortify":
			am += impact
		case "repel":
			siegeDelta = impact / 2
			dm -= impact / 2
		}
		siege = clamp(siege+max64(0, siegeDelta), 0, 100)
		moraleDelta = -impact
	} else {
		df += power
		switch p.Tactic {
		case "fortify":
			siegeDelta = -(impact + 4)
			dm += impact
		case "repel":
			siegeDelta = -impact
			am -= impact
		case "sabotage":
			siegeDelta = -(impact / 2)
			am -= impact + 4
		case "assault":
			am -= impact
			siegeDelta = -(impact / 3)
		default:
			siegeDelta = -(impact / 2)
			am -= impact / 2
		}
		siege = clamp(siege+siegeDelta, 0, 100)
		moraleDelta = -impact
	}
	am = clamp(am, 0, 120)
	dm = clamp(dm, 0, 120)
	winner := ""
	resolution := ""
	occupation := int64(0)
	if siege >= 100 || dm <= 0 {
		winner = fmt.Sprint(war["attacker_key"])
		resolution = "attacker_occupation"
		occupation = p.GameMinute + 30*1440
	} else if am <= 0 {
		winner = fmt.Sprint(war["defender_key"])
		resolution = "defender_holds"
	}
	_, e = conn.Execute(`UPDATE territory_war_operations SET siege_progress=?,attacker_morale=?,defender_morale=?,attacker_force=?,defender_force=?,last_tick_game_minute=?,winner_key=?,resolution=?,occupation_until_game_minute=?,updated_at=? WHERE war_id=?`, []any{siege, am, dm, af, df, p.GameMinute, winner, resolution, occupation, now, p.WarID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	_, e = conn.Execute(`INSERT INTO territory_war_actions(war_id,user_id,side,tactic,power,siege_delta,morale_delta,game_minute,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, []any{p.WarID, userID, side, p.Tactic, power, siegeDelta, moraleDelta, p.GameMinute, now})
	if e != nil {
		return authoritativeMutation{}, e
	}
	_, _ = conn.Execute(`UPDATE territory_wars SET attacker_score=?,defender_score=?,updated_game_minute=?,updated_at=? WHERE war_id=?`, []any{af, df, p.GameMinute, now, p.WarID})
	status := "active"
	if winner != "" {
		status = "resolved"
		_, _ = conn.Execute(`UPDATE territory_wars SET status='resolved',updated_game_minute=?,updated_at=? WHERE war_id=?`, []any{p.GameMinute, now, p.WarID})
		if winner == fmt.Sprint(war["attacker_key"]) {
			_, _ = conn.Execute(`UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MIN(100,unrest+25),updated_game_minute=?,updated_at=? WHERE territory_key=?`, []any{winner, p.GameMinute, now, fmt.Sprint(war["territory_key"])})
		}
	}
	_, e = conn.Execute(`INSERT INTO cooldowns(user_id,action,available_at) VALUES(?,?,?) ON CONFLICT(user_id,action) DO UPDATE SET available_at=excluded.available_at`, []any{userID, cdkey, now + 1800})
	if e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"war_id": p.WarID, "status": status, "side": side, "tactic": p.Tactic, "power": power, "manor_defense_bonus": manorBonus, "operations": map[string]any{"siege_progress": siege, "attacker_morale": am, "defender_morale": dm, "attacker_force": af, "defender_force": df, "winner_key": winner, "resolution": resolution, "occupation_until_game_minute": occupation}}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "territory", EventType: "war.act", EntityType: "territory_war", EntityID: fmt.Sprint(p.WarID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func caravanDispatchActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p caravanDispatchPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.Quantity < 1 || p.Quantity > 50 {
		return authoritativeMutation{}, errors.New("caravan quantity must be between 1 and 50")
	}
	if p.Escort < 0 || p.Escort > 20 {
		return authoritativeMutation{}, errors.New("caravan escort must be between 0 and 20")
	}
	if p.MinutesPerDay != 0 {
		return authoritativeMutation{}, errors.New("client-supplied caravan duration is forbidden")
	}
	r, e := conn.Execute(`SELECT realm_index,location,attributes_json,life_status FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	c := firstRowMap(r)
	if c == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	if !strings.EqualFold(strings.TrimSpace(fmt.Sprint(c["life_status"])), "alive") {
		return authoritativeMutation{}, errors.New("only a living incarnation can dispatch a caravan")
	}
	origin := fmt.Sprint(c["location"])
	if p.Destination == "" || p.Destination == origin {
		return authoritativeMutation{}, errors.New("choose a different destination")
	}
	if _, ok := catalog.Locations[p.Destination]; !ok {
		return authoritativeMutation{}, errors.New("unknown destination")
	}
	item, ok := catalog.Items[p.ItemID]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown item")
	}
	q, e := inventoryQuantityTx(conn, userID, p.ItemID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if q < p.Quantity {
		return authoritativeMutation{}, errors.New("not enough cargo")
	}
	currency := "low_spirit_stone"
	if i64(c["realm_index"]) >= 4 {
		currency = "mid_spirit_stone"
	}
	if i64(c["realm_index"]) >= 7 {
		currency = "high_spirit_stone"
	}
	plan, found := canonicalRoadRoute(catalog, origin, p.Destination, i64(c["realm_index"]))
	if !found {
		return authoritativeMutation{}, errors.New("no canonical road route connects the caravan destination")
	}
	travelMinutes := max64(30, (plan.TravelMinutes*5+3)/4)
	escortCost := max64(0, p.Escort) * 3
	routeCost := max64(1, plan.Cost*2)
	operatingCost := escortCost + routeCost
	now := nowSeconds()
	if operatingCost > 0 {
		if _, e = walletDeltaTx(conn, userID, currency, -operatingCost, now); e != nil {
			return authoritativeMutation{}, e
		}
	}
	if q == p.Quantity {
		_, e = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	} else {
		_, e = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
	}
	if e != nil {
		return authoritativeMutation{}, e
	}
	base := max64(5, item.BasePrice)
	if base <= 5 {
		base = max64(base, item.SectValue)
	}
	mult := 1.15
	if p.Smuggle {
		mult = 1.35
	}
	payout := max64(1, int64(float64(base*p.Quantity)*mult))
	risk := clamp(plan.MaxDanger+10, 5, 85)
	attrs := decodeJSONMap(c["attributes_json"])
	conceal := max64(0, i64(attrs["agility"])/2)
	cargo, _ := json.Marshal(map[string]any{
		p.ItemID:          p.Quantity,
		"_payout":         payout,
		"_currency":       currency,
		"_route":          plan.Nodes,
		"_travel_minutes": travelMinutes,
		"_operating_cost": operatingCost,
	})
	curs, e := conn.Execute(`INSERT INTO caravans(owner_type,owner_key,origin,destination,cargo_json,status,risk,depart_game_minute,arrive_game_minute,updated_at) VALUES('player',?,?,?,?,'traveling',?,?,?,?)`, []any{fmt.Sprint(userID), origin, p.Destination, string(cargo), risk, p.GameMinute, p.GameMinute + travelMinutes, now})
	if e != nil {
		return authoritativeMutation{}, e
	}
	cid := curs.LastInsertID
	tax := int64(8)
	_, e = conn.Execute(`INSERT INTO caravan_operations(caravan_id,escort_strength,concealment,smuggling,tax_rate,toll_paid,intercepted,seized,payout_final,losses_json,outcome,resolved_game_minute,updated_at) VALUES(?,?,?,?,?,0,0,0,0,'{}','traveling',NULL,?)`, []any{cid, p.Escort, conceal, boolInt(p.Smuggle), tax, now})
	if e != nil {
		return authoritativeMutation{}, e
	}
	detail, _ := json.Marshal(map[string]any{
		"escort_strength": p.Escort,
		"concealment":     conceal,
		"smuggling":       p.Smuggle,
		"tax_rate":        tax,
		"route":           plan.Nodes,
		"road_hops":       len(plan.Legs),
		"travel_minutes":  travelMinutes,
		"operating_cost":  operatingCost,
	})
	_, _ = conn.Execute(`INSERT INTO caravan_events(caravan_id,event_type,detail_json,game_minute,created_at) VALUES(?,'departed',?,?,?)`, []any{cid, string(detail), p.GameMinute, now})
	_, _ = conn.Execute(`INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,'',?,100,?,?,?,?)`, []any{userID, p.ItemID, p.Quantity, "caravan_dispatch", fmt.Sprintf("caravan:%d", cid), map[bool]string{true: "smuggled_in_transit", false: "in_transit"}[p.Smuggle], map[bool]int64{true: 20, false: 5}[p.Smuggle], p.GameMinute, now, now})
	out := map[string]any{
		"caravan_id":          cid,
		"origin":              origin,
		"destination":         p.Destination,
		"route":               plan.Nodes,
		"road_hops":           len(plan.Legs),
		"travel_minutes":      travelMinutes,
		"arrival_game_minute": p.GameMinute + travelMinutes,
		"operating_cost":      operatingCost,
		"item_id":             p.ItemID,
		"quantity":            p.Quantity,
		"escort":              p.Escort,
		"concealment":         conceal,
		"smuggling":           p.Smuggle,
		"risk":                risk,
		"payout":              payout,
		"currency_id":         currency,
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "caravan", EventType: "caravan.dispatch", EntityType: "caravan", EntityID: fmt.Sprint(cid), GameMinute: p.GameMinute, Payload: out}}, nil
}
func boolInt(v bool) int64 {
	if v {
		return 1
	}
	return 0
}
func caravanSettleActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p caravanSettlePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	now := nowSeconds()
	r, e := conn.Execute(`SELECT c.*,o.escort_strength,o.concealment,o.smuggling,o.tax_rate FROM caravans c LEFT JOIN caravan_operations o ON o.caravan_id=c.caravan_id WHERE c.status='traveling' AND c.arrive_game_minute<=? AND c.owner_type='player' AND c.owner_key=? ORDER BY c.caravan_id`, []any{p.GameMinute, fmt.Sprint(userID)})
	if e != nil {
		return authoritativeMutation{}, e
	}
	eraRisk := math.Max(.25, currentEraModifierGo(conn, "caravan_risk", 1))
	resolved := []map[string]any{}
	for _, data := range rowsToMaps(r) {
		cargo := decodeJSONMap(data["cargo_json"])
		payout := max64(0, i64(cargo["_payout"]))
		currency := fmt.Sprint(cargo["_currency"])
		if currency == "" {
			currency = "low_spirit_stone"
		}
		escort := i64(data["escort_strength"])
		conceal := i64(data["concealment"])
		smuggle := i64(data["smuggling"]) != 0
		tax := max64(0, i64(data["tax_rate"]))
		legacySafe := tax == 0 && escort == 0 && conceal == 0 && !smuggle
		effective := int64(0)
		if !legacySafe {
			effective = clamp(int64(math.Round(float64(i64(data["risk"])+map[bool]int64{true: 20, false: 0}[smuggle]-escort*2-conceal)*eraRisk)), 0, 95)
		}
		roll := stablePercentGo(data["caravan_id"], data["origin"], data["destination"], data["depart_game_minute"])
		intercepted := roll < effective
		seized := false
		loss := int64(0)
		if intercepted {
			if smuggle && roll < max64(5, effective/3) {
				seized = true
				loss = 100
			} else {
				loss = min64(75, 20+(effective-roll)/2)
			}
		}
		after := payout * (100 - loss) / 100
		toll := int64(0)
		if !smuggle && !seized {
			toll = after * tax / 100
		}
		final := max64(0, after-toll)
		outcome := "arrived"
		if intercepted {
			outcome = "intercepted"
		}
		if seized {
			outcome = "seized"
		}
		if final > 0 {
			if _, e = walletDeltaTx(conn, userID, currency, final, now); e != nil {
				return authoritativeMutation{}, e
			}
		}
		_, _ = conn.Execute(`UPDATE caravans SET status=?,updated_at=? WHERE caravan_id=?`, []any{outcome, now, i64(data["caravan_id"])})
		lossj, _ := json.Marshal(map[string]any{"percent": loss})
		_, _ = conn.Execute(`UPDATE caravan_operations SET toll_paid=?,intercepted=?,seized=?,payout_final=?,losses_json=?,outcome=?,resolved_game_minute=?,updated_at=? WHERE caravan_id=?`, []any{toll, boolInt(intercepted), boolInt(seized), final, string(lossj), outcome, p.GameMinute, now, i64(data["caravan_id"])})
		det, _ := json.Marshal(map[string]any{"risk": effective, "roll": roll, "loss_percent": loss, "tax": toll, "payout": final})
		_, _ = conn.Execute(`INSERT INTO caravan_events(caravan_id,event_type,detail_json,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{i64(data["caravan_id"]), outcome, string(det), p.GameMinute, now})
		resolved = append(resolved, map[string]any{"caravan_id": i64(data["caravan_id"]), "status": outcome, "payout": final, "currency_id": currency, "loss_percent": loss, "toll_paid": toll, "effective_risk": effective})
	}
	result := map[string]any{"resolved": resolved, "count": len(resolved)}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "caravan", EventType: "caravan.settle", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
