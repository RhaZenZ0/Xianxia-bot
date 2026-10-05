package game

// A sect's own people as masters, and a rank granted by somebody (v1.25.0).
//
// Before this a master could only be another player of the sect - and on a
// small server there was usually nobody stronger to ask - while a rank rose by
// itself the moment lifetime contribution crossed a rung. On the owner's calls:
//
//   - A member may ask one of their own sect's people to be their master
//     (`discipleship.npc_request`): somebody of the sect, living, standing where
//     the member stands, of a higher stage and at least the content's rank. No
//     roll - an elder who meets the bar takes the disciple - but a master keeps
//     only so many players at once.
//   - A master gives three things, read off `sect_system.npc_master`: a term on
//     the breakthrough roll (in the odds and the roll alike, through
//     `breakthroughModifier`), insight on a qi realm crossing, and a term on
//     cultivation that a retreat carries too. And once per life, from a rank,
//     the sect's next manual (`sect.master.teach`).
//   - Contribution only makes a member eligible. A rank is granted by asking
//     (`sect.promote`): your master, or any of the sect's people at the
//     promoter rank, standing with you, whose rank is above the rung asked for.
//
// `sect_lineage` cannot hold an NPC - both its ids are foreign-keyed to
// `characters` - so the bond is `npc_mentorships` (schema 79), one row per
// disciple. A player holds a player master or an NPC master, never both.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const sectMasterTeachEvent = "sect_master_teach"

// npcMasterRule is `sect_system.npc_master`. An absent key gives nothing: no
// bonus, no gift, no gate - a rule nobody authored is not one the engine
// invents a number for.
type npcMasterRule struct {
	MinRankLevel      int64
	MaxDisciples      int64
	BreakthroughBonus int64
	InsightOnRealm    int64
	CultivationMult   float64
	TeachRankLevel    int64
	PromoterRankLevel int64
}

func npcMasterRuleGo(c worlddata.Catalog) npcMasterRule {
	raw, _ := c.SectSystem["npc_master"].(map[string]any)
	rule := npcMasterRule{
		MinRankLevel:      maxI64(0, i64(raw["min_rank_level"])),
		MaxDisciples:      maxI64(0, i64(raw["max_disciples"])),
		BreakthroughBonus: maxI64(0, i64(raw["breakthrough_bonus"])),
		InsightOnRealm:    maxI64(0, i64(raw["insight_on_realm"])),
		CultivationMult:   1,
		TeachRankLevel:    maxI64(0, i64(raw["teach_rank_level"])),
		PromoterRankLevel: maxI64(0, i64(raw["promoter_rank_level"])),
	}
	if v, ok := raw["cultivation_mult"].(float64); ok && v > 0 {
		rule.CultivationMult = v
	}
	return rule
}

// sectRankLevelByName is the content's level for a rank name (the inverse of
// sectRankName); 0 for a name the ladder does not carry.
func sectRankLevelByName(c worlddata.Catalog, name string) int64 {
	ranks, _ := c.SectSystem["ranks"].([]any)
	for _, v := range ranks {
		row, _ := v.(map[string]any)
		if row != nil && strings.EqualFold(strings.TrimSpace(fmt.Sprint(row["name"])), strings.TrimSpace(name)) {
			return i64(row["level"])
		}
	}
	return 0
}

// sectNPC is one of a sect's people as the world holds them now.
type sectNPC struct {
	Name      string
	Sect      string
	Rank      string
	RankLevel int64
	Power     int64
	Alive     bool
}

func sectNPCTx(conn *storage.Conn, catalog worlddata.Catalog, name string) (sectNPC, bool, error) {
	name = strings.TrimSpace(name)
	if name == "" || !tableExistsTx(conn, "npc_civilization_state") || !tableExistsTx(conn, "npc_life_state") {
		return sectNPC{}, false, nil
	}
	r, err := conn.Execute(`SELECT c.faction,c.status,c.realm_index,c.phase,COALESCE(l.sect_rank,'') FROM npc_civilization_state c
        LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name WHERE c.npc_name=?`, []any{name})
	if err != nil {
		return sectNPC{}, false, err
	}
	if len(r.Rows) == 0 {
		return sectNPC{}, false, nil
	}
	row := r.Rows[0]
	rank := fmt.Sprint(row[4])
	return sectNPC{
		Name: name, Sect: fmt.Sprint(row[0]), Rank: rank, RankLevel: sectRankLevelByName(catalog, rank),
		Power: i64(row[2])*10 + i64(row[3]), Alive: fmt.Sprint(row[1]) == "alive",
	}, true, nil
}

// npcMasterOfTx is the name of the NPC who is this player's master, or "".
func npcMasterOfTx(conn *storage.Conn, userID int64) string {
	if !tableExistsTx(conn, "npc_mentorships") {
		return ""
	}
	r, err := conn.Execute(`SELECT master_npc_name FROM npc_mentorships WHERE disciple_user_id=?`, []any{userID})
	if err != nil || len(r.Rows) == 0 {
		return ""
	}
	return fmt.Sprint(r.Rows[0][0])
}

// livingNPCMasterTx is the player's master if they are still alive; a bond
// with the dead gives nothing, whatever a lagging cleanup has left behind.
func livingNPCMasterTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) string {
	name := npcMasterOfTx(conn, userID)
	if name == "" {
		return ""
	}
	npc, ok, err := sectNPCTx(conn, catalog, name)
	if err != nil || !ok || !npc.Alive {
		return ""
	}
	return name
}

// npcMasterBreakthroughBonusTx is the master's term on a breakthrough roll,
// with the master's name for the odds card.
func npcMasterBreakthroughBonusTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (int64, string) {
	rule := npcMasterRuleGo(catalog)
	if rule.BreakthroughBonus <= 0 {
		return 0, ""
	}
	name := livingNPCMasterTx(conn, catalog, userID)
	if name == "" {
		return 0, ""
	}
	return rule.BreakthroughBonus, name
}

// npcMasterCultivationMultTx is the master's term on cultivation; 1 without
// one. A retreat carries it (`loadSeclusionCarried`), because a bond holds for
// the retreat's whole length.
func npcMasterCultivationMultTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (float64, string) {
	rule := npcMasterRuleGo(catalog)
	if rule.CultivationMult == 1 {
		return 1, ""
	}
	name := livingNPCMasterTx(conn, catalog, userID)
	if name == "" {
		return 1, ""
	}
	return rule.CultivationMult, name
}

// npcMasterRealmInsightTx pays the disciple insight on a qi realm crossing
// while their master lives, through the one insight door.
func npcMasterRealmInsightTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, now float64) (map[string]any, error) {
	rule := npcMasterRuleGo(catalog)
	if rule.InsightOnRealm <= 0 {
		return nil, nil
	}
	name := livingNPCMasterTx(conn, catalog, userID)
	if name == "" {
		return nil, nil
	}
	got, err := grantInsightXPTx(conn, userID, rule.InsightOnRealm, now)
	if err != nil {
		return nil, err
	}
	return map[string]any{"master_npc_name": name, "insight_xp": got}, nil
}

// standingWithTx refuses unless the NPC stands where the player stands, in the
// sentence a player can act on.
func standingWithTx(conn *storage.Conn, catalog worlddata.Catalog, name, here string, gameMinute int64) error {
	where, err := npcWhereaboutsTx(conn, catalog, name, gameMinute, nowSeconds())
	if err != nil {
		return err
	}
	if where.Dead {
		return fmt.Errorf("%s is dead", name)
	}
	if !where.Known || where.Location != here {
		return fmt.Errorf("%s is not here; you must stand where they stand to ask", name)
	}
	return nil
}

type npcMasterRequestPayload struct {
	NPCName    string `json:"npc_name"`
	GameMinute int64  `json:"game_minute"`
}

func npcMasterRequestAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p npcMasterRequestPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.NPCName = strings.TrimSpace(p.NPCName)
	if !tableExistsTx(conn, "npc_mentorships") {
		return authoritativeMutation{}, errors.New("the sect's people cannot take disciples until the database is migrated")
	}
	mem, err := sectMembershipRow(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("only a sect member may ask one of the sect's people to be their master")
	}
	sect := fmt.Sprint(mem["sect_name"])
	if r, _ := conn.Execute(`SELECT 1 FROM sect_lineage WHERE disciple_user_id=?`, []any{userID}); len(r.Rows) > 0 {
		return authoritativeMutation{}, errors.New("you already have a recorded master")
	}
	if npcMasterOfTx(conn, userID) != "" {
		return authoritativeMutation{}, errors.New("you already have a recorded master")
	}
	npc, ok, err := sectNPCTx(conn, catalog, p.NPCName)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !ok || !strings.EqualFold(npc.Sect, sect) {
		return authoritativeMutation{}, fmt.Errorf("%s is not one of the %s's people", firstNonempty(p.NPCName, "that person"), sect)
	}
	if !npc.Alive {
		return authoritativeMutation{}, fmt.Errorf("%s is dead", npc.Name)
	}
	rule := npcMasterRuleGo(catalog)
	if rule.MinRankLevel > 0 && npc.RankLevel < rule.MinRankLevel {
		return authoritativeMutation{}, fmt.Errorf("a master is a %s or above; %s is a %s", sectRankName(catalog, rule.MinRankLevel), npc.Name, firstNonempty(npc.Rank, "member"))
	}
	c, err := loadMechanicsCharacter(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if npc.Power <= c.RealmIndex*10+c.Phase {
		return authoritativeMutation{}, fmt.Errorf("%s does not stand above you in cultivation", npc.Name)
	}
	if err := standingWithTx(conn, catalog, npc.Name, c.Location, p.GameMinute); err != nil {
		return authoritativeMutation{}, err
	}
	if rule.MaxDisciples > 0 {
		r, err := conn.Execute(`SELECT COUNT(*) FROM npc_mentorships WHERE master_npc_name=?`, []any{npc.Name})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if len(r.Rows) > 0 && i64(r.Rows[0][0]) >= rule.MaxDisciples {
			return authoritativeMutation{}, fmt.Errorf("%s already keeps %d disciples and will take no more", npc.Name, rule.MaxDisciples)
		}
	}
	now := nowSeconds()
	_, _ = conn.Execute(`UPDATE disciple_requests SET status='superseded',resolved_at=? WHERE disciple_user_id=? AND status='pending'`, []any{now, userID})
	if _, err := conn.Execute(`INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,accepted_game_minute,attention,created_at) VALUES(?,?,?,?,0,?)`,
		[]any{userID, npc.Name, sect, p.GameMinute, now}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"master_npc_name": npc.Name, "master_rank": npc.Rank, "sect_name": sect, "status": "accepted",
		"breakthrough_bonus": rule.BreakthroughBonus, "insight_on_realm": rule.InsightOnRealm,
		"cultivation_mult": rule.CultivationMult, "teach_rank": sectRankName(catalog, rule.TeachRankLevel),
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "discipleship.npc_request", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// severNPCMasterTx ends the player's bond with an NPC master; "" when there
// was none. `discipleship.leave` asks it when the player has no player master.
func severNPCMasterTx(conn *storage.Conn, userID int64) (string, error) {
	name := npcMasterOfTx(conn, userID)
	if name == "" {
		return "", nil
	}
	_, err := conn.Execute(`DELETE FROM npc_mentorships WHERE disciple_user_id=?`, []any{userID})
	return name, err
}

type sectMasterTeachPayload struct {
	GameMinute int64 `json:"game_minute"`
}

type sectMasterTeachRecord struct {
	Life     int64  `json:"life"`
	Master   string `json:"master"`
	ManualID string `json:"manual_id"`
}

func sectMasterTaughtThisLifeTx(conn *storage.Conn, userID, life int64) (bool, error) {
	if !tableExistsTx(conn, "event_log") {
		return false, nil
	}
	r, err := conn.Execute(`SELECT payload_json FROM event_log WHERE user_id=? AND event_type=?`, []any{userID, sectMasterTeachEvent})
	if err != nil {
		return false, err
	}
	for _, row := range r.Rows {
		var rec sectMasterTeachRecord
		if json.Unmarshal([]byte(fmt.Sprint(row[0])), &rec) == nil && rec.Life == life {
			return true, nil
		}
	}
	return false, nil
}

func sectMasterTeachAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectMasterTeachPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	mem, err := sectMembershipRow(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("only a sect member has a master to teach them")
	}
	master := livingNPCMasterTx(conn, catalog, userID)
	if master == "" {
		return authoritativeMutation{}, errors.New("you have no living master among the sect's people to teach you")
	}
	rule := npcMasterRuleGo(catalog)
	if rule.TeachRankLevel > 0 && i64(mem["rank_level"]) < rule.TeachRankLevel {
		return authoritativeMutation{}, fmt.Errorf("%s teaches the sect's arts to a %s or above; you hold %s", master, sectRankName(catalog, rule.TeachRankLevel), fmt.Sprint(mem["rank_name"]))
	}
	life := soulLifeTx(conn, userID)
	taught, err := sectMasterTaughtThisLifeTx(conn, userID, life)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if taught {
		return authoritativeMutation{}, fmt.Errorf("%s has already taught you what they will in this life", master)
	}
	c, err := loadMechanicsCharacter(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	owned, err := ownedManualKeys(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	sect := fmt.Sprint(mem["sect_name"])
	manualID := sectEntryManual(catalog, sect, c, owned)
	if manualID == "" {
		return authoritativeMutation{}, fmt.Errorf("%s has nothing of the %s's arts you do not already hold", master, sect)
	}
	m := catalog.TechniqueSystem.Manuals[manualID]
	if err := addInventoryTx(conn, userID, map[string]int64{m.ItemID: 1}); err != nil {
		return authoritativeMutation{}, err
	}
	now := nowSeconds()
	legal := "clean"
	if manualForbidden(m) {
		legal = "forbidden"
	}
	if tableExistsTx(conn, "item_provenance") {
		if _, err := conn.Execute(`INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(?,?,1,'sect_master',?,?,?,100,0,?,?,?)`,
			[]any{userID, m.ItemID, master, sect + " teaching of " + master, legal, p.GameMinute, now, now}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	encoded, _ := json.Marshal(sectMasterTeachRecord{Life: life, Master: master, ManualID: manualID})
	if _, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, sectMasterTeachEvent, string(encoded), now}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"master_npc_name": master, "manual_id": manualID, "name": m.Name, "item_id": m.ItemID, "min_realm_index": m.MinRealmIndex, "alignment": m.Alignment, "path": m.Path}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect.master.teach", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// nextPromotionRung is the lowest rung of the content's ladder above the rank a
// member holds: the rank they could be raised to and the lifetime contribution
// it asks. ok is false when the ladder reaches nothing higher.
func nextPromotionRung(catalog worlddata.Catalog, rank int64) (level, earned int64, ok bool) {
	for _, rung := range catalog.SectExchange().Promotion {
		if rung.RankLevel > rank && (!ok || rung.RankLevel < level) {
			level, earned, ok = rung.RankLevel, rung.Earned, true
		}
	}
	return level, earned, ok
}

// sectEligibleRankTx is the rank a member could be raised to now, or "" when
// their lifetime contribution does not reach the next rung (or there is none).
func sectEligibleRankTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (string, error) {
	if !sectEarnedColumn(conn) {
		return "", nil
	}
	r, err := conn.Execute(`SELECT rank_level,contribution_earned FROM sect_membership WHERE user_id=?`, []any{userID})
	if err != nil {
		return "", err
	}
	row := firstRowMap(r)
	if row == nil {
		return "", nil
	}
	level, earned, ok := nextPromotionRung(catalog, i64(row["rank_level"]))
	if !ok || i64(row["contribution_earned"]) < earned {
		return "", nil
	}
	return sectRankName(catalog, level), nil
}

type sectPromotePayload struct {
	NPCName    string `json:"npc_name"`
	GameMinute int64  `json:"game_minute"`
}

func sectPromoteAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p sectPromotePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.NPCName = strings.TrimSpace(p.NPCName)
	mem, err := sectMembershipRow(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if mem == nil {
		return authoritativeMutation{}, errors.New("only a sect member can be raised in rank")
	}
	sect, rank := fmt.Sprint(mem["sect_name"]), i64(mem["rank_level"])
	level, need, ok := nextPromotionRung(catalog, rank)
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("no rank above %s is granted for contribution; you hold %s", fmt.Sprint(mem["rank_name"]), fmt.Sprint(mem["rank_name"]))
	}
	target := sectRankName(catalog, level)
	earned := i64(mem["contribution_earned"])
	if earned < need {
		return authoritativeMutation{}, fmt.Errorf("%s asks %d contribution earned in the sect; you have earned %d", target, need, earned)
	}
	npc, found, err := sectNPCTx(conn, catalog, p.NPCName)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if !found || !strings.EqualFold(npc.Sect, sect) {
		return authoritativeMutation{}, fmt.Errorf("%s is not one of the %s's people", firstNonempty(p.NPCName, "that person"), sect)
	}
	if !npc.Alive {
		return authoritativeMutation{}, fmt.Errorf("%s is dead", npc.Name)
	}
	rule := npcMasterRuleGo(catalog)
	isMaster := strings.EqualFold(npcMasterOfTx(conn, userID), npc.Name)
	if !isMaster && rule.PromoterRankLevel > 0 && npc.RankLevel < rule.PromoterRankLevel {
		return authoritativeMutation{}, fmt.Errorf("a rank is granted by your master or a %s or above; %s is a %s", sectRankName(catalog, rule.PromoterRankLevel), npc.Name, firstNonempty(npc.Rank, "member"))
	}
	if npc.RankLevel <= level {
		return authoritativeMutation{}, fmt.Errorf("%s is a %s and cannot raise anybody to %s", npc.Name, firstNonempty(npc.Rank, "member"), target)
	}
	c, err := loadMechanicsCharacter(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if err := standingWithTx(conn, catalog, npc.Name, c.Location, p.GameMinute); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE sect_membership SET rank_level=?,rank_name=? WHERE user_id=? AND rank_level<?`, []any{level, target, userID, level}); err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{"sect_name": sect, "promoted_to": target, "rank_level": level, "granted_by": npc.Name, "by_master": isMaster}
	if next, err := sectEligibleRankTx(conn, catalog, userID); err == nil && next != "" {
		out["eligible_for"] = next
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "sect", EventType: "sect.promote", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
