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
	ManualID   string `json:"manual_id"`
	GameMinute int64  `json:"game_minute"`
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

// characterKarmaTx is the character's karma score, 0 for a row that is not there.
func characterKarmaTx(conn *storage.Conn, userID int64) (int64, error) {
	kr, e := conn.Execute(`SELECT karma_score FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return 0, e
	}
	if rr := firstRowMap(kr); rr != nil {
		return i64(rr["karma_score"]), nil
	}
	return 0, nil
}

// chargeFirstStudyKarmaTx is what the first study of a forbidden manual costs:
// one karma. It answers the karma score after the charge. It is the one door
// for that price - `manual.study` calls it, and so does an inheritance that
// studies its scripture at once - because an inheritance that wrote the study
// row itself never charged it (v1.12.3). A manual that is not forbidden costs
// nothing.
func chargeFirstStudyKarmaTx(conn *storage.Conn, userID int64, m worlddata.ManualDefinition, now float64) (int64, error) {
	karma, e := characterKarmaTx(conn, userID)
	if e != nil {
		return 0, e
	}
	if !manualForbidden(m) {
		return karma, nil
	}
	karma--
	if _, e = conn.Execute(`UPDATE characters SET karma_score=?,updated_at=? WHERE user_id=?`, []any{karma, now, userID}); e != nil {
		return 0, e
	}
	return karma, nil
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
	c, e := loadMechanicsCharacter(conn, catalog, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if c.RealmIndex < m.MinRealmIndex {
		return authoritativeMutation{}, fmt.Errorf("realm %d is below manual requirement %d", c.RealmIndex, m.MinRealmIndex)
	}
	now := float64(time.Now().UnixNano()) / 1e9
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
	if first {
		karma, e = chargeFirstStudyKarmaTx(conn, userID, m, now)
	} else {
		karma, e = characterKarmaTx(conn, userID)
	}
	if e != nil {
		return authoritativeMutation{}, e
	}
	if e = setCooldown(conn, userID, key, cooldownSecondsFor(cooldownForbidden), now); e != nil {
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

// manualTechniqueFor is the one statement of whether a cultivator may use a
// manual's technique at all: the technique and its manual are in the catalogue
// and the manual is studied to the technique's mastery. The battle and the raid
// both ask it (v1.9.1), before either spends anything.
func manualTechniqueFor(conn *storage.Conn, catalog worlddata.Catalog, userID int64, techID string) (worlddata.ManualTechniqueDefinition, worlddata.ManualDefinition, int64, error) {
	t, ok := catalog.TechniqueSystem.Techniques[techID]
	if !ok {
		return t, worlddata.ManualDefinition{}, 0, errors.New("unknown manual technique")
	}
	m, ok := catalog.TechniqueSystem.Manuals[t.Manual]
	if !ok {
		return t, m, 0, errors.New("technique manual is missing")
	}
	mr, e := manualRow(conn, userID, t.Manual)
	if e != nil {
		return t, m, 0, e
	}
	if mr == nil || i64(mr["mastery"]) < t.MinMastery {
		return t, m, 0, errors.New("manual mastery is insufficient")
	}
	return t, m, i64(mr["mastery"]), nil
}

// manualTechniqueUse is what spending a manual technique cost and set off,
// whichever fight it was used in.
type manualTechniqueUse struct {
	QiCost    int64
	Forbidden bool
	Karma     int64
	Exposure  int64
	Witnessed bool
	Crime     map[string]any
	Impacts   []string
}

// spendManualTechniqueTx pays for one use of a manual technique and answers
// for it: the qi (scaled by the qi body) and vitality it costs, a point of
// practice on the manual, and - for a forbidden art - the karma, the witnesses,
// the crime and the world's reaction at `location`. What the technique does to
// an opponent is the caller's, because a battle and a raid hold their
// opponents in different tables (v1.9.1).
func spendManualTechniqueTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, techID string, t worlddata.ManualTechniqueDefinition, m worlddata.ManualDefinition, gameMinute int64, location string) (manualTechniqueUse, error) {
	use := manualTechniqueUse{Impacts: []string{}}
	cr, e := conn.Execute(`SELECT qi,vitality,vitality_max,karma_score,concealment_active FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return use, e
	}
	ch := firstRowMap(cr)
	if ch == nil {
		return use, errors.New("character not found")
	}
	// The qi body (v1.0.0-rc.7): the content's qi cost is a base, scaled into
	// this cultivator's own pool and by the purity of what they hold.
	now := float64(time.Now().UnixNano()) / 1e9
	state, e := settleQi(conn, catalog, userID, gameMinute, now)
	if e != nil {
		return use, e
	}
	use.QiCost = state.Cost(t.QiCost)
	if state.Qi < use.QiCost || i64(ch["vitality"])-t.VitalityCost < 1 {
		return use, fmt.Errorf("insufficient Qi or Vitality: %d qi required, %d held", use.QiCost, state.Qi)
	}
	if _, e = conn.Execute(`UPDATE characters SET qi=qi-?,vitality=vitality-?,updated_at=? WHERE user_id=?`, []any{use.QiCost, t.VitalityCost, now, userID}); e != nil {
		return use, e
	}
	if _, e = practiceManualTx(conn, userID, t.Manual, 1, now); e != nil {
		return use, e
	}
	use.Forbidden = techniqueForbidden(t, m)
	use.Karma = i64(ch["karma_score"])
	use.Exposure = clampI(max64(1, t.Exposure), 1, 10)
	if !use.Forbidden {
		return use, nil
	}
	karmaCost := max64(0, t.KarmaCost)
	if karmaCost > 0 {
		use.Karma -= karmaCost
		if _, e = conn.Execute(`UPDATE characters SET karma_score=?,updated_at=? WHERE user_id=?`, []any{use.Karma, now, userID}); e != nil {
			return use, e
		}
	}
	chance := int64(100)
	if i64(ch["concealment_active"]) != 0 {
		chance = clampI(use.Exposure*8, 5, 95)
	}
	if chance >= 100 {
		use.Witnessed = true
	} else {
		n, e := gamerng.Intn(100)
		if e != nil {
			return use, e
		}
		use.Witnessed = int64(n) < chance
	}
	use.Impacts, e = applyForbiddenWorldTx(conn, catalog, userID, techID, t.Name, location, gameMinute, use.Exposure, karmaCost, use.Witnessed, now)
	if e != nil {
		return use, e
	}
	if use.Witnessed {
		severity := clampI(use.Exposure+karmaCost, 1, 10)
		evidence := clampI(45+use.Exposure*8, 50, 100)
		use.Crime, e = recordCrimeTx(conn, userID, location, "forbidden_cultivation", fmt.Sprintf("Witnessed use of forbidden technique %s", t.Name), "public", "witnesses:"+location, severity, evidence, gameMinute, now)
		if e != nil {
			return use, e
		}
		if _, e = adjustReputationTx(conn, userID, "Orthodox Society", -max64(2, use.Exposure*2), fmt.Sprintf("witnessed forbidden art: %s", t.Name), now); e != nil {
			return use, e
		}
		if _, e = adjustReputationTx(conn, userID, "Demonic Circles", max64(1, use.Exposure), fmt.Sprintf("witnessed forbidden art: %s", t.Name), now); e != nil {
			return use, e
		}
	}
	return use, nil
}

func manualTechniqueAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p manualTechniquePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.TechniqueID = strings.TrimSpace(p.TechniqueID)
	t, m, mastery, e := manualTechniqueFor(conn, catalog, userID, p.TechniqueID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	br, e := conn.Execute(`SELECT * FROM battles WHERE user_id=? AND status='active' ORDER BY battle_id DESC LIMIT 1`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	battle := firstRowMap(br)
	if battle == nil || i64(battle["npc_hp"]) <= 0 {
		return authoritativeMutation{}, errors.New("an active living battle target is required")
	}
	use, e := spendManualTechniqueTx(conn, catalog, userID, p.TechniqueID, t, m, p.GameMinute, fmt.Sprint(battle["location"]))
	if e != nil {
		return authoritativeMutation{}, e
	}
	now := float64(time.Now().UnixNano()) / 1e9
	damage := max64(0, t.Damage+mastery)
	heal := max64(0, t.Heal+mastery)
	suppress := max64(0, t.SuppressTurns)
	nhp := max64(0, i64(battle["npc_hp"])-damage)
	newSupp := i64(battle["npc_suppressed_turns"])
	if suppress > newSupp {
		newSupp = suppress
	}
	if _, e = conn.Execute(`UPDATE battles SET npc_hp=?,npc_suppressed_turns=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{nhp, newSupp, now, i64(battle["battle_id"])}); e != nil {
		return authoritativeMutation{}, e
	}
	if heal > 0 {
		if _, e = conn.Execute(`UPDATE characters SET vitality=MIN(vitality_max,vitality+?),updated_at=? WHERE user_id=?`, []any{heal, now, userID}); e != nil {
			return authoritativeMutation{}, e
		}
	}
	result := map[string]any{"technique_id": p.TechniqueID, "name": t.Name, "qi_cost": use.QiCost, "qi_cost_base": t.QiCost, "vitality_cost": t.VitalityCost, "damage": damage, "heal": heal, "suppress_turns": suppress, "npc_hp": nhp, "battle_id": i64(battle["battle_id"]), "forbidden": use.Forbidden, "karma_score": use.Karma, "karma_cost": t.KarmaCost, "exposure": use.Exposure, "witnessed": use.Witnessed, "crime": use.Crime, "impacts": use.Impacts}
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
	fine := crimeRestitutionFine(severity, i64(crime["evidence"]))
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
	currency := worldBaseCurrency(catalog, world)
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
	// walletDeltaTx refuses a debit beyond the balance, which is the guard the
	// hand-written compare-and-set here used to provide - and unlike it, the
	// one door keeps `characters.spirit_stones` in step with the purse.
	balance, e = walletDeltaTx(conn, catalog, userID, currency, -fine, now)
	if e != nil {
		return authoritativeMutation{}, e
	}
	res, e := conn.Execute(`UPDATE crime_records SET status='atoned',updated_at=? WHERE crime_id=? AND user_id=? AND status='open'`, []any{now, p.CrimeID, userID})
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
