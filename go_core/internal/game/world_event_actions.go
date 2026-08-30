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

type worldEventActionRule struct {
	Label         string
	Attribute     string
	TN            int64
	Contribution  int64
	Investigation int64
	Support       int64
	Interference  int64
}

var worldEventActionRules = map[string]worldEventActionRule{
	"observe":     {"Observe", "insight", 10, 1, 0, 0, 0},
	"investigate": {"Investigate", "insight", 12, 2, 2, 0, 0},
	"aid":         {"Aid Locals", "heart", 12, 2, 0, 2, 0},
	"support":     {"Support Response", "heart", 13, 3, 0, 3, 0},
	"interfere":   {"Interfere", "insight", 14, -1, 0, 0, 3},
	"stabilize":   {"Stabilize", "spirit", 14, 3, 0, 2, 0},
	"evacuate":    {"Evacuate", "heart", 12, 2, 0, 3, 0},
	"defend":      {"Defend", "body", 14, 3, 0, 2, 0},
	"gather":      {"Gather Resources", "insight", 12, 2, 0, 0, 0},
	"compete":     {"Compete", "body", 14, 2, 0, 0, 0},
	"negotiate":   {"Negotiate", "heart", 13, 2, 0, 1, 0},
	"infiltrate":  {"Infiltrate", "insight", 15, 2, 0, 0, 2},
	"exploit":     {"Exploit Opportunity", "insight", 15, 1, 0, 0, 1},
	"endure":      {"Endure", "body", 13, 2, 0, 0, 0},
	"withdraw":    {"Withdraw", "heart", 0, 0, 0, 0, 0},
}

type worldEventActPayload struct {
	EventKey   string `json:"event_key"`
	ActionKey  string `json:"action_key"`
	GameMinute int64  `json:"game_minute"`
}

func recordWorldEventActionTx(conn *storage.Conn, eventKey string, userID int64, actionKey, stance, target, attribute string, total, tn int64, success bool, contribution, investigation, support, interference int64, combatVictory bool, detail string, gameMinute int64, now float64) (map[string]any, error) {
	successI := int64(0)
	failureI := int64(1)
	if success {
		successI, failureI = 1, 0
	}
	combatI := int64(0)
	if combatVictory {
		combatI = 1
	}
	if strings.TrimSpace(stance) == "" {
		stance = actionKey
	}
	_, err := conn.Execute(`INSERT INTO world_event_participation(
        event_key,user_id,stance,contribution,investigation,support,interference,combat_victories,
        actions_taken,successes,failures,last_action,last_target,first_game_minute,last_game_minute,updated_at
    ) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)
    ON CONFLICT(event_key,user_id) DO UPDATE SET
        stance=excluded.stance,
        contribution=world_event_participation.contribution+excluded.contribution,
        investigation=world_event_participation.investigation+excluded.investigation,
        support=world_event_participation.support+excluded.support,
        interference=world_event_participation.interference+excluded.interference,
        combat_victories=world_event_participation.combat_victories+excluded.combat_victories,
        actions_taken=world_event_participation.actions_taken+1,
        successes=world_event_participation.successes+excluded.successes,
        failures=world_event_participation.failures+excluded.failures,
        last_action=excluded.last_action,last_target=excluded.last_target,last_game_minute=excluded.last_game_minute,updated_at=excluded.updated_at`,
		[]any{eventKey, userID, stance, contribution, investigation, support, interference, combatI, successI, failureI, actionKey, target, gameMinute, gameMinute, now})
	if err != nil {
		return nil, err
	}
	_, err = conn.Execute(`INSERT INTO world_event_actions(
        event_key,user_id,action_key,stance,target,attribute,total,tn,success,contribution_delta,detail,game_minute,created_at
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`, []any{eventKey, userID, actionKey, stance, target, attribute, total, tn, successI, contribution, detail, gameMinute, now})
	if err != nil {
		return nil, err
	}
	res, err := conn.Execute(`SELECT event_key,user_id,stance,contribution,investigation,support,interference,combat_victories,actions_taken,successes,failures,last_action,last_target,first_game_minute,last_game_minute FROM world_event_participation WHERE event_key=? AND user_id=?`, []any{eventKey, userID})
	if err != nil || len(res.Rows) == 0 {
		return nil, err
	}
	cols := res.Columns
	out := map[string]any{}
	for i, col := range cols {
		if i < len(res.Rows[0]) {
			out[col] = res.Rows[0][i]
		}
	}
	return out, nil
}

func worldEventActAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p worldEventActPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	eventKey := strings.TrimSpace(p.EventKey)
	actionKey := strings.ToLower(strings.TrimSpace(p.ActionKey))
	if eventKey == "" {
		return authoritativeMutation{}, errors.New("event_key is required")
	}
	rule, ok := worldEventActionRules[actionKey]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown world-event action")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living character can act in a world event")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	er, err := conn.Execute(`SELECT event_type,title,location,payload_json,active,ends_at FROM world_events WHERE event_key=? LIMIT 1`, []any{eventKey})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(er.Rows) == 0 {
		return authoritativeMutation{}, errors.New("world event not found")
	}
	row := er.Rows[0]
	title, location := fmt.Sprint(row[1]), fmt.Sprint(row[2])
	if storage.ParseInt(row[4]) != 1 || parseFloat(row[5]) <= now {
		return authoritativeMutation{}, errors.New("world event is closed")
	}
	if c.Location != location {
		return authoritativeMutation{}, fmt.Errorf("travel to %s before acting in this event", location)
	}
	payload := map[string]any{}
	_ = json.Unmarshal([]byte(fmt.Sprint(row[3])), &payload)
	severity := clampI64(storage.ParseInt(payload["severity"]), 1, 10)
	if severity == 0 {
		severity = 1
	}

	out := map[string]any{"event_key": eventKey, "title": title, "action_key": actionKey, "label": rule.Label, "severity": severity}
	if actionKey == "withdraw" {
		state, e := recordWorldEventActionTx(conn, eventKey, userID, actionKey, "withdrawn", title, "", 0, 0, true, 0, 0, 0, 0, false, "Withdrew from active participation.", p.GameMinute, now)
		if e != nil {
			return authoritativeMutation{}, e
		}
		out["success"] = true
		out["state"] = state
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "world_event", EventType: "withdraw", EntityType: "world_event", EntityID: eventKey, GameMinute: p.GameMinute, Payload: out}}, nil
	}

	bonus, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, rule.Attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	tn := rule.TN + maxI64(0, severity-2)/2
	roll, err := roll2d10(bonus, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := boolResult(roll)
	contribution, investigation, support, interference := int64(0), int64(0), int64(0), int64(0)
	if success {
		contribution, investigation, support, interference = rule.Contribution, rule.Investigation, rule.Support, rule.Interference
	} else if rule.Contribution < 0 {
		contribution = rule.Contribution
	}
	detail := fmt.Sprintf("%s %s against severity %d.", rule.Label, map[bool]string{true: "succeeded", false: "failed"}[success], severity)
	state, err := recordWorldEventActionTx(conn, eventKey, userID, actionKey, actionKey, title, rule.Attribute, storage.ParseInt(roll["total"]), tn, success, contribution, investigation, support, interference, false, detail, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out["success"] = success
	out["roll"] = roll
	out["state"] = state

	if success {
		claim, e := conn.Execute(`INSERT OR IGNORE INTO event_claims(user_id,event_key,claimed_at) VALUES(?,?,?)`, []any{userID, eventKey, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if claim.RowsAffected > 0 {
			reward, _ := payload["player_reward"].(map[string]any)
			effect, _ := payload["player_effect"].(map[string]any)
			eventID := strings.TrimSpace(fmt.Sprint(payload["definition_id"]))
			if eventID == "" || eventID == "<nil>" {
				eventID = "participation"
			}
			details, e := applyEventParticipationTx(conn, catalog, userID, c, eventID, reward, effect, storage.ParseInt(payload["karma_delta"]), storage.ParseInt(payload["fate_delta"]), p.GameMinute, now, "world_event_"+eventID)
			if e != nil {
				return authoritativeMutation{}, e
			}
			out["first_participation"] = details
		}
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "world_event", EventType: "action", EntityType: "world_event", EntityID: eventKey, GameMinute: p.GameMinute, Payload: out}}, nil
}

func parseFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case int:
		return float64(x)
	default:
		var f float64
		_, _ = fmt.Sscan(fmt.Sprint(v), &f)
		return f
	}
}
