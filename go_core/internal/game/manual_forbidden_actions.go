package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type manualStudyPayload struct {
	ManualID        string `json:"manual_id"`
	CooldownSeconds int64  `json:"cooldown_seconds"`
	GameMinute      int64  `json:"game_minute"`
}
type manualTechniquePayload struct {
	TechniqueID string `json:"technique_id"`
	GameMinute  int64  `json:"game_minute"`
}

func clampI(v, lo, hi int64) int64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
func containsFold(xs []string, vals ...string) bool {
	for _, x := range xs {
		for _, v := range vals {
			if strings.EqualFold(strings.TrimSpace(x), v) {
				return true
			}
		}
	}
	return false
}
func manualForbidden(m worlddata.ManualDefinition) bool {
	return strings.EqualFold(m.Alignment, "Demonic") || containsFold(m.Tags, "forbidden", "demonic", "evil")
}
func techniqueForbidden(t worlddata.ManualTechniqueDefinition, m worlddata.ManualDefinition) bool {
	return t.KarmaCost > 0 || containsFold(t.Tags, "forbidden", "demonic", "evil", "sacrificial", "soul_devouring") || manualForbidden(m)
}

func manualRow(conn *storage.Conn, userID int64, manualID string) (map[string]any, error) {
	r, e := conn.Execute(`SELECT * FROM character_manuals WHERE user_id=? AND manual_id=?`, []any{userID, manualID})
	if e != nil {
		return nil, e
	}
	return firstRowMap(r), nil
}
func practiceManualTx(conn *storage.Conn, userID int64, manualID string, amount int64, now float64) (map[string]any, error) {
	row, e := manualRow(conn, userID, manualID)
	if e != nil {
		return nil, e
	}
	if row == nil {
		return nil, errors.New("manual is not learned")
	}
	mastery, practice := i64(row["mastery"]), i64(row["practice"])
	if amount < 1 {
		amount = 1
	}
	practice += amount
	thresholds := []int64{3, 8, 16, 28}
	for mastery < 4 && practice >= thresholds[mastery] {
		mastery++
	}
	_, e = conn.Execute(`UPDATE character_manuals SET mastery=?,practice=?,updated_at=? WHERE user_id=? AND manual_id=?`, []any{mastery, practice, now, userID, manualID})
	if e != nil {
		return nil, e
	}
	return map[string]any{"user_id": userID, "manual_id": manualID, "mastery": mastery, "practice": practice}, nil
}

func manualStudyAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p manualStudyPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.ManualID = strings.TrimSpace(p.ManualID)
	m, ok := catalog.TechniqueSystem.Manuals[p.ManualID]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown cultivation manual")
	}
	c, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if c.RealmIndex < m.MinRealmIndex {
		return authoritativeMutation{}, fmt.Errorf("realm %d is below manual requirement %d", c.RealmIndex, m.MinRealmIndex)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if p.CooldownSeconds <= 0 {
		p.CooldownSeconds = 2700
	}
	key := "manual:" + p.ManualID
	if rem, e := cooldownRemaining(conn, userID, key, now); e != nil {
		return authoritativeMutation{}, e
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("manual study cooldown: %d seconds", rem)
	}
	row, e := manualRow(conn, userID, p.ManualID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	first := row == nil
	if first {
		inv, e := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, m.ItemID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		ir := firstRowMap(inv)
		if ir == nil || i64(ir["quantity"]) <= 0 {
			return authoritativeMutation{}, errors.New("manual item is not possessed")
		}
		_, e = conn.Execute(`INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(?,?,0,0,?,?)`, []any{userID, p.ManualID, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row, e = manualRow(conn, userID, p.ManualID)
		if e != nil {
			return authoritativeMutation{}, e
		}
	} else {
		gain := int64(1) + c.Attributes["insight"]/5
		row, e = practiceManualTx(conn, userID, p.ManualID, gain, now)
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	karma := int64(0)
	kr, e := conn.Execute(`SELECT karma_score FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if rr := firstRowMap(kr); rr != nil {
		karma = i64(rr["karma_score"])
	}
	if first && manualForbidden(m) {
		karma -= 1
		_, e = conn.Execute(`UPDATE characters SET karma_score=?,updated_at=? WHERE user_id=?`, []any{karma, now, userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	if e = setCooldown(conn, userID, key, p.CooldownSeconds, now); e != nil {
		return authoritativeMutation{}, e
	}
	result := map[string]any{"manual_id": p.ManualID, "first_study": first, "state": row, "forbidden": manualForbidden(m), "karma_score": karma}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "manuals", EventType: "manual.study", EntityType: "manual", EntityID: p.ManualID, GameMinute: p.GameMinute, Payload: result}}, nil
}

func recordCrimeTx(conn *storage.Conn, userID int64, jurisdiction, crimeType, description, witnessType, witnessKey string, severity, evidence, gameMinute int64, now float64) (map[string]any, error) {
	severity = clampI(severity, 1, 10)
	evidence = clampI(evidence, 0, 100)
	cur, e := conn.Execute(`INSERT INTO crime_records(user_id,jurisdiction,crime_type,severity,evidence,status,description,created_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,'open',?,?,?,?)`, []any{userID, jurisdiction, crimeType, severity, evidence, description, gameMinute, now, now})
	if e != nil {
		return nil, e
	}
	crimeID := cur.LastInsertID
	if witnessType != "" && witnessKey != "" {
		_, e = conn.Execute(`INSERT INTO witness_records(crime_id,witness_type,witness_key,reliability,statement,created_at) VALUES(?,?,?,?,?,?)`, []any{crimeID, witnessType, witnessKey, evidence, description, now})
		if e != nil {
			return nil, e
		}
	}
	var bounty any = nil
	if severity >= 3 && evidence >= 50 {
		amount := severity * 50
		if evidence >= 85 {
			amount *= 2
		}
		b, e := conn.Execute(`INSERT INTO bounties(user_id,jurisdiction,amount,status,reason,source_crime_id,created_game_minute,created_at,updated_at) VALUES(?,?,?,'active',?,?,?,?,?)`, []any{userID, jurisdiction, amount, description, crimeID, gameMinute, now, now})
		if e != nil {
			return nil, e
		}
		bounty = b.LastInsertID
	}
	return map[string]any{"crime_id": crimeID, "bounty_id": bounty, "severity": severity, "evidence": evidence}, nil
}

func forbiddenPolicy(catalog worlddata.Catalog, alignment string) (int64, int64, int64) {
	unrest, family, sect := int64(2), int64(2), int64(2)
	fr, ok := catalog.WorldRules["forbidden_arts"].(map[string]any)
	if !ok {
		return unrest, family, sect
	}
	key := "orthodox_public_use"
	if strings.EqualFold(alignment, "Demonic") {
		key = "demonic_public_use"
	}
	profile, ok := fr[key].(map[string]any)
	if !ok {
		return unrest, family, sect
	}
	if v := i64(profile["regional_unrest"]); v >= 0 {
		unrest = v
	}
	if v := i64(profile["family_stability_loss"]); v >= 0 {
		family = v
	}
	if v := i64(profile["sect_cohesion_loss"]); v >= 0 {
		sect = v
	}
	return unrest, family, sect
}

func applyForbiddenWorldTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, techID, name, location string, gameMinute, exposure, karmaCost int64, witnessed bool, now float64) ([]string, error) {
	impacts := []string{}
	severity := clampI(exposure, 1, 10)
	if !witnessed {
		severity = max64(1, severity/3)
		impacts = append(impacts, "the forbidden art was largely concealed, reducing political exposure")
	}
	sectName, alignment := "", "Neutral"
	sm, e := conn.Execute(`SELECT sect_name FROM sect_membership WHERE user_id=?`, []any{userID})
	if e == nil {
		if r := firstRowMap(sm); r != nil {
			sectName = fmt.Sprint(r["sect_name"])
			if def, ok := catalog.Sects[sectName]; ok {
				alignment = def.Alignment
			}
		}
	}
	unrestDelta, familyLoss, sectLoss := forbiddenPolicy(catalog, alignment)
	unrestDelta *= severity
	familyLoss *= severity
	sectLoss *= severity
	if witnessed {
		_, _ = conn.Execute(`UPDATE civilization_regions SET unrest=MIN(100,unrest+?),security=MAX(0,security-?),updated_at=? WHERE location=?`, []any{unrestDelta, max64(1, severity/2), now, location})
		_, _ = conn.Execute(`INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{location, fmt.Sprintf("Witnesses reported %s, a forbidden cultivation art.", name), severity, gameMinute, now})
		impacts = append(impacts, location+" gained unrest from reports of forbidden cultivation")
		fam, e := conn.Execute(`SELECT family_id FROM character_birth_family WHERE user_id=?`, []any{userID})
		if e == nil {
			if fr := firstRowMap(fam); fr != nil {
				fid := i64(fr["family_id"])
				_, _ = conn.Execute(`UPDATE birth_families SET stability=MAX(0,stability-?),influence=MAX(0,influence-?),updated_at=? WHERE family_id=?`, []any{familyLoss, max64(1, familyLoss/2), now, fid})
				impacts = append(impacts, "the birth family lost stability and influence from the scandal")
			}
		}
		if sectName != "" {
			if strings.EqualFold(alignment, "Demonic") {
				_, _ = conn.Execute(`UPDATE sect_politics_state SET influence=MIN(100,influence+?),doctrine_pressure=MIN(100,doctrine_pressure+?),updated_at=? WHERE sect_name=?`, []any{max64(1, severity/2), severity, now, sectName})
				impacts = append(impacts, sectName+" approved the ruthless display")
			} else if strings.EqualFold(alignment, "Orthodox") {
				_, _ = conn.Execute(`UPDATE sect_politics_state SET cohesion=MAX(0,cohesion-?),influence=MAX(0,influence-?),doctrine_pressure=MIN(100,doctrine_pressure+?),updated_at=? WHERE sect_name=?`, []any{sectLoss, max64(1, sectLoss/2), severity * 2, now, sectName})
				impacts = append(impacts, sectName+" suffered internal pressure over exposed forbidden arts")
			} else {
				_, _ = conn.Execute(`UPDATE sect_politics_state SET cohesion=MAX(0,cohesion-?),doctrine_pressure=MIN(100,doctrine_pressure+?),updated_at=? WHERE sect_name=?`, []any{max64(1, sectLoss/2), severity, now, sectName})
				impacts = append(impacts, sectName+" became divided over the forbidden technique")
			}
			_, _ = conn.Execute(`INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{sectName, fmt.Sprintf("A member publicly used %s; elders and rivals reacted according to sect doctrine.", name), severity, gameMinute, now})
		}
	}
	payload, _ := json.Marshal(map[string]any{"technique": name, "witnessed": witnessed, "karma_cost": karmaCost, "impacts": impacts})
	_, e = conn.Execute(`INSERT INTO world_action_events(user_id,action_type,target_type,target_key,location,severity,game_minute,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)`, []any{userID, "forbidden_art_used", "technique", techID, location, severity, gameMinute, string(payload), now})
	return impacts, e
}

func manualTechniqueAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p manualTechniquePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.TechniqueID = strings.TrimSpace(p.TechniqueID)
	t, ok := catalog.TechniqueSystem.Techniques[p.TechniqueID]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown manual technique")
	}
	m, ok := catalog.TechniqueSystem.Manuals[t.Manual]
	if !ok {
		return authoritativeMutation{}, errors.New("technique manual is missing")
	}
	mr, e := manualRow(conn, userID, t.Manual)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if mr == nil || i64(mr["mastery"]) < t.MinMastery {
		return authoritativeMutation{}, errors.New("manual mastery is insufficient")
	}
	br, e := conn.Execute(`SELECT * FROM battles WHERE user_id=? AND status='active' ORDER BY battle_id DESC LIMIT 1`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	battle := firstRowMap(br)
	if battle == nil || i64(battle["npc_hp"]) <= 0 {
		return authoritativeMutation{}, errors.New("an active living battle target is required")
	}
	cr, e := conn.Execute(`SELECT qi,vitality,vitality_max,karma_score,concealment_active FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(cr)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	// The qi body (v1.0.0-rc.7): the content's qi cost is a base, scaled into
	// this cultivator's own pool and by the purity of what they hold.
	now := float64(time.Now().UnixNano()) / 1e9
	state, e := settleQi(conn, catalog, userID, p.GameMinute, now)
	if e != nil {
		return authoritativeMutation{}, e
	}
	qiCost := state.Cost(t.QiCost)
	if state.Qi < qiCost || i64(ch["vitality"])-t.VitalityCost < 1 {
		return authoritativeMutation{}, fmt.Errorf("insufficient Qi or Vitality: %d qi required, %d held", qiCost, state.Qi)
	}
	_, e = conn.Execute(`UPDATE characters SET qi=qi-?,vitality=vitality-?,updated_at=? WHERE user_id=?`, []any{qiCost, t.VitalityCost, now, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	mastery := i64(mr["mastery"])
	damage := max64(0, t.Damage+mastery)
	heal := max64(0, t.Heal+mastery)
	suppress := max64(0, t.SuppressTurns)
	nhp := max64(0, i64(battle["npc_hp"])-damage)
	newSupp := i64(battle["npc_suppressed_turns"])
	if suppress > newSupp {
		newSupp = suppress
	}
	_, e = conn.Execute(`UPDATE battles SET npc_hp=?,npc_suppressed_turns=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{nhp, newSupp, now, i64(battle["battle_id"])})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if heal > 0 {
		_, e = conn.Execute(`UPDATE characters SET vitality=MIN(vitality_max,vitality+?),updated_at=? WHERE user_id=?`, []any{heal, now, userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	_, e = practiceManualTx(conn, userID, t.Manual, 1, now)
	if e != nil {
		return authoritativeMutation{}, e
	}
	forbidden := techniqueForbidden(t, m)
	karma := i64(ch["karma_score"])
	witnessed := false
	exposure := clampI(max64(1, t.Exposure), 1, 10)
	var crime map[string]any
	impacts := []string{}
	if forbidden {
		karmaCost := max64(0, t.KarmaCost)
		if karmaCost > 0 {
			karma -= karmaCost
			_, e = conn.Execute(`UPDATE characters SET karma_score=?,updated_at=? WHERE user_id=?`, []any{karma, now, userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
		}
		chance := int64(100)
		if i64(ch["concealment_active"]) != 0 {
			chance = clampI(exposure*8, 5, 95)
		}
		if chance >= 100 {
			witnessed = true
		} else {
			n, e := gamerng.Intn(100)
			if e != nil {
				return authoritativeMutation{}, e
			}
			witnessed = int64(n) < chance
		}
		location := fmt.Sprint(battle["location"])
		impacts, e = applyForbiddenWorldTx(conn, catalog, userID, p.TechniqueID, t.Name, location, p.GameMinute, exposure, karmaCost, witnessed, now)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if witnessed {
			severity := clampI(exposure+karmaCost, 1, 10)
			evidence := clampI(45+exposure*8, 50, 100)
			crime, e = recordCrimeTx(conn, userID, location, "forbidden_cultivation", fmt.Sprintf("Witnessed use of forbidden technique %s", t.Name), "public", "witnesses:"+location, severity, evidence, p.GameMinute, now)
			if e != nil {
				return authoritativeMutation{}, e
			}
			_, e = adjustReputationTx(conn, userID, "Orthodox Society", -max64(2, exposure*2), fmt.Sprintf("witnessed forbidden art: %s", t.Name), now)
			if e != nil {
				return authoritativeMutation{}, e
			}
			_, e = adjustReputationTx(conn, userID, "Demonic Circles", max64(1, exposure), fmt.Sprintf("witnessed forbidden art: %s", t.Name), now)
			if e != nil {
				return authoritativeMutation{}, e
			}
		}
	}
	result := map[string]any{"technique_id": p.TechniqueID, "name": t.Name, "qi_cost": qiCost, "qi_cost_base": t.QiCost, "vitality_cost": t.VitalityCost, "damage": damage, "heal": heal, "suppress_turns": suppress, "npc_hp": nhp, "battle_id": i64(battle["battle_id"]), "forbidden": forbidden, "karma_score": karma, "karma_cost": t.KarmaCost, "exposure": exposure, "witnessed": witnessed, "crime": crime, "impacts": impacts}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "manuals", EventType: "manual.technique", EntityType: "technique", EntityID: p.TechniqueID, GameMinute: p.GameMinute, Payload: result}}, nil
}

type crimeAtonePayload struct {
	CrimeID    int64 `json:"crime_id"`
	GameMinute int64 `json:"game_minute"`
}

func crimeAtoneAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p crimeAtonePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.CrimeID <= 0 {
		return authoritativeMutation{}, errors.New("crime_id is required")
	}
	rows, e := conn.Execute(`SELECT crime_id,jurisdiction,severity,evidence,status FROM crime_records WHERE crime_id=? AND user_id=? AND status='open'`, []any{p.CrimeID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	crime := firstRowMap(rows)
	if crime == nil {
		return authoritativeMutation{}, errors.New("that open crime record does not exist")
	}
	chars, e := conn.Execute(`SELECT location,realm_index FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(chars)
	if ch == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	jurisdiction := fmt.Sprint(crime["jurisdiction"])
	if fmt.Sprint(ch["location"]) != jurisdiction {
		return authoritativeMutation{}, fmt.Errorf("you must return to %s to negotiate restitution for this jurisdictional record", jurisdiction)
	}
	severity := max64(1, i64(crime["severity"]))
	evidence := max64(0, i64(crime["evidence"]))
	fine := max64(10, severity*25+(evidence/10)*5)
	realmIndex := i64(ch["realm_index"])
	world := "Mortal World"
	if len(catalog.Realms) > 0 {
		idx := realmIndex
		if idx < 0 {
			idx = 0
		}
		if idx >= int64(len(catalog.Realms)) {
			idx = int64(len(catalog.Realms) - 1)
		}
		world = catalog.Realms[idx].World
	}
	currency := tribulationCurrency(world)
	wallet, e := conn.Execute(`SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?`, []any{userID, currency})
	if e != nil {
		return authoritativeMutation{}, e
	}
	wr := firstRowMap(wallet)
	balance := int64(0)
	if wr != nil {
		balance = i64(wr["balance"])
	}
	if balance < fine {
		return authoritativeMutation{}, fmt.Errorf("restitution requires %d %s", fine, currency)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	balance -= fine
	res, e := conn.Execute(`UPDATE currency_wallets SET balance=? WHERE user_id=? AND currency_id=? AND balance>=?`, []any{balance, userID, currency, fine})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if res.RowsAffected != 1 {
		return authoritativeMutation{}, errors.New("restitution balance changed before settlement")
	}
	res, e = conn.Execute(`UPDATE crime_records SET status='atoned',updated_at=? WHERE crime_id=? AND user_id=? AND status='open'`, []any{now, p.CrimeID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if res.RowsAffected != 1 {
		return authoritativeMutation{}, errors.New("the crime record changed before settlement")
	}
	bountyRes, e := conn.Execute(`UPDATE bounties SET status='resolved',updated_at=? WHERE source_crime_id=? AND user_id=? AND status='active'`, []any{now, p.CrimeID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	repDelta := max64(1, severity)
	rep, e := adjustReputationTx(conn, userID, "Orthodox Society", repDelta, fmt.Sprintf("atoned crime #%d", p.CrimeID), now)
	if e != nil {
		return authoritativeMutation{}, e
	}
	result := map[string]any{
		"crime_id": p.CrimeID, "status": "atoned", "fine": fine, "currency": currency,
		"balance": balance, "bounties_resolved": bountyRes.RowsAffected, "reputation_delta": repDelta,
		"orthodox_reputation": rep, "jurisdiction": jurisdiction,
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "crime", EventType: "crime.atone", EntityType: "crime", EntityID: fmt.Sprint(p.CrimeID), GameMinute: p.GameMinute, Payload: result}}, nil
}
