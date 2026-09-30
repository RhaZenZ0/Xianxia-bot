package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The spirit sense (v1.10.0, on the owner's calls): the Formation and
// Inscription twin of a flame, and deliberately its opposite in one way -
// "no spirit capture, you build your spirit". Nothing is found and nothing is
// rolled. Practice fills it:
//
//   - a Formation or Inscription craft (a miss teaches half as much),
//   - a meditation (`cultivation.train` on the qi path),
//   - a successful scene action, at most `scene_gains_per_day` a world day,
//
// each gain raised by the cultivator's Spirit over `spirit_divisor`. When the
// stage's progress is full, qi settles it into the next stage - certain, like
// refining a flame. A stage adds to Formation and Inscription rolls, from
// `min_bonus` at the first to `max_bonus` at the last, and a fully built sense
// opens the Transcendent grade for those two trades exactly as a fully
// refined heavenly flame does for Alchemy and Forging (`opened_min_rank`).

const (
	spiritSenseDefaultMaxStage = int64(9)
	spiritSenseSceneLogType    = "spirit_sense_scene"
)

func spiritSenseRules(catalog worlddata.Catalog) worlddata.SpiritSenseSystem {
	rules := catalog.SpiritSense
	if rules.MaxStage <= 0 {
		rules.MaxStage = spiritSenseDefaultMaxStage
	}
	return rules
}

func spiritSenseServesTrade(rules worlddata.SpiritSenseSystem, profession string) bool {
	for _, trade := range rules.Trades {
		if strings.EqualFold(trade, strings.TrimSpace(profession)) {
			return true
		}
	}
	return false
}

// spiritSenseNeed is the progress a stage asks before it can be settled.
func spiritSenseNeed(rules worlddata.SpiritSenseSystem, stage int64) int64 {
	return rules.StageProgress * (stage + 1)
}

// spiritSenseBonusAt is what a built stage adds to a craft roll: nothing at
// stage 0, then evenly from min_bonus to max_bonus.
func spiritSenseBonusAt(rules worlddata.SpiritSenseSystem, stage int64) int64 {
	if stage <= 0 {
		return 0
	}
	stage = clamp(stage, 1, rules.MaxStage)
	if rules.MaxStage <= 1 {
		return rules.MaxBonus
	}
	return rules.MinBonus + (stage-1)*(rules.MaxBonus-rules.MinBonus)/(rules.MaxStage-1)
}

func spiritSenseOpensTopGrade(rules worlddata.SpiritSenseSystem, stage int64) bool {
	return stage >= rules.MaxStage
}

// loadSpiritSenseTx reads a cultivator's sense. A missing table or row is a
// sense not yet begun, never an error: a craft and a meditation read this and
// must never refuse over it (v1.1.0's migration window).
func loadSpiritSenseTx(conn *storage.Conn, userID int64) (int64, int64, bool) {
	res, err := conn.Execute(`SELECT stage,progress FROM character_spirit_sense WHERE user_id=?`, []any{userID})
	if err != nil {
		return 0, 0, false
	}
	if len(res.Rows) == 0 {
		return 0, 0, true
	}
	return storage.ParseInt(res.Rows[0][0]), storage.ParseInt(res.Rows[0][1]), true
}

// craftSpiritSenseTx is what the sense gives this craft, and whether it opens
// the top grade - nothing for a trade it does not serve.
func craftSpiritSenseTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, profession string) (int64, bool) {
	rules := spiritSenseRules(catalog)
	if !spiritSenseServesTrade(rules, profession) {
		return 0, false
	}
	stage, _, _ := loadSpiritSenseTx(conn, userID)
	return spiritSenseBonusAt(rules, stage), spiritSenseOpensTopGrade(rules, stage)
}

// spiritSenseGainTx adds one practice's progress. `source` is a key of the
// roster's `gains`; `half` halves it (a failed craft). A scene action is
// capped per world day, counted off event_log the way good-deed karma is. It
// never errors: a gain must never cost the action it rides on. The progress
// banks past a full stage - settling is the player's step - but not past what
// the last stage asks.
func spiritSenseGainTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, source string, half bool, gameMinute int64, now float64) map[string]any {
	rules := spiritSenseRules(catalog)
	gain := rules.Gains[source]
	if gain <= 0 {
		return nil
	}
	stage, progress, ok := loadSpiritSenseTx(conn, userID)
	if !ok || stage >= rules.MaxStage {
		return nil
	}
	if source == "scene" {
		key := fmt.Sprintf("day:%d", gameMinute/1440)
		counted, err := conn.Execute(`SELECT COUNT(*) FROM event_log WHERE user_id=? AND event_type=? AND json_extract(payload_json,'$.key')=?`,
			[]any{userID, spiritSenseSceneLogType, key})
		if err != nil || len(counted.Rows) == 0 || storage.ParseInt(counted.Rows[0][0]) >= rules.SceneGainsPerDay {
			return nil
		}
		payload, _ := json.Marshal(map[string]any{"key": key})
		if _, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
			[]any{userID, spiritSenseSceneLogType, string(payload), now}); err != nil {
			return nil
		}
	}
	if rules.SpiritDivisor > 0 {
		if c, err := loadMechanicsCharacter(conn, userID); err == nil {
			gain += c.Attributes["spirit"] / rules.SpiritDivisor
		}
	}
	// A Soul Cultivator's sense builds faster (v1.13.0), rounded and never
	// below the gain it would have had.
	if mult := senseGainMult(catalog, characterPathTx(conn, userID)); mult > 1 {
		gain = max64(gain, int64(math.Round(float64(gain)*mult)))
	}
	if half {
		gain = max64(1, gain/2)
	}
	ceiling := spiritSenseNeed(rules, stage)
	next := min64(ceiling, progress+gain)
	if next == progress {
		// A full stage takes nothing more until it is settled, and says so:
		// a practice that silently built nothing reads as a practice that
		// does not build it (found by the engine playtest).
		return map[string]any{"source": source, "gain": int64(0), "progress": progress, "need": ceiling, "stage": stage, "ready": true, "full": true}
	}
	if _, err := conn.Execute(`INSERT INTO character_spirit_sense(user_id,stage,progress,updated_at) VALUES(?,0,?,?)
		ON CONFLICT(user_id) DO UPDATE SET progress=excluded.progress,updated_at=excluded.updated_at`,
		[]any{userID, next, now}); err != nil {
		return nil
	}
	return map[string]any{"source": source, "gain": next - progress, "progress": next, "need": ceiling, "stage": stage, "ready": next >= ceiling}
}

// spiritSenseSettleAction settles a full stage into the next, for qi.
func spiritSenseSettleAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	rules := spiritSenseRules(catalog)
	stage, progress, ok := loadSpiritSenseTx(conn, userID)
	if !ok {
		return authoritativeMutation{}, errors.New("the spirit sense is not available yet")
	}
	if stage >= rules.MaxStage {
		return authoritativeMutation{}, errors.New("your spirit sense is fully built")
	}
	need := spiritSenseNeed(rules, stage)
	if progress < need {
		return authoritativeMutation{}, fmt.Errorf("your spirit sense is %d/%d toward its next stage; practise Formation or Inscription, meditate, or act in a scene", progress, need)
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	state, err := settleQi(conn, catalog, userID, gameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// A content qi number is a share of the reference pool, capped at half a
	// dantian, so it is flat; the stage's rising need is the price that grows.
	qiCost := state.Cost(rules.SettleQi)
	if state.Qi < qiCost {
		return authoritativeMutation{}, fmt.Errorf("settling the stage takes %d qi and you hold %d", qiCost, state.Qi)
	}
	if _, err = conn.Execute(`UPDATE characters SET qi=qi-?,updated_at=? WHERE user_id=?`, []any{qiCost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	next := stage + 1
	if _, err = conn.Execute(`UPDATE character_spirit_sense SET stage=?,progress=?,updated_at=? WHERE user_id=?`, []any{next, progress - need, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"stage": next, "max_stage": rules.MaxStage, "qi_cost": qiCost, "bonus": spiritSenseBonusAt(rules, next),
		"opens_top_grade": spiritSenseOpensTopGrade(rules, next), "progress": progress - need}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "spirit_sense", EventType: "spirit_sense.settle", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: gameMinute, Payload: result}}, nil
}

// spiritSenseStatusQuery is the sense as it stands, and what builds it. It
// never refuses.
func spiritSenseStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	rules := spiritSenseRules(catalog)
	stage, progress, _ := loadSpiritSenseTx(conn, userID)
	out := map[string]any{
		"description": rules.Description, "trades": rules.Trades, "stage": stage, "max_stage": rules.MaxStage,
		"progress": progress, "bonus": spiritSenseBonusAt(rules, stage), "opens_now": spiritSenseOpensTopGrade(rules, stage),
		"gains": rules.Gains, "scene_gains_per_day": rules.SceneGainsPerDay, "spirit_divisor": rules.SpiritDivisor,
	}
	if stage < rules.MaxStage {
		out["need"] = spiritSenseNeed(rules, stage)
		out["next_bonus"] = spiritSenseBonusAt(rules, stage+1)
		out["settle_qi"] = rules.SettleQi
	}
	return out, nil
}

// adminSetSpiritSense sets a cultivator's stage and progress (v1.10.0): the
// GM's lever for a sense a fault cost somebody, and how a harness reaches a
// settle without days of practice. Held to the roster's range and audited.
func adminSetSpiritSense(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	rules := spiritSenseRules(catalog)
	stage := storage.ParseInt(p["stage"])
	progress := storage.ParseInt(p["progress"])
	if stage < 0 || stage > rules.MaxStage {
		return nil, fmt.Errorf("stage must be 0 to %d", rules.MaxStage)
	}
	if progress < 0 || (stage < rules.MaxStage && progress > spiritSenseNeed(rules, stage)) {
		return nil, fmt.Errorf("progress must be 0 to %d at stage %d", spiritSenseNeed(rules, stage), stage)
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
	beforeStage, beforeProgress, _ := loadSpiritSenseTx(conn, uid)
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO character_spirit_sense(user_id,stage,progress,updated_at) VALUES(?,?,?,?)
		ON CONFLICT(user_id) DO UPDATE SET stage=excluded.stage,progress=excluded.progress,updated_at=excluded.updated_at`,
		[]any{uid, stage, progress, now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_spirit_sense", fmt.Sprintf("user:%d", uid),
		map[string]any{"stage": beforeStage, "progress": beforeProgress}, map[string]any{"stage": stage, "progress": progress}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "stage": stage, "progress": progress, "bonus": spiritSenseBonusAt(rules, stage)}, nil
}
