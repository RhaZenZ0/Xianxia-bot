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

type explorationEventRecord struct {
	EventID           string
	DefinitionID      string
	Title             string
	Category          string
	Kind              string
	Visibility        string
	Location          string
	Severity          int64
	State             string
	Stage             string
	PayloadJSON       string
	CreatedGameMinute int64
	ExpiresAt         float64
	ParticipantStage  string
	ParticipantStatus string
}

type explorationEventActionPayload struct {
	EventID    string `json:"event_id"`
	Action     string `json:"action"`
	GameMinute int64  `json:"game_minute"`
}

type explorationEventActionRule struct {
	Label     string
	Attribute string
	TN        int64
}

var explorationEventActionRules = map[string]explorationEventActionRule{
	"observe":  {Label: "Observe", Attribute: "insight", TN: 10},
	"approach": {Label: "Approach", Attribute: "presence", TN: 11},
	"help":     {Label: "Help", Attribute: "presence", TN: 12},
	"rob":      {Label: "Rob Them", Attribute: "agility", TN: 14},
}

var explorationEventActionRoll = rollCheck

func loadExplorationEventForUserTx(conn *storage.Conn, userID int64, eventID string, now float64) (*explorationEventRecord, error) {
	params := []any{userID, now}
	whereID := ""
	if strings.TrimSpace(eventID) != "" {
		whereID = " AND e.event_id=?"
		params = append(params, strings.TrimSpace(eventID))
	}
	res, err := conn.Execute(`SELECT e.event_id,e.definition_id,e.title,e.category,e.kind,e.visibility,e.location,e.severity,e.state,e.stage,e.payload_json,e.created_game_minute,e.expires_at,p.stage,p.status
		FROM exploration_events e
		JOIN exploration_event_participants p ON p.event_id=e.event_id
		WHERE p.user_id=? AND e.expires_at>?`+whereID+`
		ORDER BY e.created_at DESC LIMIT 1`, params)
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return nil, nil
	}
	r := res.Rows[0]
	return &explorationEventRecord{
		EventID: fmt.Sprint(r[0]), DefinitionID: fmt.Sprint(r[1]), Title: fmt.Sprint(r[2]), Category: fmt.Sprint(r[3]),
		Kind: fmt.Sprint(r[4]), Visibility: fmt.Sprint(r[5]), Location: fmt.Sprint(r[6]), Severity: storage.ParseInt(r[7]),
		State: fmt.Sprint(r[8]), Stage: fmt.Sprint(r[9]), PayloadJSON: fmt.Sprint(r[10]), CreatedGameMinute: storage.ParseInt(r[11]),
		ExpiresAt: asFloat64(r[12]), ParticipantStage: fmt.Sprint(r[13]), ParticipantStatus: fmt.Sprint(r[14]),
	}, nil
}

func asFloat64(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case int:
		return float64(x)
	default:
		var out float64
		_, _ = fmt.Sscan(fmt.Sprint(v), &out)
		return out
	}
}

func activeExplorationEventForUserTx(conn *storage.Conn, userID int64, now float64) (*explorationEventRecord, error) {
	record, err := loadExplorationEventForUserTx(conn, userID, "", now)
	if err != nil || record == nil {
		return record, err
	}
	if record.State != "active" || record.ParticipantStatus != "active" {
		return nil, nil
	}
	return record, nil
}

func explorationEventTakenActionsTx(conn *storage.Conn, eventID string, userID int64) (map[string]bool, error) {
	rows, err := conn.Execute(`SELECT action_key FROM exploration_event_actions WHERE event_id=? AND user_id=?`, []any{eventID, userID})
	if err != nil {
		return nil, err
	}
	out := map[string]bool{}
	for _, row := range rows.Rows {
		if len(row) > 0 {
			out[fmt.Sprint(row[0])] = true
		}
	}
	return out, nil
}

func explorationEventAvailableActionsTx(conn *storage.Conn, record *explorationEventRecord, userID int64) ([]map[string]any, error) {
	if record == nil || record.State != "active" || record.ParticipantStatus != "active" {
		return []map[string]any{}, nil
	}
	taken, err := explorationEventTakenActionsTx(conn, record.EventID, userID)
	if err != nil {
		return nil, err
	}
	order := []string{"observe", "approach", "help", "rob"}
	out := make([]map[string]any, 0, len(order)+1)
	for _, key := range order {
		if taken[key] {
			continue
		}
		rule := explorationEventActionRules[key]
		out = append(out, map[string]any{"key": key, "label": rule.Label, "attribute": rule.Attribute, "tn": rule.TN})
	}
	out = append(out, map[string]any{"key": "leave", "label": "Leave", "attribute": "", "tn": int64(0)})
	return out, nil
}

func explorationEventPublicTx(conn *storage.Conn, record *explorationEventRecord, userID int64) (map[string]any, error) {
	if record == nil {
		return map[string]any{"active": false}, nil
	}
	var definition worlddata.UnexpectedEvent
	_ = json.Unmarshal([]byte(record.PayloadJSON), &definition)
	actions, err := explorationEventAvailableActionsTx(conn, record, userID)
	if err != nil {
		return nil, err
	}
	active := record.State == "active" && record.ParticipantStatus == "active"
	return map[string]any{
		"active": active, "event_id": record.EventID, "id": record.DefinitionID, "definition_id": record.DefinitionID,
		"title": record.Title, "category": record.Category, "kind": record.Kind, "visibility": record.Visibility,
		"location": record.Location, "severity": record.Severity, "state": record.State, "stage": record.ParticipantStage,
		"description": definition.Description, "consequence_text": definition.ConsequenceText, "expires_at": record.ExpiresAt,
		"available_actions": actions,
	}, nil
}

func createPersonalExplorationEventTx(conn *storage.Conn, userID int64, c mechanicsCharacter, event worlddata.UnexpectedEvent, eventID string, gameMinute int64, now float64) (map[string]any, error) {
	eventID = strings.TrimSpace(eventID)
	if eventID == "" {
		return nil, errors.New("event_key is required when a personal exploration event is selected")
	}
	duration := maxI64(1, event.DurationHours)
	if event.DurationHours <= 0 {
		duration = 2
	}
	expires := now + float64(duration)*3600
	payload, _ := json.Marshal(event)
	severity := event.Severity
	if severity <= 0 {
		severity = 2
	}
	if _, err := conn.Execute(`INSERT INTO exploration_events(event_id,definition_id,title,category,kind,visibility,location,severity,state,stage,payload_json,created_game_minute,expires_at,created_at,updated_at)
		VALUES(?,?,?,?,?,'personal',?,?,'active','introduced',?,?,?,?,?)
		ON CONFLICT(event_id) DO NOTHING`, []any{eventID, event.ID, event.Title, event.Category, event.Kind, c.Location, severity, string(payload), gameMinute, expires, now, now}); err != nil {
		return nil, err
	}
	if _, err := conn.Execute(`INSERT INTO exploration_event_participants(event_id,user_id,stage,status,joined_game_minute,updated_at)
		VALUES(?,?,'introduced','active',?,?) ON CONFLICT(event_id,user_id) DO NOTHING`, []any{eventID, userID, gameMinute, now}); err != nil {
		return nil, err
	}
	record, err := loadExplorationEventForUserTx(conn, userID, eventID, now)
	if err != nil {
		return nil, err
	}
	return explorationEventPublicTx(conn, record, userID)
}

func explorationEventStatusQuery(conn *storage.Conn, userID int64, raw json.RawMessage) (map[string]any, error) {
	var p struct {
		EventID string `json:"event_id"`
	}
	if len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &p); err != nil {
			return nil, err
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	var record *explorationEventRecord
	var err error
	if strings.TrimSpace(p.EventID) == "" {
		record, err = activeExplorationEventForUserTx(conn, userID, now)
	} else {
		record, err = loadExplorationEventForUserTx(conn, userID, p.EventID, now)
	}
	if err != nil {
		return nil, err
	}
	return explorationEventPublicTx(conn, record, userID)
}

func explorationEventActAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, forceLeave bool) (authoritativeMutation, error) {
	var p explorationEventActionPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if forceLeave {
		p.Action = "leave"
	}
	p.EventID = strings.TrimSpace(p.EventID)
	p.Action = strings.ToLower(strings.TrimSpace(p.Action))
	if p.EventID == "" {
		return authoritativeMutation{}, errors.New("event_id is required")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	record, err := loadExplorationEventForUserTx(conn, userID, p.EventID, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if record == nil {
		return authoritativeMutation{}, errors.New("exploration event not found or expired")
	}
	if record.State != "active" || record.ParticipantStatus != "active" {
		return authoritativeMutation{}, errors.New("exploration event is no longer active")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.Location != record.Location {
		return authoritativeMutation{}, errors.New("return to the event location before acting")
	}
	if p.Action == "leave" {
		if _, err := conn.Execute(`INSERT INTO exploration_event_actions(event_id,user_id,action_key,detail,game_minute,created_at) VALUES(?,?,'leave','Left the exploration event.',?,?) ON CONFLICT(event_id,user_id,action_key) DO NOTHING`, []any{record.EventID, userID, p.GameMinute, now}); err != nil {
			return authoritativeMutation{}, err
		}
		if _, err := conn.Execute(`UPDATE exploration_event_participants SET stage='left',status='left',updated_at=? WHERE event_id=? AND user_id=?`, []any{now, record.EventID, userID}); err != nil {
			return authoritativeMutation{}, err
		}
		if _, err := conn.Execute(`UPDATE exploration_events SET state='left',stage='left',updated_at=? WHERE event_id=?`, []any{now, record.EventID}); err != nil {
			return authoritativeMutation{}, err
		}
		record.State, record.Stage, record.ParticipantStage, record.ParticipantStatus = "left", "left", "left", "left"
		eventOut, err := explorationEventPublicTx(conn, record, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		result := map[string]any{"action": "leave", "success": true, "resolved": true, "event": eventOut}
		return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "exploration", EventType: "exploration_event_left", EntityType: "exploration_event", EntityID: record.EventID, SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
	}

	rule, ok := explorationEventActionRules[p.Action]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown exploration event action")
	}
	taken, err := explorationEventTakenActionsTx(conn, record.EventID, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if taken[p.Action] {
		return authoritativeMutation{}, fmt.Errorf("%s has already been attempted in this event", p.Action)
	}
	base, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, rule.Attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	tn := rule.TN + maxI64(0, (record.Severity-2)/2)
	roll, err := explorationEventActionRoll(base+2, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := boolResult(roll)
	stage := p.Action + "_failed"
	if success {
		stage = p.Action + "_succeeded"
	}
	detail := fmt.Sprintf("%s %s against TN %d.", rule.Label, map[bool]string{true: "succeeded", false: "failed"}[success], tn)
	if _, err := conn.Execute(`INSERT INTO exploration_event_actions(event_id,user_id,action_key,attribute,total,tn,success,detail,game_minute,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, []any{record.EventID, userID, p.Action, rule.Attribute, storage.ParseInt(roll["total"]), tn, success, detail, p.GameMinute, now}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE exploration_event_participants SET stage=?,updated_at=? WHERE event_id=? AND user_id=?`, []any{stage, now, record.EventID, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := conn.Execute(`UPDATE exploration_events SET stage=?,updated_at=? WHERE event_id=?`, []any{stage, now, record.EventID}); err != nil {
		return authoritativeMutation{}, err
	}
	record.Stage, record.ParticipantStage = stage, stage

	var definition worlddata.UnexpectedEvent
	if err := json.Unmarshal([]byte(record.PayloadJSON), &definition); err != nil {
		return authoritativeMutation{}, err
	}
	outcome := map[string]any{}
	resolved := false
	if success && p.Action == "help" {
		outcome, err = applyEventParticipationTx(conn, catalog, userID, c, definition.ID, definition.PlayerReward, definition.PlayerEffect, definition.KarmaDelta, definition.FateDelta, p.GameMinute, now, "unexpected_"+definition.ID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		resolved = true
	}
	if success && p.Action == "rob" {
		stolen := map[string]any{"spirit_stones": storage.ParseInt(definition.PlayerReward["spirit_stones"]), "items": definition.PlayerReward["items"]}
		karmaDelta := int64(-2)
		if definition.KarmaDelta < 0 {
			karmaDelta += definition.KarmaDelta
		}
		outcome, err = applyEventParticipationTx(conn, catalog, userID, c, definition.ID+"_rob", stolen, nil, karmaDelta, 0, p.GameMinute, now, "unexpected_"+definition.ID+"_rob")
		if err != nil {
			return authoritativeMutation{}, err
		}
		resolved = true
	}
	if resolved {
		if _, err := conn.Execute(`UPDATE exploration_event_participants SET stage='resolved',status='resolved',updated_at=? WHERE event_id=? AND user_id=?`, []any{now, record.EventID, userID}); err != nil {
			return authoritativeMutation{}, err
		}
		if _, err := conn.Execute(`UPDATE exploration_events SET state='resolved',stage='resolved',updated_at=? WHERE event_id=?`, []any{now, record.EventID}); err != nil {
			return authoritativeMutation{}, err
		}
		record.State, record.Stage, record.ParticipantStage, record.ParticipantStatus = "resolved", "resolved", "resolved", "resolved"
	}
	eventOut, err := explorationEventPublicTx(conn, record, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"action": p.Action, "label": rule.Label, "success": success, "roll": roll, "resolved": resolved, "outcome": outcome, "event": eventOut}
	eventType := "exploration_event_action"
	if resolved {
		eventType = "exploration_event_resolved"
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "exploration", EventType: eventType, EntityType: "exploration_event", EntityID: record.EventID, SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}
