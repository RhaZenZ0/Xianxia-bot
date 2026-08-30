package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type sectRecommendationPayload struct {
	NPCName    string         `json:"npc_name"`
	SectName   string         `json:"sect_name"`
	Modifier   int64          `json:"modifier"`
	TN         int64          `json:"tn"`
	Bonus      int64          `json:"bonus"`
	GameMinute int64          `json:"game_minute"`
	Location   string         `json:"location"`
	Details    map[string]any `json:"details"`
}
type sectTrialPayload struct {
	SectName          string `json:"sect_name"`
	Examiner          string `json:"examiner"`
	Location          string `json:"location"`
	TrialName         string `json:"trial_name"`
	PrimaryModifier   int64  `json:"primary_modifier"`
	SecondaryModifier int64  `json:"secondary_modifier"`
	BaseTN            int64  `json:"base_tn"`
	GameMinute        int64  `json:"game_minute"`
	PrimaryDetails    any    `json:"primary_details"`
	SecondaryDetails  any    `json:"secondary_details"`
}
type sectItemPayload struct {
	ItemID   string `json:"item_id"`
	Quantity int64  `json:"quantity"`
}
type discipleRequestPayload struct {
	MasterUserID int64 `json:"master_user_id"`
}
type discipleResolvePayload struct {
	RequestID int64 `json:"request_id"`
	Accept    bool  `json:"accept"`
}
type sectManorPayload struct {
	Name       string `json:"name"`
	Facility   string `json:"facility"`
	GameMinute int64  `json:"game_minute"`
}

type checkRollGo struct {
	Die1, Die2, Modifier, TN, Total, Margin int64
	Success                                 bool
}

func roll2d10Go(mod, tn int64) (checkRollGo, error) {
	d1, e := gamerng.D10()
	if e != nil {
		return checkRollGo{}, e
	}
	d2, e := gamerng.D10()
	if e != nil {
		return checkRollGo{}, e
	}
	total := d1 + d2 + mod
	return checkRollGo{d1, d2, mod, tn, total, total - tn, total >= tn}, nil
}
func rollMapGo(r checkRollGo) map[string]any {
	return map[string]any{"die1": r.Die1, "die2": r.Die2, "modifier": r.Modifier, "tn": r.TN, "total": r.Total, "margin": r.Margin, "success": r.Success}
}
func recordSectAttemptGo(conn *storage.Conn, userID int64, sect, kind, npc, loc, result string, score, target, bonus, gm int64, details any, now float64) error {
	b, _ := json.Marshal(details)
	_, e := conn.Execute(`INSERT INTO sect_recruitment_attempts(user_id,sect_name,attempt_type,npc_name,location,result,score,target,recommendation_bonus,details_json,game_minute,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, []any{userID, sect, kind, npc, loc, result, score, target, bonus, string(b), gm, now})
	return e
}
func sectRecommendationActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectRecommendationPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.NPCName = strings.TrimSpace(p.NPCName)
	p.SectName = strings.TrimSpace(p.SectName)
	if p.NPCName == "" || p.SectName == "" {
		return authoritativeMutation{}, errors.New("npc_name and sect_name are required")
	}
	if m, _ := sectMembershipRow(conn, userID); m != nil {
		return authoritativeMutation{}, errors.New("already belongs to a public sect")
	}
	r, e := conn.Execute(`SELECT 1 FROM sect_recommendations WHERE user_id=? AND sect_name=? AND status='active'`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if firstRowMap(r) != nil {
		return authoritativeMutation{}, errors.New("an active recommendation already exists")
	}
	r, e = conn.Execute(`SELECT result,game_minute FROM sect_recruitment_attempts WHERE user_id=? AND sect_name=? AND attempt_type='recommendation' ORDER BY attempt_id DESC LIMIT 1`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if x := firstRowMap(r); x != nil && fmt.Sprint(x["result"]) == "fail" && p.GameMinute-i64(x["game_minute"]) < 1440 {
		return authoritativeMutation{}, errors.New("recommendation retry cooldown is still active")
	}
	if p.TN < 8 {
		p.TN = 8
	}
	roll, e := roll2d10Go(p.Modifier, p.TN)
	if e != nil {
		return authoritativeMutation{}, e
	}
	now := nowSeconds()
	route := false
	recID := int64(0)
	if roll.Success {
		_, _ = conn.Execute(`UPDATE sect_recommendations SET status='superseded',updated_at=? WHERE user_id=? AND sect_name=? AND status='active'`, []any{now, userID, p.SectName})
		c, e := conn.Execute(`INSERT INTO sect_recommendations(user_id,npc_name,sect_name,bonus,status,issued_game_minute,created_at,updated_at) VALUES(?,?,?,?,'active',?,?,?)`, []any{userID, p.NPCName, p.SectName, max64(0, p.Bonus), p.GameMinute, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		recID = c.LastInsertID
		_, _ = conn.Execute(`INSERT OR IGNORE INTO character_sect_discoveries(user_id,sect_name,discovery_kind,source_key,discovered_game_minute,created_at) VALUES(?,?, 'npc_recommendation', ?, ?, ?)`, []any{userID, p.SectName, p.NPCName, p.GameMinute, now})
		if p.Location != "" {
			c, _ := conn.Execute(`INSERT OR IGNORE INTO character_location_discoveries(user_id,location,discovery_kind,discovered_game_minute,created_at) VALUES(?,?,'npc_recommendation',?,?)`, []any{userID, p.Location, p.GameMinute, now})
			route = c.RowsAffected > 0
		}
	}
	details := map[string]any{"roll": rollMapGo(roll), "route_revealed": route}
	for k, v := range p.Details {
		details[k] = v
	}
	if e = recordSectAttemptGo(conn, userID, p.SectName, "recommendation", p.NPCName, p.Location, map[bool]string{true: "pass", false: "fail"}[roll.Success], roll.Total, roll.TN, map[bool]int64{true: p.Bonus, false: 0}[roll.Success], p.GameMinute, details, now); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"success": roll.Success, "roll": rollMapGo(roll), "recommendation_id": recID, "recommendation_bonus": map[bool]int64{true: p.Bonus, false: 0}[roll.Success], "route_revealed": route, "sect_name": p.SectName, "npc_name": p.NPCName}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect.recruitment.recommendation", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func sectTrialActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectTrialPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if m, _ := sectMembershipRow(conn, userID); m != nil {
		return authoritativeMutation{}, errors.New("already belongs to a public sect")
	}
	r, e := conn.Execute(`SELECT 1 FROM character_sect_discoveries WHERE user_id=? AND sect_name=?`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if firstRowMap(r) == nil {
		return authoritativeMutation{}, errors.New("sect has not been discovered")
	}
	r, e = conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	c := firstRowMap(r)
	if c == nil || fmt.Sprint(c["location"]) != p.Location {
		return authoritativeMutation{}, errors.New("character is not at the sect trial location")
	}
	r, _ = conn.Execute(`SELECT result,game_minute FROM sect_recruitment_attempts WHERE user_id=? AND sect_name=? AND attempt_type='trial' ORDER BY attempt_id DESC LIMIT 1`, []any{userID, p.SectName})
	if x := firstRowMap(r); x != nil && fmt.Sprint(x["result"]) == "fail" && p.GameMinute-i64(x["game_minute"]) < 1440 {
		return authoritativeMutation{}, errors.New("entrance trial retry cooldown is still active")
	}
	r, e = conn.Execute(`SELECT * FROM sect_recommendations WHERE user_id=? AND sect_name=? AND status='active' ORDER BY recommendation_id DESC LIMIT 1`, []any{userID, p.SectName})
	if e != nil {
		return authoritativeMutation{}, e
	}
	rec := firstRowMap(r)
	recBonus := int64(0)
	if rec != nil {
		recBonus = i64(rec["bonus"])
	}
	if p.BaseTN < 8 {
		p.BaseTN = 8
	}
	primary, e := roll2d10Go(p.PrimaryModifier, p.BaseTN)
	if e != nil {
		return authoritativeMutation{}, e
	}
	secondary, e := roll2d10Go(p.SecondaryModifier, max64(8, p.BaseTN-1))
	if e != nil {
		return authoritativeMutation{}, e
	}
	successes := 0
	if primary.Margin >= 0 {
		successes++
	}
	if secondary.Margin >= 0 {
		successes++
	}
	combined := primary.Margin + secondary.Margin
	outcome := "fail"
	if successes == 2 || combined >= 2 {
		outcome = "pass"
	} else if rec != nil && successes >= 1 && combined >= -2 {
		outcome = "conditional_pass"
	}
	now := nowSeconds()
	if rec != nil {
		_, e = conn.Execute(`UPDATE sect_recommendations SET status='used',used_game_minute=?,updated_at=? WHERE recommendation_id=? AND status='active'`, []any{p.GameMinute, now, i64(rec["recommendation_id"])})
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	if outcome == "pass" || outcome == "conditional_pass" {
		_, e = conn.Execute(`INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(?,?,'Outer Disciple',10,?) ON CONFLICT(user_id) DO UPDATE SET sect_name=excluded.sect_name,rank_name=excluded.rank_name,rank_level=excluded.rank_level,joined_at=excluded.joined_at`, []any{userID, p.SectName, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if _, e = adjustReputationTx(conn, userID, p.SectName, 5, "Passed sect entrance trial", now); e != nil {
			return authoritativeMutation{}, e
		}
	}
	details := map[string]any{"trial_name": p.TrialName, "primary_roll": rollMapGo(primary), "secondary_roll": rollMapGo(secondary), "primary_factors": p.PrimaryDetails, "secondary_factors": p.SecondaryDetails}
	if rec != nil {
		details["recommendation_source"] = fmt.Sprint(rec["npc_name"])
	}
	if e = recordSectAttemptGo(conn, userID, p.SectName, "trial", p.Examiner, p.Location, outcome, primary.Total+secondary.Total, primary.TN+secondary.TN, recBonus, p.GameMinute, details, now); e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"outcome": outcome, "primary": rollMapGo(primary), "secondary": rollMapGo(secondary), "recommendation_bonus": recBonus, "sect_name": p.SectName}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect.recruitment.trial", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func sectEconomyActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	var p sectItemPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.Quantity <= 0 {
		p.Quantity = 1
	}
	mem, e := sectMembershipRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("you are not a member of a sect")
	}
	sect := fmt.Sprint(mem["sect_name"])
	item, ok := catalog.Items[p.ItemID]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown item")
	}
	unit := max64(1, item.SectValue)
	now := nowSeconds()
	out := map[string]any{"sect_name": sect, "item_id": p.ItemID, "quantity": p.Quantity}
	if op == "sect.contribute" {
		q, e := inventoryQuantityTx(conn, userID, p.ItemID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if q < p.Quantity {
			return authoritativeMutation{}, errors.New("not enough of that item")
		}
		if q == p.Quantity {
			_, e = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
		} else {
			_, e = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?`, []any{p.Quantity, userID, p.ItemID})
		}
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, _ = conn.Execute(`INSERT INTO sects(sect_name,updated_at) VALUES(?,?) ON CONFLICT(sect_name) DO NOTHING`, []any{sect, now})
		_, e = conn.Execute(`INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES(?,?,?) ON CONFLICT(sect_name,item_id) DO UPDATE SET quantity=sect_treasury.quantity+excluded.quantity`, []any{sect, p.ItemID, p.Quantity})
		if e != nil {
			return authoritativeMutation{}, e
		}
		points := p.Quantity * unit
		_, _ = conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points+?,influence=influence+? WHERE user_id=?`, []any{points, max64(1, points/10), userID})
		_, _ = conn.Execute(`UPDATE sect_lineage SET attention=attention+? WHERE disciple_user_id=?`, []any{max64(1, points/20), userID})
		out["points"] = points
	} else {
		r, e := conn.Execute(`SELECT resources FROM sect_politics_state WHERE sect_name=?`, []any{sect})
		if e != nil {
			return authoritativeMutation{}, e
		}
		resources := int64(50)
		if x := firstRowMap(r); x != nil {
			resources = i64(x["resources"])
		}
		mult := 1.0
		if resources < 25 {
			mult = 1.60
		} else if resources < 50 {
			mult = 1.35
		} else if resources < 80 {
			mult = 1.20
		}
		unit = max64(1, int64(math.Round(float64(unit)*mult)))
		cost := unit * p.Quantity
		points := i64(mem["contribution_points"])
		if points < cost {
			return authoritativeMutation{}, errors.New("not enough sect contribution points")
		}
		r, e = conn.Execute(`SELECT quantity FROM sect_treasury WHERE sect_name=? AND item_id=?`, []any{sect, p.ItemID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if x := firstRowMap(r); x == nil || i64(x["quantity"]) < p.Quantity {
			return authoritativeMutation{}, errors.New("the sect treasury does not have enough of that item")
		}
		_, _ = conn.Execute(`UPDATE sect_treasury SET quantity=quantity-? WHERE sect_name=? AND item_id=?`, []any{p.Quantity, sect, p.ItemID})
		_, _ = conn.Execute(`UPDATE sect_membership SET contribution_points=contribution_points-? WHERE user_id=?`, []any{cost, userID})
		_, e = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity`, []any{userID, p.ItemID, p.Quantity})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["unit_cost"] = unit
		out["cost"] = cost
		out["remaining_points"] = points - cost
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: op, EntityType: "sect", EntityID: sect, Payload: out}}, nil
}
func discipleshipActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	now := nowSeconds()
	out := map[string]any{}
	if op == "discipleship.request" {
		var p discipleRequestPayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		if p.MasterUserID == userID {
			return authoritativeMutation{}, errors.New("a cultivator cannot request themselves as master")
		}
		r, e := conn.Execute(`SELECT user_id,realm_index,phase,life_status FROM characters WHERE user_id IN (?,?)`, []any{userID, p.MasterUserID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		chars := rowsToMaps(r)
		if len(chars) != 2 {
			return authoritativeMutation{}, errors.New("both cultivators must have characters")
		}
		power := map[int64]int64{}
		for _, c := range chars {
			if fmt.Sprint(c["life_status"]) != "alive" {
				return authoritativeMutation{}, errors.New("master-disciple contracts require living incarnations")
			}
			power[i64(c["user_id"])] = i64(c["realm_index"])*10 + i64(c["phase"])
		}
		if power[p.MasterUserID] <= power[userID] {
			return authoritativeMutation{}, errors.New("requested master must have a higher cultivation stage")
		}
		r, _ = conn.Execute(`SELECT 1 FROM sect_lineage WHERE disciple_user_id=?`, []any{userID})
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("you already have a recorded master")
		}
		r, _ = conn.Execute(`SELECT user_id,sect_name FROM sect_membership WHERE user_id IN (?,?)`, []any{userID, p.MasterUserID})
		ms := rowsToMaps(r)
		if len(ms) == 2 && fmt.Sprint(ms[0]["sect_name"]) != fmt.Sprint(ms[1]["sect_name"]) {
			return authoritativeMutation{}, errors.New("master and disciple must belong to the same sect")
		}
		_, _ = conn.Execute(`UPDATE disciple_requests SET status='superseded',resolved_at=? WHERE disciple_user_id=? AND status='pending'`, []any{now, userID})
		c, e := conn.Execute(`INSERT INTO disciple_requests(disciple_user_id,master_user_id,status,created_at) VALUES(?,?,'pending',?)`, []any{userID, p.MasterUserID, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out = map[string]any{"request_id": c.LastInsertID, "disciple_user_id": userID, "master_user_id": p.MasterUserID, "status": "pending"}
	} else if op == "discipleship.resolve" {
		var p discipleResolvePayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		r, e := conn.Execute(`SELECT * FROM disciple_requests WHERE request_id=? AND status='pending'`, []any{p.RequestID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		req := firstRowMap(r)
		if req == nil {
			return authoritativeMutation{}, errors.New("pending disciple request does not exist")
		}
		if i64(req["master_user_id"]) != userID {
			return authoritativeMutation{}, errors.New("only the requested master can resolve this request")
		}
		disciple := i64(req["disciple_user_id"])
		if !p.Accept {
			_, e = conn.Execute(`UPDATE disciple_requests SET status='rejected',resolved_at=? WHERE request_id=?`, []any{now, p.RequestID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			out = map[string]any{"request_id": p.RequestID, "disciple_user_id": disciple, "master_user_id": userID, "status": "rejected"}
		} else {
			r, _ = conn.Execute(`SELECT 1 FROM sect_lineage WHERE disciple_user_id=?`, []any{disciple})
			if firstRowMap(r) != nil {
				return authoritativeMutation{}, errors.New("that cultivator already has a recorded master")
			}
			r, e = conn.Execute(`SELECT user_id,realm_index,phase,life_status FROM characters WHERE user_id IN (?,?)`, []any{disciple, userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			power := map[int64]int64{}
			for _, c := range rowsToMaps(r) {
				if fmt.Sprint(c["life_status"]) != "alive" {
					return authoritativeMutation{}, errors.New("both master and disciple must still be living")
				}
				power[i64(c["user_id"])] = i64(c["realm_index"])*10 + i64(c["phase"])
			}
			if power[userID] <= power[disciple] {
				return authoritativeMutation{}, errors.New("requested master must still have a higher cultivation stage")
			}
			cursor := userID
			seen := map[int64]bool{disciple: true}
			for i := 0; i < 64; i++ {
				if seen[cursor] {
					return authoritativeMutation{}, errors.New("that contract would create a lineage cycle")
				}
				seen[cursor] = true
				rr, _ := conn.Execute(`SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?`, []any{cursor})
				x := firstRowMap(rr)
				if x == nil {
					break
				}
				cursor = i64(x["master_user_id"])
			}
			_, e = conn.Execute(`INSERT INTO sect_lineage(disciple_user_id,master_user_id,accepted_at,attention) VALUES(?,?,?,0)`, []any{disciple, userID, now})
			if e != nil {
				return authoritativeMutation{}, e
			}
			_, _ = conn.Execute(`UPDATE disciple_requests SET status='accepted',resolved_at=? WHERE request_id=?`, []any{now, p.RequestID})
			_, _ = conn.Execute(`UPDATE disciple_requests SET status='superseded',resolved_at=? WHERE disciple_user_id=? AND status='pending' AND request_id<>?`, []any{now, disciple, p.RequestID})
			out = map[string]any{"request_id": p.RequestID, "disciple_user_id": disciple, "master_user_id": userID, "status": "accepted"}
		}
	} else {
		r, e := conn.Execute(`SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		x := firstRowMap(r)
		if x == nil {
			return authoritativeMutation{}, errors.New("no recorded master")
		}
		master := i64(x["master_user_id"])
		_, e = conn.Execute(`DELETE FROM sect_lineage WHERE disciple_user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out = map[string]any{"disciple_user_id": userID, "master_user_id": master, "severed": true}
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: op, EntityType: "character", EntityID: fmt.Sprint(userID), Payload: out}}, nil
}

var sectManorFoundationCostGo = map[string]int64{"spirit_iron": 30, "spirit_herb": 20, "beast_core": 10}

type manorFacilityGo struct {
	Column string
	Cost   map[string]int64
}

var manorFacilitiesGo = map[string]manorFacilityGo{"qi_array": {"qi_array_level", map[string]int64{"spirit_herb": 8, "beast_core": 3, "spirit_iron": 2}}, "alchemy_hall": {"alchemy_hall_level", map[string]int64{"spirit_herb": 10, "beast_core": 2, "spirit_iron": 3}}, "forge_pavilion": {"forge_pavilion_level", map[string]int64{"spirit_iron": 10, "beast_core": 2, "spirit_herb": 2}}, "defense_array": {"defense_array_level", map[string]int64{"spirit_iron": 8, "beast_core": 4, "spirit_herb": 2}}}

func consumeSectTreasuryCostGo(conn *storage.Conn, sect string, cost map[string]int64) error {
	for item, qty := range cost {
		r, e := conn.Execute(`SELECT quantity FROM sect_treasury WHERE sect_name=? AND item_id=?`, []any{sect, item})
		if e != nil {
			return e
		}
		if x := firstRowMap(r); x == nil || i64(x["quantity"]) < qty {
			return fmt.Errorf("sect treasury is missing %s x%d", item, qty)
		}
	}
	for item, qty := range cost {
		if _, e := conn.Execute(`UPDATE sect_treasury SET quantity=quantity-? WHERE sect_name=? AND item_id=?`, []any{qty, sect, item}); e != nil {
			return e
		}
	}
	return nil
}
func sectManorActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	var p sectManorPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	mem, e := sectMembershipRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("you are not a sect member")
	}
	sect := fmt.Sprint(mem["sect_name"])
	now := nowSeconds()
	out := map[string]any{"sect_name": sect}
	if op == "sect.manor.establish" {
		if i64(mem["rank_level"]) < 70 {
			return authoritativeMutation{}, errors.New("only a Sect Master or Ancestor can establish the sect manor")
		}
		r, e := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		c := firstRowMap(r)
		loc := fmt.Sprint(c["location"])
		if strings.HasPrefix(loc, "abode:") || strings.HasPrefix(loc, "personal_world:") {
			return authoritativeMutation{}, errors.New("choose a normal world location")
		}
		r, _ = conn.Execute(`SELECT 1 FROM sect_manors WHERE sect_name=?`, []any{sect})
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("your sect already has a persistent manor")
		}
		name := strings.TrimSpace(p.Name)
		if len([]rune(name)) < 3 {
			return authoritativeMutation{}, errors.New("the sect manor needs a name of at least three characters")
		}
		if len([]rune(name)) > 80 {
			name = string([]rune(name)[:80])
		}
		if e = consumeSectTreasuryCostGo(conn, sect, sectManorFoundationCostGo); e != nil {
			return authoritativeMutation{}, e
		}
		_, e = conn.Execute(`INSERT INTO sect_manors(sect_name,name,base_location,founded_by_user_id,created_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,?)`, []any{sect, name, loc, userID, p.GameMinute, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		costj, _ := json.Marshal(sectManorFoundationCostGo)
		_, _ = conn.Execute(`INSERT INTO sect_manor_projects(sect_name,user_id,project_type,facility_key,from_level,to_level,cost_json,game_minute,created_at) VALUES(?,?,'establish','foundation',0,0,?,?,?)`, []any{sect, userID, string(costj), p.GameMinute, now})
		out["name"] = name
		out["base_location"] = loc
	} else {
		f, ok := manorFacilitiesGo[p.Facility]
		if !ok {
			return authoritativeMutation{}, errors.New("unknown sect-manor facility")
		}
		if i64(mem["rank_level"]) < 50 {
			return authoritativeMutation{}, errors.New("only an Elder or higher-ranked sect member can direct manor construction")
		}
		r, e := conn.Execute(`SELECT * FROM sect_manors WHERE sect_name=?`, []any{sect})
		if e != nil {
			return authoritativeMutation{}, e
		}
		m := firstRowMap(r)
		if m == nil {
			return authoritativeMutation{}, errors.New("your sect has not established a manor")
		}
		r, _ = conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
		if c := firstRowMap(r); c == nil || fmt.Sprint(c["location"]) != fmt.Sprint(m["base_location"]) {
			return authoritativeMutation{}, errors.New("construction must be directed at the sect manor")
		}
		cur := i64(m[f.Column])
		if cur >= 5 {
			return authoritativeMutation{}, errors.New("facility is already at maximum level")
		}
		target := cur + 1
		cost := map[string]int64{}
		for item, base := range f.Cost {
			cost[item] = base * target
		}
		if e = consumeSectTreasuryCostGo(conn, sect, cost); e != nil {
			return authoritativeMutation{}, e
		}
		allowed := map[string]bool{"qi_array_level": true, "alchemy_hall_level": true, "forge_pavilion_level": true, "defense_array_level": true}
		if !allowed[f.Column] {
			return authoritativeMutation{}, errors.New("invalid facility column")
		}
		q := fmt.Sprintf("UPDATE sect_manors SET %s=?,updated_at=? WHERE sect_name=?", f.Column)
		if _, e = conn.Execute(q, []any{target, now, sect}); e != nil {
			return authoritativeMutation{}, e
		}
		costj, _ := json.Marshal(cost)
		_, _ = conn.Execute(`INSERT INTO sect_manor_projects(sect_name,user_id,project_type,facility_key,from_level,to_level,cost_json,game_minute,created_at) VALUES(?,?,'upgrade',?,?,?,?,?,?)`, []any{sect, userID, p.Facility, cur, target, string(costj), p.GameMinute, now})
		out["facility"] = p.Facility
		out["from_level"] = cur
		out["to_level"] = target
		out["cost"] = cost
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: op, EntityType: "sect", EntityID: sect, GameMinute: p.GameMinute, Payload: out}}, nil
}
