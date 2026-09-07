package game

// Commissions (v0.22.0, docs/COMMISSIONS_DESIGN.md).
//
// A commission is a quest a giver NPC offers in character. Three things about
// it are engine facts and live here, not in Python and never in a model: a
// player holds one at a time, the terms are fixed at accept, and the four
// outcomes move standing through one table.
//
// The one rule the outcome table exists to enforce: `failed` and `abandoned`
// are indistinguishable in consequence. Abandoning is allowed - it is kinder
// than making someone sit on work they do not want - but it must not be the
// cheap reroll, so it costs exactly what running out the clock costs.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
)

// The refusal cooldown after a failed or abandoned commission - an operator
// decision (docs/COMMISSIONS_DESIGN.md), in game time: 1440 minutes is one
// world-day. Deadlines are not here; each commission carries its own, per set
// of terms, because "how long do I have" is part of the offer.
const commissionCooldownMinutes = 2 * 1440

// What an outcome costs. Deliberately holds nothing that distinguishes one
// outcome from another for bookkeeping - see commissionCounters - so that
// `failed` and `abandoned` can be, and are asserted to be, the same value.
type commissionOutcomeRule struct {
	Trust           int64
	Respect         int64
	Grudge          int64
	CooldownMinutes int64
}

// The outcome table. failed and abandoned are identical here on purpose;
// commission_actions_test.go compares them as whole values, so an edit that
// makes abandoning cheaper fails the build rather than the playtest.
var commissionOutcomes = map[string]commissionOutcomeRule{
	"completed": {Trust: 6, Respect: 4, Grudge: 0, CooldownMinutes: 0},
	"failed":    {Trust: -5, Respect: -3, Grudge: 4, CooldownMinutes: commissionCooldownMinutes},
	"abandoned": {Trust: -5, Respect: -3, Grudge: 4, CooldownMinutes: commissionCooldownMinutes},
}

// Identical in consequence, distinguishable in record.
var commissionCounters = map[string]string{
	"completed": "commissions_completed",
	"failed":    "commissions_failed",
	"abandoned": "commissions_abandoned",
}

type commissionAcceptPayload struct {
	QuestKey     string `json:"quest_key"`
	VariantIndex int64  `json:"variant_index"`
	GameMinute   int64  `json:"game_minute"`
}

type commissionResolvePayload struct {
	QuestKey    string `json:"quest_key"`
	Outcome     string `json:"outcome"`
	AdminRetire bool   `json:"admin_retire"`
	GameMinute  int64  `json:"game_minute"`
}

type commissionTerms struct {
	Label           string
	Rewards         map[string]any
	DeadlineMinutes int64
}

// commissionDefinition is the stored row reduced to what the engine decides
// with. Everything else on quest_definitions is presentation.
type commissionDefinition struct {
	QuestKey      string
	Title         string
	GiverNPC      string
	Tier          int64
	OwnerUserID   *int64
	RequiresSect  string
	RewardsHidden bool
	Variants      []commissionTerms
	Base          commissionTerms
}

func decodeCommissionTerms(raw string, fallbackDeadline int64) []commissionTerms {
	var list []map[string]any
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	if err := json.Unmarshal([]byte(raw), &list); err != nil {
		return nil
	}
	out := make([]commissionTerms, 0, len(list))
	for _, item := range list {
		terms := commissionTerms{Label: fmt.Sprint(item["label"]), DeadlineMinutes: fallbackDeadline}
		if rewards, ok := item["rewards"].(map[string]any); ok {
			terms.Rewards = rewards
		}
		if v, ok := item["deadline_game_minutes"]; ok {
			terms.DeadlineMinutes = i64(v)
		}
		out = append(out, terms)
	}
	return out
}

// loadCommissionDefinition returns the row for a commission, and says whether
// a definition exists at all.
//
// Three outcomes, and the middle one is why `found` exists (v0.23.1). A row
// with a giver is a commission. A row without one is an ordinary quest, which
// accepts through the same action with no deadline, no terms to lock and no
// standing to move. No row at all is not a quest, and used to be indistinguish-
// able from the second case - so the engine accepted any string as an ordinary
// quest and wrote a `character_quests` row for it. Python happened to validate
// the key before asking, which is not the same as the engine being right.
//
// Static quests (app/rules/quests.py) are seeded into quest_definitions at
// startup precisely so they land in the second case rather than the third.
func loadCommissionDefinition(conn *storage.Conn, questKey string) (*commissionDefinition, bool, error) {
	res, err := conn.Execute(`SELECT quest_key,title,giver_npc,tier,owner_user_id,status,rewards_json,variants_json,deadline_game_minutes,
		COALESCE(requires_sect,'') AS requires_sect,COALESCE(reward_visibility,'shown') AS reward_visibility
		FROM quest_definitions WHERE quest_key=?`, []any{questKey})
	if err != nil {
		return nil, false, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, false, nil
	}
	giver := strings.TrimSpace(fmt.Sprint(row["giver_npc"]))
	if giver == "" {
		return nil, true, nil
	}
	if fmt.Sprint(row["status"]) != "approved" {
		return nil, true, errors.New("that commission is not open for acceptance")
	}
	def := &commissionDefinition{
		QuestKey:      fmt.Sprint(row["quest_key"]),
		Title:         fmt.Sprint(row["title"]),
		GiverNPC:      giver,
		Tier:          i64(row["tier"]),
		RequiresSect:  strings.TrimSpace(fmt.Sprint(row["requires_sect"])),
		RewardsHidden: fmt.Sprint(row["reward_visibility"]) == "hidden",
	}
	if raw, ok := row["owner_user_id"]; ok && raw != nil {
		owner := i64(raw)
		def.OwnerUserID = &owner
	}
	deadline := i64(row["deadline_game_minutes"])
	base := commissionTerms{Label: "standard", DeadlineMinutes: deadline}
	if text, ok := row["rewards_json"].(string); ok && text != "" {
		_ = json.Unmarshal([]byte(text), &base.Rewards)
	}
	def.Base = base
	if text, ok := row["variants_json"].(string); ok {
		def.Variants = decodeCommissionTerms(text, deadline)
	}
	return def, true, nil
}

func (d *commissionDefinition) terms(index int64) (commissionTerms, error) {
	if len(d.Variants) == 0 {
		if index != 0 {
			return commissionTerms{}, errors.New("this commission has only the terms offered")
		}
		return d.Base, nil
	}
	if index < 0 || index >= int64(len(d.Variants)) {
		return commissionTerms{}, errors.New("unknown terms for this commission")
	}
	return d.Variants[index], nil
}

// heldCommission returns the quest_key of the player's active commission, or
// "" when the slot is free. This is the primary throttle in the whole design.
func heldCommission(conn *storage.Conn, userID int64) (string, error) {
	res, err := conn.Execute(`SELECT quest_key FROM character_quests WHERE user_id=? AND commission=1 AND status='active' LIMIT 1`, []any{userID})
	if err != nil {
		return "", err
	}
	if row := firstRowMap(res); row != nil {
		return fmt.Sprint(row["quest_key"]), nil
	}
	return "", nil
}

func commissionCooldownRemaining(conn *storage.Conn, userID int64, npcName string, gameMinute int64) (int64, error) {
	res, err := conn.Execute(`SELECT commission_cooldown_until_game_minute FROM npc_relationships WHERE user_id=? AND npc_name=?`, []any{userID, npcName})
	if err != nil {
		return 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, nil
	}
	return max64(0, i64(row["commission_cooldown_until_game_minute"])-gameMinute), nil
}

func commissionAcceptAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p commissionAcceptPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if strings.TrimSpace(p.QuestKey) == "" {
		return authoritativeMutation{}, errors.New("quest_key is required")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot take on work")
	}
	def, known, err := loadCommissionDefinition(conn, p.QuestKey)
	if err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(`SELECT status FROM character_quests WHERE user_id=? AND quest_key=?`, []any{userID, p.QuestKey})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row := firstRowMap(res); row != nil {
		return authoritativeMutation{}, errors.New("you have taken that quest before")
	}
	now := nowSeconds()
	if !known {
		// Not a quest at all. The engine is the authority on what exists, so
		// it refuses here rather than trusting that whoever called it checked.
		return authoritativeMutation{}, fmt.Errorf("unknown quest: %s", p.QuestKey)
	}
	if def == nil {
		// An ordinary quest: no giver, no deadline, no terms, nothing to
		// refuse it for. It does not occupy the commission slot.
		if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at)
			VALUES(?,?,'active','{}',?,0,0,?,?)`, []any{userID, p.QuestKey, p.GameMinute, now, now}); err != nil {
			return authoritativeMutation{}, err
		}
		out := map[string]any{"quest_key": p.QuestKey, "commission": false, "status": "active", "accepted_game_minute": p.GameMinute}
		return authoritativeMutation{Result: out, Event: eventledger.Event{
			Domain: "quest", EventType: "quest.accept", EntityType: "character",
			EntityID: fmt.Sprint(userID), SubjectType: "quest", SubjectID: p.QuestKey,
			GameMinute: p.GameMinute, Payload: out,
		}}, nil
	}
	if def.OwnerUserID != nil && *def.OwnerUserID != userID {
		return authoritativeMutation{}, errors.New("unknown commission")
	}
	// Sect business is checked here and not only by the offer ladder: the
	// ladder decides what a giver raises, this decides what may be taken on.
	if def.RequiresSect != "" {
		member, sectErr := sectMembershipRow(conn, userID)
		if sectErr != nil {
			return authoritativeMutation{}, sectErr
		}
		if member == nil || !strings.EqualFold(strings.TrimSpace(fmt.Sprint(member["sect_name"])), def.RequiresSect) {
			return authoritativeMutation{}, fmt.Errorf("that work is for disciples of the %s", def.RequiresSect)
		}
	}
	held, err := heldCommission(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if held != "" {
		return authoritativeMutation{}, errors.New("you already hold a commission; finish, fail or abandon it first")
	}
	remaining, err := commissionCooldownRemaining(conn, userID, def.GiverNPC, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("commission cooldown remaining: %d", remaining*60)
	}
	terms, err := def.terms(p.VariantIndex)
	if err != nil {
		return authoritativeMutation{}, err
	}
	deadlineMinutes := terms.DeadlineMinutes
	if deadlineMinutes < 0 {
		deadlineMinutes = 0
	}
	var deadline any
	if deadlineMinutes > 0 {
		deadline = p.GameMinute + deadlineMinutes
	}
	if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,deadline_game_minute,variant_index,created_at,updated_at)
		VALUES(?,?,'active','{}',?,1,?,?,?,?)`, []any{userID, def.QuestKey, p.GameMinute, deadline, p.VariantIndex, now, now}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"quest_key": def.QuestKey, "title": def.Title, "giver_npc": def.GiverNPC, "tier": def.Tier,
		"commission": true, "variant_index": p.VariantIndex, "variant_label": terms.Label, "rewards": terms.Rewards,
		"rewards_hidden": def.RewardsHidden, "requires_sect": def.RequiresSect,
		"accepted_game_minute": p.GameMinute, "deadline_game_minute": deadline,
		"deadline_game_minutes": deadlineMinutes, "status": "active",
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{
		Domain: "commission", EventType: "commission.accept", EntityType: "character",
		EntityID: fmt.Sprint(userID), SubjectType: "quest", SubjectID: def.QuestKey,
		GameMinute: p.GameMinute, Payload: out,
	}}, nil
}

// applyCommissionStandingTx moves the giver relationship by the outcome table
// and returns the row it wrote. `pay` false is the GM retire path: the record
// still says abandoned, the player pays nothing.
func applyCommissionStandingTx(conn *storage.Conn, userID int64, npcName, outcome string, gameMinute int64, charge bool) (map[string]any, error) {
	rule, ok := commissionOutcomes[outcome]
	if !ok {
		return nil, fmt.Errorf("unknown commission outcome: %s", outcome)
	}
	res, err := conn.Execute(`SELECT trust,respect,grudge,encounter_count,commissions_completed,commissions_failed,commissions_abandoned FROM npc_relationships WHERE user_id=? AND npc_name=?`, []any{userID, npcName})
	if err != nil {
		return nil, err
	}
	current := map[string]int64{"trust": 0, "respect": 0, "grudge": 0, "encounter_count": 0,
		"commissions_completed": 0, "commissions_failed": 0, "commissions_abandoned": 0}
	exists := false
	if row := firstRowMap(res); row != nil {
		exists = true
		for k := range current {
			current[k] = i64(row[k])
		}
	}
	trust, respect, grudge := current["trust"], current["respect"], current["grudge"]
	cooldownUntil := int64(0)
	if charge {
		trust = clamp(trust+rule.Trust, -100, 100)
		respect = clamp(respect+rule.Respect, -100, 100)
		grudge = clamp(grudge+rule.Grudge, -100, 100)
		if rule.CooldownMinutes > 0 {
			cooldownUntil = gameMinute + rule.CooldownMinutes
		}
	}
	counters := map[string]int64{
		"commissions_completed": current["commissions_completed"],
		"commissions_failed":    current["commissions_failed"],
		"commissions_abandoned": current["commissions_abandoned"],
	}
	counter := commissionCounters[outcome]
	counters[counter] = counters[counter] + 1
	now := nowSeconds()
	if exists {
		if _, err := conn.Execute(`UPDATE npc_relationships SET trust=?,respect=?,grudge=?,
			commission_cooldown_until_game_minute=?,commissions_completed=?,commissions_failed=?,commissions_abandoned=?,
			last_commission_outcome=?,updated_at=? WHERE user_id=? AND npc_name=?`,
			[]any{trust, respect, grudge, cooldownUntil, counters["commissions_completed"], counters["commissions_failed"],
				counters["commissions_abandoned"], outcome, now, userID, npcName}); err != nil {
			return nil, err
		}
	} else {
		if _, err := conn.Execute(`INSERT INTO npc_relationships(user_id,npc_name,trust,respect,fear,affection,debt,grudge,encounter_count,last_summary,updated_at,
			commission_cooldown_until_game_minute,commissions_completed,commissions_failed,commissions_abandoned,last_commission_outcome)
			VALUES(?,?,?,?,0,0,0,?,0,'',?,?,?,?,?,?)`,
			[]any{userID, npcName, trust, respect, grudge, now, cooldownUntil, counters["commissions_completed"],
				counters["commissions_failed"], counters["commissions_abandoned"], outcome}); err != nil {
			return nil, err
		}
	}
	return map[string]any{
		"npc_name": npcName, "trust": trust, "respect": respect, "grudge": grudge,
		"standing": trust + respect - grudge, "outcome": outcome,
		"cooldown_until_game_minute": cooldownUntil,
		"cooldown_game_minutes":      max64(0, cooldownUntil-gameMinute),
		"commissions_completed":      counters["commissions_completed"],
		"commissions_failed":         counters["commissions_failed"],
		"commissions_abandoned":      counters["commissions_abandoned"],
	}, nil
}

// payCommissionRewardTx grants the terms locked at accept. It is the same set
// of writes cultivation.reward makes, done inside the caller's transaction so
// paying and resolving cannot come apart.
func payCommissionRewardTx(conn *storage.Conn, userID int64, questKey string, rewards map[string]any) (map[string]any, error) {
	stones := i64(rewards["spirit_stones"])
	insight := i64(rewards["insight_xp"])
	items := map[string]int64{}
	if raw, ok := rewards["items"].(map[string]any); ok {
		for id, qty := range raw {
			if n := i64(qty); n > 0 {
				items[id] = n
			}
		}
	}
	if stones == 0 && insight == 0 && len(items) == 0 {
		return map[string]any{}, nil
	}
	now := nowSeconds()
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
		[]any{userID, "commission_reward:" + questKey, string(payload), now}); err != nil {
		return nil, err
	}
	return granted, nil
}

// resolveCommissionTx is the single place a commission leaves `active`. Every
// producer of an outcome - the player abandoning, quest.progress completing it,
// the tick expiring it, a GM retiring it - lands here.
func resolveCommissionTx(conn *storage.Conn, userID int64, questKey, outcome string, gameMinute int64, charge bool) (map[string]any, error) {
	if _, ok := commissionOutcomes[outcome]; !ok {
		return nil, fmt.Errorf("unknown commission outcome: %s", outcome)
	}
	res, err := conn.Execute(`SELECT variant_index,deadline_game_minute FROM character_quests WHERE user_id=? AND quest_key=? AND status='active' AND commission=1`, []any{userID, questKey})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("no active commission by that name")
	}
	variantIndex := i64(row["variant_index"])
	def, err := loadCommissionDefinitionForResolve(conn, questKey)
	if err != nil {
		return nil, err
	}
	now := nowSeconds()
	if _, err := conn.Execute(`UPDATE character_quests SET status=?,resolved_game_minute=?,updated_at=? WHERE user_id=? AND quest_key=?`,
		[]any{outcome, gameMinute, now, userID, questKey}); err != nil {
		return nil, err
	}
	granted := map[string]any{}
	if outcome == "completed" && charge {
		terms, termsErr := def.terms(variantIndex)
		if termsErr != nil {
			terms = def.Base
		}
		granted, err = payCommissionRewardTx(conn, userID, questKey, terms.Rewards)
		if err != nil {
			return nil, err
		}
	}
	standing, err := applyCommissionStandingTx(conn, userID, def.GiverNPC, outcome, gameMinute, charge)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"quest_key": questKey, "title": def.Title, "giver_npc": def.GiverNPC, "outcome": outcome,
		"variant_index": variantIndex, "rewards_granted": granted, "standing": standing,
		"resolved_game_minute": gameMinute, "charged": charge,
	}, nil
}

// A resolve must work even for a definition a GM has since retired - the
// player's held row is the contract, not the catalog entry.
func loadCommissionDefinitionForResolve(conn *storage.Conn, questKey string) (*commissionDefinition, error) {
	res, err := conn.Execute(`SELECT quest_key,title,giver_npc,tier,owner_user_id,rewards_json,variants_json,deadline_game_minutes,
		COALESCE(reward_visibility,'shown') AS reward_visibility
		FROM quest_definitions WHERE quest_key=?`, []any{questKey})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("unknown commission")
	}
	giver := strings.TrimSpace(fmt.Sprint(row["giver_npc"]))
	if giver == "" {
		return nil, errors.New("that quest is not a commission")
	}
	def := &commissionDefinition{
		QuestKey: fmt.Sprint(row["quest_key"]), Title: fmt.Sprint(row["title"]),
		GiverNPC: giver, Tier: i64(row["tier"]),
		RewardsHidden: fmt.Sprint(row["reward_visibility"]) == "hidden",
	}
	deadline := i64(row["deadline_game_minutes"])
	base := commissionTerms{Label: "standard", DeadlineMinutes: deadline}
	if text, ok := row["rewards_json"].(string); ok && text != "" {
		_ = json.Unmarshal([]byte(text), &base.Rewards)
	}
	def.Base = base
	if text, ok := row["variants_json"].(string); ok {
		def.Variants = decodeCommissionTerms(text, deadline)
	}
	return def, nil
}

func commissionResolveAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p commissionResolvePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	outcome := strings.TrimSpace(strings.ToLower(p.Outcome))
	if _, ok := commissionOutcomes[outcome]; !ok {
		return authoritativeMutation{}, errors.New("outcome must be completed, failed or abandoned")
	}
	questKey := strings.TrimSpace(p.QuestKey)
	if questKey == "" {
		var err error
		questKey, err = heldCommission(conn, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if questKey == "" {
			return authoritativeMutation{}, errors.New("you hold no commission")
		}
	}
	// A GM retire clears the slot without charging the player; the record
	// still reads abandoned so the history is honest about what happened.
	charge := !p.AdminRetire
	if p.AdminRetire {
		outcome = "abandoned"
	}
	out, err := resolveCommissionTx(conn, userID, questKey, outcome, p.GameMinute, charge)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out["admin_retire"] = p.AdminRetire
	return authoritativeMutation{Result: out, Event: eventledger.Event{
		Domain: "commission", EventType: "commission.resolve", EntityType: "character",
		EntityID: fmt.Sprint(userID), SubjectType: "quest", SubjectID: questKey,
		GameMinute: p.GameMinute, Payload: out,
	}}, nil
}

// DueCommission is one past-deadline commission the tick is about to fail.
type DueCommission struct {
	UserID   int64
	QuestKey string
}

// ExpireDueCommissions fails every active commission whose deadline has
// passed. It is the only path that makes `failed` exist without anyone
// pressing anything, and it runs in the engine so a restart cannot lose a
// deadline. The caller owns the transaction.
func ExpireDueCommissions(conn *storage.Conn, gameMinute int64) ([]DueCommission, error) {
	res, err := conn.Execute(`SELECT user_id,quest_key FROM character_quests
		WHERE status='active' AND commission=1 AND deadline_game_minute IS NOT NULL AND deadline_game_minute<=?
		ORDER BY user_id, quest_key`, []any{gameMinute})
	if err != nil {
		return nil, err
	}
	var due []DueCommission
	for _, row := range rowsToMaps(res) {
		due = append(due, DueCommission{UserID: i64(row["user_id"]), QuestKey: fmt.Sprint(row["quest_key"])})
	}
	expired := make([]DueCommission, 0, len(due))
	for _, item := range due {
		if _, err := resolveCommissionTx(conn, item.UserID, item.QuestKey, "failed", gameMinute, true); err != nil {
			return nil, err
		}
		expired = append(expired, item)
	}
	return expired, nil
}

// ---------------------------------------------------------------------------
// GM actions (v0.22.0). Both audit; neither is authoritative, because neither
// is a player's own move - the dashboard runs them as the GM.
// ---------------------------------------------------------------------------

// adminCommissionReview approves, retires or discards a commission definition.
// Retiring hides it from new takers; the players already holding it keep the
// terms they accepted, which is why this touches only quest_definitions.
func adminCommissionReview(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	questKey := stringField(p, "quest_key")
	status := strings.TrimSpace(strings.ToLower(fmt.Sprint(p["status"])))
	if questKey == "" {
		return nil, errors.New("quest_key is required")
	}
	switch status {
	case "approved", "retired", "discarded", "draft":
	default:
		return nil, errors.New("status must be draft, approved, retired or discarded")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT status,title,giver_npc FROM quest_definitions WHERE quest_key=?`, []any{questKey})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("unknown commission")
	}
	before := fmt.Sprint(row["status"])
	now := nowSeconds()
	if _, err := conn.Execute(`UPDATE quest_definitions SET status=?,reviewed_by=?,reviewed_at=?,updated_at=? WHERE quest_key=?`,
		[]any{status, adminUserID, now, now, questKey}); err != nil {
		return nil, err
	}
	out := map[string]any{"quest_key": questKey, "title": fmt.Sprint(row["title"]),
		"giver_npc": fmt.Sprint(row["giver_npc"]), "status": status, "previous_status": before}
	if err := auditAdmin(conn, adminUserID, "admin.commission.review", questKey,
		map[string]any{"status": before}, out, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}

// adminCommissionRetire clears a player's held commission without charging
// them. There is no override that hands out a second commission: the only way
// to free the slot is to end the one they have, and ending it as a GM costs
// the player nothing.
func adminCommissionRetire(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
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
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	questKey := stringField(p, "quest_key")
	if questKey == "" || questKey == "<nil>" {
		questKey, err = heldCommission(conn, uid)
		if err != nil {
			return nil, err
		}
		if questKey == "" {
			return nil, errors.New("that player holds no commission")
		}
	}
	out, err := resolveCommissionTx(conn, uid, questKey, "abandoned", gameMinute, false)
	if err != nil {
		return nil, err
	}
	out["admin_retire"] = true
	if err := auditAdmin(conn, adminUserID, "admin.commission.retire", fmt.Sprint(uid),
		map[string]any{"quest_key": questKey}, out, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return out, nil
}
