package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type perfectionPayload struct {
	Mode                 string `json:"mode"`
	GameMinute           int64  `json:"game_minute"`
	QuestCooldownSeconds int64  `json:"quest_cooldown_seconds"`
	TrialCooldownSeconds int64  `json:"trial_cooldown_seconds"`
}

type perfectionChar struct{ Realm, Phase, Cultivation, BodyRealm, BodyPhase, BodyCultivation int64 }

func loadPerfectionChar(conn *storage.Conn, userID int64) (perfectionChar, error) {
	r, err := conn.Execute(`SELECT realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return perfectionChar{}, err
	}
	if len(r.Rows) == 0 {
		return perfectionChar{}, errors.New("living character not found")
	}
	x := r.Rows[0]
	return perfectionChar{storage.ParseInt(x[0]), storage.ParseInt(x[1]), storage.ParseInt(x[2]), storage.ParseInt(x[3]), storage.ParseInt(x[4]), storage.ParseInt(x[5])}, nil
}
func perfectionConfig(c perfectionChar, body bool, catalog worlddata.Catalog) (table string, realm, phase, essence int64, sys worlddata.PerfectionSystem, err error) {
	if body {
		table = "body_realm_perfection"
		realm = c.BodyRealm
		phase = c.BodyPhase
		essence = c.BodyCultivation
		sys = catalog.BodyPerfection
	} else {
		table = "realm_perfection"
		realm = c.Realm
		phase = c.Phase
		essence = c.Cultivation
		sys = catalog.Perfection
	}
	realms := catalog.Realms
	if body {
		realms = catalog.BodyRealms
	}
	if realm < 0 || int(realm) >= len(realms) {
		err = errors.New("realm index out of range")
		return
	}
	return
}
func perfectionPhaseCost(catalog worlddata.Catalog, body bool, realm, phase int64) (int64, error) {
	realms := catalog.Realms
	if body {
		realms = catalog.BodyRealms
	}
	if realm < 0 || int(realm) >= len(realms) {
		return 0, errors.New("realm index out of range")
	}
	costs := realms[realm].PhaseCosts
	if phase < 1 || int(phase) > len(costs) {
		return 0, errors.New("phase out of range")
	}
	return costs[phase-1], nil
}
func perfectionResonanceBonus(c perfectionChar) int64 {
	if c.Realm == c.BodyRealm && c.Phase == c.BodyPhase {
		return 1
	}
	return 0
}
func cooldownReady(conn *storage.Conn, userID int64, key string) (bool, int64, error) {
	r, err := conn.Execute(`SELECT available_at FROM cooldowns WHERE user_id=? AND action=?`, []any{userID, key})
	if err != nil {
		return false, 0, err
	}
	if len(r.Rows) == 0 {
		return true, 0, nil
	}
	remaining := int64(float64Value(r.Rows[0][0]) - float64(time.Now().UnixNano())/1e9)
	if remaining <= 0 {
		return true, 0, nil
	}
	return false, remaining, nil
}
func float64Value(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case int:
		return float64(x)
	default:
		var f float64
		fmt.Sscan(fmt.Sprint(v), &f)
		return f
	}
}
func perfectionStartAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, body bool) (authoritativeMutation, error) {
	var p perfectionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadPerfectionChar(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	table, realm, phase, essence, sys, err := perfectionConfig(c, body, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if phase != 9 {
		return authoritativeMutation{}, errors.New("perfection requires stage 9")
	}
	cost, err := perfectionPhaseCost(catalog, body, realm, phase)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if essence < cost {
		return authoritativeMutation{}, fmt.Errorf("stage not filled: %d/%d", essence, cost)
	}
	r, err := conn.Execute(fmt.Sprintf(`SELECT active,completed FROM %s WHERE user_id=? AND realm_index=?`, table), []any{userID, realm})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(r.Rows) > 0 && storage.ParseInt(r.Rows[0][1]) == 1 {
		return authoritativeMutation{}, errors.New("realm already perfected")
	}
	if len(r.Rows) > 0 && storage.ParseInt(r.Rows[0][0]) == 1 {
		return authoritativeMutation{}, errors.New("perfection already active")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, err = conn.Execute(fmt.Sprintf(`INSERT INTO %s(user_id,realm_index,active,completed,progress,training_progress,quest_index,quest_preparation,completed_quests,discovered_json,updated_at) VALUES(?,?,1,0,0,0,0,0,0,'[]',?) ON CONFLICT(user_id,realm_index) DO UPDATE SET active=1,updated_at=excluded.updated_at`, table), []any{userID, realm, now})
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"started": true, "body": body, "realm_index": realm, "quest_count": len(sys.Quests), "training_cap": sys.TrainingCap}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "perfection", EventType: "perfection_started", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func loadPerfectionState(conn *storage.Conn, table string, userID, realm int64) (map[string]any, error) {
	r, err := conn.Execute(fmt.Sprintf(`SELECT active,completed,progress,training_progress,quest_index,quest_preparation,completed_quests,discovered_json FROM %s WHERE user_id=? AND realm_index=?`, table), []any{userID, realm})
	if err != nil {
		return nil, err
	}
	if len(r.Rows) == 0 {
		return nil, errors.New("no perfection path")
	}
	x := r.Rows[0]
	return map[string]any{"active": storage.ParseInt(x[0]), "completed": storage.ParseInt(x[1]), "progress": storage.ParseInt(x[2]), "training_progress": storage.ParseInt(x[3]), "quest_index": storage.ParseInt(x[4]), "quest_preparation": storage.ParseInt(x[5]), "completed_quests": storage.ParseInt(x[6]), "discovered_json": fmt.Sprint(x[7])}, nil
}

func perfectionQuestAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, body bool) (authoritativeMutation, error) {
	var p perfectionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.Mode != "prepare" && p.Mode != "attempt" {
		return authoritativeMutation{}, errors.New("mode must be prepare or attempt")
	}
	c, err := loadPerfectionChar(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	table, realm, _, _, sys, err := perfectionConfig(c, body, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	st, err := loadPerfectionState(conn, table, userID, realm)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if st["active"].(int64) != 1 {
		return authoritativeMutation{}, errors.New("perfection path is not active")
	}
	qi := st["quest_index"].(int64)
	if qi < 0 || int(qi) >= len(sys.Quests) {
		return authoritativeMutation{}, errors.New("all perfection quests are complete")
	}
	q := sys.Quests[qi]
	coolKey := "perfect_quest"
	if body {
		coolKey = "body_perfect_quest"
	}
	ready, remaining, err := cooldownReady(conn, userID, coolKey)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !ready {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if p.Mode == "prepare" {
		_, err = conn.Execute(fmt.Sprintf(`UPDATE %s SET quest_preparation=quest_preparation+1,updated_at=? WHERE user_id=? AND realm_index=? AND active=1`, table), []any{now, userID, realm})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if err = setCooldown(conn, userID, coolKey, p.QuestCooldownSeconds, now); err != nil {
			return authoritativeMutation{}, err
		}
		prep := st["quest_preparation"].(int64) + 1
		result := map[string]any{"mode": "prepare", "body": body, "quest_index": qi, "preparation": prep, "preparation_required": q.PreparationRequired, "title": q.Title, "clue": q.Clue}
		return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "perfection", EventType: "perfection_quest_prepared", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
	}
	if st["quest_preparation"].(int64) < q.PreparationRequired {
		return authoritativeMutation{}, errors.New("quest preparation is incomplete")
	}
	attr, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, q.Attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	rolled, err := rollCheck(attr+2+perfectionResonanceBonus(c), q.TN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, coolKey, p.QuestCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	reward := int64(0)
	if int(qi) < len(sys.QuestProgress) {
		reward = sys.QuestProgress[qi]
	}
	if rolled["success"].(bool) {
		discovered := []string{}
		_ = json.Unmarshal([]byte(st["discovered_json"].(string)), &discovered)
		found := false
		for _, x := range discovered {
			if x == q.Clue {
				found = true
			}
		}
		if q.Clue != "" && !found {
			discovered = append(discovered, q.Clue)
		}
		dj, _ := json.Marshal(discovered)
		_, err = conn.Execute(fmt.Sprintf(`UPDATE %s SET progress=MIN(100,progress+?),quest_index=quest_index+1,completed_quests=completed_quests+1,quest_preparation=0,discovered_json=?,updated_at=? WHERE user_id=? AND realm_index=? AND active=1`, table), []any{reward, string(dj), now, userID, realm})
		if err != nil {
			return authoritativeMutation{}, err
		}
	}
	result := map[string]any{"mode": "attempt", "body": body, "quest_index": qi, "title": q.Title, "attribute": q.Attribute, "progress_reward": reward, "roll": rolled, "success": rolled["success"]}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "perfection", EventType: "perfection_quest_attempted", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func perfectionTrialAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, body bool) (authoritativeMutation, error) {
	var p perfectionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadPerfectionChar(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	table, realm, _, _, sys, err := perfectionConfig(c, body, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	st, err := loadPerfectionState(conn, table, userID, realm)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if st["active"].(int64) != 1 {
		return authoritativeMutation{}, errors.New("perfection path is not active")
	}
	if st["completed_quests"].(int64) < int64(len(sys.Quests)) || st["progress"].(int64) < 100 {
		return authoritativeMutation{}, errors.New("final trial is locked")
	}
	key := "perfect_trial"
	if body {
		key = "body_perfect_trial"
	}
	ready, remaining, err := cooldownReady(conn, userID, key)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !ready {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	rolls := []map[string]any{}
	success := true
	for _, t := range sys.FinalTrials {
		attr, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, t.Attribute)
		if err != nil {
			return authoritativeMutation{}, err
		}
		r, err := rollCheck(attr+2+perfectionResonanceBonus(c), t.TN)
		if err != nil {
			return authoritativeMutation{}, err
		}
		r["name"] = t.Name
		r["attribute"] = t.Attribute
		rolls = append(rolls, r)
		if !r["success"].(bool) {
			success = false
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	loss := int64(0)
	if success {
		_, err = conn.Execute(fmt.Sprintf(`UPDATE %s SET progress=100,active=0,completed=1,updated_at=? WHERE user_id=? AND realm_index=?`, table), []any{now, userID, realm})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if body {
			_, err = conn.Execute(`UPDATE characters SET vitality_max=vitality_max+MAX(1,CAST(vitality_max*0.10 AS INTEGER)),vitality=vitality_max+MAX(1,CAST(vitality_max*0.10 AS INTEGER)),qi_max=qi_max+MAX(1,CAST(qi_max*0.05 AS INTEGER)),qi=qi_max+MAX(1,CAST(qi_max*0.05 AS INTEGER)),updated_at=? WHERE user_id=?`, []any{now, userID})
		} else {
			_, err = conn.Execute(`UPDATE characters SET qi_max=qi_max+MAX(1,CAST(qi_max*0.10 AS INTEGER)),qi=qi_max+MAX(1,CAST(qi_max*0.10 AS INTEGER)),vitality_max=vitality_max+MAX(1,CAST(vitality_max*0.05 AS INTEGER)),vitality=vitality_max+MAX(1,CAST(vitality_max*0.05 AS INTEGER)),updated_at=? WHERE user_id=?`, []any{now, userID})
		}
		if err != nil {
			return authoritativeMutation{}, err
		}
	} else {
		loss = 4
		if st["training_progress"].(int64) < loss {
			loss = st["training_progress"].(int64)
		}
		_, err = conn.Execute(fmt.Sprintf(`UPDATE %s SET progress=MAX(0,progress-?),training_progress=MAX(0,training_progress-?),updated_at=? WHERE user_id=? AND realm_index=?`, table), []any{loss, loss, now, userID, realm})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if err = setCooldown(conn, userID, key, p.TrialCooldownSeconds, now); err != nil {
			return authoritativeMutation{}, err
		}
	}
	result := map[string]any{"body": body, "success": success, "rolls": rolls, "training_loss": loss, "realm_index": realm}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "perfection", EventType: "perfection_final_trial", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

func perfectionAbandonAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, body bool) (authoritativeMutation, error) {
	var p perfectionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadPerfectionChar(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	table, realm, _, _, _, err := perfectionConfig(c, body, catalog)
	if err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(fmt.Sprintf(`DELETE FROM %s WHERE user_id=? AND realm_index=? AND completed=0`, table), []any{userID, realm})
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"body": body, "abandoned": res.RowsAffected > 0, "realm_index": realm}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "perfection", EventType: "perfection_abandoned", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
