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
	insight, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "insight")
	if err != nil {
		return authoritativeMutation{}, err
	}
	spirit, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, "spirit")
	if err != nil {
		return authoritativeMutation{}, err
	}
	roll, err := rollCheck(insight+spirit, 10+severity*2)
	if err != nil {
		return authoritativeMutation{}, err
	}
	newSeverity := severity
	resolved := false
	reduction := int64(0)
	now := float64(time.Now().UnixNano()) / 1e9
	if roll["success"].(bool) {
		reduction = 1
		if roll["margin"].(int64) >= 5 {
			reduction = 2
		}
		newSeverity = maxI64(0, severity-reduction)
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
	}
	result := map[string]any{"condition": p.Condition, "name": name, "treatment_item": item, "roll": roll, "success": roll["success"], "reduction": reduction, "severity_before": severity, "severity_after": newSeverity, "resolved": resolved}
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
func tribulationCurrency(world string) string {
	switch world {
	case "Spiritual World":
		return "low_spirit_crystal"
	case "Immortal World":
		return "low_immortal_stone"
	case "Celestial World":
		return "low_celestial_crystal"
	default:
		return "low_spirit_stone"
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
func tribulationPrepareAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
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
	currency := tribulationCurrency(gate.From)
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
	balance -= 5
	_, err = conn.Execute(`UPDATE currency_wallets SET balance=? WHERE user_id=? AND currency_id=?`, []any{balance, userID, currency})
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
	}{{"Heavenly Lightning", maxI64(body, will) + prep, base, "meridian_damage"}, {"Heart Tribulation", will + insight/2 + prep, base + 1, "heart_demon"}, {"Void & Karma Rejection", spirit + will/2 + prep + karmaMod, base + 2, "soul_wound"}}
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
	if success {
		if err = adjustReputationGo(conn, userID, "Heavenly Recognition", 8, "cleared:"+gate.Name); err != nil {
			return authoritativeMutation{}, err
		}
		fate, err = addFateGo(conn, userID, "tribulation_cleared:"+gate.Name, p.GameMinute)
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	result := map[string]any{"gate_realm_index": gate.Realm, "gate_name": gate.Name, "path": path, "from_world": gate.From, "to_world": gate.To, "preparation_used": prep, "waves": waves, "success": success, "successes": successes, "attempts": attempts, "fate_after": fate}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "tribulation", EventType: "tribulation_attempted", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
