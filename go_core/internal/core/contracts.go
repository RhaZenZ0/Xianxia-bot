package core

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"
)

const APIVersion = "v1"

type Request struct {
	APIVersion      string          `json:"api_version"`
	Operation       string          `json:"operation"`
	IdempotencyKey  string          `json:"idempotency_key"`
	ActorID         string          `json:"actor_id"`
	ExpectedVersion int64           `json:"expected_version"`
	Payload         json.RawMessage `json:"payload"`
}

type Response struct {
	APIVersion     string `json:"api_version"`
	Operation      string `json:"operation"`
	IdempotencyKey string `json:"idempotency_key"`
	StateVersion   int64  `json:"state_version"`
	Result         any    `json:"result"`
}

type ContractError struct {
	Code    string
	Message string
	Status  int
}

func (e *ContractError) Error() string { return e.Message }

func (e *ContractError) Payload() map[string]any {
	return map[string]any{
		"api_version": APIVersion,
		"error":       map[string]string{"code": e.Code, "message": e.Message},
	}
}

func invalid(code, message string) *ContractError {
	return &ContractError{Code: code, Message: message, Status: 400}
}

func Validate(req Request) *ContractError {
	if req.APIVersion != APIVersion {
		return invalid("unsupported_version", "Expected v1.")
	}
	supported := map[string]bool{
		"check.resolve": true, "quest.progress": true,
		"relationship.update": true, "scene.transition": true,
	}
	if !supported[req.Operation] {
		name := req.Operation
		if name == "" {
			name = "<empty>"
		}
		return invalid("unsupported_operation", fmt.Sprintf("Unsupported operation: %s.", name))
	}
	key := strings.TrimSpace(req.IdempotencyKey)
	if key == "" || len([]rune(key)) > 160 {
		return invalid("invalid_idempotency_key", "Idempotency key must contain 1..160 characters.")
	}
	actor := strings.TrimSpace(req.ActorID)
	if actor == "" || len(actor) > 32 {
		return invalid("invalid_actor_id", "Actor ID must be a decimal Discord snowflake string.")
	}
	for _, r := range actor {
		if r < '0' || r > '9' {
			return invalid("invalid_actor_id", "Actor ID must be a decimal Discord snowflake string.")
		}
	}
	if req.ExpectedVersion < 0 {
		return invalid("invalid_expected_version", "Expected version cannot be negative.")
	}
	if req.ExpectedVersion == math.MaxInt64 {
		return invalid("invalid_expected_version", "Expected version is too large to advance.")
	}
	if len(req.Payload) == 0 || string(req.Payload) == "null" {
		return invalid("invalid_payload", "Payload must be an object.")
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(req.Payload, &object); err != nil || object == nil {
		return invalid("invalid_payload", "Payload must be an object.")
	}
	return nil
}

func Apply(req Request) (Response, *ContractError) {
	if err := Validate(req); err != nil {
		return Response{}, err
	}
	var result any
	var err *ContractError
	switch req.Operation {
	case "check.resolve":
		result, err = resolveCheck(req.Payload)
	case "relationship.update":
		result, err = updateRelationship(req.Payload)
	case "quest.progress":
		result, err = progressQuest(req.Payload)
	case "scene.transition":
		result, err = transitionScene(req.Payload)
	}
	if err != nil {
		return Response{}, err
	}
	return Response{
		APIVersion: APIVersion, Operation: req.Operation,
		IdempotencyKey: req.IdempotencyKey,
		StateVersion:   req.ExpectedVersion + 1, Result: result,
	}, nil
}

func degree(margin int64) string {
	switch {
	case margin >= 10:
		return "Overwhelming Success"
	case margin >= 5:
		return "Strong Success"
	case margin >= 0:
		return "Success"
	case margin >= -3:
		return "Soft Failure"
	case margin >= -7:
		return "Hard Failure"
	default:
		return "Severe Failure"
	}
}

type checkPayload struct {
	Dice     []int64 `json:"dice"`
	Modifier int64   `json:"modifier"`
	TN       int64   `json:"tn"`
}

func resolveCheck(raw json.RawMessage) (any, *ContractError) {
	var payload checkPayload
	if err := json.Unmarshal(raw, &payload); err != nil || len(payload.Dice) != 2 {
		return nil, invalid("invalid_dice", "check.resolve requires exactly two injected d10 values.")
	}
	if payload.Dice[0] < 1 || payload.Dice[0] > 10 || payload.Dice[1] < 1 || payload.Dice[1] > 10 {
		return nil, invalid("invalid_dice", "Injected d10 values must be within 1..10.")
	}
	total := saturatingAdd(saturatingAdd(payload.Dice[0], payload.Dice[1]), payload.Modifier)
	margin := saturatingSub(total, payload.TN)
	return map[string]any{
		"die1": payload.Dice[0], "die2": payload.Dice[1], "modifier": payload.Modifier,
		"tn": payload.TN, "total": total, "margin": margin,
		"success": total >= payload.TN, "degree": degree(margin),
	}, nil
}

var relationshipDimensions = []string{"trust", "respect", "fear", "affection", "debt", "grudge"}

type relationshipPayload struct {
	Current map[string]int64 `json:"current"`
	Deltas  map[string]int64 `json:"deltas"`
	Summary string           `json:"summary"`
}

func clamp(value, low, high int64) int64 {
	if value < low {
		return low
	}
	if value > high {
		return high
	}
	return value
}

func saturatingAdd(value, delta int64) int64 {
	if delta > 0 && value > math.MaxInt64-delta {
		return math.MaxInt64
	}
	if delta < 0 && value < math.MinInt64-delta {
		return math.MinInt64
	}
	return value + delta
}

func saturatingSub(value, delta int64) int64 {
	if delta > 0 && value < math.MinInt64+delta {
		return math.MinInt64
	}
	if delta < 0 && value > math.MaxInt64+delta {
		return math.MaxInt64
	}
	return value - delta
}

func clampAdd(value, delta, low, high int64) int64 {
	return clamp(saturatingAdd(value, delta), low, high)
}

func updateRelationship(raw json.RawMessage) (any, *ContractError) {
	var payload relationshipPayload
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, invalid("invalid_relationship", "Relationship state and deltas must be objects.")
	}
	if payload.Current == nil {
		payload.Current = map[string]int64{}
	}
	if payload.Deltas == nil {
		payload.Deltas = map[string]int64{}
	}
	result := map[string]any{}
	for _, key := range relationshipDimensions {
		result[key] = clampAdd(payload.Current[key], payload.Deltas[key], -100, 100)
	}
	result["encounter_count"] = clamp(payload.Current["encounter_count"], 0, 1<<62) + 1
	runes := []rune(payload.Summary)
	if len(runes) > 800 {
		runes = runes[:800]
	}
	result["last_summary"] = string(runes)
	return result, nil
}

type objective struct {
	ID     string  `json:"id"`
	Type   string  `json:"type"`
	Target *string `json:"target"`
	Count  int64   `json:"count"`
}

type questPayload struct {
	Progress      map[string]int64 `json:"progress"`
	Objectives    []objective      `json:"objectives"`
	ObjectiveType string           `json:"objective_type"`
	Amount        *int64           `json:"amount"`
	Target        *string          `json:"target"`
}

func progressQuest(raw json.RawMessage) (any, *ContractError) {
	var payload questPayload
	if err := json.Unmarshal(raw, &payload); err != nil || payload.Objectives == nil {
		return nil, invalid("invalid_objectives", "quest.progress requires an objective list.")
	}
	if payload.Progress == nil {
		payload.Progress = map[string]int64{}
	}
	updated := map[string]int64{}
	for key, value := range payload.Progress {
		updated[key] = clamp(value, 0, 1<<62)
	}
	touched := false
	complete := true
	for _, objective := range payload.Objectives {
		if objective.ID == "" {
			return nil, invalid("invalid_objective", "Every quest objective requires an ID.")
		}
		required := objective.Count
		if required < 1 {
			required = 1
		}
		current := clamp(updated[objective.ID], 0, required)
		if objective.Type == payload.ObjectiveType {
			matches := objective.Target == nil || payload.Target == nil || strings.EqualFold(*objective.Target, *payload.Target)
			if matches {
				amount := int64(1)
				if payload.Amount != nil {
					amount = *payload.Amount
				}
				if amount < 0 {
					amount = 0
				}
				current = clampAdd(current, amount, 0, required)
				updated[objective.ID] = current
				touched = true
			}
		}
		if current < required {
			complete = false
		}
	}
	return map[string]any{"progress": updated, "touched": touched, "complete": complete}, nil
}

type scenePayload struct {
	PhysicalLocation string                     `json:"physical_location"`
	SceneType        string                     `json:"scene_type"`
	SceneKey         string                     `json:"scene_key"`
	SceneLabel       string                     `json:"scene_label"`
	ChannelID        *int64                     `json:"channel_id"`
	Metadata         map[string]json.RawMessage `json:"metadata"`
}

func truncate(value string, limit int) string {
	runes := []rune(value)
	if len(runes) > limit {
		runes = runes[:limit]
	}
	return string(runes)
}

func transitionScene(raw json.RawMessage) (any, *ContractError) {
	var payload scenePayload
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, invalid("invalid_scene", "Physical location, scene type, and scene key are required.")
	}
	payload.PhysicalLocation = strings.TrimSpace(payload.PhysicalLocation)
	payload.SceneType = strings.TrimSpace(payload.SceneType)
	payload.SceneKey = strings.TrimSpace(payload.SceneKey)
	if payload.PhysicalLocation == "" || payload.SceneType == "" || payload.SceneKey == "" {
		return nil, invalid("invalid_scene", "Physical location, scene type, and scene key are required.")
	}
	label := payload.SceneLabel
	if label == "" {
		label = payload.SceneKey
	}
	if payload.Metadata == nil {
		payload.Metadata = map[string]json.RawMessage{}
	}
	return map[string]any{
		"physical_location": truncate(payload.PhysicalLocation, 200),
		"scene_type":        truncate(payload.SceneType, 80),
		"scene_key":         truncate(payload.SceneKey, 240),
		"scene_label":       truncate(label, 200),
		"channel_id":        payload.ChannelID,
		"metadata":          payload.Metadata,
	}, nil
}
