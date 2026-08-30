package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/core"
	"xianxia/core/internal/storage"
)

type ActionRequest struct {
	APIVersion      string          `json:"api_version,omitempty"`
	ActionID        string          `json:"action_id,omitempty"`
	Operation       string          `json:"operation"`
	ActorID         int64           `json:"actor_id"`
	ExpectedVersion *int64          `json:"expected_version,omitempty"`
	Payload         json.RawMessage `json:"payload"`
}

type ActionResponse struct {
	APIVersion   string `json:"api_version,omitempty"`
	ActionID     string `json:"action_id,omitempty"`
	Operation    string `json:"operation"`
	StateVersion int64  `json:"state_version,omitempty"`
	Replayed     bool   `json:"replayed,omitempty"`
	Result       any    `json:"result"`
}

func Apply(databasePath string, req ActionRequest) (ActionResponse, error) {
	return ApplyWithWorld(databasePath, "", req)
}

func ApplyWithWorld(databasePath, worldPath string, req ActionRequest) (ActionResponse, error) {
	if isAuthoritativeOperation(req.Operation) {
		return applyAuthoritative(databasePath, worldPath, req)
	}
	if req.ActorID < 0 {
		return ActionResponse{}, errors.New("actor_id cannot be negative")
	}
	conn, err := storage.Open(databasePath)
	if err != nil {
		return ActionResponse{}, err
	}
	defer conn.Close()
	var result any
	switch req.Operation {
	case "relationship.update":
		result, err = relationshipUpdate(conn, req.ActorID, req.Payload)
	case "scene.transition":
		result, err = sceneTransition(conn, req.ActorID, req.Payload)
	case "quest.progress":
		result, err = questProgress(conn, req.ActorID, req.Payload)
	case "combat.apply_damage":
		result, err = combatApplyDamage(conn, req.ActorID, req.Payload)
	case "cultivation.reward":
		result, err = cultivationReward(conn, req.ActorID, req.Payload)
	case "admin.world.advance_time":
		result, err = adminAdvanceTime(conn, req.ActorID, req.Payload)
	case "admin.player.grant_currency":
		result, err = adminGrantCurrency(conn, req.ActorID, req.Payload)
	case "admin.player.karma":
		result, err = adminKarma(conn, req.ActorID, req.Payload)
	case "admin.player.teleport":
		result, err = adminTeleport(conn, req.ActorID, req.Payload)
	case "admin.player.revive":
		result, err = adminRevive(conn, req.ActorID, req.Payload)
	case "admin.player.clear_battle":
		result, err = adminClearBattle(conn, req.ActorID, req.Payload)
	case "admin.automation.set":
		result, err = adminAutomationSet(conn, req.ActorID, req.Payload)
	case "admin.simulation.interval":
		result, err = adminSimulationInterval(conn, req.ActorID, req.Payload)
	case "admin.audit":
		result, err = adminAuditOnly(conn, req.ActorID, req.Payload)
	default:
		err = fmt.Errorf("unsupported authoritative operation: %s", req.Operation)
	}
	if err != nil {
		return ActionResponse{}, err
	}
	return ActionResponse{Operation: req.Operation, Result: result}, nil
}

func begin(conn *storage.Conn) error { return conn.ExecScript("BEGIN IMMEDIATE;") }
func rollback(conn *storage.Conn)    { _ = conn.Rollback() }

func firstRowMap(result storage.Result) map[string]any {
	if len(result.Rows) == 0 {
		return nil
	}
	out := make(map[string]any, len(result.Columns))
	for i, name := range result.Columns {
		if i < len(result.Rows[0]) {
			out[name] = result.Rows[0][i]
		}
	}
	return out
}

func clamp(v, lo, hi int64) int64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
func i64(v any) int64 { return storage.ParseInt(v) }

type relationshipPayload struct {
	NPCName   string `json:"npc_name"`
	Trust     int64  `json:"trust"`
	Respect   int64  `json:"respect"`
	Fear      int64  `json:"fear"`
	Affection int64  `json:"affection"`
	Debt      int64  `json:"debt"`
	Grudge    int64  `json:"grudge"`
	Summary   string `json:"summary"`
}

func relationshipUpdate(conn *storage.Conn, userID int64, raw json.RawMessage) (any, error) {
	var p relationshipPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	p.NPCName = strings.TrimSpace(p.NPCName)
	if p.NPCName == "" {
		return nil, errors.New("npc_name is required")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	rowRes, err := conn.Execute(`SELECT trust,respect,fear,affection,debt,grudge,encounter_count FROM npc_relationships WHERE user_id=? AND npc_name=?`, []any{userID, p.NPCName})
	if err != nil {
		return nil, err
	}
	current := map[string]int64{"trust": 0, "respect": 0, "fear": 0, "affection": 0, "debt": 0, "grudge": 0, "encounter_count": 0}
	if row := firstRowMap(rowRes); row != nil {
		for k := range current {
			current[k] = i64(row[k])
		}
	}
	values := map[string]int64{
		"trust": clamp(current["trust"]+p.Trust, -100, 100), "respect": clamp(current["respect"]+p.Respect, -100, 100),
		"fear": clamp(current["fear"]+p.Fear, -100, 100), "affection": clamp(current["affection"]+p.Affection, -100, 100),
		"debt": clamp(current["debt"]+p.Debt, -100, 100), "grudge": clamp(current["grudge"]+p.Grudge, -100, 100),
	}
	encounter := current["encounter_count"] + 1
	summary := []rune(p.Summary)
	if len(summary) > 800 {
		summary = summary[:800]
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, err = conn.Execute(`INSERT INTO npc_relationships(user_id,npc_name,trust,respect,fear,affection,debt,grudge,encounter_count,last_summary,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,npc_name) DO UPDATE SET trust=excluded.trust,respect=excluded.respect,fear=excluded.fear,affection=excluded.affection,debt=excluded.debt,grudge=excluded.grudge,encounter_count=excluded.encounter_count,last_summary=excluded.last_summary,updated_at=excluded.updated_at`, []any{userID, p.NPCName, values["trust"], values["respect"], values["fear"], values["affection"], values["debt"], values["grudge"], encounter, string(summary), now})
	if err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	out := map[string]any{"user_id": userID, "npc_name": p.NPCName, "encounter_count": encounter, "last_summary": string(summary), "updated_at": now}
	for k, v := range values {
		out[k] = v
	}
	return out, nil
}

type scenePayload struct {
	PhysicalLocation string         `json:"physical_location"`
	SceneType        string         `json:"scene_type"`
	SceneKey         string         `json:"scene_key"`
	SceneLabel       string         `json:"scene_label"`
	ChannelID        *int64         `json:"channel_id"`
	Metadata         map[string]any `json:"metadata"`
}

func sceneTransition(conn *storage.Conn, userID int64, raw json.RawMessage) (any, error) {
	var p scenePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	metadata, _ := json.Marshal(p.Metadata)
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	var channelID any
	if p.ChannelID != nil {
		channelID = *p.ChannelID
	}
	_, err := conn.Execute(`INSERT INTO player_scene_state(user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET physical_location=excluded.physical_location,scene_type=excluded.scene_type,scene_key=excluded.scene_key,scene_label=excluded.scene_label,channel_id=excluded.channel_id,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at`, []any{userID, p.PhysicalLocation, p.SceneType, p.SceneKey, p.SceneLabel, channelID, string(metadata), now})
	if err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": userID, "physical_location": p.PhysicalLocation, "scene_type": p.SceneType, "scene_key": p.SceneKey, "scene_label": p.SceneLabel, "channel_id": p.ChannelID, "metadata": p.Metadata}, nil
}

type questPayload struct {
	QuestKey      string           `json:"quest_key"`
	Objectives    []map[string]any `json:"objectives"`
	ObjectiveType string           `json:"objective_type"`
	Amount        *int64           `json:"amount"`
	Target        *string          `json:"target"`
}

func questProgress(conn *storage.Conn, userID int64, raw json.RawMessage) (any, error) {
	var p questPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if err := rejectCallerGameMinute(raw); err != nil {
		return nil, err
	}
	if p.QuestKey == "" {
		return nil, errors.New("quest_key is required")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT progress_json FROM character_quests WHERE user_id=? AND quest_key=? AND status='active'`, []any{userID, p.QuestKey})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		_ = conn.Commit()
		return map[string]any{"touched": false, "complete": false}, nil
	}
	progress := map[string]int64{}
	if text, ok := row["progress_json"].(string); ok && text != "" {
		_ = json.Unmarshal([]byte(text), &progress)
	}
	payloadMap := map[string]any{"progress": progress, "objectives": p.Objectives, "objective_type": p.ObjectiveType, "target": p.Target}
	if p.Amount != nil {
		payloadMap["amount"] = *p.Amount
	}
	encoded, _ := json.Marshal(payloadMap)
	coreResp, coreErr := core.Apply(core.Request{APIVersion: core.APIVersion, Operation: "quest.progress", IdempotencyKey: fmt.Sprintf("go-quest-%d-%s-%d", userID, p.QuestKey, time.Now().UnixNano()), ActorID: fmt.Sprint(userID), ExpectedVersion: 0, Payload: encoded})
	if coreErr != nil {
		return nil, coreErr
	}
	transition, ok := coreResp.Result.(map[string]any)
	if !ok {
		return nil, errors.New("quest transition result malformed")
	}
	if touched, _ := transition["touched"].(bool); !touched {
		if err := conn.Commit(); err != nil {
			return nil, err
		}
		return transition, nil
	}
	nextJSON, _ := json.Marshal(transition["progress"])
	complete, _ := transition["complete"].(bool)
	status := "active"
	var completed any = nil
	if complete {
		status = "completed"
		completed = gameMinute
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, err = conn.Execute(`UPDATE character_quests SET progress_json=?,status=?,completed_game_minute=?,updated_at=? WHERE user_id=? AND quest_key=?`, []any{string(nextJSON), status, completed, now, userID, p.QuestKey})
	if err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	transition["quest_key"] = p.QuestKey
	transition["status"] = status
	return transition, nil
}

type combatPayload struct {
	BattleID int64 `json:"battle_id"`
	Damage   int64 `json:"damage"`
}

func combatApplyDamage(conn *storage.Conn, userID int64, raw json.RawMessage) (any, error) {
	var p combatPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if p.Damage < 0 {
		p.Damage = 0
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT b.player_hp,c.vitality,c.vitality_max FROM battles b JOIN characters c ON c.user_id=b.user_id WHERE b.battle_id=? AND b.user_id=? AND b.status='active'`, []any{p.BattleID, userID})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		_ = conn.Commit()
		return map[string]any{}, nil
	}
	current := i64(row["player_hp"])
	if v := i64(row["vitality"]); v < current {
		current = v
	}
	updated := current - p.Damage
	if updated < 0 {
		updated = 0
	}
	vmax := i64(row["vitality_max"])
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET vitality=?,updated_at=? WHERE user_id=?`, []any{updated, now, userID}); err != nil {
		return nil, err
	}
	if _, err = conn.Execute(`UPDATE battles SET player_hp=?,player_hp_max=MAX(player_hp_max,?),version=version+1,updated_at=? WHERE battle_id=? AND user_id=? AND status='active'`, []any{updated, vmax, now, p.BattleID, userID}); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"vitality": updated, "vitality_max": vmax}, nil
}

type cultivationPayload struct {
	Cultivation    int64            `json:"cultivation"`
	CultivationCap *int64           `json:"cultivation_cap"`
	SpiritStones   int64            `json:"spirit_stones"`
	InsightXP      int64            `json:"insight_xp"`
	Items          map[string]int64 `json:"items"`
	EventType      string           `json:"event_type"`
}

func cultivationReward(conn *storage.Conn, userID int64, raw json.RawMessage) (any, error) {
	var p cultivationPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if p.Cultivation < 0 {
		p.Cultivation = 0
	}
	if p.EventType == "" {
		p.EventType = "reward"
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	awarded := p.Cultivation
	if p.CultivationCap != nil {
		res, err := conn.Execute(`SELECT cultivation FROM characters WHERE user_id=?`, []any{userID})
		if err != nil {
			return nil, err
		}
		current := int64(0)
		if row := firstRowMap(res); row != nil {
			current = i64(row["cultivation"])
		}
		room := *p.CultivationCap - current
		if room < 0 {
			room = 0
		}
		if awarded > room {
			awarded = room
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`UPDATE characters SET cultivation=cultivation+?,spirit_stones=spirit_stones+?,insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`, []any{awarded, p.SpiritStones, p.InsightXP, now, userID}); err != nil {
		return nil, err
	}
	if p.SpiritStones != 0 {
		if _, err := conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance`, []any{userID, "low_spirit_stone", p.SpiritStones}); err != nil {
			return nil, err
		}
	}
	for item, qty := range p.Items {
		if qty <= 0 {
			continue
		}
		if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity`, []any{userID, item, qty}); err != nil {
			return nil, err
		}
	}
	eventPayload, _ := json.Marshal(map[string]any{"cultivation": awarded, "spirit_stones": p.SpiritStones, "insight_xp": p.InsightXP, "items": p.Items})
	if _, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{userID, p.EventType, string(eventPayload), now}); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"cultivation_awarded": awarded}, nil
}

func auditAdmin(conn *storage.Conn, adminUserID int64, action, target string, before, after any, reason string) error {
	beforeJSON, _ := json.Marshal(before)
	afterJSON, _ := json.Marshal(after)
	_, err := conn.Execute(`INSERT INTO admin_audit_log(admin_user_id,action,target,before_json,after_json,reason,created_at) VALUES(?,?,?,?,?,?,?)`, []any{
		adminUserID, action, target, string(beforeJSON), string(afterJSON), strings.TrimSpace(reason), float64(time.Now().UnixNano()) / 1e9,
	})
	return err
}

func decodeMap(raw json.RawMessage) (map[string]any, error) {
	var p map[string]any
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	return p, nil
}

func requiredInt(p map[string]any, key string) (int64, error) {
	v, ok := p[key]
	if !ok {
		return 0, fmt.Errorf("%s is required", key)
	}
	return storage.ParseInt(v), nil
}

func adminAdvanceTime(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	minutes, err := requiredInt(p, "minutes")
	if err != nil {
		return nil, err
	}
	if minutes == 0 || minutes < -5256000 || minutes > 5256000 {
		return nil, errors.New("minutes must be between -5256000 and 5256000 and non-zero")
	}
	requestedScale := int64(-1)
	if rawScale, ok := p["scale"]; ok {
		requestedScale = storage.ParseInt(rawScale)
		if requestedScale < 0 {
			return nil, errors.New("scale cannot be negative")
		}
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='world_clock'`, nil)
	if err != nil {
		return nil, err
	}
	defaultScale := int64(4)
	if requestedScale >= 0 {
		defaultScale = requestedScale
	}
	state := map[string]any{"anchor_game_minute": int64(480), "anchor_real_ts": now, "scale": defaultScale}
	if row := firstRowMap(res); row != nil {
		if text, ok := row["value_json"].(string); ok {
			_ = json.Unmarshal([]byte(text), &state)
		}
	}
	anchor := storage.ParseInt(state["anchor_game_minute"])
	storedScale := storage.ParseInt(state["scale"])
	if storedScale < 0 {
		storedScale = 0
	}
	nextScale := storedScale
	if requestedScale >= 0 {
		nextScale = requestedScale
	}
	anchorReal, _ := state["anchor_real_ts"].(float64)
	if anchorReal <= 0 {
		anchorReal = now
	}
	elapsedRealMinutes := (now - anchorReal) / 60.0
	if elapsedRealMinutes < 0 {
		elapsedRealMinutes = 0
	}
	current := anchor + int64(elapsedRealMinutes*float64(storedScale))
	if current < 0 {
		current = 0
	}
	updated := current + minutes
	if updated < 0 {
		updated = 0
	}
	next := map[string]any{"anchor_game_minute": updated, "anchor_real_ts": now, "scale": nextScale}
	encoded, _ := json.Marshal(next)
	_, err = conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, []any{string(encoded), now})
	if err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.world.advance_time", "world_clock", map[string]any{"game_minute": current}, map[string]any{"game_minute": updated, "delta": minutes}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"game_minute": updated, "delta": minutes}, nil
}

func adminGrantCurrency(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	amount, err := requiredInt(p, "amount")
	if err != nil {
		return nil, err
	}
	currency := strings.TrimSpace(fmt.Sprint(p["currency_id"]))
	if uid <= 0 || amount <= 0 || amount > 2000000000 || currency == "" || len(currency) > 80 {
		return nil, errors.New("invalid user_id, amount, or currency_id")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	beforeRes, _ := conn.Execute(`SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?`, []any{uid, currency})
	before := int64(0)
	if r := firstRowMap(beforeRes); r != nil {
		before = storage.ParseInt(r["balance"])
	}
	_, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance`, []any{uid, currency, amount})
	if err != nil {
		return nil, err
	}
	after := before + amount
	if currency == "low_spirit_stone" {
		_, err = conn.Execute(`UPDATE characters SET spirit_stones=MAX(0,spirit_stones+?),updated_at=? WHERE user_id=?`, []any{amount, float64(time.Now().UnixNano()) / 1e9, uid})
		if err != nil {
			return nil, err
		}
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.grant_currency", fmt.Sprintf("user:%d", uid), map[string]any{"currency": currency, "balance": before}, map[string]any{"currency": currency, "amount": amount, "balance": after}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "currency_id": currency, "amount": amount, "balance": after}, nil
}

func adminKarma(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	delta, err := requiredInt(p, "delta")
	if err != nil {
		return nil, err
	}
	if uid <= 0 || delta < -2000 || delta > 2000 {
		return nil, errors.New("invalid user_id or karma delta")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name,karma_score FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	before := storage.ParseInt(row["karma_score"])
	after := clamp(before+delta, -1000, 1000)
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET karma_score=?,updated_at=? WHERE user_id=?`, []any{after, now, uid}); err != nil {
		return nil, err
	}
	payload, _ := json.Marshal(map[string]any{"delta": delta, "reason": strings.TrimSpace(fmt.Sprint(p["reason"])), "score": after})
	if _, err = conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`, []any{uid, "karma_change", string(payload), now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.karma", fmt.Sprintf("user:%d", uid), map[string]any{"karma": before}, map[string]any{"karma": after, "delta": delta}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "karma_score": after, "delta": delta}, nil
}

func adminTeleport(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	loc := strings.TrimSpace(fmt.Sprint(p["location"]))
	if uid <= 0 || loc == "" {
		return nil, errors.New("user_id and location are required")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	countRes, err := conn.Execute(`SELECT COUNT(*) AS n FROM catalog_locations`, nil)
	if err != nil {
		return nil, err
	}
	locationCount := int64(0)
	if row := firstRowMap(countRes); row != nil {
		locationCount = storage.ParseInt(row["n"])
	}
	if locationCount > 0 {
		valid, err := conn.Execute(`SELECT 1 FROM catalog_locations WHERE name=?`, []any{loc})
		if err != nil {
			return nil, err
		}
		if firstRowMap(valid) == nil {
			return nil, errors.New("unknown canonical location")
		}
	}
	res, err := conn.Execute(`SELECT name,location FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	before := fmt.Sprint(row["location"])
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET location=?,updated_at=? WHERE user_id=?`, []any{loc, now, uid}); err != nil {
		return nil, err
	}
	_, _ = conn.Execute(`UPDATE player_scene_state SET physical_location=?,scene_type='world',scene_key='',scene_label=?,channel_id=NULL,metadata_json='{}',updated_at=? WHERE user_id=?`, []any{loc, loc, now, uid})
	if err := auditAdmin(conn, adminUserID, "admin.player.teleport", fmt.Sprintf("user:%d", uid), map[string]any{"location": before}, map[string]any{"location": loc}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "location": loc}, nil
}

func adminRevive(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name,life_status,vitality,vitality_max,qi,qi_max FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET life_status='alive',death_game_minute=NULL,reincarnation_ready_game_minute=NULL,vitality=vitality_max,qi=qi_max,updated_at=? WHERE user_id=?`, []any{now, uid}); err != nil {
		return nil, err
	}
	_, _ = conn.Execute(`UPDATE reincarnation_state SET active=0 WHERE user_id=?`, []any{uid})
	_, _ = conn.Execute(`UPDATE battles SET status='abandoned',updated_at=? WHERE user_id=? AND status='active'`, []any{now, uid})
	if err := auditAdmin(conn, adminUserID, "admin.player.revive", fmt.Sprintf("user:%d", uid), row, map[string]any{"life_status": "alive", "vitality": "full", "qi": "full"}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "life_status": "alive"}, nil
}

func adminClearBattle(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`UPDATE battles SET status='abandoned',updated_at=? WHERE user_id=? AND status='active'`, []any{float64(time.Now().UnixNano()) / 1e9, uid})
	if err != nil {
		return nil, err
	}
	count := res.RowsAffected
	if err := auditAdmin(conn, adminUserID, "admin.player.clear_battle", fmt.Sprintf("user:%d", uid), map[string]any{}, map[string]any{"cleared": count}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "cleared": count}, nil
}

func adminAutomationSet(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	name := strings.TrimSpace(fmt.Sprint(p["system"]))
	enabled, ok := p["enabled"].(bool)
	if !ok {
		return nil, errors.New("enabled must be boolean")
	}
	defaults := map[string]bool{
		"event_expiry":            true,
		"auction_settlement":      true,
		"unexpected_events":       true,
		"maintenance_cleanup":     true,
		"npc_civilization":        true,
		"npc_life":                true,
		"sect_politics":           true,
		"dynamic_economy":         true,
		"clan_dynamics":           true,
		"background_seclusion":    true,
		"black_markets":           true,
		"autonomous_world_events": true,
	}
	if _, ok := defaults[name]; !ok {
		return nil, errors.New("unknown automation system")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='automation_settings'`, nil)
	if err != nil {
		return nil, err
	}
	settings := defaults
	if row := firstRowMap(res); row != nil {
		if text, ok := row["value_json"].(string); ok {
			_ = json.Unmarshal([]byte(text), &settings)
		}
	}
	before := settings[name]
	settings[name] = enabled
	encoded, _ := json.Marshal(settings)
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES('automation_settings',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, []any{string(encoded), now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.automation.set", name, map[string]any{"enabled": before}, map[string]any{"enabled": enabled}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"system": name, "enabled": enabled, "settings": settings}, nil
}

func adminSimulationInterval(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	name := strings.TrimSpace(fmt.Sprint(p["system"]))
	days, err := requiredInt(p, "days")
	if err != nil {
		return nil, err
	}
	if days < 1 || days > 365 {
		return nil, errors.New("days must be 1..365")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT interval_game_minutes FROM world_simulation_state WHERE system=?`, []any{name})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("unknown simulation system")
	}
	before := storage.ParseInt(row["interval_game_minutes"])
	after := days * 1440
	if _, err = conn.Execute(`UPDATE world_simulation_state SET interval_game_minutes=? WHERE system=?`, []any{after, name}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.simulation.interval", name, map[string]any{"interval_game_minutes": before}, map[string]any{"interval_game_minutes": after, "days": days}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"system": name, "days": days, "interval_game_minutes": after}, nil
}

func adminAuditOnly(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	action := strings.TrimSpace(fmt.Sprint(p["action"]))
	target := strings.TrimSpace(fmt.Sprint(p["target"]))
	if action == "" {
		return nil, errors.New("action is required")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	before := p["before"]
	after := p["after"]
	if before == nil {
		before = map[string]any{}
	}
	if after == nil {
		after = map[string]any{}
	}
	if err := auditAdmin(conn, adminUserID, action, target, before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"logged": true, "action": action, "target": target}, nil
}
