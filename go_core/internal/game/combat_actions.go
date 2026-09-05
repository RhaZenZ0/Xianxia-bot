package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type combatTurnPayload struct {
	BattleID         int64  `json:"battle_id"`
	Style            string `json:"style"`
	GameMinute       int64  `json:"game_minute"`
	MinutesPerYear   int64  `json:"minutes_per_year"`
	BaseSamsaraYears int64  `json:"base_samsara_years"`
	MaxWaitSeconds   int64  `json:"max_wait_seconds"`
	Action           string `json:"action"`
}
type combatTechniquePayload struct {
	BattleID         int64  `json:"battle_id"`
	Technique        string `json:"technique"`
	GameMinute       int64  `json:"game_minute"`
	MinutesPerYear   int64  `json:"minutes_per_year"`
	BaseSamsaraYears int64  `json:"base_samsara_years"`
	MaxWaitSeconds   int64  `json:"max_wait_seconds"`
}
type combatItemPayload struct {
	BattleID   int64  `json:"battle_id"`
	ItemID     string `json:"item_id"`
	GameMinute int64  `json:"game_minute"`
}
type combatFinalizePayload struct {
	BattleID   int64  `json:"battle_id"`
	Outcome    string `json:"outcome"`
	GameMinute int64  `json:"game_minute"`
}

type battleRow struct {
	BattleID, UserID                                       int64
	NPCName                                                string
	NPCRealm, NPCStage, PlayerHP, PlayerMax, NPCHP, NPCMax int64
	Status, Location, Source, TargetKey                    string
	Suppressed, Version                                    int64
}

func loadBattle(conn *storage.Conn, userID, battleID int64) (battleRow, error) {
	q := `SELECT battle_id,user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,status,location,source,target_key,npc_suppressed_turns,version FROM battles WHERE user_id=? AND status='active'`
	args := []any{userID}
	if battleID > 0 {
		q += ` AND battle_id=?`
		args = append(args, battleID)
	}
	q += ` ORDER BY battle_id DESC LIMIT 1`
	r, e := conn.Execute(q, args)
	if e != nil {
		return battleRow{}, e
	}
	if len(r.Rows) == 0 {
		return battleRow{}, errors.New("no active battle")
	}
	x := r.Rows[0]
	return battleRow{i64(x[0]), i64(x[1]), fmt.Sprint(x[2]), i64(x[3]), i64(x[4]), i64(x[5]), i64(x[6]), i64(x[7]), i64(x[8]), fmt.Sprint(x[9]), fmt.Sprint(x[10]), fmt.Sprint(x[11]), fmt.Sprint(x[12]), i64(x[13]), i64(x[14])}, nil
}
func bval(r map[string]any, k string) bool { v, _ := r[k].(bool); return v }
func combatCompanionBonus(conn *storage.Conn, userID int64) (int64, error) {
	bonus := int64(0)
	r, e := conn.Execute(`SELECT rank,evolution_stage,loyalty FROM spirit_beasts WHERE user_id=? AND active=1 ORDER BY beast_id LIMIT 1`, []any{userID})
	if e != nil {
		return 0, e
	}
	if len(r.Rows) > 0 {
		bonus += i64(r.Rows[0][0])/2 + i64(r.Rows[0][1]) + i64(r.Rows[0][2])/40
	}
	a, e := conn.Execute(`SELECT bond_level FROM artifact_bonds WHERE user_id=? AND awakened=1`, []any{userID})
	if e != nil {
		return 0, e
	}
	ab := int64(0)
	for _, x := range a.Rows {
		ab += 1 + i64(x[0])/4
	}
	if ab > 4 {
		ab = 4
	}
	return bonus + ab, nil
}

// equipDefs is a second copy of the same four combat stats as
// equipmentDefinitionsGo (group_combat_actions.go), used only by 1v1 combat.
// Keep bugslayer_sword's tuple here in sync with both that map and
// app/advanced_runtime.py's EQUIPMENT_DEFINITIONS - see
// tests/python/contracts/test_equipment_stat_parity.py.
var equipDefs = map[string][4]int64{"spirit_iron_sword": {4, 0, 1, 0}, "spirit_iron_armor": {0, 5, 1, -1}, "cloud_stepping_boots": {0, 1, 0, 4}, "lesser_stygian_seal": {1, 1, 4, 0}, "bone_comb": {0, 0, 5, 1}, "cracked_nether_mirror": {0, 2, 3, 0}, bugslayerSwordItemID: {5, 1, 1, 1}}

func combatEquipment(conn *storage.Conn, userID int64) (attack, defense, spirit, agility int64, err error) {
	r, e := conn.Execute(`SELECT item_id,durability,max_durability,quality FROM equipment_instances WHERE user_id=? AND equipped=1 AND durability>0`, []any{userID})
	if e != nil {
		err = e
		return
	}
	for _, x := range r.Rows {
		d, ok := equipDefs[fmt.Sprint(x[0])]
		if !ok {
			continue
		}
		condition := float64(i64(x[1])) / float64(maxI64(1, i64(x[2])))
		if condition < 0.25 {
			condition = 0.25
		}
		if condition > 1 {
			condition = 1
		}
		quality := 1 + (float64(i64(x[3]))-100)/200
		if quality < 0.5 {
			quality = 0.5
		}
		attack += int64(math.Round(float64(d[0]) * condition * quality))
		defense += int64(math.Round(float64(d[1]) * condition * quality))
		spirit += int64(math.Round(float64(d[2]) * condition * quality))
		agility += int64(math.Round(float64(d[3]) * condition * quality))
	}
	return
}

// damageEquipmentGo is the single shared choke point for combat durability
// wear across every combat system (1v1, boss raids, bounty-hunter pursuits) -
// see the callers in group_combat_actions.go and economy_actions.go. Routing
// every durability decrement through here (rather than each caller running
// its own copy of this UPDATE pair) is what lets indestructibleEquipmentIDsGo
// protect the Bugslayer Sword everywhere at once.
func damageEquipmentGo(conn *storage.Conn, userID, amount int64) error {
	if amount <= 0 {
		return nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	query := `UPDATE equipment_instances SET durability=MAX(0,durability-?),updated_at=? WHERE user_id=? AND equipped=1`
	args := []any{amount, now, userID}
	if ids := indestructibleEquipmentIDsGo(); len(ids) > 0 {
		placeholders := make([]string, len(ids))
		for i, id := range ids {
			placeholders[i] = "?"
			args = append(args, id)
		}
		query += ` AND item_id NOT IN (` + strings.Join(placeholders, ",") + `)`
	}
	if _, e := conn.Execute(query, args); e != nil {
		return e
	}
	_, e := conn.Execute(`UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND durability<=0`, []any{now, userID})
	return e
}
func conditionDefinitionGo(key string) (name, category, treatment string) {
	name, category, treatment = strings.Title(strings.ReplaceAll(key, "_", " ")), "Condition", "recovery_pill"
	switch key {
	case "flesh_wound":
		name, category, treatment = "Flesh Wound", "Injury", "recovery_pill"
	case "bone_fracture":
		name, category, treatment = "Bone Fracture", "Injury", "recovery_pill"
	case "meridian_damage":
		name, category, treatment = "Meridian Damage", "Cultivation Injury", "jade_life_herb"
	case "dantian_damage":
		name, category, treatment = "Dantian Damage", "Cultivation Injury", "jade_life_herb"
	case "foundation_crack":
		name, category, treatment = "Foundation Crack", "Cultivation Injury", "jade_life_herb"
	case "soul_wound":
		name, category, treatment = "Soul Wound", "Soul Injury", "heart_calming_pill"
	case "poison":
		name, category, treatment = "Spiritual Poison", "Poison", "purging_phoenix_pill"
	case "qi_deviation":
		name, category, treatment = "Qi Deviation", "Deviation", "heart_calming_pill"
	case "heart_demon":
		name, category, treatment = "Heart Demon", "Heart Demon", "heart_calming_pill"
	}
	return
}
func conditionEffectGo(key string, severity int64) map[string]any {
	if severity < 1 {
		severity = 1
	}
	if severity > 5 {
		severity = 5
	}
	name, category, _ := conditionDefinitionGo(key)
	mods := []map[string]any{}
	switch key {
	case "flesh_wound":
		mods = []map[string]any{{"stat": "body", "operation": "add", "value": -severity}, {"stat": "combat_bonus", "operation": "add", "value": -maxI64(1, (severity+1)/2)}}
	case "bone_fracture":
		mods = []map[string]any{{"stat": "body", "operation": "add", "value": -severity}, {"stat": "agility", "operation": "add", "value": -severity}}
	case "meridian_damage":
		mods = []map[string]any{{"stat": "spirit", "operation": "add", "value": -severity}, {"stat": "cultivation_gain", "operation": "mul", "value": math.Max(0.55, 1.0-float64(severity)*0.07)}, {"stat": "combat_bonus", "operation": "add", "value": -maxI64(1, severity/2)}}
	case "dantian_damage":
		mods = []map[string]any{{"stat": "spirit", "operation": "add", "value": -severity}, {"stat": "cultivation_gain", "operation": "mul", "value": math.Max(0.45, 1.0-float64(severity)*0.10)}, {"stat": "breakthrough_bonus", "operation": "add", "value": -severity}}
	case "foundation_crack":
		mods = []map[string]any{{"stat": "breakthrough_bonus", "operation": "add", "value": -(severity * 2)}, {"stat": "cultivation_gain", "operation": "mul", "value": math.Max(0.50, 1.0-float64(severity)*0.08)}}
	case "soul_wound":
		mods = []map[string]any{{"stat": "insight", "operation": "add", "value": -severity}, {"stat": "spirit", "operation": "add", "value": -severity}, {"stat": "sense_precision_bonus", "operation": "add", "value": -(severity * 2)}}
	case "poison":
		mods = []map[string]any{{"stat": "body", "operation": "add", "value": -severity}, {"stat": "agility", "operation": "add", "value": -maxI64(1, severity/2)}}
	case "qi_deviation":
		mods = []map[string]any{{"stat": "spirit", "operation": "add", "value": -severity}, {"stat": "will", "operation": "add", "value": -severity}, {"stat": "cultivation_gain", "operation": "mul", "value": math.Max(0.40, 1.0-float64(severity)*0.11)}}
	case "heart_demon":
		mods = []map[string]any{{"stat": "will", "operation": "add", "value": -severity}, {"stat": "insight", "operation": "add", "value": -maxI64(1, severity/2)}, {"stat": "breakthrough_bonus", "operation": "add", "value": -(severity * 2)}}
	}
	return map[string]any{"effect_key": "condition:" + key, "name": name, "category": category, "severity": severity, "special": true, "modifiers": mods, "tags": []string{"persistent", "condition", key}}
}
func applyCombatCondition(conn *storage.Conn, userID int64, key string, severity int64, sourceType, sourceID string, gameMinute int64) (map[string]any, error) {
	if severity < 1 {
		severity = 1
	}
	if severity > 5 {
		severity = 5
	}
	name, category, _ := conditionDefinitionGo(key)
	r, e := conn.Execute(`SELECT condition_id,severity FROM character_conditions WHERE user_id=? AND condition_key=? AND state='active'`, []any{userID, key})
	if e != nil {
		return nil, e
	}
	conditionID := int64(0)
	if len(r.Rows) > 0 {
		conditionID = i64(r.Rows[0][0])
		severity = maxI64(severity, i64(r.Rows[0][1]))
	}
	effect := conditionEffectGo(key, severity)
	ej, _ := json.Marshal(effect)
	now := float64(time.Now().UnixNano()) / 1e9
	if conditionID > 0 {
		_, e = conn.Execute(`UPDATE character_conditions SET severity=?,category=?,name=?,source_type=?,source_id=?,effect_json=?,updated_game_minute=?,updated_at=? WHERE condition_id=?`, []any{severity, category, name, sourceType, sourceID, string(ej), gameMinute, now, conditionID})
	} else {
		_, e = conn.Execute(`INSERT INTO character_conditions(user_id,condition_key,category,name,severity,state,source_type,source_id,effect_json,created_game_minute,updated_game_minute,created_at,updated_at) VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?)`, []any{userID, key, category, name, severity, sourceType, sourceID, string(ej), gameMinute, gameMinute, now, now})
	}
	if e != nil {
		return nil, e
	}
	_, e = conn.Execute(`INSERT INTO active_effects(user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at) VALUES(?,?,?,?,?,?,1,?,NULL,?) ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET name=excluded.name,effect_json=excluded.effect_json,stacks=1,starts_game_minute=excluded.starts_game_minute,ends_game_minute=NULL,created_at=excluded.created_at`, []any{userID, "condition:" + key, name, "condition", key, string(ej), gameMinute, now})
	if e != nil {
		return nil, e
	}
	return map[string]any{"condition_key": key, "name": name, "severity": severity}, nil
}

func spendFateGo(conn *storage.Conn, userID int64, reason string, gameMinute int64) (int64, error) {
	r, e := conn.Execute(`SELECT points,lifetime_earned,lifetime_spent FROM character_fate WHERE user_id=?`, []any{userID})
	if e != nil {
		return 0, e
	}
	points, earned, spent := int64(0), int64(0), int64(0)
	if len(r.Rows) > 0 {
		points = i64(r.Rows[0][0])
		earned = i64(r.Rows[0][1])
		spent = i64(r.Rows[0][2])
	}
	if points <= 0 {
		return 0, nil
	}
	points--
	spent++
	now := float64(time.Now().UnixNano()) / 1e9
	_, e = conn.Execute(`INSERT INTO character_fate(user_id,points,lifetime_earned,lifetime_spent,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET points=excluded.points,lifetime_earned=excluded.lifetime_earned,lifetime_spent=excluded.lifetime_spent,updated_at=excluded.updated_at`, []any{userID, points, earned, spent, now})
	if e != nil {
		return 0, e
	}
	_, e = conn.Execute(`INSERT INTO fate_ledger(user_id,delta,balance_after,reason,game_minute,created_at) VALUES(?,?,?,?,?,?)`, []any{userID, -1, points, reason, gameMinute, now})
	return points, e
}
func addFateGo(conn *storage.Conn, userID int64, reason string, gameMinute int64) (int64, error) {
	r, e := conn.Execute(`SELECT points,lifetime_earned,lifetime_spent FROM character_fate WHERE user_id=?`, []any{userID})
	if e != nil {
		return 0, e
	}
	points, earned, spent := int64(0), int64(0), int64(0)
	if len(r.Rows) > 0 {
		points = i64(r.Rows[0][0])
		earned = i64(r.Rows[0][1])
		spent = i64(r.Rows[0][2])
	}
	old := points
	if points < 9 {
		points++
		earned++
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, e = conn.Execute(`INSERT INTO character_fate(user_id,points,lifetime_earned,lifetime_spent,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET points=excluded.points,lifetime_earned=excluded.lifetime_earned,lifetime_spent=excluded.lifetime_spent,updated_at=excluded.updated_at`, []any{userID, points, earned, spent, now})
	if e != nil {
		return 0, e
	}
	if points > old {
		_, e = conn.Execute(`INSERT INTO fate_ledger(user_id,delta,balance_after,reason,game_minute,created_at) VALUES(?,?,?,?,?,?)`, []any{userID, 1, points, reason, gameMinute, now})
	}
	return points, e
}

type combatStartPayload struct {
	Kind          string `json:"kind"`
	NPCName       string `json:"npc_name"`
	NPCRealmIndex int64  `json:"npc_realm_index"`
	NPCStage      int64  `json:"npc_stage"`
	Severity      int64  `json:"severity"`
	Source        string `json:"source"`
	TargetKey     string `json:"target_key"`
	GameMinute    int64  `json:"game_minute"`
}

// combatStartAction is the authoritative counterpart to Python's former
// Database.create_battle: it decides the opponent's starting stats and
// creates the battle row server-side, instead of trusting a client-computed
// npc_hp. Two encounter kinds are supported, matching the two call sites
// that previously computed this in Python:
//
//   - "challenge": npc_realm_index/npc_stage identify a real, already
//     server-resolved NPC/family-head opponent (resolution of *which* NPC
//     is still a Python/simulation concern - out of scope here); Go owns
//     the HP curve derived from that realm/stage.
//   - "event": the opponent is an ad-hoc "hostile manifestation" scaled off
//     the player's own realm/phase and an event severity - Go derives the
//     opponent's realm/stage itself from the caller's own canonical
//     character row rather than trusting client-supplied npc_realm_index/
//     npc_stage for this kind.
//
// Player HP/HP-max are always read from the caller's own canonical
// characters row, never from the payload, closing the trust gap the old
// Python path left between "read character" and "insert battle".
func combatStartAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p combatStartPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	kind := strings.ToLower(strings.TrimSpace(p.Kind))
	if kind != "challenge" && kind != "event" {
		return authoritativeMutation{}, errors.New("kind must be challenge or event")
	}
	npcName := strings.TrimSpace(p.NPCName)
	if npcName == "" {
		return authoritativeMutation{}, errors.New("npc_name is required")
	}
	source := strings.TrimSpace(p.Source)
	if source == "" {
		return authoritativeMutation{}, errors.New("source is required")
	}
	targetKey := strings.TrimSpace(p.TargetKey)

	c, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot enter battle")
	}
	r, e := conn.Execute(`SELECT vitality,vitality_max FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if len(r.Rows) == 0 {
		return authoritativeMutation{}, errors.New("create a cultivation character first")
	}
	playerHP := maxI64(1, i64(r.Rows[0][0]))
	playerMax := maxI64(playerHP, i64(r.Rows[0][1]))

	var npcRealm, npcStage, npcHP int64
	switch kind {
	case "challenge":
		// realm/stage identify a real NPC already resolved server-side by
		// the caller; Go still owns and clamps the resulting HP curve.
		npcRealm = maxI64(0, p.NPCRealmIndex)
		npcStage = maxI64(1, minI64(9, p.NPCStage))
		npcHP = maxI64(10, 12+npcRealm*4+npcStage*2)
	case "event":
		sev := maxI64(0, p.Severity)
		npcRealm = maxI64(0, c.RealmIndex+maxI64(0, sev-5)/3)
		npcStage = maxI64(1, minI64(9, c.Phase+maxI64(0, sev-4)/2))
		npcHP = maxI64(12, 14+npcRealm*5+npcStage*2+sev*2)
	}

	if targetKey != "" {
		rr, e := conn.Execute(`SELECT 1 FROM battles WHERE target_key=? AND status='active' AND user_id<>? LIMIT 1`, []any{targetKey, userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if len(rr.Rows) > 0 {
			return authoritativeMutation{}, errors.New("that opponent is already locked in an unresolved battle")
		}
	}

	now := float64(time.Now().UnixNano()) / 1e9
	if _, e = conn.Execute(`UPDATE battles SET status='abandoned',version=version+1,updated_at=? WHERE user_id=? AND status='active'`, []any{now, userID}); e != nil {
		return authoritativeMutation{}, e
	}
	ins, e := conn.Execute(
		`INSERT INTO battles(user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,status,location,source,target_key,created_at,updated_at)
		 VALUES(?,?,?,?,?,?,?,?, 'active',?,?,?,?,?)`,
		[]any{userID, npcName, npcRealm, npcStage, playerHP, playerMax, npcHP, npcHP, c.Location, source, targetKey, now, now},
	)
	if e != nil {
		return authoritativeMutation{}, e
	}
	battleID := ins.LastInsertID
	out := map[string]any{
		"battle_id": battleID, "npc_name": npcName, "npc_realm_index": npcRealm, "npc_stage": npcStage,
		"player_hp": playerHP, "player_hp_max": playerMax, "npc_hp": npcHP, "npc_hp_max": npcHP,
		"location": c.Location, "source": source, "target_key": targetKey, "status": "active",
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "battle_started", EntityType: "battle", EntityID: fmt.Sprint(battleID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func combatTurnAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p combatTurnPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	style := strings.ToLower(strings.TrimSpace(p.Style))
	if style != "attack" && style != "defend" && style != "flee" {
		return authoritativeMutation{}, errors.New("style must be attack, defend, or flee")
	}
	c, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot fight")
	}
	b, e := loadBattle(conn, userID, p.BattleID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if b.NPCHP <= 0 {
		return authoritativeMutation{}, errors.New("opponent already defeated; finalize the battle")
	}
	bundle, e := loadAptitudes(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	mods, e := loadEffectModifiers(conn, userID, p.GameMinute, bundle, c, catalog)
	if e != nil {
		return authoritativeMutation{}, e
	}
	resonance := dualCheckBonus(c)
	lawBonus := int64(math.Round(mods.Add["combat_bonus"]))
	comp, e := combatCompanionBonus(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	atk, def, _, eag, e := combatEquipment(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	realm, stage := c.RealmIndex, c.Phase
	php, nhp := b.PlayerHP, b.NPCHP
	lines := []string{}
	out := map[string]any{"battle_id": b.BattleID, "style": style, "npc_name": b.NPCName, "equipment_attack": atk, "equipment_defense": def, "companion_bonus": comp}
	if strings.TrimSpace(p.Action) != "" {
		out["action"] = strings.TrimSpace(p.Action)
	}
	defenseBonus := def
	if style == "flee" {
		mod := mods.value(c.Attributes["agility"], "agility") + realm*2 + stage/3 + resonance + lawBonus + comp + eag
		r, e := roll2d10(mod, 11+b.NPCRealm*2+b.NPCStage/3)
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["player_roll"] = r
		if bval(r, "success") {
			if e = damageEquipmentGo(conn, userID, 1); e != nil {
				return authoritativeMutation{}, e
			}
			now := float64(time.Now().UnixNano()) / 1e9
			_, e = conn.Execute(`UPDATE battles SET status='escaped',version=version+1,updated_at=? WHERE battle_id=?`, []any{now, b.BattleID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			out["escaped"] = true
			out["status"] = "escaped"
			return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "battle_escaped", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
		}
	}
	if style == "defend" {
		defenseBonus = 4 + maxI64(mods.value(c.Attributes["will"], "will"), mods.value(c.Attributes["body"], "body"))/3 + def
		out["defending"] = true
	} else if style == "attack" {
		mod := maxI64(mods.value(c.Attributes["body"], "body"), mods.value(c.Attributes["spirit"], "spirit")) + realm*2 + stage/3 + resonance + lawBonus + comp + atk
		r, e := roll2d10(mod, 10+b.NPCRealm*2+b.NPCStage/3)
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["player_roll"] = r
		if bval(r, "success") {
			margin := i64(r["margin"])
			dmg := maxI64(1, 2+maxI64(0, margin)/3+realm/4+maxI64(0, atk)/3)
			hasBugslayer, bsErr := hasEquippedItemGo(conn, userID, bugslayerSwordItemID)
			if bsErr != nil {
				return authoritativeMutation{}, bsErr
			}
			if bugslayerCombatPassiveTriggers(hasBugslayer, margin) {
				dmg += bugslayerPassiveBonusDamage
				b.Suppressed = maxI64(b.Suppressed, 1)
				out["bugslayer_passive"] = bugslayerPassiveName
				out["bugslayer_bonus_damage"] = bugslayerPassiveBonusDamage
			}
			nhp = maxI64(0, nhp-dmg)
			out["damage_dealt"] = dmg
		}
	}
	if e = damageEquipmentGo(conn, userID, 1); e != nil {
		return authoritativeMutation{}, e
	}
	if nhp <= 0 {
		now := float64(time.Now().UnixNano()) / 1e9
		_, e = conn.Execute(`UPDATE battles SET npc_hp=0,player_hp=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{php, now, b.BattleID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["npc_hp"] = int64(0)
		out["player_hp"] = php
		out["opponent_defeated"] = true
		out["status"] = "active"
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "opponent_defeated", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
	}
	if b.Suppressed > 0 {
		b.Suppressed--
		out["counter_suppressed"] = true
	} else {
		counter, e := roll2d10(4+b.NPCRealm*2+b.NPCStage/3, 10+realm*2+stage/3+defenseBonus+lawBonus+comp)
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["counter_roll"] = counter
		if bval(counter, "success") {
			margin := i64(counter["margin"])
			dmg := maxI64(1, 2+maxI64(0, margin)/4+maxI64(0, b.NPCRealm-realm))
			php = maxI64(0, php-dmg)
			out["damage_taken"] = dmg
			_, e = conn.Execute(`UPDATE characters SET vitality=MAX(0,vitality-?),updated_at=? WHERE user_id=?`, []any{dmg, float64(time.Now().UnixNano()) / 1e9, userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
		}
	}
	out["npc_hp"] = nhp
	out["player_hp"] = php
	if php <= 0 {
		gap := maxI64(0, (b.NPCRealm-realm)*9+(b.NPCStage-stage))
		fatalChance := minI64(75, 8+gap*3)
		rr, e := gamerng.Intn(100)
		if e != nil {
			return authoritativeMutation{}, e
		}
		fatal := int64(rr) < fatalChance
		out["fatality_roll"] = rr
		out["fatality_chance"] = fatalChance
		now := float64(time.Now().UnixNano()) / 1e9
		_, e = conn.Execute(`UPDATE battles SET player_hp=0,npc_hp=?,status='lost',npc_suppressed_turns=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{nhp, b.Suppressed, now, b.BattleID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["status"] = "lost"
		if fatal {
			fr, e := conn.Execute(`SELECT points FROM character_fate WHERE user_id=?`, []any{userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			fate := int64(0)
			if len(fr.Rows) > 0 {
				fate = i64(fr.Rows[0][0])
			}
			if fate > 0 {
				remaining, e := spendFateGo(conn, userID, "averted_true_death:"+b.NPCName, p.GameMinute)
				if e != nil {
					return authoritativeMutation{}, e
				}
				_, e = conn.Execute(`UPDATE characters SET vitality=1,updated_at=? WHERE user_id=?`, []any{now, userID})
				if e != nil {
					return authoritativeMutation{}, e
				}
				key := "bone_fracture"
				sev := int64(2)
				if gap >= 18 {
					key = "soul_wound"
					sev = 3
				}
				inj, e := applyCombatCondition(conn, userID, key, sev, "fate_rescue", fmt.Sprint(b.BattleID), p.GameMinute)
				if e != nil {
					return authoritativeMutation{}, e
				}
				out["fate_rescue"] = true
				out["fate_remaining"] = remaining
				out["injury"] = inj
				out["player_hp"] = int64(1)
			} else {
				death, e := recordTrueDeathAuthoritative(conn, userID, trueDeathPayload{GameMinute: p.GameMinute, Reason: "battle:" + b.NPCName, MinutesPerYear: p.MinutesPerYear, BaseSamsaraYears: p.BaseSamsaraYears, MaxWaitSeconds: p.MaxWaitSeconds})
				if e != nil {
					return authoritativeMutation{}, e
				}
				out["true_death"] = death
			}
		} else {
			key := "flesh_wound"
			sev := int64(1)
			if gap >= 9 {
				key = "bone_fracture"
				sev = 2
			}
			inj, e := applyCombatCondition(conn, userID, key, sev, "battle", fmt.Sprint(b.BattleID), p.GameMinute)
			if e != nil {
				return authoritativeMutation{}, e
			}
			out["injury"] = inj
		}
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "battle_lost", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, e = conn.Execute(`UPDATE battles SET player_hp=?,npc_hp=?,npc_suppressed_turns=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{php, nhp, b.Suppressed, now, b.BattleID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	_ = lines
	out["status"] = "active"
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "battle_turn", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func lawStageIndex(catalog worlddata.Catalog, comp int64) int {
	idx := 0
	for _, s := range catalog.LawSystem.Stages {
		if comp >= int64(s.Min) && s.Index > idx {
			idx = s.Index
		}
	}
	return idx
}
func combatTechniqueAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p combatTechniquePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	t, ok := catalog.LawSystem.Techniques[p.Technique]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown Law technique")
	}
	c, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	b, e := loadBattle(conn, userID, p.BattleID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if b.NPCHP <= 0 {
		return authoritativeMutation{}, errors.New("opponent already defeated")
	}
	r, e := conn.Execute(`SELECT comprehension FROM law_progress WHERE user_id=? AND law_id=?`, []any{userID, t.Law})
	if e != nil {
		return authoritativeMutation{}, e
	}
	comp := int64(0)
	if len(r.Rows) > 0 {
		comp = i64(r.Rows[0][0])
	}
	if lawStageIndex(catalog, comp) < t.RequiresStage || c.RealmIndex < int64(t.MinRealmIndex) {
		return authoritativeMutation{}, errors.New("technique requirements are no longer met")
	}
	if p.Technique == "world_collapse" {
		pw, e := conn.Execute(`SELECT 1 FROM personal_worlds WHERE user_id=? LIMIT 1`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if len(pw.Rows) == 0 {
			return authoritativeMutation{}, errors.New("World Collapse requires a stabilized personal world")
		}
	}
	mod := comp/10 + c.RealmIndex*2 + c.Phase/3 + c.Attributes["insight"]
	tn := b.NPCRealm*2 + b.NPCStage/3 + 8
	roll, e := roll2d10(mod, tn)
	if e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"battle_id": b.BattleID, "technique": p.Technique, "technique_name": t.Name, "roll": roll, "npc_hp": b.NPCHP}
	if bval(roll, "success") {
		margin := i64(roll["margin"])
		turns := int64(1)
		if margin >= 5 {
			turns = 2
		}
		switch p.Technique {
		case "spatial_lockdown":
			b.Suppressed = maxI64(b.Suppressed, turns)
			out["suppressed_turns"] = b.Suppressed
		case "spatial_strangulation":
			dmg := maxI64(2, 3+comp/25+maxI64(0, margin)/3)
			b.NPCHP = maxI64(0, b.NPCHP-dmg)
			b.Suppressed = maxI64(b.Suppressed, 1)
			out["damage_dealt"] = dmg
			out["npc_hp"] = b.NPCHP
			out["opponent_defeated"] = b.NPCHP <= 0
		default:
			out["law_dominance"] = true
		}
	}
	// Match combat.turn's equipment wear: every offensive action in this
	// battle system costs durability, not just a plain attack, so a
	// technique can't be spammed as a durability-free alternative.
	if e = damageEquipmentGo(conn, userID, 1); e != nil {
		return authoritativeMutation{}, e
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if b.NPCHP <= 0 {
		_, e = conn.Execute(`UPDATE battles SET npc_hp=0,npc_suppressed_turns=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{b.Suppressed, now, b.BattleID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["npc_hp"] = int64(0)
		out["opponent_defeated"] = true
		out["status"] = "active"
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "opponent_defeated", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
	}

	// A Law technique is still an offensive action taken mid-battle - like a
	// normal combat.turn attack, it exposes the caster to the opponent's
	// counter-attack (respecting suppression) instead of being a free,
	// risk-free way to bypass the risk/injury/fatality economy that every
	// other offensive action in this battle system enforces.
	bundle, e := loadAptitudes(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	mods, e := loadEffectModifiers(conn, userID, p.GameMinute, bundle, c, catalog)
	if e != nil {
		return authoritativeMutation{}, e
	}
	resonance := dualCheckBonus(c)
	lawBonus := int64(math.Round(mods.Add["combat_bonus"]))
	compBonus, e := combatCompanionBonus(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	_, def, _, _, e := combatEquipment(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	realm, stage := c.RealmIndex, c.Phase
	php := b.PlayerHP
	if b.Suppressed > 0 {
		b.Suppressed--
		out["counter_suppressed"] = true
	} else {
		counter, e := roll2d10(4+b.NPCRealm*2+b.NPCStage/3, 10+realm*2+stage/3+def+lawBonus+compBonus+resonance)
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["counter_roll"] = counter
		if bval(counter, "success") {
			margin := i64(counter["margin"])
			dmg := maxI64(1, 2+maxI64(0, margin)/4+maxI64(0, b.NPCRealm-realm))
			php = maxI64(0, php-dmg)
			out["damage_taken"] = dmg
			if _, e = conn.Execute(`UPDATE characters SET vitality=MAX(0,vitality-?),updated_at=? WHERE user_id=?`, []any{dmg, now, userID}); e != nil {
				return authoritativeMutation{}, e
			}
		}
	}
	out["npc_hp"] = b.NPCHP
	out["player_hp"] = php
	if php <= 0 {
		gap := maxI64(0, (b.NPCRealm-realm)*9+(b.NPCStage-stage))
		fatalChance := minI64(75, 8+gap*3)
		rr, e := gamerng.Intn(100)
		if e != nil {
			return authoritativeMutation{}, e
		}
		fatal := int64(rr) < fatalChance
		out["fatality_roll"] = rr
		out["fatality_chance"] = fatalChance
		_, e = conn.Execute(`UPDATE battles SET player_hp=0,npc_hp=?,status='lost',npc_suppressed_turns=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{b.NPCHP, b.Suppressed, now, b.BattleID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["status"] = "lost"
		if fatal {
			fr, e := conn.Execute(`SELECT points FROM character_fate WHERE user_id=?`, []any{userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			fate := int64(0)
			if len(fr.Rows) > 0 {
				fate = i64(fr.Rows[0][0])
			}
			if fate > 0 {
				remaining, e := spendFateGo(conn, userID, "averted_true_death:"+b.NPCName, p.GameMinute)
				if e != nil {
					return authoritativeMutation{}, e
				}
				if _, e = conn.Execute(`UPDATE characters SET vitality=1,updated_at=? WHERE user_id=?`, []any{now, userID}); e != nil {
					return authoritativeMutation{}, e
				}
				key := "bone_fracture"
				sev := int64(2)
				if gap >= 18 {
					key = "soul_wound"
					sev = 3
				}
				inj, e := applyCombatCondition(conn, userID, key, sev, "fate_rescue", fmt.Sprint(b.BattleID), p.GameMinute)
				if e != nil {
					return authoritativeMutation{}, e
				}
				out["fate_rescue"] = true
				out["fate_remaining"] = remaining
				out["injury"] = inj
				out["player_hp"] = int64(1)
			} else {
				death, e := recordTrueDeathAuthoritative(conn, userID, trueDeathPayload{GameMinute: p.GameMinute, Reason: "battle:" + b.NPCName, MinutesPerYear: p.MinutesPerYear, BaseSamsaraYears: p.BaseSamsaraYears, MaxWaitSeconds: p.MaxWaitSeconds})
				if e != nil {
					return authoritativeMutation{}, e
				}
				out["true_death"] = death
			}
		} else {
			key := "flesh_wound"
			sev := int64(1)
			if gap >= 9 {
				key = "bone_fracture"
				sev = 2
			}
			inj, e := applyCombatCondition(conn, userID, key, sev, "battle", fmt.Sprint(b.BattleID), p.GameMinute)
			if e != nil {
				return authoritativeMutation{}, e
			}
			out["injury"] = inj
		}
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "battle_lost", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
	}
	_, e = conn.Execute(`UPDATE battles SET player_hp=?,npc_hp=?,npc_suppressed_turns=?,version=version+1,updated_at=? WHERE battle_id=?`, []any{php, b.NPCHP, b.Suppressed, now, b.BattleID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	out["status"] = "active"
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "law_technique", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func combatRecoveryItemAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p combatItemPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	b, e := loadBattle(conn, userID, p.BattleID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	item, ok := catalog.Items[p.ItemID]
	if !ok || (item.Use.Instant.QiRestore <= 0 && item.Use.Instant.VitalityRestore <= 0) {
		return authoritativeMutation{}, errors.New("item has no instant battle recovery effect")
	}
	r, e := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	if len(r.Rows) == 0 || i64(r.Rows[0][0]) <= 0 {
		return authoritativeMutation{}, errors.New("recovery item is no longer in inventory")
	}
	qty := i64(r.Rows[0][0])
	if qty == 1 {
		_, e = conn.Execute(`DELETE FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	} else {
		_, e = conn.Execute(`UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	}
	if e != nil {
		return authoritativeMutation{}, e
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, e = conn.Execute(`UPDATE characters SET qi=MIN(qi_max,qi+?),vitality=MIN(vitality_max,vitality+?),updated_at=? WHERE user_id=?`, []any{item.Use.Instant.QiRestore, item.Use.Instant.VitalityRestore, now, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	st, e := conn.Execute(`SELECT qi,qi_max,vitality,vitality_max FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	x := st.Rows[0]
	newVitality, newVitalityMax := i64(x[2]), i64(x[3])
	if item.Use.Instant.VitalityRestore > 0 {
		// Keep battles.player_hp in lockstep with characters.vitality the same
		// way every other combat mutation does (combat.turn/combat.technique
		// decrement both together on damage) - a mid-battle heal that only
		// touched characters.vitality left the battle panel's HP bar stale
		// until the next turn recomputed it.
		if _, e = conn.Execute(`UPDATE battles SET player_hp=?,player_hp_max=MAX(player_hp_max,?),version=version+1,updated_at=? WHERE battle_id=? AND status='active'`, []any{newVitality, newVitalityMax, now, b.BattleID}); e != nil {
			return authoritativeMutation{}, e
		}
	}
	out := map[string]any{"battle_id": p.BattleID, "item_id": p.ItemID, "item_name": item.Name, "qi": i64(x[0]), "qi_max": i64(x[1]), "vitality": newVitality, "vitality_max": newVitalityMax, "qi_restore": item.Use.Instant.QiRestore, "vitality_restore": item.Use.Instant.VitalityRestore}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "recovery_item", EntityType: "battle", EntityID: fmt.Sprint(p.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
}

func combatFinalizeAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p combatFinalizePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	outcome := strings.ToLower(strings.TrimSpace(p.Outcome))
	if outcome != "spare" && outcome != "kill" {
		return authoritativeMutation{}, errors.New("outcome must be spare or kill")
	}
	b, e := loadBattle(conn, userID, p.BattleID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if b.NPCHP > 0 {
		return authoritativeMutation{}, errors.New("opponent is still fighting")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, e = conn.Execute(`UPDATE battles SET status='won',final_outcome=?,finalized_at=?,version=version+1,updated_at=? WHERE battle_id=? AND user_id=? AND status='active' AND npc_hp<=0`, []any{outcome, now, now, b.BattleID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	reward := 5 + b.NPCRealm
	_, e = conn.Execute(`UPDATE characters SET insight_xp=insight_xp+?,updated_at=? WHERE user_id=?`, []any{reward, now, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	severity := clampI64(1+b.NPCRealm/4+b.NPCStage/3, 1, 10)
	out := map[string]any{"battle_id": b.BattleID, "outcome": outcome, "npc_name": b.NPCName, "npc_realm_index": b.NPCRealm, "npc_stage": b.NPCStage, "location": b.Location, "source": b.Source, "severity": severity, "insight_xp_awarded": reward, "event_manifestation": strings.HasPrefix(b.Source, "event:")}
	if !strings.HasPrefix(b.Source, "event:") {
		karmaDelta := int64(2)
		repDelta := int64(2)
		if outcome == "kill" {
			karmaDelta = -maxI64(1, severity)
			repDelta = -maxI64(1, severity)
			if !strings.HasPrefix(b.Source, "auction:") {
				_, e = conn.Execute(`UPDATE characters SET karma_score=MAX(-1000,MIN(1000,karma_score+?)),updated_at=? WHERE user_id=?`, []any{karmaDelta, now, userID})
				if e != nil {
					return authoritativeMutation{}, e
				}
			} else {
				karmaDelta = 0
			}
			_, e = conn.Execute(`INSERT INTO grudges(user_id,holder_type,holder_key,intensity,status,reason,created_game_minute,created_at,updated_at) VALUES(?,?,?,?,'active',?,?,?,?) ON CONFLICT(user_id,holder_type,holder_key) WHERE status='active' DO UPDATE SET intensity=MIN(10,grudges.intensity+excluded.intensity),reason=excluded.reason,updated_at=excluded.updated_at`, []any{userID, "victim_lineage", b.NPCName, maxI64(1, severity), "Blood debt created by the death of " + b.NPCName, p.GameMinute, now, now})
			if e != nil {
				return authoritativeMutation{}, e
			}
			out["grudge_intensity_delta"] = maxI64(1, severity)
		} else {
			_, e = conn.Execute(`UPDATE characters SET karma_score=MAX(-1000,MIN(1000,karma_score+2)),updated_at=? WHERE user_id=?`, []any{now, userID})
			if e != nil {
				return authoritativeMutation{}, e
			}
			if severity >= 3 {
				f, e := addFateGo(conn, userID, "meaningful_mercy:"+b.NPCName, p.GameMinute)
				if e != nil {
					return authoritativeMutation{}, e
				}
				out["fate_after"] = f
			}
		}
		_, e = conn.Execute(`INSERT INTO faction_reputation(user_id,faction_key,score,last_reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,faction_key) DO UPDATE SET score=MAX(-100,MIN(100,faction_reputation.score+excluded.score)),last_reason=excluded.last_reason,updated_at=excluded.updated_at`, []any{userID, "Merciful Reputation", repDelta, map[bool]string{true: "spared " + b.NPCName, false: "killed " + b.NPCName}[outcome == "spare"], now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		kr, _ := conn.Execute(`SELECT karma_score FROM characters WHERE user_id=?`, []any{userID})
		if len(kr.Rows) > 0 {
			out["karma_score"] = i64(kr.Rows[0][0])
		}
		out["world_action_type"] = map[bool]string{true: "npc_spared", false: "npc_killed"}[outcome == "spare"]
		aftermath, aftermathErr := applyCombatAftermathTx(conn, userID, b, outcome, severity, p.GameMinute, now)
		if aftermathErr != nil {
			return authoritativeMutation{}, aftermathErr
		}
		out["impacts"] = aftermath.Impacts
	} else {
		eventKey := strings.TrimPrefix(b.Source, "event:")
		out["event_key"] = eventKey
		state, recErr := recordWorldEventActionTx(conn, eventKey, userID, "battle_victory", "defend", b.NPCName, "combat", 0, 0, true, 4, 0, 2, 0, true, "Defeated an event-specific hostile manifestation during "+eventKey+".", p.GameMinute, now)
		if recErr != nil {
			return authoritativeMutation{}, recErr
		}
		out["event_participation"] = state
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "combat", EventType: "battle_finalized", EntityType: "battle", EntityID: fmt.Sprint(b.BattleID), GameMinute: p.GameMinute, Payload: out}}, nil
}
