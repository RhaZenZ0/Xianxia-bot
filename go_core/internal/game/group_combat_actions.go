package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type equipmentDefinitionGo struct {
	Slot                                            string
	MaxDurability, Attack, Defense, Spirit, Agility int64
	Indestructible                                  bool
}

func equipmentDefinitionsGo() map[string]equipmentDefinitionGo {
	return map[string]equipmentDefinitionGo{
		"spirit_iron_sword":     {"weapon", 120, 4, 0, 1, 0, false},
		"spirit_iron_armor":     {"armor", 160, 0, 5, 1, -1, false},
		"cloud_stepping_boots":  {"boots", 100, 0, 1, 0, 4, false},
		"lesser_stygian_seal":   {"accessory", 90, 1, 1, 4, 0, false},
		"bone_comb":             {"accessory", 80, 0, 0, 5, 1, false},
		"cracked_nether_mirror": {"accessory", 75, 0, 2, 3, 0, false},
		// A one-of-a-kind GM reward (granted via /admin player grant, never
		// crafted or bought) - Indestructible=true is what actually protects
		// it from durability decay; see indestructibleEquipmentIDsGo below.
		// Keep the four combat stats in sync with combat_actions.go's
		// equipDefs and app/advanced_runtime.py's EQUIPMENT_DEFINITIONS (see
		// tests/python/contracts/test_equipment_stat_parity.py).
		bugslayerSwordItemID: {"weapon", 100, 5, 1, 1, 1, true},
	}
}

const (
	bugslayerSwordItemID = "bugslayer_sword"
	bugslayerPassiveName = "Heavenly Flawfinder"
	// bugslayerPassiveBonusDamage is added on top of normal damage when the
	// passive triggers, in both the 1v1 and boss combat systems.
	bugslayerPassiveBonusDamage = int64(2)
	// bugslayerPassiveMargin matches roll2d10's own "Strong Success" tier
	// (aptitude_actions.go) exactly - not an arbitrary number. The sword
	// rewards a hit that was already convincing by this project's own
	// definition of one.
	bugslayerPassiveMargin = int64(5)
	// bugslayerBossAccuracyMargin: bossActActionGo's roll is a 0-99
	// percentile where lower is better, so requiring the roll to land at
	// least this far under the accuracy threshold restricts the boss passive
	// to a clean hit rather than a merely-connecting one.
	bugslayerBossAccuracyMargin = int64(20)
)

// indestructibleEquipmentIDsGo lists item ids that never lose durability from
// combat wear - hand-authored unique rewards where "cannot be worn down" is a
// mechanical guarantee, not just flavor text. Data-driven off
// equipmentDefinitionsGo so a future indestructible item needs no second
// place to register it.
func indestructibleEquipmentIDsGo() []string {
	var ids []string
	for id, d := range equipmentDefinitionsGo() {
		if d.Indestructible {
			ids = append(ids, id)
		}
	}
	return ids
}

func isIndestructibleEquipmentGo(itemID string) bool {
	d, ok := equipmentDefinitionsGo()[itemID]
	return ok && d.Indestructible
}

func hasEquippedItemGo(conn *storage.Conn, userID int64, itemID string) (bool, error) {
	r, err := conn.Execute(
		`SELECT 1 FROM equipment_instances WHERE user_id=? AND item_id=? AND equipped=1 LIMIT 1`,
		[]any{userID, itemID},
	)
	if err != nil {
		return false, err
	}
	return len(r.Rows) > 0, nil
}

// bugslayerCombatPassiveTriggers gates the 1v1 "Heavenly Flawfinder" passive:
// a Strong Success (or better) attack roll while the sword is equipped.
func bugslayerCombatPassiveTriggers(equipped bool, margin int64) bool {
	return equipped && margin >= bugslayerPassiveMargin
}

// bugslayerBossPassiveTriggers gates the raid version of the same passive:
// only a plain attack (never a Law technique) that lands as a clean hit.
func bugslayerBossPassiveTriggers(equipped bool, style string, accuracy, roll int64) bool {
	return equipped && style == "attack" && accuracy-roll >= bugslayerBossAccuracyMargin
}

type formationPositionGo struct{ Attack, Defense, Support int64 }

var formationPositionsGo = map[string]formationPositionGo{
	"vanguard": {1, 4, 0}, "core": {4, 1, 0}, "flank": {3, 2, 0}, "support": {0, 2, 4},
}

type formationStanceGo struct{ Attack, Defense, CohesionCost int64 }

var formationStancesGo = map[string]formationStanceGo{
	"balanced": {0, 0, 0}, "aggressive": {3, -2, 2}, "defensive": {-1, 4, 1},
}

type bossPhaseGo struct {
	Name                            string
	Threshold                       float64
	Attack, Defense, CohesionDamage int64
}
type bossTemplateGo struct {
	Name, Location                    string
	RealmIndex, MaxHP, RewardCurrency int64
	RewardItem                        string
	RewardQuantity                    int64
	Phases                            []bossPhaseGo
}

var bossTemplatesGo = map[string]bossTemplateGo{
	"iron_tusk_boar_king":     {"Iron-Tusk Boar King", "Greenriver Town", 2, 180, 120, "beast_core", 2, []bossPhaseGo{{"Mountain-Shaking Charge", .66, 8, 3, 4}, {"Blood Frenzy", .33, 11, 2, 7}, {"Last Roar", 0, 14, 1, 10}}},
	"moonfen_drowned_serpent": {"Moonfen Drowned Serpent", "Moonfen Marsh", 4, 260, 220, "beast_core", 3, []bossPhaseGo{{"Drowning Mist", .70, 10, 4, 5}, {"Venom Tide", .35, 14, 3, 8}, {"Blackwater Coil", 0, 18, 2, 12}}},
	"nine_echo_sword_wraith":  {"Nine-Echo Sword Wraith", "Sword Grave of Nine Echoes", 7, 420, 420, "nine_echo_sword_tablet", 1, []bossPhaseGo{{"First Three Echoes", .70, 14, 7, 6}, {"Sixfold Sword Domain", .35, 19, 6, 10}, {"Ninth Echo: Severing", 0, 25, 4, 15}}},
}

type idPayload struct {
	ID         int64 `json:"id"`
	GameMinute int64 `json:"game_minute"`
}
type equipmentBindPayload struct {
	ItemID string `json:"item_id"`
}
type partyCreatePayload struct {
	Name string `json:"name"`
}
type partyJoinPayload struct {
	PartyID int64 `json:"party_id"`
}
type formationCreatePayload struct {
	Name string `json:"name"`
}
type formationAssignPayload struct {
	FormationID  int64  `json:"formation_id"`
	TargetUserID int64  `json:"target_user_id"`
	Position     string `json:"position"`
}
type formationActivatePayload struct {
	FormationID int64  `json:"formation_id"`
	Stance      string `json:"stance"`
}
type bossStartPayload struct {
	TemplateKey string `json:"template_key"`
	GameMinute  int64  `json:"game_minute"`
}
type bossActPayload struct {
	EncounterID int64  `json:"encounter_id"`
	Style       string `json:"style"`
	Technique   string `json:"technique"`
	GameMinute  int64  `json:"game_minute"`
	Version     *int64 `json:"version,omitempty"`
}

func activePartyRow(conn *storage.Conn, userID int64) (map[string]any, error) {
	r, e := conn.Execute(`SELECT p.* FROM parties p JOIN party_members pm ON pm.party_id=p.party_id WHERE pm.user_id=? AND p.status='active' LIMIT 1`, []any{userID})
	if e != nil {
		return nil, e
	}
	return firstRowMap(r), nil
}
func equipmentRows(conn *storage.Conn, userID int64, equippedOnly bool) ([]map[string]any, error) {
	q := `SELECT * FROM equipment_instances WHERE user_id=?`
	if equippedOnly {
		q += ` AND equipped=1`
	}
	q += ` ORDER BY equipment_id`
	r, e := conn.Execute(q, []any{userID})
	if e != nil {
		return nil, e
	}
	return rowsToMaps(r), nil
}
func equipmentPowerRows(rows []map[string]any) map[string]int64 {
	out := map[string]int64{"attack": 0, "defense": 0, "spirit": 0, "agility": 0}
	defs := equipmentDefinitionsGo()
	for _, r := range rows {
		if i64(r["durability"]) <= 0 {
			continue
		}
		d, ok := defs[fmt.Sprint(r["item_id"])]
		if !ok {
			continue
		}
		q := float64(max64(1, i64(r["quality"]))) / 100.0
		out["attack"] += int64(math.Round(float64(d.Attack) * q))
		out["defense"] += int64(math.Round(float64(d.Defense) * q))
		out["spirit"] += int64(math.Round(float64(d.Spirit) * q))
		out["agility"] += int64(math.Round(float64(d.Agility) * q))
	}
	return out
}
func formationBonusGo(conn *storage.Conn, partyID, userID int64) (map[string]int64, error) {
	out := map[string]int64{"attack": 0, "defense": 0, "support": 0}
	r, e := conn.Execute(`SELECT pf.cohesion,pf.stance,fp.position FROM party_formations pf JOIN formation_positions fp ON fp.formation_id=pf.formation_id WHERE pf.party_id=? AND pf.active=1 AND fp.user_id=? LIMIT 1`, []any{partyID, userID})
	if e != nil {
		return out, e
	}
	row := firstRowMap(r)
	if row == nil {
		return out, nil
	}
	pos, ok := formationPositionsGo[fmt.Sprint(row["position"])]
	if !ok {
		return out, nil
	}
	stance := formationStancesGo[fmt.Sprint(row["stance"])]
	scale := math.Max(.25, math.Min(1, float64(i64(row["cohesion"]))/100.0))
	out["attack"] = int64(math.Round(float64(pos.Attack+stance.Attack) * scale))
	out["defense"] = int64(math.Round(float64(pos.Defense+stance.Defense) * scale))
	out["support"] = int64(math.Round(float64(pos.Support) * scale))
	return out, nil
}
func equipmentAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	now := nowSeconds()
	result := map[string]any{}
	switch op {
	case "equipment.bind":
		var p equipmentBindPayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		d, ok := equipmentDefinitionsGo()[p.ItemID]
		if !ok {
			return authoritativeMutation{}, errors.New("that item is not part of the equipment catalog")
		}
		q, e := inventoryQuantityTx(conn, userID, p.ItemID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if q <= 0 {
			return authoritativeMutation{}, errors.New("you do not carry that item")
		}
		if q == 1 {
			_, e = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
		} else {
			_, e = conn.Execute(`UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
		}
		if e != nil {
			return authoritativeMutation{}, e
		}
		c, e := conn.Execute(`INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at) VALUES(?,?,?,?,?,100,0,?,?)`, []any{userID, p.ItemID, d.Slot, d.MaxDurability, d.MaxDurability, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		result = map[string]any{"equipment_id": c.LastInsertID, "item_id": p.ItemID, "slot": d.Slot, "durability": d.MaxDurability, "max_durability": d.MaxDurability}
	case "equipment.equip", "equipment.unequip", "equipment.repair":
		var p idPayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		r, e := conn.Execute(`SELECT * FROM equipment_instances WHERE user_id=? AND equipment_id=?`, []any{userID, p.ID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row := firstRowMap(r)
		if row == nil {
			return authoritativeMutation{}, errors.New("equipment not found")
		}
		if op == "equipment.equip" {
			if i64(row["durability"]) <= 0 {
				return authoritativeMutation{}, errors.New("broken equipment cannot be equipped")
			}
			_, e = conn.Execute(`UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND slot=?`, []any{now, userID, fmt.Sprint(row["slot"])})
			if e == nil {
				_, e = conn.Execute(`UPDATE equipment_instances SET equipped=1,updated_at=? WHERE user_id=? AND equipment_id=?`, []any{now, userID, p.ID})
			}
		}
		if op == "equipment.unequip" {
			_, e = conn.Execute(`UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND equipment_id=?`, []any{now, userID, p.ID})
		}
		if op == "equipment.repair" {
			missing := max64(0, i64(row["max_durability"])-i64(row["durability"]))
			cost := max64(0, (missing+19)/20)
			if missing > 0 {
				q, er := inventoryQuantityTx(conn, userID, "spirit_iron")
				if er != nil {
					return authoritativeMutation{}, er
				}
				if q < cost {
					return authoritativeMutation{}, fmt.Errorf("repair requires %d Spirit Iron", cost)
				}
				if q == cost {
					_, e = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id='spirit_iron'`, []any{userID})
				} else {
					_, e = conn.Execute(`UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id='spirit_iron'`, []any{cost, userID})
				}
				if e == nil {
					_, e = conn.Execute(`UPDATE equipment_instances SET durability=max_durability,updated_at=? WHERE user_id=? AND equipment_id=?`, []any{now, userID, p.ID})
				}
			}
			result["repair_cost"] = cost
		}
		if e != nil {
			return authoritativeMutation{}, e
		}
		result["equipment_id"] = p.ID
		result["operation"] = op
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "equipment", EventType: op, EntityType: "character", EntityID: fmt.Sprint(userID), Payload: result}}, nil
}
func partyAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	now := nowSeconds()
	result := map[string]any{}
	if op == "party.create" {
		var p partyCreatePayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		if a, _ := activePartyRow(conn, userID); a != nil {
			return authoritativeMutation{}, errors.New("already in an active party")
		}
		name := strings.TrimSpace(p.Name)
		if name == "" {
			name = "Cultivation Party"
		}
		if len([]rune(name)) > 80 {
			name = string([]rune(name)[:80])
		}
		c, e := conn.Execute(`INSERT INTO parties(leader_user_id,name,status,created_at,updated_at) VALUES(?,?,'active',?,?)`, []any{userID, name, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, e = conn.Execute(`INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(?,?,'leader',?)`, []any{c.LastInsertID, userID, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		result = map[string]any{"party_id": c.LastInsertID, "name": name}
	} else if op == "party.join" {
		var p partyJoinPayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		if a, _ := activePartyRow(conn, userID); a != nil {
			return authoritativeMutation{}, errors.New("already in an active party")
		}
		r, e := conn.Execute(`SELECT * FROM parties WHERE party_id=? AND status='active'`, []any{p.PartyID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if firstRowMap(r) == nil {
			return authoritativeMutation{}, errors.New("active party not found")
		}
		_, e = conn.Execute(`INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(?,?,'member',?)`, []any{p.PartyID, userID, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		result = map[string]any{"party_id": p.PartyID, "joined": true}
	} else {
		party, e := activePartyRow(conn, userID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if party == nil {
			return authoritativeMutation{}, errors.New("not in an active party")
		}
		pid := i64(party["party_id"])
		r, e := conn.Execute(`SELECT 1 FROM boss_encounters WHERE party_id=? AND status='active'`, []any{pid})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("cannot leave a party during an active boss encounter")
		}
		_, e = conn.Execute(`DELETE FROM party_members WHERE party_id=? AND user_id=?`, []any{pid, userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, _ = conn.Execute(`DELETE FROM formation_positions WHERE user_id=? AND formation_id IN (SELECT formation_id FROM party_formations WHERE party_id=?)`, []any{userID, pid})
		_, _ = conn.Execute(`UPDATE party_formations SET active=0,updated_at=? WHERE party_id=? AND active=1 AND (SELECT COUNT(*) FROM formation_positions fp WHERE fp.formation_id=party_formations.formation_id)<2`, []any{now, pid})
		if i64(party["leader_user_id"]) == userID {
			rr, e := conn.Execute(`SELECT user_id FROM party_members WHERE party_id=? ORDER BY joined_at LIMIT 1`, []any{pid})
			if e != nil {
				return authoritativeMutation{}, e
			}
			n := firstRowMap(rr)
			if n != nil {
				nl := i64(n["user_id"])
				_, _ = conn.Execute(`UPDATE parties SET leader_user_id=?,updated_at=? WHERE party_id=?`, []any{nl, now, pid})
				_, _ = conn.Execute(`UPDATE party_members SET role='leader' WHERE party_id=? AND user_id=?`, []any{pid, nl})
			} else {
				_, _ = conn.Execute(`UPDATE parties SET status='disbanded',updated_at=? WHERE party_id=?`, []any{now, pid})
			}
		}
		result = map[string]any{"party_id": pid, "left": true}
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "party", EventType: op, EntityType: "party", EntityID: fmt.Sprint(result["party_id"]), Payload: result}}, nil
}
func formationAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	party, e := activePartyRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if party == nil {
		return authoritativeMutation{}, errors.New("active party not found")
	}
	pid := i64(party["party_id"])
	now := nowSeconds()
	result := map[string]any{"party_id": pid}
	if op == "formation.create" {
		if i64(party["leader_user_id"]) != userID {
			return authoritativeMutation{}, errors.New("only the party leader can create formations")
		}
		var p formationCreatePayload
		if e = json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		name := strings.TrimSpace(p.Name)
		if name == "" {
			name = "Unnamed Formation"
		}
		c, e := conn.Execute(`INSERT INTO party_formations(party_id,name,stance,cohesion,active,created_at,updated_at) VALUES(?,?,'balanced',100,0,?,?)`, []any{pid, name, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		result["formation_id"] = c.LastInsertID
	} else if op == "formation.assign" {
		if i64(party["leader_user_id"]) != userID {
			return authoritativeMutation{}, errors.New("only the party leader can assign positions")
		}
		var p struct {
			FormationID  int64  `json:"formation_id"`
			TargetUserID int64  `json:"target_user_id"`
			Position     string `json:"position"`
		}
		if e = json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		p.Position = strings.ToLower(strings.TrimSpace(p.Position))
		if _, ok := formationPositionsGo[p.Position]; !ok {
			return authoritativeMutation{}, errors.New("unknown formation position")
		}
		r, _ := conn.Execute(`SELECT 1 FROM party_formations WHERE party_id=? AND formation_id=?`, []any{pid, p.FormationID})
		if firstRowMap(r) == nil {
			return authoritativeMutation{}, errors.New("formation not found")
		}
		r, _ = conn.Execute(`SELECT 1 FROM party_members WHERE party_id=? AND user_id=?`, []any{pid, p.TargetUserID})
		if firstRowMap(r) == nil {
			return authoritativeMutation{}, errors.New("target is not in the party")
		}
		_, e = conn.Execute(`DELETE FROM formation_positions WHERE formation_id=? AND (user_id=? OR position=?)`, []any{p.FormationID, p.TargetUserID, p.Position})
		if e == nil {
			_, e = conn.Execute(`INSERT INTO formation_positions(formation_id,user_id,position,assigned_at) VALUES(?,?,?,?)`, []any{p.FormationID, p.TargetUserID, p.Position, now})
		}
		if e != nil {
			return authoritativeMutation{}, e
		}
		result["formation_id"] = p.FormationID
		result["target_user_id"] = p.TargetUserID
		result["position"] = p.Position
	} else {
		var p formationActivatePayload
		if e = json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		if i64(party["leader_user_id"]) != userID {
			return authoritativeMutation{}, errors.New("only the party leader can change the formation")
		}
		p.Stance = strings.ToLower(strings.TrimSpace(p.Stance))
		if p.Stance == "" {
			p.Stance = "balanced"
		}
		if _, ok := formationStancesGo[p.Stance]; !ok {
			return authoritativeMutation{}, errors.New("unknown formation stance")
		}
		r, _ := conn.Execute(`SELECT 1 FROM party_formations WHERE party_id=? AND formation_id=?`, []any{pid, p.FormationID})
		if firstRowMap(r) == nil {
			return authoritativeMutation{}, errors.New("formation not found")
		}
		if op == "formation.activate" {
			r, _ = conn.Execute(`SELECT COUNT(*) AS n FROM formation_positions WHERE formation_id=?`, []any{p.FormationID})
			if i64(firstRowMap(r)["n"]) < 2 {
				return authoritativeMutation{}, errors.New("formation needs at least two assigned members")
			}
			_, _ = conn.Execute(`UPDATE party_formations SET active=0,updated_at=? WHERE party_id=?`, []any{now, pid})
			_, e = conn.Execute(`UPDATE party_formations SET active=1,stance=?,cohesion=MAX(50,cohesion),updated_at=? WHERE formation_id=?`, []any{p.Stance, now, p.FormationID})
		} else {
			_, e = conn.Execute(`UPDATE party_formations SET stance=?,updated_at=? WHERE formation_id=? AND active=1`, []any{p.Stance, now, p.FormationID})
		}
		if e != nil {
			return authoritativeMutation{}, e
		}
		result["formation_id"] = p.FormationID
		result["stance"] = p.Stance
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "party", EventType: op, EntityType: "formation", EntityID: fmt.Sprint(result["formation_id"]), Payload: result}}, nil
}
func bossStartActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p bossStartPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	t, ok := bossTemplatesGo[p.TemplateKey]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown boss template")
	}
	party, e := activePartyRow(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if party == nil {
		return authoritativeMutation{}, errors.New("active party required")
	}
	pid := i64(party["party_id"])
	r, _ := conn.Execute(`SELECT 1 FROM boss_encounters WHERE party_id=? AND status='active'`, []any{pid})
	if firstRowMap(r) != nil {
		return authoritativeMutation{}, errors.New("party already has an active boss encounter")
	}
	r, e = conn.Execute(`SELECT pm.user_id,c.life_status,c.location,c.vitality_max FROM party_members pm JOIN characters c ON c.user_id=pm.user_id WHERE pm.party_id=?`, []any{pid})
	if e != nil {
		return authoritativeMutation{}, e
	}
	members := rowsToMaps(r)
	if len(members) == 0 {
		return authoritativeMutation{}, errors.New("party has no members")
	}
	for _, m := range members {
		if fmt.Sprint(m["life_status"]) != "alive" || fmt.Sprint(m["location"]) != t.Location {
			return authoritativeMutation{}, fmt.Errorf("all party members must be alive at %s", t.Location)
		}
	}
	scale := .8 + .2*float64(len(members))
	hp := int64(math.Round(float64(t.MaxHP) * scale))
	now := nowSeconds()
	c, e := conn.Execute(`INSERT INTO boss_encounters(party_id,template_key,location,boss_name,boss_hp,boss_hp_max,phase_index,round_index,status,version,started_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,?,0,1,'active',0,?,?,?)`, []any{pid, p.TemplateKey, t.Location, t.Name, hp, hp, p.GameMinute, now, now})
	if e != nil {
		return authoritativeMutation{}, e
	}
	for _, m := range members {
		v := max64(1, i64(m["vitality_max"]))
		if _, e = conn.Execute(`INSERT INTO boss_participants(encounter_id,user_id,vitality,vitality_max,acted_round,total_damage,guard,status,updated_at) VALUES(?,?,?, ?,0,0,0,'active',?)`, []any{c.LastInsertID, i64(m["user_id"]), v, v, now}); e != nil {
			return authoritativeMutation{}, e
		}
	}
	result := map[string]any{"encounter_id": c.LastInsertID, "party_id": pid, "boss_name": t.Name, "boss_hp": hp, "boss_hp_max": hp, "round_index": 1}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "boss", EventType: "boss.start", EntityType: "boss_encounter", EntityID: fmt.Sprint(c.LastInsertID), GameMinute: p.GameMinute, Payload: result}}, nil
}
func bossActActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p bossActPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.Style = strings.ToLower(strings.TrimSpace(p.Style))
	if p.Style != "attack" && p.Style != "technique" && p.Style != "guard" && p.Style != "support" {
		return authoritativeMutation{}, errors.New("style must be attack, technique, guard, or support")
	}
	r, e := conn.Execute(`SELECT * FROM boss_encounters WHERE encounter_id=? AND status='active'`, []any{p.EncounterID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	enc := firstRowMap(r)
	if enc == nil {
		return authoritativeMutation{}, errors.New("active boss encounter not found")
	}
	if p.Version != nil && *p.Version != i64(enc["version"]) {
		return authoritativeMutation{}, errors.New("stale boss encounter version")
	}
	r, e = conn.Execute(`SELECT * FROM boss_participants WHERE encounter_id=? AND user_id=? AND status='active'`, []any{p.EncounterID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	part := firstRowMap(r)
	if part == nil {
		return authoritativeMutation{}, errors.New("you are not an active raid participant")
	}
	round := i64(enc["round_index"])
	if i64(part["acted_round"]) >= round {
		return authoritativeMutation{}, errors.New("you already acted this round")
	}
	t, ok := bossTemplatesGo[fmt.Sprint(enc["template_key"])]
	if !ok {
		return authoritativeMutation{}, errors.New("boss template unavailable")
	}
	phaseIdx := i64(enc["phase_index"])
	if phaseIdx < 0 || int(phaseIdx) >= len(t.Phases) {
		phaseIdx = 0
	}
	phase := t.Phases[phaseIdx]
	now := nowSeconds()
	r, e = conn.Execute(`SELECT attributes_json,realm_index FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	cr := firstRowMap(r)
	attrs := decodeJSONMap(cr["attributes_json"])
	body := i64(attrs["body"])
	spirit := i64(attrs["spirit"])
	agi := i64(attrs["agility"])
	erows, _ := equipmentRows(conn, userID, true)
	equip := equipmentPowerRows(erows)
	form, _ := formationBonusGo(conn, i64(enc["party_id"]), userID)
	base := max64(body, spirit) + i64(cr["realm_index"]) + equip["attack"] + form["attack"]
	events := []string{}
	damage := int64(0)
	if p.Style == "guard" {
		_, e = conn.Execute(`UPDATE boss_participants SET guard=1,acted_round=?,updated_at=? WHERE encounter_id=? AND user_id=?`, []any{round, now, p.EncounterID, userID})
		events = append(events, "You brace within the formation and prepare to absorb the boss counterattack.")
	} else if p.Style == "support" {
		rr, _ := conn.Execute(`SELECT user_id,vitality,vitality_max FROM boss_participants WHERE encounter_id=? AND status='active' ORDER BY vitality*1.0/vitality_max ASC,user_id LIMIT 1`, []any{p.EncounterID})
		tar := firstRowMap(rr)
		heal := max64(2, 2+spirit/2+form["support"])
		if tar != nil {
			_, e = conn.Execute(`UPDATE boss_participants SET vitality=MIN(vitality_max,vitality+?),updated_at=? WHERE encounter_id=? AND user_id=?`, []any{heal, now, p.EncounterID, i64(tar["user_id"])})
			events = append(events, fmt.Sprintf("Support restores %d raid vitality.", heal))
		}
		if e == nil {
			_, e = conn.Execute(`UPDATE boss_participants SET acted_round=?,updated_at=? WHERE encounter_id=? AND user_id=?`, []any{round, now, p.EncounterID, userID})
		}
	} else {
		bonus := int64(1)
		if p.Style == "technique" {
			// Bring this in line with the real combat.technique system:
			// using a technique requires actually having unlocked it (Law
			// stage + realm), and its bonus scales with how deeply that Law
			// is comprehended - not a flat, unconditional upgrade over
			// attack available to every raider regardless of investment.
			p.Technique = strings.TrimSpace(p.Technique)
			techDef, ok := catalog.LawSystem.Techniques[p.Technique]
			if !ok {
				return authoritativeMutation{}, errors.New("unknown Law technique")
			}
			lr, e := conn.Execute(`SELECT comprehension FROM law_progress WHERE user_id=? AND law_id=?`, []any{userID, techDef.Law})
			if e != nil {
				return authoritativeMutation{}, e
			}
			comp := int64(0)
			if row := firstRowMap(lr); row != nil {
				comp = i64(row["comprehension"])
			}
			if lawStageIndex(catalog, comp) < techDef.RequiresStage || i64(cr["realm_index"]) < int64(techDef.MinRealmIndex) {
				return authoritativeMutation{}, errors.New("technique requirements are no longer met")
			}
			bonus = 2 + comp/20
		}
		roll := stablePercentGo(p.EncounterID, round, userID, p.Style, i64(enc["version"]))
		accuracy := 65 + agi*2 + equip["agility"] - phase.Defense*2
		bugslayerGuard := int64(0)
		if roll < clamp(accuracy, 15, 95) {
			damage = max64(1, base+bonus+(100-roll)/20-phase.Defense)
			if p.Style == "attack" {
				hasBugslayer, bsErr := hasEquippedItemGo(conn, userID, bugslayerSwordItemID)
				if bsErr != nil {
					return authoritativeMutation{}, bsErr
				}
				if bugslayerBossPassiveTriggers(hasBugslayer, p.Style, accuracy, roll) {
					damage += bugslayerPassiveBonusDamage
					bugslayerGuard = 1
					events = append(
						events,
						fmt.Sprintf(
							"%s exposes a flaw: +%d damage and the next boss hit against you is disrupted.",
							bugslayerPassiveName,
							bugslayerPassiveBonusDamage,
						),
					)
				}
			}
		}
		_, e = conn.Execute(`UPDATE boss_encounters SET boss_hp=MAX(0,boss_hp-?),updated_at=? WHERE encounter_id=?`, []any{damage, now, p.EncounterID})
		if e == nil {
			_, e = conn.Execute(
				`UPDATE boss_participants SET acted_round=?,total_damage=total_damage+?,guard=MAX(guard,?),updated_at=? WHERE encounter_id=? AND user_id=?`,
				[]any{round, damage, bugslayerGuard, now, p.EncounterID, userID},
			)
		}
		_ = damageEquipmentGo(conn, userID, 1)
		events = append(events, fmt.Sprintf("%s deals %d damage.", strings.Title(p.Style), damage))
	}
	if e != nil {
		return authoritativeMutation{}, e
	}
	rr, e := conn.Execute(`SELECT boss_hp,boss_hp_max FROM boss_encounters WHERE encounter_id=?`, []any{p.EncounterID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	hprow := firstRowMap(rr)
	hp := i64(hprow["boss_hp"])
	hpmax := max64(1, i64(hprow["boss_hp_max"]))
	status := "active"
	if hp <= 0 {
		status = "victory"
		_, e = conn.Execute(`UPDATE boss_encounters SET status='victory',winner_party_id=party_id,finished_game_minute=?,version=version+1,updated_at=? WHERE encounter_id=?`, []any{p.GameMinute, now, p.EncounterID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		rr, _ = conn.Execute(`SELECT user_id FROM boss_participants WHERE encounter_id=?`, []any{p.EncounterID})
		for _, m := range rowsToMaps(rr) {
			_, e = conn.Execute(`INSERT OR IGNORE INTO boss_reward_claims(encounter_id,user_id,currency_amount,item_id,item_quantity,claimed,created_at) VALUES(?,?,?,?,?,0,?)`, []any{p.EncounterID, i64(m["user_id"]), t.RewardCurrency, t.RewardItem, t.RewardQuantity, now})
			if e != nil {
				return authoritativeMutation{}, e
			}
		}
		events = append(events, t.Name+" is defeated. Raid rewards are ready to claim.")
	} else {
		ratio := float64(hp) / float64(hpmax)
		newPhase := int64(len(t.Phases) - 1)
		for idx, ph := range t.Phases {
			if ratio > ph.Threshold {
				newPhase = int64(idx)
				break
			}
		}
		if newPhase != phaseIdx {
			phaseIdx = newPhase
			phase = t.Phases[newPhase]
			_, _ = conn.Execute(`UPDATE boss_encounters SET phase_index=?,version=version+1,updated_at=? WHERE encounter_id=?`, []any{newPhase, now, p.EncounterID})
			events = append(events, "Boss phase shifts to "+phase.Name)
		}
		rr, _ = conn.Execute(`SELECT COUNT(*) AS n FROM boss_participants WHERE encounter_id=? AND status='active' AND acted_round<?`, []any{p.EncounterID, round})
		if i64(firstRowMap(rr)["n"]) == 0 {
			rr, _ = conn.Execute(`SELECT * FROM boss_participants WHERE encounter_id=? AND status='active'`, []any{p.EncounterID})
			for _, tar := range rowsToMaps(rr) {
				tid := i64(tar["user_id"])
				crr, _ := conn.Execute(`SELECT attributes_json,realm_index FROM characters WHERE user_id=?`, []any{tid})
				tc := firstRowMap(crr)
				ta := decodeJSONMap(tc["attributes_json"])
				trs, _ := equipmentRows(conn, tid, true)
				te := equipmentPowerRows(trs)
				tf, _ := formationBonusGo(conn, i64(enc["party_id"]), tid)
				def := i64(ta["body"]) + i64(tc["realm_index"]) + te["defense"] + tf["defense"]
				incoming := max64(1, phase.Attack+stablePercentGo(p.EncounterID, round, tid, "boss")/20-def/2)
				if i64(tar["guard"]) != 0 {
					incoming = max64(1, incoming/2)
				}
				nv := max64(0, i64(tar["vitality"])-incoming)
				st := "active"
				if nv <= 0 {
					st = "knocked_out"
				}
				_, _ = conn.Execute(`UPDATE boss_participants SET vitality=?,status=?,guard=0,updated_at=? WHERE encounter_id=? AND user_id=?`, []any{nv, st, now, p.EncounterID, tid})
				_ = damageEquipmentGo(conn, tid, 1)
				events = append(events, fmt.Sprintf("%s hits %d for %d raid vitality.", phase.Name, tid, incoming))
			}
			fr, _ := conn.Execute(`SELECT formation_id,stance FROM party_formations WHERE party_id=? AND active=1`, []any{i64(enc["party_id"])})
			if f := firstRowMap(fr); f != nil {
				loss := phase.CohesionDamage + formationStancesGo[fmt.Sprint(f["stance"])].CohesionCost
				_, _ = conn.Execute(`UPDATE party_formations SET cohesion=MAX(0,cohesion-?),updated_at=? WHERE formation_id=?`, []any{loss, now, i64(f["formation_id"])})
				events = append(events, fmt.Sprintf("Formation cohesion falls by %d.", loss))
			}
			sr, _ := conn.Execute(`SELECT COUNT(*) AS n FROM boss_participants WHERE encounter_id=? AND status='active'`, []any{p.EncounterID})
			if i64(firstRowMap(sr)["n"]) <= 0 {
				status = "defeat"
				_, e = conn.Execute(`UPDATE boss_encounters SET status='defeat',finished_game_minute=?,version=version+1,updated_at=? WHERE encounter_id=?`, []any{p.GameMinute, now, p.EncounterID})
			} else {
				_, e = conn.Execute(`UPDATE boss_encounters SET round_index=round_index+1,version=version+1,updated_at=? WHERE encounter_id=?`, []any{now, p.EncounterID})
			}
		} else {
			_, e = conn.Execute(`UPDATE boss_encounters SET version=version+1,updated_at=? WHERE encounter_id=?`, []any{now, p.EncounterID})
		}
	}
	if e != nil {
		return authoritativeMutation{}, e
	}
	rr, _ = conn.Execute(`SELECT * FROM boss_encounters WHERE encounter_id=?`, []any{p.EncounterID})
	out := firstRowMap(rr)
	out["events"] = events
	out["action_damage"] = damage
	out["status"] = status
	if status == "active" {
		out["status"] = fmt.Sprint(firstRowMap(rr)["status"])
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "boss", EventType: "boss.act", EntityType: "boss_encounter", EntityID: fmt.Sprint(p.EncounterID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func bossClaimActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p idPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	r, e := conn.Execute(`SELECT * FROM boss_reward_claims WHERE encounter_id=? AND user_id=? AND claimed=0`, []any{p.ID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	row := firstRowMap(r)
	if row == nil {
		return authoritativeMutation{}, errors.New("no unclaimed reward")
	}
	now := nowSeconds()
	amt := i64(row["currency_amount"])
	if amt != 0 {
		if _, e = walletDeltaTx(conn, userID, "low_spirit_stone", amt, now); e != nil {
			return authoritativeMutation{}, e
		}
	}
	item := fmt.Sprint(row["item_id"])
	qty := i64(row["item_quantity"])
	if item != "" && qty > 0 {
		_, e = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity`, []any{userID, item, qty})
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	_, e = conn.Execute(`UPDATE boss_reward_claims SET claimed=1,claimed_at=? WHERE encounter_id=? AND user_id=?`, []any{now, p.ID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	result := map[string]any{"encounter_id": p.ID, "currency_amount": amt, "item_id": item, "item_quantity": qty}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "boss", EventType: "boss.claim", EntityType: "boss_encounter", EntityID: fmt.Sprint(p.ID), Payload: result}}, nil
}
