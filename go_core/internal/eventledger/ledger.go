package eventledger

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

type Receipt struct {
	ActionID     string `json:"action_id"`
	ActorID      int64  `json:"actor_id"`
	Operation    string `json:"operation"`
	StateVersion int64  `json:"state_version"`
	Result       any    `json:"result"`
}

type Event struct {
	EventUID     string
	Domain       string
	EventType    string
	ActorID      *int64
	EntityType   string
	EntityID     string
	SubjectType  string
	SubjectID    string
	GameMinute   int64
	StateVersion int64
	Payload      any
}

func CurrentActorVersion(conn *storage.Conn, actorID int64) (int64, error) {
	res, err := conn.Execute(`SELECT state_version FROM authoritative_actor_versions WHERE actor_id=?`, []any{actorID})
	if err != nil {
		return 0, err
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0, nil
	}
	return storage.ParseInt(res.Rows[0][0]), nil
}

func Replay(conn *storage.Conn, actionID string) (*Receipt, error) {
	actionID = strings.TrimSpace(actionID)
	if actionID == "" {
		return nil, nil
	}
	res, err := conn.Execute(`SELECT actor_id,operation,state_version,result_json FROM authoritative_action_receipts WHERE action_id=?`, []any{actionID})
	if err != nil {
		return nil, err
	}
	if len(res.Rows) == 0 {
		return nil, nil
	}
	row := res.Rows[0]
	if len(row) < 4 {
		return nil, errors.New("invalid authoritative action receipt")
	}
	var result any
	text := fmt.Sprint(row[3])
	if err := json.Unmarshal([]byte(text), &result); err != nil {
		return nil, fmt.Errorf("decode action receipt: %w", err)
	}
	return &Receipt{ActionID: actionID, ActorID: storage.ParseInt(row[0]), Operation: fmt.Sprint(row[1]), StateVersion: storage.ParseInt(row[2]), Result: result}, nil
}

func AdvanceActorVersion(conn *storage.Conn, actorID, expectedVersion int64, now float64) (int64, error) {
	if expectedVersion < 0 {
		return 0, errors.New("expected_version cannot be negative")
	}
	current, err := CurrentActorVersion(conn, actorID)
	if err != nil {
		return 0, err
	}
	if current != expectedVersion {
		return 0, fmt.Errorf("stale expected_version: expected %d current %d", expectedVersion, current)
	}
	next := current + 1
	_, err = conn.Execute(`INSERT INTO authoritative_actor_versions(actor_id,state_version,updated_at) VALUES(?,?,?)
ON CONFLICT(actor_id) DO UPDATE SET state_version=excluded.state_version,updated_at=excluded.updated_at`, []any{actorID, next, now})
	return next, err
}

func TouchEntityVersion(conn *storage.Conn, domain, entityType, entityID string, stateVersion int64, now float64) error {
	domain = strings.TrimSpace(domain)
	entityType = strings.TrimSpace(entityType)
	entityID = strings.TrimSpace(entityID)
	if domain == "" || entityType == "" || entityID == "" {
		return nil
	}
	_, err := conn.Execute(`INSERT INTO authoritative_entity_versions(domain,entity_type,entity_id,state_version,updated_at) VALUES(?,?,?,?,?)
ON CONFLICT(domain,entity_type,entity_id) DO UPDATE SET state_version=excluded.state_version,updated_at=excluded.updated_at`, []any{domain, entityType, entityID, stateVersion, now})
	return err
}

func Append(conn *storage.Conn, event Event) error {
	if strings.TrimSpace(event.EventUID) == "" {
		return errors.New("event_uid is required")
	}
	if strings.TrimSpace(event.Domain) == "" {
		return errors.New("event domain is required")
	}
	if strings.TrimSpace(event.EventType) == "" {
		return errors.New("event type is required")
	}
	payload, err := json.Marshal(event.Payload)
	if err != nil {
		return err
	}
	var actor any
	if event.ActorID != nil {
		actor = *event.ActorID
	}
	_, err = conn.Execute(`INSERT INTO domain_events(event_uid,domain,event_type,actor_id,entity_type,entity_id,subject_type,subject_id,game_minute,state_version,payload_json,created_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, []any{event.EventUID, event.Domain, event.EventType, actor, event.EntityType, event.EntityID, event.SubjectType, event.SubjectID, event.GameMinute, event.StateVersion, string(payload), float64(time.Now().UnixNano()) / 1e9})
	return err
}

func RecordReceipt(conn *storage.Conn, receipt Receipt, now float64) error {
	result, err := json.Marshal(receipt.Result)
	if err != nil {
		return err
	}
	_, err = conn.Execute(`INSERT INTO authoritative_action_receipts(action_id,actor_id,operation,state_version,result_json,created_at) VALUES(?,?,?,?,?,?)`, []any{receipt.ActionID, receipt.ActorID, receipt.Operation, receipt.StateVersion, string(result), now})
	return err
}
