package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// heartDemonResistanceScale turns `heart_demon_resistance` - a 0-100 sort of
// number, authored as 25 on the Heart Calming Pill - into a term on a 2d10
// check. Ten, so the pill is worth +2 against a TN in the high teens: enough
// to matter on the one wave of three a cultivator most often loses, and not
// enough to buy the gate.
const heartDemonResistanceScale = int64(10)

// A treatment always mends (v1.0.16). The roll decides how much - one level
// on a failure, two on a success, three on a strong one - and never whether.
//
// Before this the roll was Insight + Spirit against 10 + 2 x severity, a
// failure mended nothing, and the pill was spent either way. Qi Deviation,
// Meridian Damage and Dantian Damage each take their severity off Spirit (a
// Soul Wound off both), so the stat the cure rolled was the one the ailment had
// already lowered: a fresh cultivator had 15-28% against a severity-3 deviation
// and 0-3% at severity 5, and every Force deviation raised it a level. It was
// reported from play as six Heart-Calming Pills, six failures, and "I can't
// heal injuries". A pill is medicine: swallowing one always does something, so
// a condition at severity S costs at most S of them.
const (
	conditionTreatBaseTN = int64(10)
	// A strong success mends one level more than a success, the rollCheck
	// degree already named "Strong Success".
	conditionTreatStrongMargin = int64(5)
)

// conditionTreatTN is the number a treatment is rolled against: one per level
// of severity, where it was two.
func conditionTreatTN(severity int64) int64 { return conditionTreatBaseTN + severity }

// conditionTreatReduction is how many levels a treatment mends. It is never
// zero: the roll decides how much, not whether.
func conditionTreatReduction(success bool, margin int64) int64 {
	switch {
	case success && margin >= conditionTreatStrongMargin:
		return 3
	case success:
		return 2
	}
	return 1
}

type conditionTreatPayload struct {
	Condition  string `json:"condition"`
	GameMinute int64  `json:"game_minute"`
}

func conditionTreatAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p conditionTreatPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	r, err := conn.Execute(`SELECT condition_id,name,severity FROM character_conditions WHERE user_id=? AND condition_key=? AND state='active'`, []any{userID, p.Condition})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(r.Rows) == 0 {
		return authoritativeMutation{}, errors.New("active condition not found")
	}
	conditionID := storage.ParseInt(r.Rows[0][0])
	name := fmt.Sprint(r.Rows[0][1])
	severity := storage.ParseInt(r.Rows[0][2])
	if severity < 1 {
		severity = 1
	}
	_, _, item := conditionDefinitionGo(p.Condition)
	ir, err := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, item})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(ir.Rows) == 0 || storage.ParseInt(ir.Rows[0][0]) < 1 {
		return authoritativeMutation{}, fmt.Errorf("treatment requires 1x %s", item)
	}
	if _, err = conn.Execute(`UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?`, []any{userID, item}); err != nil {
		return authoritativeMutation{}, err
	}
	// The ailment is left out of its own cure: see `canonicalAttribute`.
	itself := effectSource{Type: "condition", ID: p.Condition}
	insight, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "insight", itself)
	if err != nil {
		return authoritativeMutation{}, err
	}
	spirit, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "spirit", itself)
	if err != nil {
		return authoritativeMutation{}, err
	}
	roll, err := rollCheck(insight+spirit, conditionTreatTN(severity))
	if err != nil {
		return authoritativeMutation{}, err
	}
	reduction := conditionTreatReduction(roll["success"].(bool), roll["margin"].(int64))
	newSeverity := maxI64(0, severity-reduction)
	resolved := false
	now := float64(time.Now().UnixNano()) / 1e9
	if newSeverity == 0 {
		resolved = true
		_, err = conn.Execute(`UPDATE character_conditions SET state='resolved',severity=0,resolved_game_minute=?,updated_game_minute=?,updated_at=? WHERE condition_id=?`, []any{p.GameMinute, p.GameMinute, now, conditionID})
		if err == nil {
			_, err = conn.Execute(`DELETE FROM active_effects WHERE user_id=? AND source_type='condition' AND source_id=?`, []any{userID, p.Condition})
		}
	} else {
		effect := conditionEffectGo(p.Condition, newSeverity)
		ej, _ := json.Marshal(effect)
		_, err = conn.Execute(`UPDATE character_conditions SET severity=?,effect_json=?,updated_game_minute=?,updated_at=? WHERE condition_id=?`, []any{newSeverity, string(ej), p.GameMinute, now, conditionID})
		if err == nil {
			_, err = conn.Execute(`UPDATE active_effects SET effect_json=?,name=?,starts_game_minute=?,created_at=? WHERE user_id=? AND source_type='condition' AND source_id=?`, []any{string(ej), name, p.GameMinute, now, userID, p.Condition})
		}
	}
	if err != nil {
		return authoritativeMutation{}, err
	}
	// The treatment does what the treatment item does. `recovery_pill` carries
	// `use.instant.vitality_restore: 8` and is also the named treatment for the
	// two combat injuries - so before this, treating a flesh wound *spent* the
	// one item in the game that would have healed you and restored nothing, and
	// `item.use` restored eight and cleared no condition. One pill did one of
	// two jobs and a player needed two to get back where they started, at the
	// end of the losing fight that had just left them on zero.
	//
	// It is applied whether or not the roll landed, because the pill was
	// swallowed either way: the roll decides whether the injury mends, not
	// whether medicine is medicine. Only `recovery_pill` carries an instant
	// restore today, so this reaches exactly the two conditions that leave a
	// cultivator at zero; `jade_life_herb`, `heart_calming_pill` and
	// `purging_phoenix_pill` carry none and are untouched by construction.
	restored := map[string]any{}
	if def, ok := catalog.Items[item]; ok {
		vit, qi := def.Use.Instant.VitalityRestore, def.Use.Instant.QiRestore
		if vit > 0 || qi > 0 {
			if _, err = conn.Execute(
				`UPDATE characters SET qi=MIN(qi_max,qi+?),vitality=MIN(vitality_max,vitality+?),updated_at=? WHERE user_id=?`,
				[]any{qi, vit, now, userID},
			); err != nil {
				return authoritativeMutation{}, err
			}
			restored["vitality"], restored["qi"] = vit, qi
		}
	}
	result := map[string]any{"condition": p.Condition, "name": name, "treatment_item": item, "roll": roll, "success": roll["success"], "mended": reduction > 0, "reduction": reduction, "severity_before": severity, "severity_after": newSeverity, "resolved": resolved, "restored": restored}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "condition", EventType: "condition_treated", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

type tribulationGate struct {
	Realm          int64
	From, To, Name string
}

var tribulationGates = map[int64]tribulationGate{7: {7, "Mortal World", "Spiritual World", "Mortal Ascension Tribulation"}, 15: {15, "Spiritual World", "Immortal World", "Transcendence Tribulation"}, 23: {23, "Immortal World", "Celestial World", "Celestial Ascension Tribulation"}}

type tribulationCharacter struct{ Realm, Phase, Cultivation, BodyRealm, BodyPhase, BodyCultivation, Karma int64 }

func loadTribulationCharacter(conn *storage.Conn, userID int64) (tribulationCharacter, error) {
	r, err := conn.Execute(`SELECT realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,karma_score FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return tribulationCharacter{}, err
	}
	if len(r.Rows) == 0 {
		return tribulationCharacter{}, errors.New("living character not found")
	}
	x := r.Rows[0]
	return tribulationCharacter{storage.ParseInt(x[0]), storage.ParseInt(x[1]), storage.ParseInt(x[2]), storage.ParseInt(x[3]), storage.ParseInt(x[4]), storage.ParseInt(x[5]), storage.ParseInt(x[6])}, nil
}
func tribulationGateFor(phase, realm int64) (tribulationGate, bool) {
	if phase != 9 {
		return tribulationGate{}, false
	}
	g, ok := tribulationGates[realm]
	return g, ok
}

// eligibleTribulation resolves which gate/path a tribulation action should act
// on. A dual cultivator can, in principle, be simultaneously bottlenecked on
// both the qi and body paths at once (each at phase 9 of its own gate realm) -
// those are tracked as independent tribulation_state rows keyed by realm, so
// neither should be permanently inaccessible just because the other exists.
// requestedPath ("qi" or "body", case-insensitive) lets the caller pick which
// one to act on when both are eligible; an empty/unrecognized value keeps the
// historical default of preferring qi, so existing callers that don't send a
// path are unaffected.
func eligibleTribulation(c tribulationCharacter, requestedPath string) (tribulationGate, string, bool) {
	qiGate, qiOK := tribulationGateFor(c.Phase, c.Realm)
	bodyGate, bodyOK := tribulationGateFor(c.BodyPhase, c.BodyRealm)
	switch strings.ToLower(strings.TrimSpace(requestedPath)) {
	case "body":
		if bodyOK {
			return bodyGate, "Body", true
		}
		return tribulationGate{}, "", false
	case "qi":
		if qiOK {
			return qiGate, "Qi", true
		}
		return tribulationGate{}, "", false
	default:
		if qiOK {
			return qiGate, "Qi", true
		}
		if bodyOK {
			return bodyGate, "Body", true
		}
		return tribulationGate{}, "", false
	}
}

type tribulationPayload struct {
	GameMinute int64  `json:"game_minute"`
	Path       string `json:"path"`
}

func tribulationState(conn *storage.Conn, userID, realm int64) (prep, attempts int64, cleared bool, err error) {
	r, e := conn.Execute(`SELECT preparation,attempts,cleared FROM tribulation_state WHERE user_id=? AND gate_realm_index=?`, []any{userID, realm})
	if e != nil {
		err = e
		return
	}
	if len(r.Rows) > 0 {
		prep = storage.ParseInt(r.Rows[0][0])
		attempts = storage.ParseInt(r.Rows[0][1])
		cleared = storage.ParseInt(r.Rows[0][2]) != 0
	}
	return
}
func tribulationPrepareAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p tribulationPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadTribulationCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gate, path, ok := eligibleTribulation(c, p.Path)
	if !ok {
		return authoritativeMutation{}, errors.New("not at a world-crossing ascension gate")
	}
	prep, _, cleared, err := tribulationState(conn, userID, gate.Realm)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if cleared {
		return authoritativeMutation{}, errors.New("tribulation already cleared")
	}
	if prep >= 5 {
		return authoritativeMutation{}, errors.New("tribulation preparation is already capped")
	}
	currency := worldBaseCurrency(catalog, gate.From)
	wr, err := conn.Execute(`SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?`, []any{userID, currency})
	if err != nil {
		return authoritativeMutation{}, err
	}
	balance := int64(0)
	if len(wr.Rows) > 0 {
		balance = storage.ParseInt(wr.Rows[0][0])
	}
	if balance < 5 {
		return authoritativeMutation{}, fmt.Errorf("preparation requires 5 %s", currency)
	}
	balance, err = walletDeltaTx(conn, catalog, userID, currency, -5, float64(time.Now().UnixNano())/1e9)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	prep++
	_, err = conn.Execute(`INSERT INTO tribulation_state(user_id,gate_realm_index,preparation,attempts,cleared,last_result,updated_game_minute,updated_at) VALUES(?,?,?,0,0,'',?,?) ON CONFLICT(user_id,gate_realm_index) DO UPDATE SET preparation=?,updated_game_minute=excluded.updated_game_minute,updated_at=excluded.updated_at`, []any{userID, gate.Realm, prep, p.GameMinute, now, prep})
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"gate_realm_index": gate.Realm, "gate_name": gate.Name, "path": path, "from_world": gate.From, "to_world": gate.To, "preparation": prep, "currency": currency, "balance": balance}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "tribulation", EventType: "tribulation_prepared", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
func adjustReputationGo(conn *storage.Conn, userID int64, faction string, delta int64, reason string) error {
	now := float64(time.Now().UnixNano()) / 1e9
	_, err := conn.Execute(`INSERT INTO faction_reputation(user_id,faction_key,score,last_reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,faction_key) DO UPDATE SET score=MAX(-100,MIN(100,faction_reputation.score+excluded.score)),last_reason=excluded.last_reason,updated_at=excluded.updated_at`, []any{userID, faction, delta, reason, now})
	return err
}
func tribulationAttemptAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p tribulationPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadTribulationCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gate, path, ok := eligibleTribulation(c, p.Path)
	if !ok {
		return authoritativeMutation{}, errors.New("not at a world-crossing tribulation gate")
	}
	realm, phase, essence := c.Realm, c.Phase, c.Cultivation
	if path == "Body" {
		realm, phase, essence = c.BodyRealm, c.BodyPhase, c.BodyCultivation
	}
	cost, err := perfectionPhaseCost(catalog, path == "Body", realm, phase)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if essence < cost {
		return authoritativeMutation{}, fmt.Errorf("stage not filled: %d/%d", essence, cost)
	}
	prep, attempts, cleared, err := tribulationState(conn, userID, gate.Realm)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if cleared {
		return authoritativeMutation{}, errors.New("tribulation already cleared")
	}
	if prep < 0 {
		prep = 0
	}
	if prep > 5 {
		prep = 5
	}
	body, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "body")
	if err != nil {
		return authoritativeMutation{}, err
	}
	will, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "will")
	if err != nil {
		return authoritativeMutation{}, err
	}
	insight, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "insight")
	if err != nil {
		return authoritativeMutation{}, err
	}
	spirit, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "spirit")
	if err != nil {
		return authoritativeMutation{}, err
	}
	// The Heart Calming Pill has said since it was authored that it
	// "suppresses heart-demon disturbances", and the number saying so was read
	// by nothing until v1.0.0-rc.58. The Heart Tribulation is where the heart
	// demon comes from - a failed wave applies `heart_demon` a few lines below
	// - so this is where a settled mind is worth something. Divided by
	// heartDemonResistanceScale because the content authors 25 against a 2d10
	// check: the pill is +2 on a TN in the high teens, a real share of a wave
	// a cultivator is allowed to fail one of, and not a free pass.
	heartCalm, err := canonicalAdditiveEffectBonus(conn, catalog, userID, "", p.GameMinute, "heart_demon_resistance")
	if err != nil {
		return authoritativeMutation{}, err
	}
	karmaMod := c.Karma / 25
	if karmaMod < -4 {
		karmaMod = -4
	}
	if karmaMod > 4 {
		karmaMod = 4
	}
	base := int64(14) + gate.Realm/2
	defs := []struct {
		Name         string
		Modifier, TN int64
		Condition    string
	}{{"Heavenly Lightning", maxI64(body, will) + prep, base, "meridian_damage"}, {"Heart Tribulation", will + insight/2 + prep + heartCalm/heartDemonResistanceScale, base + 1, "heart_demon"}, {"Void & Karma Rejection", spirit + will/2 + prep + karmaMod, base + 2, "soul_wound"}}
	waves := []map[string]any{}
	successes := int64(0)
	for _, d := range defs {
		roll, err := rollCheck(d.Modifier, d.TN)
		if err != nil {
			return authoritativeMutation{}, err
		}
		roll["name"] = d.Name
		roll["condition_key"] = d.Condition
		if roll["success"].(bool) {
			successes++
		} else {
			sev := int64(1)
			if roll["margin"].(int64) <= -4 {
				sev = 2
			}
			applied, err := applyCombatCondition(conn, userID, d.Condition, sev, "tribulation", fmt.Sprint(gate.Realm), p.GameMinute)
			if err != nil {
				return authoritativeMutation{}, err
			}
			roll["condition"] = applied
		}
		waves = append(waves, roll)
	}
	success := successes >= 2
	wj, _ := json.Marshal(waves)
	now := float64(time.Now().UnixNano()) / 1e9
	attempts++
	_, err = conn.Execute(`INSERT INTO tribulation_attempts(user_id,gate_realm_index,preparation_used,waves_json,success,created_game_minute,created_at) VALUES(?,?,?,?,?,?,?)`, []any{userID, gate.Realm, prep, string(wj), map[bool]int{true: 1, false: 0}[success], p.GameMinute, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	last := "failed"
	if success {
		last = "cleared"
	}
	_, err = conn.Execute(`INSERT INTO tribulation_state(user_id,gate_realm_index,preparation,attempts,cleared,last_result,updated_game_minute,updated_at) VALUES(?,?,0,1,?,?,?,?) ON CONFLICT(user_id,gate_realm_index) DO UPDATE SET preparation=0,attempts=tribulation_state.attempts+1,cleared=MAX(tribulation_state.cleared,excluded.cleared),last_result=excluded.last_result,updated_game_minute=excluded.updated_game_minute,updated_at=excluded.updated_at`, []any{userID, gate.Realm, map[bool]int{true: 1, false: 0}[success], last, p.GameMinute, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	fate := int64(-1)
	questKey := ""
	if success {
		if err = adjustReputationGo(conn, userID, "Heavenly Recognition", 8, "cleared:"+gate.Name); err != nil {
			return authoritativeMutation{}, err
		}
		fate, err = addFateGo(conn, userID, "tribulation_cleared:"+gate.Name, p.GameMinute)
		if err != nil {
			return authoritativeMutation{}, err
		}
		// What survives a tribulation is now told what it is for
		// (v1.0.0-rc.44). Clearing the gate wrote `cleared`, paid a
		// reputation point and a fate point, and left the player holding a
		// permission with nothing naming the door it opens. The quest is
		// authored per departing world in `world_crossing_system.quests` and
		// handed over the way every giver-less quest is; a world with no
		// authored quest simply hands nothing over, which is a content
		// decision rather than a fault.
		if authored, ok := catalog.WorldCrossing.Quests[gate.From]; ok {
			granted, gerr := grantOrdinaryQuestTx(conn, userID, strings.TrimSpace(authored.QuestKey), p.GameMinute)
			if gerr != nil {
				return authoritativeMutation{}, gerr
			}
			if granted {
				questKey = strings.TrimSpace(authored.QuestKey)
			}
		}
	}
	result := map[string]any{"gate_realm_index": gate.Realm, "gate_name": gate.Name, "path": path, "from_world": gate.From, "to_world": gate.To, "preparation_used": prep, "waves": waves, "success": success, "successes": successes, "attempts": attempts, "fate_after": fate, "quest_granted": questKey, "heart_demon_resistance": heartCalm}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "tribulation", EventType: "tribulation_attempted", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
