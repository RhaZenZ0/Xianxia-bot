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
	case "admin.player.fate":
		result, err = adminFate(conn, req.ActorID, req.Payload)
	case "admin.player.teleport":
		result, err = adminTeleport(conn, req.ActorID, req.Payload)
	case "admin.player.revive":
		result, err = adminRevive(conn, req.ActorID, req.Payload)
	case "admin.player.clear_battle":
		result, err = adminClearBattle(conn, req.ActorID, req.Payload)
	case "admin.automation.set":
		result, err = adminAutomationSet(conn, req.ActorID, req.Payload)
	case "admin.narration.set_chain":
		result, err = adminNarrationSetChain(conn, req.ActorID, req.Payload)
	case "admin.simulation.interval":
		result, err = adminSimulationInterval(conn, req.ActorID, req.Payload)
	case "admin.commission.review":
		result, err = adminQuestReview(conn, req.ActorID, req.Payload, "admin.commission.review")
	case "admin.commission.retire":
		result, err = adminCommissionRetire(conn, req.ActorID, req.Payload)
	// v0.24.0. The review action was always general - it changes the status of
	// a quest_definitions row and never looked at whether the row had a giver -
	// but it was named and audited as though commissions were the only thing
	// with a status. The Quests workbench approves, retires and discards every
	// kind of definition through this name; the older one keeps working.
	case "admin.quest.review":
		result, err = adminQuestReview(conn, req.ActorID, req.Payload, "admin.quest.review")
	case "admin.quest.save":
		result, err = adminQuestSave(conn, req.ActorID, req.Payload)
	case "admin.audit":
		result, err = adminAuditOnly(conn, req.ActorID, req.Payload)
	case "admin.player.set_realm":
		result, err = adminSetRealm(conn, req.ActorID, req.Payload)
	case "admin.player.set_resource_caps":
		result, err = adminSetResourceCaps(conn, req.ActorID, req.Payload)
	case "admin.player.adjust_item":
		result, err = adminAdjustItem(conn, req.ActorID, req.Payload)
	case "admin.player.reset_cooldowns":
		result, err = adminResetCooldowns(conn, req.ActorID, req.Payload)
	case "admin.player.force_end_scene":
		result, err = adminForceEndScene(conn, req.ActorID, req.Payload)
	case "admin.npc.relocate":
		result, err = adminNpcRelocate(conn, req.ActorID, req.Payload)
	case "admin.world_event.end":
		result, err = adminEndWorldEvent(conn, req.ActorID, req.Payload)
	case "admin.bulk.grant_currency":
		result, err = adminBulkGrantCurrency(conn, req.ActorID, req.Payload)
	case "admin.bulk.reset_cooldowns":
		result, err = adminBulkResetCooldowns(conn, req.ActorID, req.Payload)
	case "admin.player.set_sect":
		result, err = adminSetSect(conn, req.ActorID, req.Payload)
	case "sect.discover":
		result, err = sectDiscoverAction(conn, req.ActorID, req.Payload)
	case "admin.player.set_master":
		result, err = adminSetMaster(conn, req.ActorID, req.Payload)
	case "admin.player.set_sect_rank":
		result, err = adminSetSectRank(conn, req.ActorID, req.Payload)
	case "admin.player.master_attention":
		result, err = adminMasterAttention(conn, req.ActorID, req.Payload)
	case "admin.player.grant_storage":
		result, err = adminGrantStorage(conn, req.ActorID, req.Payload)
	case "admin.world.spawn_realm":
		result, err = adminSpawnRealm(conn, req.ActorID, req.Payload)
	case "admin.player.set_realm_perfection":
		result, err = adminSetRealmPerfection(conn, req.ActorID, req.Payload)
	case "admin.player.set_spiritual_root":
		result, err = adminSetSpiritualRoot(conn, req.ActorID, req.Payload)
	case "admin.player.set_bloodline":
		result, err = adminSetBloodline(conn, req.ActorID, req.Payload)
	case "admin.player.set_physique":
		result, err = adminSetPhysique(conn, req.ActorID, req.Payload)
	case "admin.player.set_tribulation":
		result, err = adminSetTribulation(conn, req.ActorID, req.Payload)
	case "admin.player.clear_condition":
		result, err = adminClearCondition(conn, req.ActorID, req.Payload)
	case "admin.player.force_reincarnation_ready":
		result, err = adminForceReincarnationReady(conn, req.ActorID, req.Payload)
	case "admin.player.set_pill_toxicity":
		result, err = adminSetPillToxicity(conn, req.ActorID, req.Payload)
	case "admin.player.set_beast_stats":
		result, err = adminSetBeastStats(conn, req.ActorID, req.Payload)
	case "admin.player.remove_equipment":
		result, err = adminRemoveEquipment(conn, req.ActorID, req.Payload)
	case "admin.player.set_abode_access":
		result, err = adminSetAbodeAccess(conn, req.ActorID, req.Payload)
	case "admin.player.set_moderation":
		result, err = adminSetModeration(conn, req.ActorID, req.Payload)
	case "admin.audit.undo_last":
		result, err = adminUndoLastAction(conn, req.ActorID, req.Payload)
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
	QuestKey      string  `json:"quest_key"`
	ObjectiveType string  `json:"objective_type"`
	Amount        *int64  `json:"amount"`
	Target        *string `json:"target"`
	// What the quest asks for and what it pays are deliberately NOT here.
	// Until v0.24.0 the caller sent both with every progress report, which
	// meant Python decided the terms and a GM editing a definition rewrote a
	// deal a player had already accepted. Both now come off the player's own
	// character_quests row (quest_terms.go). Callers may still send the old
	// fields; they are ignored.
}

// Ceilings on what one quest completion may pay, as a backstop independent of
// whatever the caller sent. The GM budget in app/ops/config.py is lower
// (50 xp / 200 stones / 3 items); these exist so that a bug or a compromised
// caller cannot mint an economy through the quest path.
const (
	questRewardMaxInsight = 500
	questRewardMaxStones  = 2000
	questRewardMaxItems   = 20
)

// grantQuestRewardTx pays a completed non-commission quest inside the caller's
// transaction.
//
// Before v0.22.2 this was a second engine call made by Python after
// quest.progress had already committed the completion. If anything went wrong
// in between - a dropped connection, an engine restart, a killed worker - the
// quest was completed and never paid, and no retry could fix it: the next
// progress report skips a quest that is no longer active. Completion and
// payment are now one commit.
func grantQuestRewardTx(conn *storage.Conn, userID int64, questKey string, rewards map[string]any) (map[string]any, error) {
	stones := clamp(i64(rewards["spirit_stones"]), 0, questRewardMaxStones)
	insight := clamp(i64(rewards["insight_xp"]), 0, questRewardMaxInsight)
	items := map[string]int64{}
	total := int64(0)
	if raw, ok := rewards["items"].(map[string]any); ok {
		for id, qty := range raw {
			n := i64(qty)
			if n <= 0 {
				continue
			}
			if total+n > questRewardMaxItems {
				n = questRewardMaxItems - total
			}
			if n <= 0 {
				break
			}
			items[id] = n
			total += n
		}
	}
	if stones == 0 && insight == 0 && len(items) == 0 {
		return map[string]any{}, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if stones != 0 || insight != 0 {
		if _, err := conn.Execute(`UPDATE characters SET spirit_stones=spirit_stones+?,insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`,
			[]any{stones, insight, now, userID}); err != nil {
			return nil, err
		}
	}
	if stones != 0 {
		if _, err := walletDeltaTx(conn, userID, "low_spirit_stone", stones, now); err != nil {
			return nil, err
		}
	}
	if len(items) > 0 {
		if err := addInventoryTx(conn, userID, items); err != nil {
			return nil, err
		}
	}
	granted := map[string]any{}
	if stones != 0 {
		granted["spirit_stones"] = stones
	}
	if insight != 0 {
		granted["insight_xp"] = insight
	}
	if len(items) > 0 {
		granted["items"] = items
	}
	payload, _ := json.Marshal(granted)
	if _, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, "quest_reward:" + questKey, string(payload), now}); err != nil {
		return nil, err
	}
	return granted, nil
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
	// v0.24.0: the terms come off the player's own row, not out of the payload.
	// `terms_json` is selected only when it exists, so an engine pointed at a
	// database Python has not migrated yet still runs - it simply falls back to
	// reading the definition, which is all it could ever do before.
	pinnedTerms, err := tableHasColumns(conn, "character_quests", questTermsColumn)
	if err != nil {
		return nil, err
	}
	columns := `progress_json,commission,variant_index`
	if pinnedTerms {
		columns += `,` + questTermsColumn
	}
	res, err := conn.Execute(
		`SELECT `+columns+` FROM character_quests WHERE user_id=? AND quest_key=? AND status='active'`,
		[]any{userID, p.QuestKey})
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
	terms, err := acceptedQuestTermsTx(conn, userID, p.QuestKey, i64(row["variant_index"]), row[questTermsColumn], gameMinute)
	if err != nil {
		return nil, err
	}
	payloadMap := map[string]any{"progress": progress, "objectives": terms.Objectives, "objective_type": p.ObjectiveType, "target": p.Target}
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
	isCommission := i64(row["commission"]) == 1
	status := "active"
	var completed any = nil
	if complete {
		completed = gameMinute
		// A commission's status is flipped by resolveCommissionTx below,
		// which looks the row up as `active` first. Writing `completed` here
		// made that lookup fail on the last objective of every commission
		// (v0.34.0 playtest finding: "no active commission by that name"),
		// rolling the whole progress back - so the final objective could
		// never be turned in. Only an ordinary quest completes on this line.
		if !isCommission {
			status = "completed"
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, err = conn.Execute(`UPDATE character_quests SET progress_json=?,status=?,completed_game_minute=?,updated_at=? WHERE user_id=? AND quest_key=?`, []any{string(nextJSON), status, completed, now, userID, p.QuestKey})
	if err != nil {
		return nil, err
	}
	// A commission that just finished its objectives resolves here rather
	// than simply flipping status: paying the locked terms and moving
	// standing with the giver belongs in one place (commission_actions.go),
	// so completion by progress and completion by any other route cannot
	// drift apart. Python does not grant the reward for these.
	if complete && isCommission {
		resolved, resolveErr := resolveCommissionTx(conn, userID, p.QuestKey, "completed", gameMinute, true)
		if resolveErr != nil {
			return nil, resolveErr
		}
		transition["commission"] = resolved
	} else if complete {
		granted, grantErr := grantQuestRewardTx(conn, userID, p.QuestKey, terms.Rewards)
		if grantErr != nil {
			return nil, grantErr
		}
		transition["rewards_granted"] = granted
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

// stringField reads an optional string out of a decoded payload.
//
// fmt.Sprint on a missing key yields the four characters "<nil>", which is not
// empty - so `stringField(p, "realm_id") == ""` never fires
// for an absent field, and a required-field check written that way passes on
// exactly the payload it was meant to reject.
func stringField(p map[string]any, key string) string {
	v, ok := p[key]
	if !ok || v == nil {
		return ""
	}
	return strings.TrimSpace(fmt.Sprint(v))
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
	currency := stringField(p, "currency_id")
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
	payload, _ := json.Marshal(map[string]any{"delta": delta, "reason": stringField(p, "reason"), "score": after})
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

// adminFate lets a GM grant or deduct Fate outside the automatic canonical
// triggers (spendFateGo on an averted true death, addFateGo on meaningful
// mercy or clearing a tribulation). It reuses the same adjustFateGo
// primitive those triggers use - clamped to 0-9, and recorded in
// fate_ledger - so a manual grant shows up in the player's own /fate
// history exactly like an automatic one, distinguishable only by its reason
// text.
func adminFate(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	if uid <= 0 || delta < -9 || delta > 9 {
		return nil, errors.New("invalid user_id or fate delta")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	res, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	beforeRes, err := conn.Execute(`SELECT points FROM character_fate WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	before := int64(0)
	if beforeRow := firstRowMap(beforeRes); beforeRow != nil {
		before = i64(beforeRow["points"])
	}
	now := float64(time.Now().UnixNano()) / 1e9
	reason := stringField(p, "reason")
	after, err := adjustFateGo(conn, uid, delta, firstNonempty(reason, "GM dashboard fate adjustment"), gameMinute, now)
	if err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.fate", fmt.Sprintf("user:%d", uid), map[string]any{"points": before}, map[string]any{"points": after, "delta": delta}, reason); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "points": after, "delta": delta}, nil
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
	loc := stringField(p, "location")
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

// adminClearBattle force-ends whatever kind of stuck combat state a player is
// in. It originally only reached the 1v1 `battles` table, which silently left
// a player stuck in a group-combat boss encounter (found only via
// `boss_participants`, not `battles`) or an active/pending PvP match/challenge
// untouched - a real gap for the actual "player is stuck" scenarios this
// action exists for. All four are now cleared in one transaction, one audit
// entry, with a single combined "cleared" count across them.
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
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(`UPDATE battles SET status='abandoned',updated_at=? WHERE user_id=? AND status='active'`, []any{now, uid})
	if err != nil {
		return nil, err
	}
	cleared := res.RowsAffected
	bossRes, err := conn.Execute(`UPDATE boss_encounters SET status='abandoned',updated_at=? WHERE status='active' AND encounter_id IN (SELECT encounter_id FROM boss_participants WHERE user_id=? AND status='active')`, []any{now, uid})
	if err != nil {
		return nil, err
	}
	cleared += bossRes.RowsAffected
	pvpMatchRes, err := conn.Execute(`UPDATE pvp_matches SET status='abandoned',updated_at=? WHERE status='active' AND (player1_user_id=? OR player2_user_id=?)`, []any{now, uid, uid})
	if err != nil {
		return nil, err
	}
	cleared += pvpMatchRes.RowsAffected
	pvpChallengeRes, err := conn.Execute(`UPDATE pvp_challenges SET status='cancelled' WHERE status='pending' AND (challenger_user_id=? OR target_user_id=?)`, []any{uid, uid})
	if err != nil {
		return nil, err
	}
	cleared += pvpChallengeRes.RowsAffected
	if err := auditAdmin(conn, adminUserID, "admin.player.clear_battle", fmt.Sprintf("user:%d", uid), map[string]any{}, map[string]any{"cleared": cleared}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "cleared": cleared}, nil
}

func adminAutomationSet(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	name := stringField(p, "system")
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
		// v0.31.0: when on, exploration openings and hunt results are
		// narrated by the model without being asked; off, they read from
		// the procedural pool and offer a Narrate-it button.
		"ai_routine_narration": false,
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
	name := stringField(p, "system")
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
	action := stringField(p, "action")
	target := stringField(p, "target")
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

// adminSetRealm lets a GM directly set a character's realm_index and phase -
// a story correction / GM override that bypasses normal breakthrough gating.
// Every other mutation of these fields only happens as a side effect of the
// bespoke breakthrough/perfection logic (cultivation_actions.go,
// perfection_actions.go); this is the only direct admin path to them. The
// world's content pack defines 32 realms (index 0-31), each with 9 phases
// (1-9) - see content/world.json - so those are the sanity bounds, checked
// statically rather than against a loaded catalog (this plain admin.*
// dispatch path, unlike the authoritative one, has no catalog loaded).
func adminSetRealm(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	realmIndex, err := requiredInt(p, "realm_index")
	if err != nil {
		return nil, err
	}
	phase, err := requiredInt(p, "phase")
	if err != nil {
		return nil, err
	}
	if uid <= 0 || realmIndex < 0 || realmIndex > 31 || phase < 1 || phase > 9 {
		return nil, errors.New("invalid user_id, realm_index (0-31), or phase (1-9)")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name,realm_index,phase FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	before := map[string]any{"realm_index": storage.ParseInt(row["realm_index"]), "phase": storage.ParseInt(row["phase"])}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET realm_index=?,phase=?,updated_at=? WHERE user_id=?`, []any{realmIndex, phase, now, uid}); err != nil {
		return nil, err
	}
	after := map[string]any{"realm_index": realmIndex, "phase": phase}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_realm", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "realm_index": realmIndex, "phase": phase}, nil
}

// adminSetResourceCaps lets a GM directly set vitality_max and/or qi_max.
// Every other mutation of these caps only happens as a breakthrough/
// perfection/secret-realm reward; there was previously no direct admin path.
// Either field may be omitted (only the supplied one changes). Lowering a cap
// below the character's current resource clamps that resource down to the
// new cap so it never reads higher than its own maximum; raising a cap does
// NOT also refill the resource - that stays a separate, deliberate choice
// (e.g. pair with admin.player.revive if a full refill is also wanted).
func adminSetResourceCaps(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	_, hasVitality := p["vitality_max"]
	_, hasQi := p["qi_max"]
	if !hasVitality && !hasQi {
		return nil, errors.New("vitality_max or qi_max is required")
	}
	vitalityMax := storage.ParseInt(p["vitality_max"])
	qiMax := storage.ParseInt(p["qi_max"])
	if hasVitality && (vitalityMax < 1 || vitalityMax > 1000000) {
		return nil, errors.New("vitality_max out of range")
	}
	if hasQi && (qiMax < 1 || qiMax > 1000000) {
		return nil, errors.New("qi_max out of range")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name,vitality,vitality_max,qi,qi_max FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	before := map[string]any{"vitality_max": storage.ParseInt(row["vitality_max"]), "qi_max": storage.ParseInt(row["qi_max"])}
	newVitalityMax := storage.ParseInt(row["vitality_max"])
	newQiMax := storage.ParseInt(row["qi_max"])
	if hasVitality {
		newVitalityMax = vitalityMax
	}
	if hasQi {
		newQiMax = qiMax
	}
	newVitality := storage.ParseInt(row["vitality"])
	if newVitality > newVitalityMax {
		newVitality = newVitalityMax
	}
	newQi := storage.ParseInt(row["qi"])
	if newQi > newQiMax {
		newQi = newQiMax
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE characters SET vitality_max=?,qi_max=?,vitality=?,qi=?,updated_at=? WHERE user_id=?`, []any{newVitalityMax, newQiMax, newVitality, newQi, now, uid}); err != nil {
		return nil, err
	}
	after := map[string]any{"vitality_max": newVitalityMax, "qi_max": newQiMax}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_resource_caps", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "vitality_max": newVitalityMax, "qi_max": newQiMax, "vitality": newVitality, "qi": newQi}, nil
}

// adminAdjustItem grants (positive quantity) or removes (negative quantity)
// an inventory item by a signed delta, floored at 0 (removing more than the
// character holds just zeroes it out rather than erroring, matching how
// currency grants floor at 0 elsewhere in this file). There was previously no
// admin path to inventory at all - every other inventory write is a side
// effect of unrelated gameplay logic (crafting, combat rewards, quests).
func adminAdjustItem(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	item := stringField(p, "item_id")
	delta, err := requiredInt(p, "quantity")
	if err != nil {
		return nil, err
	}
	if uid <= 0 || item == "" || len(item) > 80 || delta == 0 || delta < -1000000 || delta > 1000000 {
		return nil, errors.New("invalid user_id, item_id, or quantity delta")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	beforeRes, err := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{uid, item})
	if err != nil {
		return nil, err
	}
	before := int64(0)
	if r := firstRowMap(beforeRes); r != nil {
		before = storage.ParseInt(r["quantity"])
	}
	after := before + delta
	if after < 0 {
		after = 0
	}
	if delta > 0 && isUniqueEquipmentGo(item) {
		// A unique reward is one per character, carried or bound - see
		// uniqueEquipmentIDsGo. Bound copies live in equipment_instances, not
		// inventory, so a quantity check alone would let a GM grant a second
		// carried copy to someone already wielding one.
		if after > 1 {
			return nil, fmt.Errorf("%s is unique: a character can hold at most one", item)
		}
		boundRes, err := conn.Execute(`SELECT COUNT(*) AS n FROM equipment_instances WHERE user_id=? AND item_id=?`, []any{uid, item})
		if err != nil {
			return nil, err
		}
		if r := firstRowMap(boundRes); r != nil && storage.ParseInt(r["n"]) > 0 {
			return nil, fmt.Errorf("%s is unique and this character already has it bound", item)
		}
	}
	if after == 0 {
		if _, err = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{uid, item}); err != nil {
			return nil, err
		}
	} else if _, err = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=excluded.quantity`, []any{uid, item, after}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.adjust_item", fmt.Sprintf("user:%d", uid), map[string]any{"item_id": item, "quantity": before}, map[string]any{"item_id": item, "quantity": after, "delta": delta}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "item_id": item, "quantity": after, "delta": delta}, nil
}

// adminResetCooldowns clears a player's cooldowns table rows - all of them,
// or just one named action if "action" is supplied. There was previously no
// admin path to this simple keyed table at all.
//
// The sect entrance trial's retry wait is not a cooldowns row: it is read
// off the last failed sect_recruitment_attempts row (a day, sect_actions.go),
// so "reset cooldowns" left it standing (v0.34.0 playtest finding). A full
// reset now ages those rows out too - the attempt history stays, dated a day
// earlier than it was, which is exactly "the wait is over" to that check
// whatever the world's age (a world younger than a day cannot be helped by
// dating them to zero).
func adminResetCooldowns(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	action := stringField(p, "action")
	if action == "<nil>" {
		action = ""
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	var res storage.Result
	if action == "" {
		res, err = conn.Execute(`DELETE FROM cooldowns WHERE user_id=?`, []any{uid})
	} else {
		res, err = conn.Execute(`DELETE FROM cooldowns WHERE user_id=? AND action=?`, []any{uid, action})
	}
	if err != nil {
		return nil, err
	}
	cleared := res.RowsAffected
	trialRetries := int64(0)
	if action == "" || action == "sect_trial" {
		aged, err := conn.Execute(`UPDATE sect_recruitment_attempts SET game_minute=game_minute-1440 WHERE user_id=? AND attempt_type='trial' AND result='fail'`, []any{uid})
		if err != nil && !strings.Contains(err.Error(), "no such table") {
			return nil, err
		}
		if err == nil {
			trialRetries = aged.RowsAffected
		}
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.reset_cooldowns", fmt.Sprintf("user:%d", uid), map[string]any{}, map[string]any{"cleared": cleared, "trial_retries_cleared": trialRetries, "action": action}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "cleared": cleared, "trial_retries_cleared": trialRetries}, nil
}

// adminForceEndScene resets a stuck player_scene_state row back to
// scene_type='world' at the player's CURRENT location, without moving them -
// unlike admin.player.teleport, which resets the scene as a side effect of
// also relocating the character. This is for a player stuck in a bad scene
// state where teleporting them away isn't the right fix.
func adminForceEndScene(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	res, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("character not found")
	}
	beforeRes, err := conn.Execute(`SELECT scene_type,scene_label FROM player_scene_state WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	before := map[string]any{"scene_type": "world", "scene_label": ""}
	if beforeRow := firstRowMap(beforeRes); beforeRow != nil {
		before = map[string]any{"scene_type": beforeRow["scene_type"], "scene_label": beforeRow["scene_label"]}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE player_scene_state SET scene_type='world',scene_key='',scene_label='',channel_id=NULL,metadata_json='{}',updated_at=? WHERE user_id=?`, []any{now, uid}); err != nil {
		return nil, err
	}
	after := map[string]any{"scene_type": "world", "scene_label": ""}
	if err := auditAdmin(conn, adminUserID, "admin.player.force_end_scene", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "scene_type": "world"}, nil
}

// adminNpcRelocate force-moves an NPC's current_location. NPCs are otherwise
// entirely simulation-owned (npc_civilization_state/npc_life_state are only
// written by the automatic simulation package) - there was no admin write
// path to NPC state at all before this.
func adminNpcRelocate(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	name := stringField(p, "npc_name")
	loc := stringField(p, "location")
	if name == "" || loc == "" {
		return nil, errors.New("npc_name and location are required")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT current_location FROM npc_civilization_state WHERE npc_name=?`, []any{name})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("npc not found")
	}
	before := fmt.Sprint(row["current_location"])
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE npc_civilization_state SET current_location=?,updated_at=? WHERE npc_name=?`, []any{loc, now, name}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.npc.relocate", fmt.Sprintf("npc:%s", name), map[string]any{"location": before}, map[string]any{"location": loc}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"npc_name": name, "location": loc}, nil
}

// adminEndWorldEvent ends an active world_events row early. Automatic
// spawning/expiry happens in the simulation package; there was previously no
// admin path to end one specific event on demand (only throttle the
// automatic cadence via admin.automation.set/admin.simulation.interval).
func adminEndWorldEvent(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	key := stringField(p, "event_key")
	if key == "" {
		return nil, errors.New("event_key is required")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT title,active FROM world_events WHERE event_key=?`, []any{key})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("world event not found")
	}
	before := storage.ParseInt(row["active"])
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE world_events SET active=0,ends_at=? WHERE event_key=?`, []any{now, key}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.world_event.end", fmt.Sprintf("event:%s", key), map[string]any{"active": before}, map[string]any{"active": 0}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"event_key": key, "title": row["title"], "active": false}, nil
}

// adminBulkGrantCurrency applies a currency grant to every character in one
// transaction with a single "all" audit entry, rather than the frontend
// looping N single-target admin.player.grant_currency calls (which would
// mean N round trips and N separate audit rows for one GM decision). No bulk
// primitive existed before this - admin.world.advance_time only looks bulk
// because it's one shared clock row, not a per-character loop.
func adminBulkGrantCurrency(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	amount, err := requiredInt(p, "amount")
	if err != nil {
		return nil, err
	}
	currency := stringField(p, "currency_id")
	if amount <= 0 || amount > 2000000000 || currency == "" || len(currency) > 80 {
		return nil, errors.New("invalid amount or currency_id")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT user_id FROM characters`, nil)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	var count int64
	for _, r := range res.Rows {
		if len(r) == 0 {
			continue
		}
		uid := storage.ParseInt(r[0])
		if _, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance`, []any{uid, currency, amount}); err != nil {
			return nil, err
		}
		if currency == "low_spirit_stone" {
			if _, err = conn.Execute(`UPDATE characters SET spirit_stones=MAX(0,spirit_stones+?),updated_at=? WHERE user_id=?`, []any{amount, now, uid}); err != nil {
				return nil, err
			}
		}
		count++
	}
	if err := auditAdmin(conn, adminUserID, "admin.bulk.grant_currency", "all", map[string]any{}, map[string]any{"currency": currency, "amount": amount, "characters": count}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"currency_id": currency, "amount": amount, "characters": count}, nil
}

// adminBulkResetCooldowns clears every character's cooldowns in one
// transaction with a single "all" audit entry, same rationale as
// adminBulkGrantCurrency above.
func adminBulkResetCooldowns(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`DELETE FROM cooldowns`, nil)
	if err != nil {
		return nil, err
	}
	cleared := res.RowsAffected
	if err := auditAdmin(conn, adminUserID, "admin.bulk.reset_cooldowns", "all", map[string]any{}, map[string]any{"cleared": cleared}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"cleared": cleared}, nil
}

// adminSetSect lets a GM assign, re-rank, or remove a player's sect
// membership directly. Auto-vivifies the sects row exactly like Python's
// set_sect_membership() does, so a "set" call never fails on a sect_name
// that isn't in the sects table yet. joined_at is preserved on a rank
// change (only stamped fresh on brand-new membership). sect_name carries no
// DB-level enum and rank_name/rank_level have no canonical ladder anywhere
// in this codebase to enforce (sect_actions.go only hardcodes one rank on
// recruitment) - the GM supplies both explicitly.
func adminSetSect(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	remove, _ := p["remove"].(bool)

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	beforeRes, err := conn.Execute(`SELECT sect_name,rank_name,rank_level FROM sect_membership WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	beforeRow := firstRowMap(beforeRes)
	var before map[string]any
	if beforeRow != nil {
		before = map[string]any{"sect_name": beforeRow["sect_name"], "rank_name": beforeRow["rank_name"], "rank_level": storage.ParseInt(beforeRow["rank_level"])}
	} else {
		before = map[string]any{"sect_name": nil}
	}

	if remove {
		if beforeRow == nil {
			return nil, errors.New("player has no sect membership to remove")
		}
		if _, err = conn.Execute(`DELETE FROM sect_membership WHERE user_id=?`, []any{uid}); err != nil {
			return nil, err
		}
		if err := auditAdmin(conn, adminUserID, "admin.player.set_sect", fmt.Sprintf("user:%d", uid), before, map[string]any{"sect_name": nil}, fmt.Sprint(p["reason"])); err != nil {
			return nil, err
		}
		if err := conn.Commit(); err != nil {
			return nil, err
		}
		return map[string]any{"user_id": uid, "name": charRow["name"], "sect_name": nil}, nil
	}

	sectName := stringField(p, "sect_name")
	rankName := stringField(p, "rank_name")
	rankLevel, err := requiredInt(p, "rank_level")
	if err != nil {
		return nil, err
	}
	if sectName == "" || len(sectName) > 120 || rankName == "" || len(rankName) > 60 || rankLevel < 0 || rankLevel > 100 {
		return nil, errors.New("invalid sect_name, rank_name, or rank_level (0-100)")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO sects(sect_name,updated_at) VALUES(?,?) ON CONFLICT(sect_name) DO NOTHING`, []any{sectName, now}); err != nil {
		return nil, err
	}
	if _, err = conn.Execute(`INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(?,?,?,?,?)
		ON CONFLICT(user_id) DO UPDATE SET sect_name=excluded.sect_name,rank_name=excluded.rank_name,rank_level=excluded.rank_level`,
		[]any{uid, sectName, rankName, rankLevel, now}); err != nil {
		return nil, err
	}
	after := map[string]any{"sect_name": sectName, "rank_name": rankName, "rank_level": rankLevel}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_sect", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "sect_name": sectName, "rank_name": rankName, "rank_level": rankLevel}, nil
}

// adminSetRealmPerfection directly sets progress (0-100) on a player's
// cultivation (realm_perfection) or body (body_realm_perfection) perfection
// track for one realm_index. Upserts - the row may not exist yet if the
// player never started that realm's perfection quests, and forcing the GM
// to pre-create it first would defeat the purpose of a direct override.
func adminSetRealmPerfection(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	realmIndex, err := requiredInt(p, "realm_index")
	if err != nil {
		return nil, err
	}
	progress, err := requiredInt(p, "progress")
	if err != nil {
		return nil, err
	}
	track := strings.ToLower(stringField(p, "track"))
	var table string
	switch track {
	case "cultivation":
		table = "realm_perfection"
	case "body":
		table = "body_realm_perfection"
	default:
		return nil, errors.New(`track must be "cultivation" or "body"`)
	}
	if uid <= 0 || realmIndex < 0 || realmIndex > 31 {
		return nil, errors.New("invalid user_id or realm_index (0-31)")
	}
	progress = clamp(progress, 0, 100)

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	beforeRes, err := conn.Execute(`SELECT progress FROM `+table+` WHERE user_id=? AND realm_index=?`, []any{uid, realmIndex})
	if err != nil {
		return nil, err
	}
	before := int64(0)
	if r := firstRowMap(beforeRes); r != nil {
		before = storage.ParseInt(r["progress"])
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO `+table+`(user_id,realm_index,progress,updated_at) VALUES(?,?,?,?)
		ON CONFLICT(user_id,realm_index) DO UPDATE SET progress=excluded.progress,updated_at=excluded.updated_at`,
		[]any{uid, realmIndex, progress, now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_realm_perfection", fmt.Sprintf("user:%d", uid), map[string]any{"track": track, "realm_index": realmIndex, "progress": before}, map[string]any{"track": track, "realm_index": realmIndex, "progress": progress}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "track": track, "realm_index": realmIndex, "progress": progress}, nil
}

// adminSetSpiritualRoot directly edits grade/purity/mutation only - elements,
// stability, refinement_progress and compatibility are out of scope for this
// admin op and are left untouched (the UPDATE never mentions them, and the
// INSERT arm omits them from its column list so SQLite applies the table's
// normal DEFAULTs when creating a first-time row). Upserts: a legacy
// character predating this table has no row yet.
func adminSetSpiritualRoot(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	grade := stringField(p, "grade")
	validGrades := map[string]bool{"Mortal": true, "Common": true, "Refined": true, "Earth": true, "Heaven": true, "Immortal": true}
	if !validGrades[grade] {
		return nil, errors.New("grade must be one of Mortal, Common, Refined, Earth, Heaven, Immortal")
	}
	purity, err := requiredInt(p, "purity")
	if err != nil {
		return nil, err
	}
	mutation := stringField(p, "mutation")
	if mutation == "<nil>" {
		mutation = ""
	}
	if uid <= 0 || len(mutation) > 200 {
		return nil, errors.New("invalid user_id or mutation (max 200 chars)")
	}
	purity = clamp(purity, 0, 100)

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	beforeRes, err := conn.Execute(`SELECT grade,purity,mutation FROM character_spiritual_roots WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	var before map[string]any
	if r := firstRowMap(beforeRes); r != nil {
		before = map[string]any{"grade": r["grade"], "purity": storage.ParseInt(r["purity"]), "mutation": r["mutation"]}
	} else {
		before = map[string]any{"grade": nil}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO character_spiritual_roots(user_id,grade,purity,mutation,updated_at) VALUES(?,?,?,?,?)
		ON CONFLICT(user_id) DO UPDATE SET grade=excluded.grade,purity=excluded.purity,mutation=excluded.mutation,updated_at=excluded.updated_at`,
		[]any{uid, grade, purity, mutation, now}); err != nil {
		return nil, err
	}
	after := map[string]any{"grade": grade, "purity": purity, "mutation": mutation}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_spiritual_root", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "grade": grade, "purity": purity, "mutation": mutation}, nil
}

// adminSetBloodline edits purity/evolution_stage/progress on one existing
// character_bloodlines row, identified by (user_id, bloodline_id) - a
// character can hold several bloodlines. Requires the row to already exist
// (errors "bloodline not found for this character" otherwise) rather than
// upserting: name/affinity/state would need real content-pack values that a
// raw admin op has no safe way to invent. evolution_stage only floors at 0 -
// there is no fixed upper bound in Go (it depends on the content pack's
// evolutions[] length per bloodline_id), matching the same unclamped
// upper-bound risk profile as normal gameplay code (aptitude_actions.go).
func adminSetBloodline(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	bloodlineID := stringField(p, "bloodline_id")
	purity, err := requiredInt(p, "purity")
	if err != nil {
		return nil, err
	}
	evolutionStage, err := requiredInt(p, "evolution_stage")
	if err != nil {
		return nil, err
	}
	progress, err := requiredInt(p, "progress")
	if err != nil {
		return nil, err
	}
	if uid <= 0 || bloodlineID == "" || len(bloodlineID) > 80 {
		return nil, errors.New("invalid user_id or bloodline_id")
	}
	purity = clamp(purity, 0, 100)
	progress = clamp(progress, 0, 100)
	if evolutionStage < 0 {
		evolutionStage = 0
	}

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	beforeRes, err := conn.Execute(`SELECT name,purity,evolution_stage,progress FROM character_bloodlines WHERE user_id=? AND bloodline_id=?`, []any{uid, bloodlineID})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(beforeRes)
	if row == nil {
		return nil, errors.New("bloodline not found for this character")
	}
	before := map[string]any{"purity": storage.ParseInt(row["purity"]), "evolution_stage": storage.ParseInt(row["evolution_stage"]), "progress": storage.ParseInt(row["progress"])}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE character_bloodlines SET purity=?,evolution_stage=?,progress=?,updated_at=? WHERE user_id=? AND bloodline_id=?`,
		[]any{purity, evolutionStage, progress, now, uid, bloodlineID}); err != nil {
		return nil, err
	}
	after := map[string]any{"purity": purity, "evolution_stage": evolutionStage, "progress": progress}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_bloodline", fmt.Sprintf("user:%d bloodline:%s", uid, bloodlineID), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "bloodline_id": bloodlineID, "name": row["name"], "purity": purity, "evolution_stage": evolutionStage, "progress": progress}, nil
}

// adminSetPhysique edits evolution_stage/progress/stability on the
// character's single physique row (1:1 on user_id, always present - every
// character gets the ordinary_mortal_body default). Requires the row to
// exist; errors rather than upserting since that "should never happen" case
// most likely indicates corrupted state worth surfacing, not silently
// papering over. Same evolution_stage floor-at-0-only reasoning as bloodline.
func adminSetPhysique(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	evolutionStage, err := requiredInt(p, "evolution_stage")
	if err != nil {
		return nil, err
	}
	progress, err := requiredInt(p, "progress")
	if err != nil {
		return nil, err
	}
	stability, err := requiredInt(p, "stability")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	progress = clamp(progress, 0, 100)
	stability = clamp(stability, 0, 100)
	if evolutionStage < 0 {
		evolutionStage = 0
	}

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	beforeRes, err := conn.Execute(`SELECT name,evolution_stage,progress,stability FROM character_physiques WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(beforeRes)
	if row == nil {
		return nil, errors.New("physique not found for this character")
	}
	before := map[string]any{"evolution_stage": storage.ParseInt(row["evolution_stage"]), "progress": storage.ParseInt(row["progress"]), "stability": storage.ParseInt(row["stability"])}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE character_physiques SET evolution_stage=?,progress=?,stability=?,updated_at=? WHERE user_id=?`,
		[]any{evolutionStage, progress, stability, now, uid}); err != nil {
		return nil, err
	}
	after := map[string]any{"evolution_stage": evolutionStage, "progress": progress, "stability": stability}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_physique", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": row["name"], "evolution_stage": evolutionStage, "progress": progress, "stability": stability}, nil
}

// adminSetTribulation clears or resets a stuck tribulation gate.
// "clear" sets cleared=1 (unlocking the breakthrough past that gate),
// preparation=0, last_result='cleared' and deliberately leaves attempts
// alone (a pure historical counter). "reset" wipes the gate's state
// entirely (preparation=0, attempts=0, cleared=0, last_result=”) so the
// player can re-attempt clean. gate_realm_index is validated against the
// exact tribulationGates set (7/15/23) already defined in
// progression_actions.go. updated_game_minute is read from the canonical
// world clock via canonicalWorldGameMinute, the same helper every other
// gameplay write in this package uses for "now" in game-minute terms.
func adminSetTribulation(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	gateRealmIndex, err := requiredInt(p, "gate_realm_index")
	if err != nil {
		return nil, err
	}
	if _, ok := tribulationGates[gateRealmIndex]; !ok {
		return nil, errors.New("gate_realm_index must be one of the tribulation gates (7, 15, 23)")
	}
	mode := strings.ToLower(stringField(p, "mode"))
	if mode != "clear" && mode != "reset" {
		return nil, errors.New(`mode must be "clear" or "reset"`)
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
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	beforeRes, err := conn.Execute(`SELECT preparation,attempts,cleared,last_result FROM tribulation_state WHERE user_id=? AND gate_realm_index=?`, []any{uid, gateRealmIndex})
	if err != nil {
		return nil, err
	}
	var before map[string]any
	if r := firstRowMap(beforeRes); r != nil {
		before = map[string]any{"preparation": storage.ParseInt(r["preparation"]), "attempts": storage.ParseInt(r["attempts"]), "cleared": storage.ParseInt(r["cleared"]), "last_result": r["last_result"]}
	} else {
		before = map[string]any{"preparation": 0, "attempts": 0, "cleared": 0, "last_result": ""}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	var after map[string]any
	if mode == "clear" {
		if _, err = conn.Execute(`INSERT INTO tribulation_state(user_id,gate_realm_index,preparation,cleared,last_result,updated_game_minute,updated_at) VALUES(?,?,0,1,'cleared',?,?)
			ON CONFLICT(user_id,gate_realm_index) DO UPDATE SET preparation=0,cleared=1,last_result='cleared',updated_game_minute=excluded.updated_game_minute,updated_at=excluded.updated_at`,
			[]any{uid, gateRealmIndex, gameMinute, now}); err != nil {
			return nil, err
		}
		after = map[string]any{"preparation": 0, "cleared": 1, "last_result": "cleared"}
	} else {
		if _, err = conn.Execute(`INSERT INTO tribulation_state(user_id,gate_realm_index,preparation,attempts,cleared,last_result,updated_game_minute,updated_at) VALUES(?,?,0,0,0,'',?,?)
			ON CONFLICT(user_id,gate_realm_index) DO UPDATE SET preparation=0,attempts=0,cleared=0,last_result='',updated_game_minute=excluded.updated_game_minute,updated_at=excluded.updated_at`,
			[]any{uid, gateRealmIndex, gameMinute, now}); err != nil {
			return nil, err
		}
		after = map[string]any{"preparation": 0, "attempts": 0, "cleared": 0, "last_result": ""}
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_tribulation", fmt.Sprintf("user:%d gate:%d", uid, gateRealmIndex), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "gate_realm_index": gateRealmIndex, "mode": mode}, nil
}

// adminClearCondition resolves one or every active character_conditions row
// for a player, mirroring the exact resolve semantics progression_actions.go
// already uses when a condition clears through ordinary gameplay
// (cultivationCondition's "cleared" branch): state='resolved', severity
// zeroed, resolved_game_minute stamped from the canonical world clock. A
// condition_id targets one specific row (the dashboard lists real row IDs,
// so this is unambiguous even though a character can hold several active
// conditions with different condition_keys at once); clear_all:true instead
// resolves every currently-active row for the player in one transaction/one
// audit entry, for "the GM wants this character's slate wiped" rather than
// clearing debuffs one at a time.
func adminClearCondition(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	clearAll, _ := p["clear_all"].(bool)
	var conditionID int64
	if !clearAll {
		conditionID, err = requiredInt(p, "condition_id")
		if err != nil {
			return nil, err
		}
		if conditionID <= 0 {
			return nil, errors.New("invalid condition_id")
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
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9

	if clearAll {
		rows, err := conn.Execute(`SELECT condition_id,condition_key,name,severity FROM character_conditions WHERE user_id=? AND state='active'`, []any{uid})
		if err != nil {
			return nil, err
		}
		if len(rows.Rows) == 0 {
			return nil, errors.New("player has no active conditions to clear")
		}
		before := make([]map[string]any, 0, len(rows.Rows))
		for _, values := range rows.Rows {
			r := make(map[string]any, len(rows.Columns))
			for i, name := range rows.Columns {
				if i < len(values) {
					r[name] = values[i]
				}
			}
			before = append(before, map[string]any{"condition_id": storage.ParseInt(r["condition_id"]), "condition_key": r["condition_key"], "name": r["name"], "severity": storage.ParseInt(r["severity"])})
		}
		if _, err = conn.Execute(`UPDATE character_conditions SET state='resolved',severity=0,resolved_game_minute=?,updated_game_minute=?,updated_at=? WHERE user_id=? AND state='active'`,
			[]any{gameMinute, gameMinute, now, uid}); err != nil {
			return nil, err
		}
		after := map[string]any{"cleared_count": len(before)}
		if err := auditAdmin(conn, adminUserID, "admin.player.clear_condition", fmt.Sprintf("user:%d", uid), map[string]any{"active_conditions": before}, after, fmt.Sprint(p["reason"])); err != nil {
			return nil, err
		}
		if err := conn.Commit(); err != nil {
			return nil, err
		}
		return map[string]any{"user_id": uid, "name": charRow["name"], "cleared_count": len(before)}, nil
	}

	rowRes, err := conn.Execute(`SELECT condition_key,name,severity,state FROM character_conditions WHERE condition_id=? AND user_id=?`, []any{conditionID, uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(rowRes)
	if row == nil {
		return nil, errors.New("condition not found for this character")
	}
	if fmt.Sprint(row["state"]) != "active" {
		return nil, errors.New("condition is not active")
	}
	before := map[string]any{"condition_key": row["condition_key"], "name": row["name"], "severity": storage.ParseInt(row["severity"]), "state": "active"}
	if _, err = conn.Execute(`UPDATE character_conditions SET state='resolved',severity=0,resolved_game_minute=?,updated_game_minute=?,updated_at=? WHERE condition_id=?`,
		[]any{gameMinute, gameMinute, now, conditionID}); err != nil {
		return nil, err
	}
	after := map[string]any{"state": "resolved", "severity": 0}
	if err := auditAdmin(conn, adminUserID, "admin.player.clear_condition", fmt.Sprintf("user:%d condition:%d", uid, conditionID), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "condition_id": conditionID, "condition_key": row["condition_key"]}, nil
}

// adminForceReincarnationReady unsticks a soul waiting out
// reincarnation_state.reincarnation_ready_at - a real-world Unix-seconds wall
// clock gate checked by reincarnateAction (lifecycle_actions.go) with no
// bypass anywhere else in the codebase. A player who reincarnates at an
// inconvenient real-world hour (e.g. right before the GM has to log off) has
// no way to proceed until that wall-clock deadline passes; this lets a GM
// clear it early. This only touches reincarnation_ready_at on the player's
// single active reincarnation_state row - it deliberately does not edit any
// of the other six dynasty/samsara tables (soul_legacy,
// samsara_dynasty_history, samsara_ancestral_leads,
// samsara_investigation_quests, samsara_dynasty_claims,
// samsara_dynasty_conflicts), which stay read-only in the admin console.
func adminForceReincarnationReady(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	res, err := conn.Execute(`SELECT reincarnation_ready_at,previous_name FROM reincarnation_state WHERE user_id=? AND active=1`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("no active Samsara cycle is waiting for this soul")
	}
	before := map[string]any{"reincarnation_ready_at": row["reincarnation_ready_at"]}
	if _, err = conn.Execute(`UPDATE reincarnation_state SET reincarnation_ready_at=0 WHERE user_id=? AND active=1`, []any{uid}); err != nil {
		return nil, err
	}
	after := map[string]any{"reincarnation_ready_at": int64(0)}
	if err := auditAdmin(conn, adminUserID, "admin.player.force_reincarnation_ready", fmt.Sprintf("user:%d", uid), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "previous_name": row["previous_name"], "reincarnation_ready_at": int64(0)}, nil
}

// adminSetPillToxicity directly sets alchemy_state.pill_toxicity. This is the
// one crafting-adjacent table that reincarnateAction's wipe-on-death table
// list (lifecycle_actions.go) does NOT clear, so toxicity otherwise persists
// forever across lives with no other fix path - upserts since a character
// who has never crafted has no alchemy_state row yet.
func adminSetPillToxicity(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	toxicity, err := requiredInt(p, "pill_toxicity")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	toxicity = clamp(toxicity, 0, 1000)
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	beforeRes, err := conn.Execute(`SELECT pill_toxicity FROM alchemy_state WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	before := int64(0)
	if r := firstRowMap(beforeRes); r != nil {
		before = storage.ParseInt(r["pill_toxicity"])
	}
	if _, err = conn.Execute(`INSERT INTO alchemy_state(user_id,pill_toxicity,last_toxicity_game_minute,updated_at) VALUES(?,?,?,?)
		ON CONFLICT(user_id) DO UPDATE SET pill_toxicity=excluded.pill_toxicity,last_toxicity_game_minute=excluded.last_toxicity_game_minute,updated_at=excluded.updated_at`,
		[]any{uid, toxicity, gameMinute, now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_pill_toxicity", fmt.Sprintf("user:%d", uid), map[string]any{"pill_toxicity": before}, map[string]any{"pill_toxicity": toxicity}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "pill_toxicity": toxicity}, nil
}

// adminSetBeastStats lets a GM correct loyalty and/or evolution_stage on one
// spirit_beasts row after a bad tame/evolve interaction. Only the fields the
// caller actually supplies are changed. Deliberately narrower than a full
// column editor - name/species/rank/element/etc are left alone.
func adminSetBeastStats(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	beastID, err := requiredInt(p, "beast_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 || beastID <= 0 {
		return nil, errors.New("invalid user_id or beast_id")
	}
	_, hasLoyalty := p["loyalty"]
	_, hasEvolution := p["evolution_stage"]
	if !hasLoyalty && !hasEvolution {
		return nil, errors.New("loyalty or evolution_stage is required")
	}
	requestedLoyalty := clamp(storage.ParseInt(p["loyalty"]), 0, 100)
	requestedEvolution := storage.ParseInt(p["evolution_stage"])
	if requestedEvolution < 0 {
		requestedEvolution = 0
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT name,loyalty,evolution_stage FROM spirit_beasts WHERE user_id=? AND beast_id=?`, []any{uid, beastID})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("spirit beast not found for this character")
	}
	newLoyalty := storage.ParseInt(row["loyalty"])
	if hasLoyalty {
		newLoyalty = requestedLoyalty
	}
	newEvolution := storage.ParseInt(row["evolution_stage"])
	if hasEvolution {
		newEvolution = requestedEvolution
	}
	before := map[string]any{"loyalty": storage.ParseInt(row["loyalty"]), "evolution_stage": storage.ParseInt(row["evolution_stage"])}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE spirit_beasts SET loyalty=?,evolution_stage=?,updated_at=? WHERE user_id=? AND beast_id=?`,
		[]any{newLoyalty, newEvolution, now, uid, beastID}); err != nil {
		return nil, err
	}
	after := map[string]any{"loyalty": newLoyalty, "evolution_stage": newEvolution}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_beast_stats", fmt.Sprintf("user:%d beast:%d", uid, beastID), before, after, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "beast_id": beastID, "name": row["name"], "loyalty": newLoyalty, "evolution_stage": newEvolution}, nil
}

// adminRemoveEquipment force-deletes one equipment_instances row. The table's
// partial unique index (idx_equipment_one_slot, WHERE equipped=1) can leave a
// player stuck unable to re-equip a slot if state gets weird; this is the
// GM's fix-it tool for that.
func adminRemoveEquipment(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	equipmentID, err := requiredInt(p, "equipment_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 || equipmentID <= 0 {
		return nil, errors.New("invalid user_id or equipment_id")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT item_id,slot,equipped,quality FROM equipment_instances WHERE equipment_id=? AND user_id=?`, []any{equipmentID, uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("equipment instance not found for this character")
	}
	if _, err = conn.Execute(`DELETE FROM equipment_instances WHERE equipment_id=? AND user_id=?`, []any{equipmentID, uid}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.remove_equipment", fmt.Sprintf("user:%d equipment:%d", uid, equipmentID), row, map[string]any{}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "equipment_id": equipmentID, "removed": true}, nil
}

// adminSetAbodeAccess grants or revokes one cave_abode_access row. This is
// the one relational gap in cave-abode admin support - a player locked out
// of a friend's abode (or needing access revoked) has no other fix path;
// facility levels themselves are left alone since they're earned through
// normal abode.upgrade gameplay.
func adminSetAbodeAccess(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	ownerID, err := requiredInt(p, "owner_user_id")
	if err != nil {
		return nil, err
	}
	guestID, err := requiredInt(p, "guest_user_id")
	if err != nil {
		return nil, err
	}
	if ownerID <= 0 || guestID <= 0 {
		return nil, errors.New("invalid owner_user_id or guest_user_id")
	}
	revoke, _ := p["revoke"].(bool)
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	abodeRes, err := conn.Execute(`SELECT name FROM cave_abodes WHERE user_id=?`, []any{ownerID})
	if err != nil {
		return nil, err
	}
	if firstRowMap(abodeRes) == nil {
		return nil, errors.New("owner does not have a cave abode")
	}
	target := fmt.Sprintf("owner:%d guest:%d", ownerID, guestID)
	if revoke {
		res, err := conn.Execute(`DELETE FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?`, []any{ownerID, guestID})
		if err != nil {
			return nil, err
		}
		if err := auditAdmin(conn, adminUserID, "admin.player.set_abode_access", target, map[string]any{"access": "granted_or_absent"}, map[string]any{"access": "revoked", "rows_affected": res.RowsAffected}, fmt.Sprint(p["reason"])); err != nil {
			return nil, err
		}
		if err := conn.Commit(); err != nil {
			return nil, err
		}
		return map[string]any{"owner_user_id": ownerID, "guest_user_id": guestID, "access": "revoked"}, nil
	}
	accessRole := stringField(p, "access_role")
	if accessRole == "" || accessRole == "<nil>" {
		accessRole = "guest"
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO cave_abode_access(owner_user_id,guest_user_id,access_role,created_at) VALUES(?,?,?,?)
		ON CONFLICT(owner_user_id,guest_user_id) DO UPDATE SET access_role=excluded.access_role`, []any{ownerID, guestID, accessRole, now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_abode_access", target, map[string]any{"access": "none_or_prior"}, map[string]any{"access": "granted", "access_role": accessRole}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"owner_user_id": ownerID, "guest_user_id": guestID, "access_role": accessRole}, nil
}

// narrationSlots are the chain positions a GM may set from the dashboard. The
// engine stores them and audits the change; it deliberately does NOT validate
// the slug against OpenRouter's catalogue. Which model ids are real, and which
// are free, is Python's question (it holds the key and the catalogue) and the
// route audit's - Go's job here is durable state and an audit row.
var narrationSlots = []string{
	"routine_model",
	"routine_fallback_model",
	"epic_model",
	"epic_fallback_model",
	"dynamic_free_model",
	// v0.31.0: the ten-dollar switch, stored as "true"/"false" beside the
	// chain so one save and one audit row carry both.
	"credits_topped_up",
}

// adminNarrationSetChain persists the GM-chosen narration chain in world_state.
//
// It follows admin.automation.set exactly - one JSON blob under a world_state
// key, written in the same transaction as its audit row - so it needs no schema
// change. Narration is presentation, not canonical mechanics, so this is the
// rare engine action that stores a setting the engine itself never reads: the
// bot reads it back at startup and when the dashboard pokes it.
func adminNarrationSetChain(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	incoming, ok := p["slots"].(map[string]any)
	if !ok {
		return nil, errors.New("slots must be an object")
	}
	for key := range incoming {
		known := false
		for _, slot := range narrationSlots {
			if slot == key {
				known = true
				break
			}
		}
		if !known {
			return nil, fmt.Errorf("unknown narration slot: %s", key)
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
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key='narration_chain'`, nil)
	if err != nil {
		return nil, err
	}
	stored := map[string]any{}
	if row := firstRowMap(res); row != nil {
		if text, ok := row["value_json"].(string); ok {
			_ = json.Unmarshal([]byte(text), &stored)
		}
	}
	before := map[string]any{}
	for _, slot := range narrationSlots {
		if value, ok := stored[slot]; ok {
			before[slot] = value
		}
	}
	for key, value := range incoming {
		// An empty fallback slot is a real answer ("no named second hop"), so
		// it is stored as "" rather than dropped - otherwise clearing a slot
		// would read back as "never set" and fall through to the .env default.
		stored[key] = fmt.Sprint(value)
	}
	encoded, err := json.Marshal(stored)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES('narration_chain',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, []any{string(encoded), now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.narration.set_chain", "narration_chain", before, stored, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"slots": stored}, nil
}
